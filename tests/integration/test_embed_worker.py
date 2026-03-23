from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from psycopg.rows import dict_row

from maildb.ingest.embed import embed_worker

pytestmark = pytest.mark.integration


def _insert_test_email(pool, message_id="test@example.com"):
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, sender_name, body_text, created_at)
               VALUES (%(id)s, %(message_id)s, 'thread-1', 'Test', 'Sender', 'Body text', now())""",
            {"id": uuid4(), "message_id": message_id},
        )
        conn.commit()


def test_embed_worker_processes_null_embeddings(test_pool, test_settings):
    _insert_test_email(test_pool, "embed-test-1@example.com")
    _insert_test_email(test_pool, "embed-test-2@example.com")

    mock_client = MagicMock()
    mock_client.embed_batch.return_value = [[0.1] * 768, [0.2] * 768]

    count = embed_worker(
        database_url=test_settings.database_url,
        ollama_url=test_settings.ollama_url,
        embedding_model=test_settings.embedding_model,
        embedding_dimensions=test_settings.embedding_dimensions,
        batch_size=10,
        _embedding_client=mock_client,
    )
    assert count == 2

    with test_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT count(*) AS c FROM emails WHERE embedding IS NOT NULL")
        assert cur.fetchone()["c"] == 2


def test_embed_worker_exits_when_no_work(test_pool, test_settings):
    mock_client = MagicMock()
    count = embed_worker(
        database_url=test_settings.database_url,
        ollama_url="http://localhost:11434",
        embedding_model="nomic-embed-text",
        embedding_dimensions=768,
        batch_size=10,
        _embedding_client=mock_client,
    )
    assert count == 0
    mock_client.embed_batch.assert_not_called()
