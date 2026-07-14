# src/chronicle_server/querysyntax.py
"""Structured search syntax parser (spec §5.3). Pure function; never throws on user input."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


@dataclass
class ParsedQuery:
    scope_updates: dict[str, Any] = field(default_factory=dict)
    free_text: str = ""
    unsupported: list[str] = field(default_factory=list)


# Operators that update scope when well-formed.
_SCOPE_OPS = frozenset(
    {
        "from",
        "to",
        "participant",
        "subject",
        "after",
        "before",
        "on",
        "mailbox",
        "filetype",
        "filename",
        "has",
        "is",
    }
)

# Operators deferred to later subsystems — collect into unsupported, never error.
_UNSUPPORTED_OPS = frozenset({"topic", "person", "organization", "domain"})

# Token: optional leading '-', operator word, ':', then either "quoted value" or bare value.
# Bare value runs until whitespace.
_TOKEN_RE = re.compile(
    r"""
    (?P<neg>-)?
    (?P<op>[A-Za-z][A-Za-z0-9_-]*)
    :
    (?:
        "(?P<quoted>(?:\\.|[^"\\])*)"
        |
        (?P<bare>\S+)
    )
    """,
    re.VERBOSE,
)

# Plain free-text token (no colon operator form) or unknown-op:value kept as text.
_WORD_RE = re.compile(r"\S+")


def _unquote(value: str) -> str:
    """Unescape simple backslash escapes inside a quoted value."""
    return re.sub(r"\\(.)", r"\1", value)


def _parse_iso_date(raw: str) -> str | None:
    """Accept YYYY-MM-DD (or longer ISO prefix); return date part or None."""
    raw = raw.strip()
    if len(raw) < 10:
        return None
    try:
        date.fromisoformat(raw[:10])
    except ValueError:
        return None
    return raw[:10]


def _append_list(updates: dict[str, Any], key: str, value: str) -> None:
    existing = updates.get(key)
    if existing is None:
        updates[key] = [value]
    elif isinstance(existing, list):
        existing.append(value)
    else:
        updates[key] = [value]


def _set_date_bound(
    updates: dict[str, Any],
    *,
    from_: str | None = None,
    to: str | None = None,
) -> None:
    date_obj = updates.get("date")
    if not isinstance(date_obj, dict):
        date_obj = {}
        updates["date"] = date_obj
    if from_ is not None:
        date_obj["from"] = from_
    if to is not None:
        date_obj["to"] = to


def parse_query(raw: str) -> ParsedQuery:
    """Parse structured operators out of *raw*; residual tokens become free_text.

    Never raises on user input. Unsupported operators are collected, not rejected.
    Unknown ``word:`` operators are treated as plain free text.
    """
    if not isinstance(raw, str) or not raw.strip():
        return ParsedQuery(scope_updates={}, free_text="", unsupported=[])

    scope_updates: dict[str, Any] = {}
    unsupported: list[str] = []
    free_parts: list[str] = []

    pos = 0
    n = len(raw)
    while pos < n:
        # Skip whitespace
        if raw[pos].isspace():
            pos += 1
            continue

        m = _TOKEN_RE.match(raw, pos)
        if m is not None:
            op = m.group("op").lower()
            neg = m.group("neg") is not None
            quoted = m.group("quoted")
            value = _unquote(quoted) if quoted is not None else (m.group("bare") or "")

            token_text = m.group(0)
            pos = m.end()

            # Negation: only -topic: is a known unsupported exclusion; other -ops → unsupported.
            if neg:
                if op == "topic":
                    unsupported.append(token_text)
                else:
                    unsupported.append(token_text)
                continue

            if op in _UNSUPPORTED_OPS:
                unsupported.append(token_text)
                continue

            if op not in _SCOPE_OPS:
                # Unknown word: operators are plain text.
                free_parts.append(token_text)
                continue

            if op == "from":
                _append_list(scope_updates, "senders", value)
            elif op == "to":
                _append_list(scope_updates, "recipients", value)
            elif op == "participant":
                _append_list(scope_updates, "participants", value)
            elif op == "subject":
                scope_updates["subject_contains"] = value
            elif op == "after":
                d = _parse_iso_date(value)
                if d is not None:
                    _set_date_bound(scope_updates, from_=d)
                else:
                    free_parts.append(token_text)
            elif op == "before":
                d = _parse_iso_date(value)
                if d is not None:
                    _set_date_bound(scope_updates, to=d)
                else:
                    free_parts.append(token_text)
            elif op == "on":
                d = _parse_iso_date(value)
                if d is not None:
                    day = date.fromisoformat(d)
                    nxt = (day + timedelta(days=1)).isoformat()
                    _set_date_bound(scope_updates, from_=d, to=nxt)
                else:
                    free_parts.append(token_text)
            elif op == "mailbox":
                _append_list(scope_updates, "mailboxes", value)
            elif op == "filetype":
                _append_list(scope_updates, "file_types", value)
            elif op == "filename":
                _append_list(scope_updates, "filenames", value)
            elif op == "has":
                v = value.lower()
                if v == "attachment":
                    scope_updates["has_attachment"] = True
                elif v == "failed-extraction":
                    unsupported.append(token_text)
                else:
                    unsupported.append(token_text)
            elif op == "is":
                v = value.lower()
                if v == "message":
                    _append_list(scope_updates, "source_types", "message")
                elif v == "attachment":
                    _append_list(scope_updates, "source_types", "attachment")
                elif v == "thread":
                    unsupported.append(token_text)
                else:
                    unsupported.append(token_text)
            continue

        # Not an operator token — take next word as free text.
        wm = _WORD_RE.match(raw, pos)
        if wm is None:
            break
        free_parts.append(wm.group(0))
        pos = wm.end()

    free_text = " ".join(free_parts).strip()
    if free_text:
        scope_updates["free_text"] = free_text

    return ParsedQuery(
        scope_updates=scope_updates,
        free_text=free_text,
        unsupported=unsupported,
    )
