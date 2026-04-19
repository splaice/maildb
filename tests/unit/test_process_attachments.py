from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maildb.ingest import process_attachments as pa


def test_run_rejects_multi_worker_without_database_url() -> None:
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
    with (
        patch.object(pa, "ensure_pending_rows", return_value=0),
        patch.object(pa, "_reclaim_stale", return_value=0),
        pytest.raises(ValueError, match="database_url is required"),
    ):
        pa.run(pool, attachment_dir=Path("/tmp"), workers=2)


def test_run_single_worker_runs_in_process() -> None:
    """workers=1 must call the in-process claim loop, never spawn subprocesses."""
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
    with (
        patch.object(pa, "ensure_pending_rows", return_value=0),
        patch.object(pa, "_reclaim_stale", return_value=0),
        patch.object(pa, "_claim_and_process_loop") as loop,
        patch.object(pa, "ProcessPoolExecutor") as ppe,
    ):
        pa.run(pool, attachment_dir=Path("/tmp"), workers=1)
    loop.assert_called_once()
    ppe.assert_not_called()


def test_run_multi_worker_uses_process_pool_executor() -> None:
    """workers > 1 must dispatch through ProcessPoolExecutor, not threads."""
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
    executor = MagicMock()
    executor.__enter__.return_value = executor
    future = MagicMock()
    future.result.return_value = None
    executor.submit.return_value = future
    with (
        patch.object(pa, "ensure_pending_rows", return_value=0),
        patch.object(pa, "_reclaim_stale", return_value=0),
        patch.object(pa, "ProcessPoolExecutor", return_value=executor) as ppe,
    ):
        pa.run(
            pool,
            attachment_dir=Path("/tmp"),
            workers=4,
            database_url="postgresql://localhost/maildb",
        )
    ppe.assert_called_once_with(max_workers=4)
    assert executor.submit.call_count == 4
    # Each submit should pass the subprocess-safe entrypoint with database_url.
    for call in executor.submit.call_args_list:
        args, kwargs = call
        assert args[0] is pa._subprocess_worker
        assert kwargs["database_url"] == "postgresql://localhost/maildb"
        assert kwargs["attachment_dir"] == Path("/tmp")


def test_subprocess_worker_opens_fresh_pool_and_closes_it() -> None:
    """The subprocess entrypoint must build its own pool and close it on exit."""
    fake_pool = MagicMock()
    with (
        patch.object(pa, "ConnectionPool", return_value=fake_pool) as cp,
        patch.object(pa, "_claim_and_process_loop") as loop,
    ):
        pa._subprocess_worker(
            database_url="postgresql://localhost/maildb",
            attachment_dir=Path("/tmp"),
            retry_failed=True,
            selector_sql="",
            selector_params=None,
        )
    cp.assert_called_once_with(
        conninfo="postgresql://localhost/maildb", min_size=1, max_size=2, open=True
    )
    loop.assert_called_once()
    fake_pool.close.assert_called_once()


def test_run_with_timeout_disabled_calls_fn_directly() -> None:
    """Timeout=0 must skip signal setup entirely and return the function result."""
    calls = []
    result = pa._run_with_timeout(0, lambda: calls.append("x") or "ok")
    assert result == "ok"
    assert calls == ["x"]


def test_run_with_timeout_raises_extraction_timeout_error() -> None:
    """SIGALRM-based timeout: a sleep longer than the ceiling raises and includes the ceiling."""
    with pytest.raises(pa.ExtractionTimeoutError, match="timed out after 1s"):
        pa._run_with_timeout(1, lambda: time.sleep(3))


def test_process_one_timeout_marks_row_failed_with_timeout_reason() -> None:
    """When extract_markdown blows the budget, the row is failed with reason starting
    with 'timed out after' — so ops can query and retry them as a group."""
    pool = MagicMock()
    load_ret = {
        "id": 99,
        "filename": "slow.pdf",
        "content_type": "application/pdf",
        "storage_path": "aa/bb/x",
    }
    set_status = MagicMock()
    with (
        patch.object(pa, "_load_attachment", return_value=load_ret),
        patch.object(pa, "_set_status", set_status),
        patch.object(
            pa,
            "_run_with_timeout",
            side_effect=pa.ExtractionTimeoutError("timed out after 300s"),
        ),
    ):
        pa.process_one(pool, 99, attachment_dir=Path("/tmp"), extract_timeout_s=300)
    set_status.assert_called_once()
    _, kwargs = set_status.call_args
    assert kwargs["status"] == "failed"
    assert kwargs["reason"].startswith("timed out after ")
