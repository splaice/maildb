# src/chronicle_server/scope.py
"""QueryScope: working-set filter model, SQL builder, and fingerprint."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DateRange(BaseModel):
    from_: str | None = Field(None, alias="from")  # ISO date or datetime
    to: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class QueryScope(BaseModel):
    version: int = 1
    date: DateRange | None = None
    mailboxes: list[str] = []  # source_account values
    senders: list[str] = []  # exact sender_address values
    # v2 additive fields (defaults leave existing callers unaffected)
    recipients: list[str] = []  # recipient address filter (to/cc/bcc containment)
    participants: list[str] = []  # sender OR recipient match
    subject_contains: str | None = None
    has_attachment: bool | None = None
    file_types: list[str] = []  # attachment content-type families
    filenames: list[str] = []  # attachment filename filters
    source_types: list[str] = []  # "message" / "attachment"
    free_text: str | None = None  # residual query text after syntax extraction
    model_config = ConfigDict(populate_by_name=True)


def _escape_like(value: str) -> str:
    """Escape ``\\``, ``%``, ``_`` for ILIKE ... ESCAPE '\\'."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _recipient_containment(param_key: str) -> str:
    """GIN-indexable recipients containment (to/cc/bcc), matching MailDB.correspondence."""
    return (
        f"(recipients @> jsonb_build_object('to', %({param_key})s::jsonb) "
        f"OR recipients @> jsonb_build_object('cc', %({param_key})s::jsonb) "
        f"OR recipients @> jsonb_build_object('bcc', %({param_key})s::jsonb))"
    )


def scope_filters(scope: QueryScope) -> tuple[list[str], dict[str, Any]]:
    """Build WHERE conditions and named params over the ``emails`` table.

    Emits conditions only for provided fields:

    - ``date >= %(scope_from)s`` / ``date < %(scope_to)s``
    - ``source_account = ANY(%(mailboxes)s)``
    - ``sender_address = ANY(%(senders)s)``
    - recipient GIN containment (to/cc/bcc) for each of ``recipients``
    - participant = sender OR recipient for each of ``participants``
    - ``subject ILIKE`` with escaped pattern for ``subject_contains``
    - ``has_attachment = %(has_attachment)s``

    Parameterized; never interpolates values into SQL.
    """
    conditions: list[str] = []
    params: dict[str, Any] = {}

    if scope.date is not None:
        if scope.date.from_ is not None:
            conditions.append("date >= %(scope_from)s")
            params["scope_from"] = scope.date.from_
        if scope.date.to is not None:
            conditions.append("date < %(scope_to)s")
            params["scope_to"] = scope.date.to

    if scope.mailboxes:
        conditions.append("source_account = ANY(%(mailboxes)s)")
        params["mailboxes"] = list(scope.mailboxes)

    if scope.senders:
        conditions.append("sender_address = ANY(%(senders)s)")
        params["senders"] = list(scope.senders)

    if scope.recipients:
        rcpt_parts: list[str] = []
        for i, addr in enumerate(scope.recipients):
            key = f"recipient_arr_{i}"
            rcpt_parts.append(_recipient_containment(key))
            params[key] = json.dumps([addr])
        conditions.append("(" + " OR ".join(rcpt_parts) + ")")

    if scope.participants:
        part_parts: list[str] = []
        for i, addr in enumerate(scope.participants):
            sender_key = f"participant_sender_{i}"
            rcpt_key = f"participant_arr_{i}"
            part_parts.append(
                f"(sender_address = %({sender_key})s OR {_recipient_containment(rcpt_key)})"
            )
            params[sender_key] = addr
            params[rcpt_key] = json.dumps([addr])
        conditions.append("(" + " OR ".join(part_parts) + ")")

    if scope.subject_contains is not None:
        conditions.append("subject ILIKE %(subject_pattern)s ESCAPE '\\'")
        params["subject_pattern"] = f"%{_escape_like(scope.subject_contains)}%"

    if scope.has_attachment is not None:
        conditions.append("has_attachment = %(has_attachment)s")
        params["has_attachment"] = scope.has_attachment

    return conditions, params


def scope_fingerprint(scope: QueryScope) -> str:
    """Return ``qs_`` + first 16 hex chars of sha256(canonical JSON).

    Canonical form: model dump with sorted keys, aliases, exclude-none.
    Stable across key order of the input.
    """
    data = scope.model_dump(mode="json", by_alias=True, exclude_none=True)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"qs_{digest}"
