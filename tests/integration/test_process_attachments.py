from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

import maildb.ingest.process_attachments as process_attachments_module
from maildb.ingest.process_attachments import (
    _reclaim_stale,
    ensure_pending_rows,
    process_one,
    run,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def _insert_attachment(pool, sha256: str, ct: str, filename: str, size: int = 10) -> int:
    """Insert a minimal attachments row and return its id."""
    with pool.connection() as conn:
        cur = conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (sha256, filename, ct, size, f"{sha256}/{sha256}/{sha256}"),
        )
        att_id = cur.fetchone()[0]
        conn.commit()
    return att_id


def test_ensure_pending_rows_creates_missing(test_pool):
    att_id = _insert_attachment(test_pool, "11", "application/pdf", "a.pdf")
    ensure_pending_rows(test_pool)
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT status FROM attachment_contents WHERE attachment_id = %s", (att_id,)
        )
        assert cur.fetchone()[0] == "pending"
    # Idempotent
    ensure_pending_rows(test_pool)
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM attachment_contents WHERE attachment_id = %s", (att_id,)
        )
        assert cur.fetchone()[0] == 1


def test_process_one_success_path(test_pool, tmp_path: Path):
    att_id = _insert_attachment(test_pool, "22", "text/plain", "greeting.txt")
    # Stage the attachment file on disk where the worker expects it.
    sp = tmp_path / "22" / "22" / "22"
    sp.parent.mkdir(parents=True)
    sp.write_text("Hello world from the attachment")

    ensure_pending_rows(test_pool)
    with patch(
        "maildb.ingest.process_attachments._embed_chunks",
        return_value=None,  # embedding step is stubbed here; covered in later task
    ):
        process_one(test_pool, att_id, attachment_dir=tmp_path)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT status, markdown IS NOT NULL, markdown_bytes, extraction_ms, "
            "extractor_version FROM attachment_contents WHERE attachment_id = %s",
            (att_id,),
        )
        status, has_md, md_bytes, ms, version = cur.fetchone()
    assert status == "extracted"
    assert has_md is True
    assert md_bytes > 0
    assert ms >= 0
    assert version.startswith("passthrough")

    # On-disk mirror written
    mirror = tmp_path / "22" / "22" / "22.md"
    assert mirror.exists()
    assert "Hello world" in mirror.read_text()


def test_process_one_failure_records_reason(test_pool, tmp_path: Path):
    att_id = _insert_attachment(test_pool, "33", "application/pdf", "broken.pdf")
    sp = tmp_path / "33" / "33" / "33"
    sp.parent.mkdir(parents=True)
    sp.write_bytes(b"not really a pdf")

    ensure_pending_rows(test_pool)
    with patch(
        "maildb.ingest.process_attachments.extract_markdown",
        side_effect=Exception("boom"),
    ):
        process_one(test_pool, att_id, attachment_dir=tmp_path)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT status, reason FROM attachment_contents WHERE attachment_id = %s",
            (att_id,),
        )
        status, reason = cur.fetchone()
    assert status == "failed"
    assert reason and "boom" in reason


def test_process_one_unsupported_records_skipped(test_pool, tmp_path: Path):
    att_id = _insert_attachment(test_pool, "44", "audio/mpeg", "voicemail.mp3")
    ensure_pending_rows(test_pool)
    process_one(test_pool, att_id, attachment_dir=tmp_path)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT status, reason FROM attachment_contents WHERE attachment_id = %s",
            (att_id,),
        )
        status, reason = cur.fetchone()
    assert status == "skipped"
    assert "not supported" in reason.lower()


def test_run_processes_multiple(test_pool, tmp_path: Path):
    ids = []
    for i, sha in enumerate(["55", "66", "77"]):
        aid = _insert_attachment(test_pool, sha, "text/plain", f"t{i}.txt")
        sp = tmp_path / sha / sha / sha
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(f"content {i}")
        ids.append(aid)
    ensure_pending_rows(test_pool)
    with patch("maildb.ingest.process_attachments._embed_chunks", return_value=None):
        run(test_pool, attachment_dir=tmp_path, workers=1)

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM attachment_contents WHERE status = 'extracted'")
        assert cur.fetchone()[0] >= 3


def test_process_one_embeds_chunks_when_ollama_available(test_pool, tmp_path, test_settings):
    """With a mocked EmbeddingClient, chunks get embedded and the embedding column is populated."""
    att_id = _insert_attachment(test_pool, "ee", "text/plain", "embed.txt", size=80)
    sp = tmp_path / "ee" / "ee" / "ee"
    sp.parent.mkdir(parents=True)
    sp.write_text("# Heading\n\nA paragraph that will become a chunk.")

    ensure_pending_rows(test_pool)
    client = MagicMock()
    client.embed_batch.return_value = [[0.1] * 768]

    with patch.object(
        process_attachments_module,
        "_build_embedding_client",
        return_value=client,
    ):
        process_one(test_pool, att_id, attachment_dir=tmp_path)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM attachment_chunks WHERE attachment_id = %s "
            "AND embedding IS NOT NULL",
            (att_id,),
        )
        assert cur.fetchone()[0] >= 1
    assert client.embed_batch.called


def test_watchdog_reclaims_stale_extracting_row(test_pool, tmp_path):
    """A row stuck in 'extracting' with a stale extracted_at is reset to 'pending'
    by the _reclaim_stale helper run at the top of run()."""
    att_id = _insert_attachment(test_pool, "wd", "text/plain", "stale.txt")
    ensure_pending_rows(test_pool)

    # Force the row into 'extracting' with a stale timestamp (older than the watchdog threshold).
    with test_pool.connection() as conn:
        conn.execute(
            "UPDATE attachment_contents "
            "SET status = 'extracting', extracted_at = now() - interval '2 hours' "
            "WHERE attachment_id = %s",
            (att_id,),
        )
        conn.commit()

    reclaimed = _reclaim_stale(test_pool)
    assert reclaimed >= 1

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT status FROM attachment_contents WHERE attachment_id = %s",
            (att_id,),
        )
        assert cur.fetchone()[0] == "pending"
