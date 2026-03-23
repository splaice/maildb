# tests/integration/test_ingest.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from maildb.ingest import backfill_embeddings, ingest_mbox

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


def test_ingest_mbox_inserts_messages(test_pool) -> None:
    mock_embed = MagicMock()
    mock_embed.embed_batch.return_value = [[0.1] * 768] * 10

    result = ingest_mbox(test_pool, mock_embed, FIXTURES / "sample.mbox")
    assert result["inserted"] > 0
    assert result["total"] == 10

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        assert cur.fetchone()[0] == result["inserted"]


def test_ingest_mbox_deduplication(test_pool) -> None:
    mock_embed = MagicMock()
    mock_embed.embed_batch.return_value = [[0.1] * 768] * 10

    result1 = ingest_mbox(test_pool, mock_embed, FIXTURES / "sample.mbox")
    result2 = ingest_mbox(test_pool, mock_embed, FIXTURES / "sample.mbox")

    assert result2["skipped"] == result1["inserted"]
    assert result2["inserted"] == 0

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        assert cur.fetchone()[0] == result1["inserted"]


def test_ingest_mbox_null_embedding_on_failure(test_pool) -> None:
    mock_embed = MagicMock()
    mock_embed.embed_batch.side_effect = ConnectionError("Ollama down")

    result = ingest_mbox(test_pool, mock_embed, FIXTURES / "sample.mbox")
    assert result["inserted"] > 0
    assert result["failed_embeddings"] > 0

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails WHERE embedding IS NULL")
        assert cur.fetchone()[0] == result["inserted"]


def test_backfill_embeddings(test_pool) -> None:
    # First insert without embeddings
    mock_embed_fail = MagicMock()
    mock_embed_fail.embed_batch.side_effect = ConnectionError("down")
    ingest_mbox(test_pool, mock_embed_fail, FIXTURES / "sample.mbox")

    # Now backfill
    mock_embed_ok = MagicMock()
    mock_embed_ok.embed_batch.return_value = [[0.2] * 768] * 100  # enough for any batch

    count = backfill_embeddings(test_pool, mock_embed_ok)
    assert count > 0

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails WHERE embedding IS NULL")
        assert cur.fetchone()[0] == 0
