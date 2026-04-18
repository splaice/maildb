from __future__ import annotations

from maildb.tokenizer import count_tokens, truncate_to_tokens


def test_count_tokens_nonzero_for_nonempty() -> None:
    assert count_tokens("Hello world") > 0


def test_count_tokens_empty_is_zero() -> None:
    assert count_tokens("") == 0


def test_count_tokens_scales_with_length() -> None:
    short = count_tokens("hello")
    long = count_tokens("hello " * 500)
    assert long > short * 100


def test_truncate_preserves_short_input() -> None:
    text = "short text"
    assert truncate_to_tokens(text, 1000) == text


def test_truncate_cuts_to_limit() -> None:
    text = "word " * 1000
    truncated = truncate_to_tokens(text, 50)
    assert count_tokens(truncated) <= 50


def test_truncate_result_fits_within_limit_exactly() -> None:
    text = "The quick brown fox jumps over the lazy dog. " * 100
    limit = 32
    truncated = truncate_to_tokens(text, limit)
    assert count_tokens(truncated) <= limit
    # Should use most of the available budget, not be trivially short
    assert count_tokens(truncated) >= limit - 4
