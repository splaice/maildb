# src/maildb/embeddings.py
from __future__ import annotations

import re
from typing import cast

import ollama
import structlog

logger = structlog.get_logger()

# nomic-embed-text has an 8192 token context window.
# We target 7500 tokens to leave headroom.
MAX_EMBEDDING_TOKENS = 7_500

_URL_PATTERN = re.compile(r"https?://\S+")


def estimate_tokens(text: str) -> int:
    """Estimate token count using byte-length heuristic with URL adjustment."""
    base_tokens = len(text.encode("utf-8")) // 4
    url_bytes = sum(len(m.group().encode("utf-8")) for m in _URL_PATTERN.finditer(text))
    url_extra = url_bytes // 2
    return base_tokens + url_extra


def build_embedding_text(
    subject: str | None,
    sender_name: str | None,
    body_text: str | None,
) -> str:
    """Build the text string used for embedding, truncated to fit model context."""
    text = f"Subject: {subject or ''}\nFrom: {sender_name or ''}\n\n{body_text or ''}"
    if estimate_tokens(text) <= MAX_EMBEDDING_TOKENS:
        return text
    # Binary search for the right truncation point
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= MAX_EMBEDDING_TOKENS:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


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
