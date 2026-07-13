# tests/test_cursor.py
from __future__ import annotations

import pytest

from chronicle_server.cursor import decode_cursor, encode_cursor

SECRET = "test-secret-key-for-cursors"


def test_cursor_roundtrip() -> None:
    payload = {"offset": 10, "sort": "date", "dir": "desc", "nested": {"a": 1}}
    token = encode_cursor(payload, SECRET)
    assert isinstance(token, str)
    assert "." in token
    assert decode_cursor(token, SECRET) == payload


def test_cursor_stable_encoding() -> None:
    payload = {"b": 2, "a": 1}
    t1 = encode_cursor(payload, SECRET)
    t2 = encode_cursor({"a": 1, "b": 2}, SECRET)
    assert t1 == t2


def test_tampered_payload_raises() -> None:
    token = encode_cursor({"n": 1}, SECRET)
    payload_b64, _, sig_b64 = token.partition(".")
    # Flip a character in the payload segment.
    flipped = ("A" if payload_b64[0] != "A" else "B") + payload_b64[1:]
    bad = f"{flipped}.{sig_b64}"
    with pytest.raises(ValueError):
        decode_cursor(bad, SECRET)


def test_tampered_signature_raises() -> None:
    token = encode_cursor({"n": 1}, SECRET)
    payload_b64, _, sig_b64 = token.partition(".")
    flipped = ("A" if sig_b64[0] != "A" else "B") + sig_b64[1:]
    bad = f"{payload_b64}.{flipped}"
    with pytest.raises(ValueError):
        decode_cursor(bad, SECRET)


def test_wrong_secret_raises() -> None:
    token = encode_cursor({"n": 1}, SECRET)
    with pytest.raises(ValueError):
        decode_cursor(token, "other-secret")


@pytest.mark.parametrize("bad", ["", "no-dot", ".", "a.", ".b", "a.b.c"])
def test_malformed_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        decode_cursor(bad, SECRET)
