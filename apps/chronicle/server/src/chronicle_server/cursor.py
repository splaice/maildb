# src/chronicle_server/cursor.py
"""Opaque, HMAC-signed cursor tokens for list pagination."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(token: str) -> bytes:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad)


def encode_cursor(payload: dict[str, Any], secret_key: str) -> str:
    """URL-safe base64 of compact JSON, HMAC-SHA256-signed with secret_key.

    Format: ``{payload_b64}.{sig_b64}`` (no padding).
    """
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret_key.encode("utf-8"), body, hashlib.sha256).digest()
    return f"{_b64encode(body)}.{_b64encode(sig)}"


def decode_cursor(token: str, secret_key: str) -> dict[str, Any]:
    """Verify and decode a cursor token.

    Raises ValueError on bad signature or format.
    """
    if not isinstance(token, str) or "." not in token:
        msg = "malformed cursor"
        raise ValueError(msg)

    payload_b64, _, sig_b64 = token.partition(".")
    if not payload_b64 or not sig_b64 or "." in sig_b64:
        msg = "malformed cursor"
        raise ValueError(msg)

    try:
        body = _b64decode(payload_b64)
        sig = _b64decode(sig_b64)
    except (ValueError, TypeError) as exc:
        msg = "malformed cursor"
        raise ValueError(msg) from exc

    expected = hmac.new(secret_key.encode("utf-8"), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        msg = "invalid cursor signature"
        raise ValueError(msg)

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "malformed cursor payload"
        raise ValueError(msg) from exc

    if not isinstance(data, dict):
        msg = "cursor payload must be an object"
        raise ValueError(msg)
    return data
