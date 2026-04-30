"""Active-jobs status reporter.

Gives a single-snapshot view of:
 - active ``maildb`` processes (via ``ps``)
 - attachment-extraction counts (overall and per content-type bucket)
 - rows currently ``extracting`` and how long they've been stuck
 - recent throughput (docs/min over a window)
 - rough ETA to drain remaining work

Shipped as a thin library so the CLI layer can wire formatting separately.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from maildb.ingest.extraction import route_content_type

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool


@dataclass
class ProcessInfo:
    pid: int
    elapsed: str  # ps etime column, e.g. "07:06:12" or "1-04:00:00"
    cpu_pct: float
    rss_kb: int
    command: str


@dataclass
class InFlight:
    attachment_id: int
    filename: str
    size_bytes: int
    stuck_for_s: int


@dataclass
class TypeYield:
    """Per-content-type extraction counts and yield ratio."""

    bucket: str  # pdf | docx | xlsx | pptx | image | text | html | doc_legacy | xls_legacy | other
    extracted: int
    failed: int
    skipped: int
    pending: int

    @property
    def total(self) -> int:
        return self.extracted + self.failed + self.skipped + self.pending

    @property
    def yield_pct(self) -> float | None:
        """extracted / (extracted + failed) * 100, or None if both are 0."""
        denom = self.extracted + self.failed
        if denom == 0:
            return None
        return self.extracted / denom * 100.0


@dataclass
class JobsSnapshot:
    processes: list[ProcessInfo]
    counts: dict[str, int]
    in_flight: list[InFlight]
    completed_in_window: dict[str, int]  # extracted/failed/skipped
    window_minutes: int
    rate_per_min: float
    eta_for_status: dict[str, int | None]  # pending/failed: seconds or None
    yield_by_type: list[TypeYield]


def list_maildb_processes(exclude_pid: int | None = None) -> list[ProcessInfo]:
    """Return maildb-related processes by parsing ``ps`` output.

    Matches on ``/maildb`` in the command string so we catch both the
    entrypoint and the spawned worker subprocesses. Skips short-lived wrappers.
    """
    result = subprocess.run(
        ["/bin/ps", "-eo", "pid,pcpu,rss,etime,args"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    processes: list[ProcessInfo] = []
    own = exclude_pid if exclude_pid is not None else os.getpid()

    for raw_line in result.stdout.splitlines()[1:]:  # skip header
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid_s, pcpu_s, rss_s, etime, args = parts
        if "maildb" not in args:
            continue
        # postgres backends show the db name in their args (e.g. "postgres: maildb maildb ...")
        # and the invoking zsh wrapper isn't a job either
        if args.startswith("postgres:"):
            continue
        if args.startswith(("/bin/zsh", "zsh ")):
            continue
        if "grep" in args:
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid == own:
            continue
        try:
            cpu = float(pcpu_s)
            rss = int(rss_s)
        except ValueError:
            continue
        processes.append(
            ProcessInfo(pid=pid, elapsed=etime, cpu_pct=cpu, rss_kb=rss, command=args)
        )
    return processes


def attachment_counts(pool: ConnectionPool) -> dict[str, int]:
    with pool.connection() as conn:
        cur = conn.execute("SELECT status, count(*) FROM attachment_contents GROUP BY status")
        return {status: int(n) for status, n in cur.fetchall()}


def in_flight_rows(pool: ConnectionPool) -> list[InFlight]:
    with pool.connection() as conn:
        cur = conn.execute(
            """
            SELECT ac.attachment_id,
                   a.filename,
                   a.size,
                   extract(epoch from (now() - ac.extracted_at))::int AS stuck_for_s
              FROM attachment_contents ac
              JOIN attachments a ON a.id = ac.attachment_id
             WHERE ac.status = 'extracting'
             ORDER BY ac.extracted_at ASC NULLS LAST
            """
        )
        return [
            InFlight(
                attachment_id=aid,
                filename=filename or "",
                size_bytes=int(size or 0),
                stuck_for_s=int(stuck or 0),
            )
            for aid, filename, size, stuck in cur.fetchall()
        ]


def yield_by_content_type(pool: ConnectionPool) -> list[TypeYield]:
    """Per-bucket extraction counts joined from attachments + attachment_contents.

    Buckets follow ``route_content_type`` (pdf/docx/xlsx/pptx/image/text/html/
    doc_legacy/xls_legacy); anything unsupported folds into ``other`` so the
    output covers the full corpus. Sorted by descending total volume.
    """
    with pool.connection() as conn:
        cur = conn.execute(
            """
            SELECT a.content_type, ac.status, count(*)
              FROM attachment_contents ac
              JOIN attachments a ON a.id = ac.attachment_id
             GROUP BY a.content_type, ac.status
            """
        )
        rows = cur.fetchall()

    by_bucket: dict[str, dict[str, int]] = {}
    for ct, status, n in rows:
        bucket = route_content_type(ct) or "other"
        slot = by_bucket.setdefault(
            bucket, {"extracted": 0, "failed": 0, "skipped": 0, "pending": 0}
        )
        if status in slot:
            slot[status] += int(n)

    out = [TypeYield(bucket=b, **counts) for b, counts in by_bucket.items()]
    out.sort(key=lambda y: y.total, reverse=True)
    return out


def completed_in_window(pool: ConnectionPool, *, window_minutes: int) -> dict[str, int]:
    with pool.connection() as conn:
        cur = conn.execute(
            """
            SELECT status, count(*)
              FROM attachment_contents
             WHERE status IN ('extracted','failed','skipped')
               AND extracted_at > now() - (%s || ' minutes')::interval
             GROUP BY status
            """,
            (window_minutes,),
        )
        return {status: int(n) for status, n in cur.fetchall()}


def snapshot(
    pool: ConnectionPool,
    *,
    window_minutes: int = 30,
    exclude_pid: int | None = None,
) -> JobsSnapshot:
    """Assemble a full snapshot. Window is the rolling period used for rate/ETA."""
    processes = list_maildb_processes(exclude_pid=exclude_pid)
    counts = attachment_counts(pool)
    in_flight = in_flight_rows(pool)
    completed = completed_in_window(pool, window_minutes=window_minutes)
    yield_by_type = yield_by_content_type(pool)

    total_completed = sum(completed.values())
    rate_per_min = total_completed / window_minutes if window_minutes > 0 else 0.0

    eta: dict[str, int | None] = {}
    for status in ("pending", "failed"):
        remaining = counts.get(status, 0)
        if rate_per_min > 0 and remaining > 0:
            eta[status] = int(remaining / rate_per_min * 60)
        else:
            eta[status] = None

    return JobsSnapshot(
        processes=processes,
        counts=counts,
        in_flight=in_flight,
        completed_in_window=completed,
        window_minutes=window_minutes,
        rate_per_min=rate_per_min,
        eta_for_status=eta,
        yield_by_type=yield_by_type,
    )


def format_duration(seconds: int) -> str:
    """Compact duration: '42s', '7m 31s', '3h 04m', '2d 14h'."""
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {s:02d}s"
    hours, m = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {m:02d}m"
    days, h = divmod(hours, 24)
    return f"{days}d {h:02d}h"


def format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n //= 1024
    return f"{n}TB"


def _render_yield_by_type(yields: list[TypeYield]) -> list[str]:
    if not yields:
        return []
    lines = [
        "",
        "## Yield by content-type",
        f"  {'bucket':<11}  {'extracted':>9}  {'failed':>7}  "
        f"{'skipped':>7}  {'pending':>7}  {'yield':>6}",
    ]
    for y in yields:
        yield_s = f"{y.yield_pct:.1f}%" if y.yield_pct is not None else "    —"
        lines.append(
            f"  {y.bucket:<11}  {y.extracted:>9,}  {y.failed:>7,}  "
            f"{y.skipped:>7,}  {y.pending:>7,}  {yield_s:>6}"
        )
    return lines


def render(snap: JobsSnapshot) -> str:
    """Render a JobsSnapshot as a plain-text report."""
    lines: list[str] = ["# maildb jobs", ""]

    lines.append("## Active processes")
    if snap.processes:
        lines.append(f"  {'PID':>7}  {'CPU%':>5}  {'RSS':>8}  {'UPTIME':>10}  COMMAND")
        for p in snap.processes:
            cmd = p.command[:80]
            rss_mb = p.rss_kb // 1024
            lines.append(f"  {p.pid:>7}  {p.cpu_pct:>5.1f}  {rss_mb:>5}MB  {p.elapsed:>10}  {cmd}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("## Attachment extraction")
    for status in ("pending", "extracting", "extracted", "failed", "skipped"):
        n = snap.counts.get(status, 0)
        lines.append(f"  {status:<11} {n:>9,}")

    if snap.in_flight:
        lines.append("")
        lines.append("## In flight")
        for f in snap.in_flight:
            age = format_duration(f.stuck_for_s)
            size = format_bytes(f.size_bytes)
            lines.append(f"  id={f.attachment_id}  {size:>7}  age={age}  {f.filename[:70]}")

    lines.append("")
    lines.append(f"## Throughput (last {snap.window_minutes}m)")
    total = sum(snap.completed_in_window.values())
    lines.append(f"  completed: {total:,}")
    for status in ("extracted", "failed", "skipped"):
        n = snap.completed_in_window.get(status, 0)
        lines.append(f"    {status:<10} {n:>7,}")
    lines.append(f"  rate:      {snap.rate_per_min:.2f} docs/min")
    if snap.rate_per_min > 0:
        lines.append(f"             {snap.rate_per_min * 60:.1f} docs/hour")

    lines.extend(_render_yield_by_type(snap.yield_by_type))

    lines.append("")
    lines.append("## ETA")
    if snap.rate_per_min == 0:
        lines.append("  cannot estimate (no completions in window)")
    else:
        for status, seconds in snap.eta_for_status.items():
            remaining = snap.counts.get(status, 0)
            if seconds is None or remaining == 0:
                continue
            lines.append(f"  drain {status} ({remaining:,} rows): {format_duration(seconds)}")

    return "\n".join(lines) + "\n"
