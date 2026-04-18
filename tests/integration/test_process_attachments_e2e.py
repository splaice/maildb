from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maildb.ingest.process_attachments import ensure_pending_rows, run

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "attachments"


def test_e2e_pdf_extraction_and_search(test_pool, test_settings, tmp_path):
    # Stage hello.pdf as a content-addressed attachment.
    src = FIXTURES / "hello.pdf"
    sha = "ee11"
    stage_path = tmp_path / sha / sha / sha
    stage_path.parent.mkdir(parents=True)
    stage_path.write_bytes(src.read_bytes())

    with test_pool.connection() as conn:
        cur = conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (sha, "hello.pdf", "application/pdf", src.stat().st_size, f"{sha}/{sha}/{sha}"),
        )
        att_id = cur.fetchone()[0]
        conn.commit()

    ensure_pending_rows(test_pool)

    # Stub embedding client; real Marker runs.
    fake_client = MagicMock()
    fake_client.embed_batch.side_effect = lambda texts: [[0.1] * 768 for _ in texts]

    with patch(
        "maildb.ingest.process_attachments._build_embedding_client",
        return_value=fake_client,
    ):
        counts = run(
            test_pool,
            attachment_dir=tmp_path,
            workers=1,
            retry_failed=False,
        )
    assert counts["extracted"] >= 1

    # Extracted markdown landed in DB and on disk.
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT markdown, markdown_bytes FROM attachment_contents WHERE attachment_id = %s",
            (att_id,),
        )
        md, md_bytes = cur.fetchone()
    assert md is not None
    assert md_bytes > 0
    assert "Hello" in md or "hello" in md.lower()
    assert (tmp_path / sha / sha / f"{sha}.md").exists()

    # At least one chunk with a non-null embedding.
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM attachment_chunks "
            "WHERE attachment_id = %s AND embedding IS NOT NULL",
            (att_id,),
        )
        assert cur.fetchone()[0] >= 1
