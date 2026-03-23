# src/maildb/embeddings.py
from __future__ import annotations

from typing import cast

import ollama
import structlog

logger = structlog.get_logger()


def build_embedding_text(
    subject: str | None,
    sender_name: str | None,
    body_text: str | None,
) -> str:
    """Build the text string used for embedding."""
    return f"Subject: {subject or ''}\nFrom: {sender_name or ''}\n\n{body_text or ''}"


class EmbeddingClient:
    """Wraps the Ollama Python client for embedding generation."""

    def __init__(self, ollama_url: str, model_name: str, dimensions: int) -> None:
        self._client = ollama.Client(host=ollama_url)
        self._model = model_name
        self._dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text string."""
        response = self._client.embed(model=self._model, input=text)
        return cast(list[float], response["embeddings"][0])

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        response = self._client.embed(model=self._model, input=texts)
        return cast(list[list[float]], response["embeddings"])
