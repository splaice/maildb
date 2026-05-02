"""Per-run log directories under ``~/.maildb/logs/<run-id>/``.

Replaces the ad-hoc ``drain.log`` / ``drain-A.log`` / ``retry-drain.log``
files that accumulated in the project root during the Apr 2026 drain
(issues #72, #77). Each supervisor invocation gets its own run-id
directory containing:

  - ``drain.log``  — supervisor structlog output
  - ``run.json``   — start/finish metadata, pid, command args, final counts

A retention cap removes the oldest directories so the home dir doesn't
grow unboundedly. Active runs are discoverable so ``maildb jobs`` can
surface the path of the in-progress drain.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_LOGS_ROOT = Path("~/.maildb/logs").expanduser()
DEFAULT_RETENTION = 20  # keep last N run-id dirs; older are removed


@dataclass
class RunLogDir:
    """The on-disk artifacts for a single drain run."""

    run_id: str
    dir: Path
    drain_log: Path
    run_json: Path


def _new_run_id() -> str:
    """Sortable ID combining UTC timestamp + short random suffix.

    Lexicographic sort matches creation order, so the latest run is
    always the largest name. Suffix avoids collisions when two runs
    start in the same second.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def create_run_log_dir(
    *,
    root: Path = DEFAULT_LOGS_ROOT,
    command_args: list[str],
) -> RunLogDir:
    """Allocate a new run-id directory and seed run.json with start metadata."""
    root.mkdir(parents=True, exist_ok=True)
    run_id = _new_run_id()
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    rl = RunLogDir(
        run_id=run_id,
        dir=run_dir,
        drain_log=run_dir / "drain.log",
        run_json=run_dir / "run.json",
    )
    metadata: dict[str, Any] = {
        "run_id": run_id,
        "pid": os.getpid(),
        "started_at": datetime.now(UTC).isoformat(),
        "command_args": list(command_args),
    }
    rl.run_json.write_text(json.dumps(metadata, indent=2) + "\n")
    return rl


def finalize_run(
    rl: RunLogDir,
    *,
    exit_code: int,
    counts: dict[str, int],
) -> None:
    """Append finish-time, exit code, and final counts to run.json."""
    try:
        meta = json.loads(rl.run_json.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        meta = {"run_id": rl.run_id}
    meta["finished_at"] = datetime.now(UTC).isoformat()
    meta["exit_code"] = exit_code
    meta["counts"] = dict(counts)
    rl.run_json.write_text(json.dumps(meta, indent=2) + "\n")


def attach_file_logger(rl: RunLogDir) -> logging.Handler:
    """Route stdlib logging (and structlog, which goes through stdlib) to
    ``drain.log`` for the duration of the supervisor run.

    Caller is expected to ``logging.getLogger().removeHandler(handler)`` on exit
    so we don't leak handlers across multiple sequential runs in the same process.
    """
    handler = logging.FileHandler(rl.drain_log, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(handler)
    return handler


def prune_old_run_dirs(
    *,
    root: Path = DEFAULT_LOGS_ROOT,
    keep: int = DEFAULT_RETENTION,
) -> int:
    """Remove all but the most recent ``keep`` run directories. Returns the
    number of directories deleted. No-op if the root does not exist."""
    if not root.exists():
        return 0
    dirs = sorted(
        (p for p in root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    to_remove = dirs[: max(0, len(dirs) - keep)]
    for d in to_remove:
        shutil.rmtree(d, ignore_errors=True)
    return len(to_remove)


def find_active_run_log(*, root: Path = DEFAULT_LOGS_ROOT) -> RunLogDir | None:
    """Return the most recent run dir whose ``run.json`` lacks ``finished_at``.

    Used by ``maildb jobs`` to point operators at the live drain log.
    Returns None if the root doesn't exist or every run has already finalized.
    """
    if not root.exists():
        return None
    for d in sorted(
        (p for p in root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    ):
        meta_path = d / "run.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            continue
        if meta.get("finished_at"):
            continue
        return RunLogDir(
            run_id=d.name,
            dir=d,
            drain_log=d / "drain.log",
            run_json=meta_path,
        )
    return None


# Re-export so callers don't need to know about ``time`` here for ordering tests.
__all__ = [
    "DEFAULT_LOGS_ROOT",
    "DEFAULT_RETENTION",
    "RunLogDir",
    "attach_file_logger",
    "create_run_log_dir",
    "finalize_run",
    "find_active_run_log",
    "prune_old_run_dirs",
    "time",
]
