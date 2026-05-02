"""Tests for ~/.maildb/logs/<run-id>/ persistence (issues #72, #77)."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from maildb.ingest import run_logs

if TYPE_CHECKING:
    from pathlib import Path


def test_create_run_log_dir_creates_directory_and_run_json(tmp_path: Path):
    """A fresh run gets a unique run-id directory + an initial run.json with
    pid, start time, and command-line args."""
    rl = run_logs.create_run_log_dir(
        root=tmp_path, command_args=["maildb", "process_attachments", "run"]
    )
    assert rl.dir.is_dir()
    assert rl.dir.parent == tmp_path
    assert rl.drain_log == rl.dir / "drain.log"
    assert rl.run_json == rl.dir / "run.json"
    assert rl.run_json.exists()
    meta = json.loads(rl.run_json.read_text())
    assert meta["run_id"] == rl.run_id
    assert meta["pid"] > 0
    assert meta["started_at"]  # ISO-8601 string
    assert meta["command_args"] == ["maildb", "process_attachments", "run"]
    assert "finished_at" not in meta or meta["finished_at"] is None


def test_run_id_is_sortable_by_creation_time(tmp_path: Path):
    """Run IDs sort lexicographically in creation order — required for
    'find the most recent run' lookups."""
    a = run_logs.create_run_log_dir(root=tmp_path, command_args=[])
    time.sleep(1.1)  # one-second resolution in the timestamp prefix
    b = run_logs.create_run_log_dir(root=tmp_path, command_args=[])
    assert a.run_id < b.run_id


def test_finalize_writes_finish_metadata(tmp_path: Path):
    rl = run_logs.create_run_log_dir(root=tmp_path, command_args=[])
    run_logs.finalize_run(rl, exit_code=0, counts={"extracted": 5, "failed": 1, "skipped": 0})
    meta = json.loads(rl.run_json.read_text())
    assert meta["finished_at"]
    assert meta["exit_code"] == 0
    assert meta["counts"] == {"extracted": 5, "failed": 1, "skipped": 0}


def test_prune_old_run_dirs_keeps_most_recent_n(tmp_path: Path):
    """Older run-id directories are removed once retention is exceeded;
    the most recent N are kept."""
    rls = []
    for _ in range(5):
        rls.append(run_logs.create_run_log_dir(root=tmp_path, command_args=[]))
        time.sleep(1.05)

    run_logs.prune_old_run_dirs(root=tmp_path, keep=3)

    remaining = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    expected = sorted(rl.run_id for rl in rls[-3:])
    assert remaining == expected


def test_prune_old_run_dirs_no_op_when_under_cap(tmp_path: Path):
    rls = [run_logs.create_run_log_dir(root=tmp_path, command_args=[]) for _ in range(2)]
    run_logs.prune_old_run_dirs(root=tmp_path, keep=10)
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 2
    for rl in rls:
        assert rl.dir.is_dir()


def test_find_active_run_log_returns_unfinished_run(tmp_path: Path):
    a = run_logs.create_run_log_dir(root=tmp_path, command_args=[])
    run_logs.finalize_run(a, exit_code=0, counts={})
    time.sleep(1.05)
    b = run_logs.create_run_log_dir(root=tmp_path, command_args=[])

    active = run_logs.find_active_run_log(root=tmp_path)
    assert active is not None
    assert active.run_id == b.run_id


def test_find_active_run_log_returns_none_when_all_finalized(tmp_path: Path):
    rl = run_logs.create_run_log_dir(root=tmp_path, command_args=[])
    run_logs.finalize_run(rl, exit_code=0, counts={})
    assert run_logs.find_active_run_log(root=tmp_path) is None


def test_find_active_run_log_returns_none_when_root_missing(tmp_path: Path):
    """Operator may invoke jobs before any drain has ever run."""
    assert run_logs.find_active_run_log(root=tmp_path / "nonexistent") is None
