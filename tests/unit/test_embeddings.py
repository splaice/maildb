# tests/unit/test_embeddings.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from maildb.embeddings import EmbeddingClient, build_embedding_text


def test_build_embedding_text_all_fields() -> None:
    result = build_embedding_text("Q1 Budget", "Alice Smith", "Let's discuss the budget.")
    assert result == "Subject: Q1 Budget\nFrom: Alice Smith\n\nLet's discuss the budget."


def test_build_embedding_text_no_subject() -> None:
    result = build_embedding_text(None, "Alice", "Hello")
    assert result == "Subject: \nFrom: Alice\n\nHello"


def test_build_embedding_text_no_sender() -> None:
    result = build_embedding_text("Test", None, "Body text")
    assert result == "Subject: Test\nFrom: \n\nBody text"


def test_build_embedding_text_no_body() -> None:
    result = build_embedding_text("Test", "Alice", None)
    assert result == "Subject: Test\nFrom: Alice\n\n"


@pytest.fixture
def mock_ollama() -> MagicMock:
    with patch("maildb.embeddings.ollama.Client") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        def _embed_side_effect(model: str, input: str | list[str]) -> dict:
            if isinstance(input, list):
                return {"embeddings": [[0.1] * 768 for _ in input]}
            return {"embeddings": [[0.1] * 768]}

        mock_client.embed.side_effect = _embed_side_effect
        yield mock_cls


def test_embed_single(mock_ollama: MagicMock) -> None:
    client = EmbeddingClient(
        ollama_url="http://localhost:11434",
        model_name="nomic-embed-text",
        dimensions=768,
    )
    result = client.embed("test text")
    assert len(result) == 768
    mock_ollama.return_value.embed.assert_called_once()


def test_embed_batch(mock_ollama: MagicMock) -> None:
    client = EmbeddingClient(
        ollama_url="http://localhost:11434",
        model_name="nomic-embed-text",
        dimensions=768,
    )
    results = client.embed_batch(["text1", "text2"])
    assert len(results) == 2
    assert len(results[0]) == 768
