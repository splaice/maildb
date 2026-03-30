from pathlib import Path
from uuid import uuid4

import pytest

from maildb.ingest.orchestrator import get_status, reset_pipeline, run_pipeline
from maildb.ingest.tasks import complete_task, create_task

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


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


def test_get_status(test_pool):
    status = get_status(test_pool)
    assert "split" in status
    assert "parse" in status
    assert "index" in status
    assert "embed" in status


def test_reset_full(test_pool):
    task = create_task(test_pool, phase="split")
    complete_task(test_pool, task["id"])
    _insert_unembedded_email(test_pool, "reset-test@example.com")
    reset_pipeline(test_pool, phase=None)
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM ingest_tasks")
        assert cur.fetchone()[0] == 0
        cur = conn.execute("SELECT count(*) FROM emails")
        assert cur.fetchone()[0] == 0


def test_reset_embed_phase(test_pool):
    parse_task = create_task(test_pool, phase="parse")
    complete_task(test_pool, parse_task["id"])
    embed_task = create_task(test_pool, phase="embed")
    complete_task(test_pool, embed_task["id"])
    with test_pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, embedding, created_at)
               VALUES (%(id)s, 'reset-emb@example.com', 't1', 'Test', %(emb)s, now())""",
            {"id": uuid4(), "emb": [0.1] * 768},
        )
        conn.commit()
    reset_pipeline(test_pool, phase="embed")
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM ingest_tasks WHERE phase = 'embed'")
        assert cur.fetchone()[0] == 0
        cur = conn.execute("SELECT count(*) FROM ingest_tasks WHERE phase = 'parse'")
        assert cur.fetchone()[0] == 1
        cur = conn.execute("SELECT count(*) FROM emails WHERE embedding IS NOT NULL")
        assert cur.fetchone()[0] == 0


def test_reset_parse_phase(test_pool):
    split_task = create_task(test_pool, phase="split")
    complete_task(test_pool, split_task["id"])
    parse_task = create_task(test_pool, phase="parse")
    complete_task(test_pool, parse_task["id"])
    embed_task = create_task(test_pool, phase="embed")
    complete_task(test_pool, embed_task["id"])
    _insert_unembedded_email(test_pool, "reset-parse@example.com")
    reset_pipeline(test_pool, phase="parse")
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM ingest_tasks WHERE phase = 'split'")
        assert cur.fetchone()[0] == 1
        cur = conn.execute(
            "SELECT count(*) FROM ingest_tasks WHERE phase IN ('parse', 'index', 'embed')"
        )
        assert cur.fetchone()[0] == 0
        cur = conn.execute("SELECT count(*) FROM emails")
        assert cur.fetchone()[0] == 0
