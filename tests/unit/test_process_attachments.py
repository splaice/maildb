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


def _fake_extract_result(markdown: str = "# hello\n\nworld"):
    m = MagicMock()
    m.markdown = markdown
    m.extractor_version = "test-v1"
    return m


def test_embed_chunks_raises_when_all_retries_fail() -> None:
    """If single-row retry also fails, _embed_chunks raises EmbedFailedError
    rather than silently writing zero-vector sentinels."""
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    cur = conn.execute.return_value
    # Two chunks pre-inserted and read back for embed
    cur.fetchall.return_value = [(1, 0, "chunk a"), (2, 1, "chunk b")]

    client = MagicMock()
    client._dimensions = 768
    client.embed_batch.side_effect = RuntimeError("ollama down")
    client.embed.side_effect = RuntimeError("ollama still down")

    with (
        patch.object(pa, "_build_embedding_client", return_value=client),
        pytest.raises(pa.EmbedFailedError),
    ):
        pa._embed_chunks(pool, [{"attachment_id": 42}])


def test_embed_chunks_success_writes_real_vectors() -> None:
    """Happy path: real vectors get written, no exception."""
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    cur = conn.execute.return_value
    cur.fetchall.return_value = [(1, 0, "a"), (2, 1, "b")]

    client = MagicMock()
    client._dimensions = 3
    client.embed_batch.return_value = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    with patch.object(pa, "_build_embedding_client", return_value=client):
        pa._embed_chunks(pool, [{"attachment_id": 42}])

    # No zero-vector writes
    written = [c.args for c in conn.execute.call_args_list if "UPDATE" in c.args[0]]
    assert written, "expected UPDATE statements"
    for _sql, params in written:
        assert "[0.0, 0.0, 0.0]" not in params[0]


def test_process_one_empty_markdown_marks_skipped() -> None:
    """Extraction succeeded but produced no text → skipped with 'empty extraction',
    no embed attempted."""
    pool = MagicMock()
    set_status = MagicMock()
    embed = MagicMock()
    with (
        patch.object(
            pa,
            "_load_attachment",
            return_value={
                "id": 1,
                "filename": "x.png",
                "content_type": "image/png",
                "storage_path": "a/x",
            },
        ),
        patch.object(pa, "_run_with_timeout", return_value=_fake_extract_result("")),
        patch.object(pa, "_set_status", set_status),
        patch.object(pa, "_embed_chunks", embed),
    ):
        pa.process_one(pool, 1, attachment_dir=Path("/tmp"))
    set_status.assert_called_once()
    _, kwargs = set_status.call_args
    assert kwargs["status"] == "skipped"
    assert kwargs["reason"] == "empty extraction"
    embed.assert_not_called()


def test_process_one_markdown_produces_no_chunks_marks_skipped() -> None:
    """Markdown present but chunker returns [] (e.g. whitespace-only) → skipped."""
    pool = MagicMock()
    set_status = MagicMock()
    embed = MagicMock()
    with (
        patch.object(
            pa,
            "_load_attachment",
            return_value={
                "id": 1,
                "filename": "x.pdf",
                "content_type": "application/pdf",
                "storage_path": "a/x",
            },
        ),
        patch.object(pa, "_run_with_timeout", return_value=_fake_extract_result("   \n\n   ")),
        patch.object(pa, "chunk_markdown", return_value=[]),
        patch.object(pa, "_set_status", set_status),
        patch.object(pa, "_embed_chunks", embed),
    ):
        pa.process_one(pool, 1, attachment_dir=Path("/tmp"))
    set_status.assert_called_once()
    _, kwargs = set_status.call_args
    assert kwargs["status"] == "skipped"
    assert kwargs["reason"] == "empty extraction"
    embed.assert_not_called()


def test_process_one_embed_failure_marks_failed_and_drops_chunks() -> None:
    """If _embed_chunks raises EmbedFailedError, the row is marked failed with
    reason prefix 'embed failed:' and chunks are deleted for clean retry."""
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    set_status = MagicMock()
    with (
        patch.object(
            pa,
            "_load_attachment",
            return_value={
                "id": 7,
                "filename": "x.pdf",
                "content_type": "application/pdf",
                "storage_path": "a/x",
            },
        ),
        patch.object(pa, "_run_with_timeout", return_value=_fake_extract_result("# h\n\nbody")),
        patch.object(pa, "_embed_chunks", side_effect=pa.EmbedFailedError("ollama timeout")),
        patch.object(pa, "_set_status", set_status),
    ):
        pa.process_one(pool, 7, attachment_dir=Path("/tmp"))
    set_status.assert_called_once()
    _, kwargs = set_status.call_args
    assert kwargs["status"] == "failed"
    assert kwargs["reason"].startswith("embed failed:")
    # chunks deleted for this attachment (one DELETE before insert, one after failure)
    delete_calls = [
        c for c in conn.execute.call_args_list if "DELETE FROM attachment_chunks" in c.args[0]
    ]
    assert len(delete_calls) >= 2


def test_sweep_empty_extractions_flips_rows_without_chunks() -> None:
    """Sweep: status='extracted' with zero chunks → status='skipped', reason='empty extraction'."""
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    cur = conn.execute.return_value
    cur.rowcount = 3

    n = pa.sweep_empty_extractions(pool)

    assert n == 3
    sql = conn.execute.call_args_list[0].args[0]
    assert "UPDATE attachment_contents" in sql
    assert "status = 'skipped'" in sql
    assert "'empty extraction'" in sql


def test_reembed_zero_vectors_re_embeds_them() -> None:
    """reembed_zero_vectors scans for zero-vector chunks, re-embeds each, and
    updates the stored vector."""
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    # First query: find zero-vector chunks
    find_cur = MagicMock()
    find_cur.fetchall.return_value = [(101, 50, "chunk text one")]
    # Subsequent UPDATEs
    update_cur = MagicMock()
    conn.execute.side_effect = [find_cur, update_cur]

    client = MagicMock()
    client._dimensions = 3
    client.embed.return_value = [0.9, 0.1, 0.2]

    with patch.object(pa, "_build_embedding_client", return_value=client):
        stats = pa.reembed_zero_vectors(pool)

    assert stats["reembedded"] == 1
    assert stats["failed"] == 0
    client.embed.assert_called_once_with("chunk text one")


def test_reembed_zero_vectors_marks_row_failed_on_persistent_error() -> None:
    """When embed raises for a chunk, its attachment_contents row is marked failed
    with reason 'embed failed: …' and its chunks are dropped."""
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value
    find_cur = MagicMock()
    find_cur.fetchall.return_value = [(101, 50, "chunk text one")]
    # execute is called for: find → _set_status (inside failure path) → delete
    conn.execute.side_effect = [find_cur] + [MagicMock() for _ in range(10)]

    client = MagicMock()
    client._dimensions = 3
    client.embed.side_effect = RuntimeError("ollama dead")

    set_status = MagicMock()
    with (
        patch.object(pa, "_build_embedding_client", return_value=client),
        patch.object(pa, "_set_status", set_status),
    ):
        stats = pa.reembed_zero_vectors(pool)

    assert stats["reembedded"] == 0
    assert stats["failed"] == 1
    set_status.assert_called_once()
    args, kwargs = set_status.call_args
    assert args[1] == 50  # attachment_id (positional)
    assert kwargs["status"] == "failed"
    assert kwargs["reason"].startswith("embed failed:")


# --- Supervised single-worker with hard-kill timeout -------------------------


def test_find_stuck_extracting_filters_by_claimed_by() -> None:
    """SQL selects 'extracting' rows past the timeout AND owned by this supervisor only.

    Without the ownership filter, parallel supervisors race and mass-mark each
    other's actively-running rows as hard-timeout (issue #59).
    """
    pool = MagicMock()
    cur = pool.connection.return_value.__enter__.return_value.execute.return_value
    cur.fetchall.return_value = [(42,), (99,)]

    result = pa._find_stuck_extracting(pool, extract_timeout_s=300, claimed_by="sup-uuid-A")

    assert result == [42, 99]
    call = pool.connection.return_value.__enter__.return_value.execute.call_args
    sql, params = call.args
    assert "status = 'extracting'" in sql
    assert "extracted_at" in sql
    assert "claimed_by" in sql
    assert params[1] == "sup-uuid-A"


def test_claim_row_writes_claimed_by() -> None:
    """_claim_row tags the row with the supervisor's UUID so the matching
    _find_stuck_extracting query can scope to its own work only."""
    pool = MagicMock()
    cur = pool.connection.return_value.__enter__.return_value.execute.return_value
    cur.fetchone.return_value = (7,)

    pa._claim_row(pool, retry_failed=True, claimed_by="sup-uuid-X")

    call = pool.connection.return_value.__enter__.return_value.execute.call_args
    sql, params = call.args
    assert "claimed_by" in sql
    assert params["claimed_by"] == "sup-uuid-X"


def test_run_single_worker_with_timeout_uses_supervised_path() -> None:
    """When extract_timeout_s>0, workers=1 takes the supervised-subprocess path."""
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
    with (
        patch.object(pa, "ensure_pending_rows", return_value=0),
        patch.object(pa, "_reclaim_stale", return_value=0),
        patch.object(pa, "_claim_and_process_loop") as loop,
        patch.object(pa, "_run_supervised_single_worker") as sup,
    ):
        pa.run(
            pool,
            attachment_dir=Path("/tmp"),
            workers=1,
            database_url="postgresql://x/y",
            extract_timeout_s=300,
        )
    sup.assert_called_once()
    loop.assert_not_called()


def test_run_single_worker_without_timeout_uses_in_process_path() -> None:
    """extract_timeout_s=0 still uses the in-process claim loop (no subprocess)."""
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
    with (
        patch.object(pa, "ensure_pending_rows", return_value=0),
        patch.object(pa, "_reclaim_stale", return_value=0),
        patch.object(pa, "_claim_and_process_loop") as loop,
        patch.object(pa, "_run_supervised_single_worker") as sup,
    ):
        pa.run(
            pool,
            attachment_dir=Path("/tmp"),
            workers=1,
            extract_timeout_s=0,
        )
    loop.assert_called_once()
    sup.assert_not_called()


def test_supervised_run_exits_when_no_work_remaining() -> None:
    """If _count_selected reports 0, supervisor returns without spawning a worker."""
    pool = MagicMock()
    with (
        patch.object(pa, "_count_selected", return_value=0),
        patch.object(pa, "_mp_context") as ctx,
    ):
        pa._run_supervised_single_worker(
            pool,
            attachment_dir=Path("/tmp"),
            retry_failed=True,
            selector_sql="",
            selector_params={},
            database_url="postgresql://x/y",
            extract_timeout_s=300,
        )
    ctx.assert_not_called()


def test_supervised_run_spawns_worker_when_work_remains() -> None:
    """One worker is spawned per iteration until no work remains."""
    pool = MagicMock()
    fake_ctx = MagicMock()
    worker = MagicMock()
    worker.is_alive.side_effect = [True, False]  # poll once then worker exits
    fake_ctx.Process.return_value = worker

    with (
        patch.object(pa, "_count_selected", side_effect=[5, 0]),
        patch.object(pa, "_find_stuck_extracting", return_value=[]),
        patch.object(pa, "_mp_context", return_value=fake_ctx),
        patch.object(pa, "time") as tmod,
    ):
        tmod.sleep = MagicMock()
        pa._run_supervised_single_worker(
            pool,
            attachment_dir=Path("/tmp"),
            retry_failed=True,
            selector_sql="",
            selector_params={},
            database_url="postgresql://x/y",
            extract_timeout_s=300,
        )

    worker.start.assert_called_once()
    worker.kill.assert_not_called()
    worker.join.assert_called()


def test_supervised_run_kills_and_marks_failed_on_stuck() -> None:
    """When _find_stuck_extracting returns a row, supervisor SIGKILLs the worker and
    marks the row failed with reason prefix 'hard-timeout:'."""
    pool = MagicMock()
    fake_ctx = MagicMock()
    worker = MagicMock()
    worker.is_alive.return_value = True
    worker.pid = 12345
    fake_ctx.Process.return_value = worker

    set_status = MagicMock()
    with (
        patch.object(pa, "_count_selected", side_effect=[5, 0]),
        patch.object(pa, "_find_stuck_extracting", side_effect=[[42], []]),
        patch.object(pa, "_mp_context", return_value=fake_ctx),
        patch.object(pa, "_set_status", set_status),
        patch.object(pa, "_killpg_quietly") as killpg,
        patch.object(pa, "time") as tmod,
    ):
        tmod.sleep = MagicMock()
        pa._run_supervised_single_worker(
            pool,
            attachment_dir=Path("/tmp"),
            retry_failed=True,
            selector_sql="",
            selector_params={},
            database_url="postgresql://x/y",
            extract_timeout_s=300,
        )

    worker.kill.assert_called_once()
    killpg.assert_called_once_with(12345)  # process-group kill reaps grandchildren
    set_status.assert_called_once()
    args, kwargs = set_status.call_args
    assert args[1] == 42
    assert kwargs["status"] == "failed"
    assert kwargs["reason"].startswith("hard-timeout:")
    assert "300" in kwargs["reason"]


def test_supervised_run_threads_unique_claimed_by_uuid() -> None:
    """Supervisor generates a UUID once and passes the SAME value to:
    - the spawned worker (so its claims tag the row with this id)
    - _find_stuck_extracting (so it only sees rows it owns)

    Without this, two supervisors race on shared stuck-row detection.
    """
    pool = MagicMock()
    fake_ctx = MagicMock()
    worker = MagicMock()
    worker.is_alive.side_effect = [True, False]
    worker.pid = 99
    fake_ctx.Process.return_value = worker

    find_stuck = MagicMock(return_value=[])
    with (
        patch.object(pa, "_count_selected", side_effect=[5, 0]),
        patch.object(pa, "_find_stuck_extracting", find_stuck),
        patch.object(pa, "_mp_context", return_value=fake_ctx),
        patch.object(pa, "time") as tmod,
    ):
        tmod.sleep = MagicMock()
        pa._run_supervised_single_worker(
            pool,
            attachment_dir=Path("/tmp"),
            retry_failed=True,
            selector_sql="",
            selector_params={},
            database_url="postgresql://x/y",
            extract_timeout_s=300,
        )

    # Worker was given a claimed_by kwarg
    process_kwargs = fake_ctx.Process.call_args.kwargs["kwargs"]
    assert "claimed_by" in process_kwargs
    sup_id = process_kwargs["claimed_by"]
    assert sup_id  # non-empty

    # _find_stuck_extracting was called with the SAME id
    assert find_stuck.call_args.kwargs.get("claimed_by") == sup_id


def test_killpg_quietly_swallows_lookup_errors() -> None:
    """SIGKILL on a pgid that no longer exists should not raise."""
    with patch("os.killpg", side_effect=ProcessLookupError):
        pa._killpg_quietly(12345)  # must not raise


# --- NUL-byte sanitization (issues #62, #67) ---------------------------------


def test_strip_nul_removes_nul_bytes() -> None:
    assert pa._strip_nul("hello\x00world") == "helloworld"
    assert pa._strip_nul("\x00\x00\x00") == ""
    assert pa._strip_nul("clean") == "clean"
    assert pa._strip_nul(None) is None


def test_set_status_strips_nul_from_markdown_and_reason() -> None:
    """Marker can emit raw NUL bytes; PG text fields cannot store them.
    _set_status must scrub both markdown and reason before INSERT — the
    March 2026 email-parse fix only covered email bodies (#62, #67)."""
    pool = MagicMock()
    conn = pool.connection.return_value.__enter__.return_value

    pa._set_status(
        pool,
        99,
        status="extracted",
        markdown="head\x00ing\x00\nbody",
        reason="oops\x00here",
    )

    # Inspect the params dict passed to UPDATE
    call = conn.execute.call_args
    _sql, params = call.args
    assert "\x00" not in params["markdown"]
    assert params["markdown"] == "heading\nbody"
    assert "\x00" not in params["reason"]
    assert params["reason"] == "oopshere"
