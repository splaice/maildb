# tests/unit/test_embeddings.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from maildb.embeddings import (
    MAX_EMBEDDING_TOKENS,
    EmbeddingClient,
    build_embedding_text,
    estimate_tokens,
)


def test_estimate_tokens_english_prose() -> None:
    text = "Hello world this is a test. " * 200  # ~5600 chars
    tokens = estimate_tokens(text)
    assert 1000 < tokens < 2000


def test_estimate_tokens_url_heavy() -> None:
    text = "https://example.com/path/to/resource?query=value&other=123 " * 100
    tokens = estimate_tokens(text)
    char_ratio = len(text) / tokens
    assert char_ratio < 3.0


def test_build_embedding_text_allows_longer_prose() -> None:
    prose = "The quick brown fox jumps over the lazy dog. " * 200  # ~9000 chars
    result = build_embedding_text("Test Subject", "Alice", prose)
    assert len(result) > 6000


def test_build_embedding_text_truncates_url_heavy() -> None:
    urls = "https://example.com/very/long/path/resource?q=abc123&r=def456 " * 200
    result = build_embedding_text("Test Subject", "Alice", urls)
    tokens = estimate_tokens(result)
    assert tokens <= MAX_EMBEDDING_TOKENS


def test_build_embedding_text_short_text_unchanged() -> None:
    result = build_embedding_text("Subject", "Alice", "Short body")
    assert result == "Subject: Subject\nFrom: Alice\n\nShort body"


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
