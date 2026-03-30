# src/maildb/embeddings.py
from __future__ import annotations

from typing import cast

import ollama
import structlog

logger = structlog.get_logger()


# nomic-embed-text has an 8192 token context window.
# Token-dense content (URLs, code, non-ASCII) can yield ~1.2 chars/token,
# so 6000 chars safely stays within the limit for all content types.
MAX_EMBEDDING_CHARS = 6_000


def build_embedding_text(
    subject: str | None,
    sender_name: str | None,
    body_text: str | None,
) -> str:
    """Build the text string used for embedding, truncated to fit model context."""
    text = f"Subject: {subject or ''}\nFrom: {sender_name or ''}\n\n{body_text or ''}"
    if len(text) > MAX_EMBEDDING_CHARS:
        text = text[:MAX_EMBEDDING_CHARS]
    return text


class EmbeddingClient:
    """Wraps the Ollama Python client for embedding generation."""

    def __init__(self, ollama_url: str, model_name: str, dimensions: int) -> None:
        self._client = ollama.Client(host=ollama_url)
        self._model = model_name
        self._dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text string."""
        response = self._client.embed(model=self._model, input=text)
        return cast("list[float]", response["embeddings"][0])

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        response = self._client.embed(model=self._model, input=texts)
        return cast("list[list[float]]", response["embeddings"])
