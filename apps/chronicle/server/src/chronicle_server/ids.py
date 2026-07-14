# src/chronicle_server/ids.py
"""Stable source IDs for messages, attachments, and threads."""

from __future__ import annotations

import base64
from typing import Literal
from uuid import UUID

SourceKind = Literal["msg", "att", "thr"]

_KINDS = frozenset({"msg", "att", "thr"})


def encode_source_id(kind: str, key: int | str | UUID) -> str:
    """Encode a stable opaque source ID.

    - msg_<int>  — UUID.int of emails.id
    - att_<int>  — attachments.id
    - thr_<b64>  — base64url (no padding) of emails.thread_id text
    """
    if kind == "msg":
        if isinstance(key, UUID):
            n = key.int
        elif isinstance(key, int):
            n = key
        elif isinstance(key, str):
            n = UUID(key).int
        else:
            msg = f"msg key must be int|str|UUID, got {type(key).__name__}"
            raise ValueError(msg)
        if n < 0:
            msg = "msg key must be non-negative"
            raise ValueError(msg)
        return f"msg_{n}"

    if kind == "att":
        if isinstance(key, bool) or not isinstance(key, int):
            msg = f"att key must be int, got {type(key).__name__}"
            raise ValueError(msg)
        if key < 0:
            msg = "att key must be non-negative"
            raise ValueError(msg)
        return f"att_{key}"

    if kind == "thr":
        if not isinstance(key, str):
            msg = f"thr key must be str, got {type(key).__name__}"
            raise ValueError(msg)
        raw = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")
        return f"thr_{raw}"

    msg = f"unknown source kind: {kind!r}"
    raise ValueError(msg)


def decode_source_id(sid: str) -> tuple[SourceKind, int | str]:
    """Decode a source ID into (kind, key).

    Raises ValueError on malformed input.
    """
    if not isinstance(sid, str) or "_" not in sid:
        msg = f"malformed source id: {sid!r}"
        raise ValueError(msg)

    kind, _, rest = sid.partition("_")
    if kind not in _KINDS or rest == "":
        msg = f"malformed source id: {sid!r}"
        raise ValueError(msg)

    if kind == "msg":
        if not rest.isdigit():
            msg = f"malformed msg id: {sid!r}"
            raise ValueError(msg)
        n = int(rest)
        # Validate it fits in a UUID.
        try:
            UUID(int=n)
        except ValueError as exc:
            msg = f"malformed msg id: {sid!r}"
            raise ValueError(msg) from exc
        return "msg", n

    if kind == "att":
        if not rest.isdigit():
            msg = f"malformed att id: {sid!r}"
            raise ValueError(msg)
        return "att", int(rest)

    # thr — base64url of raw thread_id text (restore padding)
    pad = "=" * (-len(rest) % 4)
    try:
        decoded = base64.b64decode(rest + pad, altchars=b"-_", validate=True)
        thread_id = decoded.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        msg = f"malformed thr id: {sid!r}"
        raise ValueError(msg) from exc
    if thread_id == "":
        msg = f"malformed thr id: {sid!r}"
        raise ValueError(msg)
    return "thr", thread_id


def msg_key_to_uuid(key: int) -> UUID:
    """Convert a decoded msg key (UUID.int) back to UUID."""
    return UUID(int=key)
