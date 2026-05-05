"""Spike: try Microsoft MarkItDown on samples from our skipped attachment pool.

Pulls one example per interesting content_type, runs MarkItDown over each,
prints timing + markdown preview. Read-only — no DB writes.

Run with:
  uv run --with 'markitdown[all]' python scripts/spike_markitdown.py
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

# Content types worth testing. The residuals report's "read-as-text fallback"
# bucket is the headline use case. doc_legacy/xls_legacy and zip are bonus.
TARGETS = [
    "text/calendar",
    "application/ics",
    "text/csv",
    "application/json",
    "application/xml",
    "application/rtf",
    "text/x-vcard",
    "application/x-iwork-keynote-sffkey",
    "application/x-iwork-pages-sffpages",
    "application/zip",
    "application/msword",  # doc_legacy — see if MarkItDown does it
    "application/vnd.ms-excel",  # xls_legacy
    "application/octet-stream",  # mystery bucket
    "application/pgp-signature",  # likely ignored, sanity
    "image/svg+xml",
]

ATTACHMENT_BASE = Path("~/maildb/attachments").expanduser()
DSN = "postgresql://maildb@localhost:5432/maildb"


@dataclass
class Sample:
    aid: int
    content_type: str
    filename: str
    size: int
    storage_path: str


def fetch_samples() -> list[Sample]:
    out: list[Sample] = []
    with psycopg.connect(DSN) as conn:
        for ct in TARGETS:
            row = conn.execute(
                """
                SELECT a.id, a.content_type, a.filename, a.size, a.storage_path
                FROM attachment_contents c
                JOIN attachments a ON a.id = c.attachment_id
                WHERE c.status = 'skipped' AND a.content_type = %s
                ORDER BY a.size ASC
                LIMIT 1
                """,
                (ct,),
            ).fetchone()
            if row is None:
                continue
            out.append(Sample(*row))
    return out


def run_one(md, sample: Sample) -> tuple[bool, float, str, str]:
    """Returns (ok, elapsed_s, markdown, error_msg)."""
    full = ATTACHMENT_BASE / sample.storage_path
    t0 = time.perf_counter()
    try:
        result = md.convert(str(full))
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return False, elapsed, "", f"{type(e).__name__}: {e}"
    elapsed = time.perf_counter() - t0
    return True, elapsed, result.markdown or "", ""


def main() -> None:
    print("== MarkItDown spike on skipped attachments ==\n")
    from markitdown import MarkItDown

    md = MarkItDown()
    samples = fetch_samples()
    print(f"Found {len(samples)} samples across {len(TARGETS)} target types\n")

    summary = []
    for s in samples:
        ok, elapsed, markdown, err = run_one(md, s)
        chars = len(markdown)
        status = "OK " if ok else "ERR"
        print(f"--- {status} [{s.content_type}] {s.filename!r} ({s.size}B) ---")
        print(f"    elapsed: {elapsed * 1000:.0f} ms   markdown: {chars} chars")
        if not ok:
            print(f"    error:   {err}")
        else:
            preview = markdown.replace("\n", "\n    ")[:600]
            if not preview.strip():
                preview = "<empty markdown>"
            print(f"    preview:\n    {preview}")
            if chars > 600:
                print(f"    ... [truncated, {chars - 600} more chars]")
        print()
        summary.append((s.content_type, ok, elapsed, chars, err))

    # Final compact table
    print("\n== Summary ==")
    print(f"{'content_type':<40s} {'ok':<4s} {'ms':>6s} {'chars':>7s}  notes")
    for ct, ok, elapsed, chars, err in summary:
        flag = "OK" if ok else "ERR"
        note = err[:50] if not ok else ("empty" if chars == 0 else "")
        print(f"{ct:<40s} {flag:<4s} {elapsed * 1000:>6.0f} {chars:>7d}  {note}")


if __name__ == "__main__":
    main()
