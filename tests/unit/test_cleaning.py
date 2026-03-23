# tests/unit/test_cleaning.py
from __future__ import annotations

from maildb.parsing import (
    clean_body,
    normalize_whitespace,
    remove_quoted_replies,
    remove_signature,
)


def test_remove_quoted_replies_single_level() -> None:
    text = "Hello\n> quoted line\nWorld"
    assert remove_quoted_replies(text) == "Hello\nWorld"


def test_remove_quoted_replies_nested() -> None:
    text = "Hello\n>> deeply quoted\n> quoted\nWorld"
    assert remove_quoted_replies(text) == "Hello\nWorld"


def test_remove_quoted_replies_outlook() -> None:
    text = "Hello\n-----Original Message-----\nFrom: someone\nOld content"
    assert remove_quoted_replies(text) == "Hello"


def test_remove_signature_standard() -> None:
    text = "Hello World\n-- \nJohn Doe\nCEO"
    assert remove_signature(text) == "Hello World"


def test_remove_signature_no_signature() -> None:
    text = "Hello World\nNo sig here"
    assert remove_signature(text) == "Hello World\nNo sig here"


def test_normalize_whitespace() -> None:
    text = "Hello\n\n\n\nWorld  \n  \nEnd"
    result = normalize_whitespace(text)
    assert "\n\n\n" not in result
    assert result == "Hello\n\nWorld\n\nEnd"


def test_clean_body_full_pipeline() -> None:
    text = "New content\n> old reply\n-- \nSig line\n\n\n"
    result = clean_body(text)
    assert "old reply" not in result
    assert "Sig line" not in result
    assert result == "New content"


def test_clean_body_empty_input() -> None:
    assert clean_body("") == ""
    assert clean_body(None) == ""
