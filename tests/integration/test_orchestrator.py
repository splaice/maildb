from pathlib import Path
from uuid import uuid4

import pytest

from maildb.ingest.orchestrator import count_unembedded, get_status, run_pipeline
from maildb.ingest.tasks import complete_task, create_task

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


def test_run_pipeline_split_and_parse(test_pool, test_settings, tmp_path):
    """Pipeline should split, parse, and index a small mbox."""
    result = run_pipeline(
        mbox_path=FIXTURES / "sample.mbox",
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,
        parse_workers=2,
        skip_embed=True,
    )
    assert result["parse"]["completed"] > 0
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        assert cur.fetchone()[0] > 0


def _insert_unembedded_email(pool, message_id):
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, sender_name,
                   body_text, created_at)
               VALUES (%(id)s, %(message_id)s, 'thread-1', 'Test', 'Sender',
                   'Body text', now())""",
            {"id": uuid4(), "message_id": message_id},
        )
        conn.commit()


def test_embed_resumes_after_completion(test_pool):
    embed_task = create_task(test_pool, phase="embed")
    complete_task(test_pool, embed_task["id"], messages_total=0)
    _insert_unembedded_email(test_pool, "resume-test@example.com")

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails WHERE embedding IS NULL")
        assert cur.fetchone()[0] == 1

    assert count_unembedded(test_pool) == 1


def test_get_status(test_pool):
    status = get_status(test_pool)
    assert "split" in status
    assert "parse" in status
    assert "index" in status
    assert "embed" in status
