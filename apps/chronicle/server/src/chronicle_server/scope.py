# src/chronicle_server/scope.py
"""QueryScope v1: working-set filter model, SQL builder, and fingerprint."""

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
    model_config = ConfigDict(populate_by_name=True)


def scope_filters(scope: QueryScope) -> tuple[list[str], dict[str, Any]]:
    """Build WHERE conditions and named params over the ``emails`` table.

    Emits conditions only for provided fields:

    - ``date >= %(scope_from)s`` / ``date < %(scope_to)s``
    - ``source_account = ANY(%(mailboxes)s)``
    - ``sender_address = ANY(%(senders)s)``

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
