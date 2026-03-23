import pytest

from maildb.ingest.index import run_index_phase

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
