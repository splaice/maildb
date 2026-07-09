import pytest

from maildb.ingest.index import (
    create_embed_backlog_index,
    drop_embed_backlog_index,
    run_index_phase,
)

pytestmark = pytest.mark.integration


def test_run_index_phase_creates_indexes(test_pool):
    run_index_phase(test_pool)
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'emails' AND indexname LIKE 'idx_%'"
        )
        indexes = {row[0] for row in cur.fetchall()}
    assert "idx_email_sender_address" in indexes
    assert "idx_email_date" in indexes
    assert "idx_email_thread_sender_date" in indexes


def test_embed_backlog_index_create_drop_idempotent(test_pool):
    create_embed_backlog_index(test_pool)
    create_embed_backlog_index(test_pool)

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_email_embedding_null'")
        assert cur.fetchone() is not None

    drop_embed_backlog_index(test_pool)
    drop_embed_backlog_index(test_pool)

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_email_embedding_null'")
        assert cur.fetchone() is None
