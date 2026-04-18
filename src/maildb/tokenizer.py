"""Precise token counting using the nomic-embed-text HF tokenizer.

Replaces the byte-length heuristic in `estimate_tokens`. The tokenizer
is loaded once and cached at module level — it's a lightweight Rust
object that's safe to share across threads.
"""

from __future__ import annotations

from functools import lru_cache

from tokenizers import Tokenizer  # type: ignore[import-untyped]

_MODEL_NAME = "nomic-ai/nomic-embed-text-v1"


@lru_cache(maxsize=1)
def get_tokenizer() -> Tokenizer:
    """Return the cached tokenizer, loading on first use."""
    return Tokenizer.from_pretrained(_MODEL_NAME)


def count_tokens(text: str) -> int:
    """Return the exact token count for the given text."""
    if not text:
        return 0
    return len(get_tokenizer().encode(text).ids)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Return a prefix of text whose token count is <= max_tokens."""
    if not text:
        return text
    tok = get_tokenizer()
    enc = tok.encode(text)
    if len(enc.ids) <= max_tokens:
        return text
    # Decode the truncated token list, then re-verify the count.
    # This guards against decode artifacts that might exceed max_tokens.
    truncated = tok.decode(enc.ids[:max_tokens])  # type: ignore[no-any-return]
    truncated_count = len(tok.encode(truncated).ids)
    if truncated_count <= max_tokens:
        return truncated  # type: ignore[no-any-return]
    # If decoding created overage, binary-search for the safe point
    low, high = 0, max_tokens
    while low < high:
        mid = (low + high + 1) // 2
        candidate = tok.decode(enc.ids[:mid])  # type: ignore[no-any-return]
        if len(tok.encode(candidate).ids) <= max_tokens:
            low = mid
        else:
            high = mid - 1
    return tok.decode(enc.ids[:low])  # type: ignore[no-any-return]
