from __future__ import annotations

import inspect
import json
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import wraps
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import structlog
from mcp.server.fastmcp import Context, FastMCP

from maildb.maildb import MailDB

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

logger = structlog.get_logger()

RESPONSE_SIZE_WARNING_BYTES = 50_000  # 50KB


def log_tool[F: Callable[..., Any]](func: F) -> F:
    """Decorator that logs MCP tool entry params and exit stats."""
    sig = inspect.signature(func)

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Bind args to param names, excluding 'ctx'
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        params = {k: v for k, v in bound.arguments.items() if k != "ctx" and v is not None}

        tool_name = func.__name__
        logger.debug("tool_entry", tool=tool_name, **params)

        t0 = time.monotonic()
        result = func(*args, **kwargs)
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)

        # Compute result stats
        if isinstance(result, dict) and "results" in result:
            row_count = len(result["results"])
        elif isinstance(result, list):
            row_count = len(result)
        else:
            row_count = 1
        response_bytes = len(json.dumps(result, default=str).encode())

        if response_bytes > RESPONSE_SIZE_WARNING_BYTES:
            logger.warning(
                "tool_exit",
                tool=tool_name,
                rows=row_count,
                response_bytes=response_bytes,
                elapsed_ms=elapsed_ms,
                warning="response exceeds 50KB",
            )
        else:
            logger.debug(
                "tool_exit",
                tool=tool_name,
                rows=row_count,
                response_bytes=response_bytes,
                elapsed_ms=elapsed_ms,
            )

        return result

    return wrapper  # type: ignore[return-value]


# --- Serialization ---

SERIALIZABLE_EMAIL_FIELDS = frozenset(
    {
        "id",
        "message_id",
        "thread_id",
        "subject",
        "sender_name",
        "sender_address",
        "sender_domain",
        "recipients",
        "date",
        "body_text",
        "body_length",
        "body_truncated",
        "has_attachment",
        "attachments",
        "labels",
        "in_reply_to",
        "references",
        "created_at",
    }
)

# Default fields for list tools: everything except body_text (replaced by body_length)
DEFAULT_LIST_FIELDS = SERIALIZABLE_EMAIL_FIELDS - {"body_text"}


def _serialize_email(
    email: Any,
    fields: frozenset[str] | None = None,
    body_max_chars: int | None = None,
) -> dict[str, Any]:
    """Convert an Email dataclass to a JSON-serializable dict."""
    d = asdict(email)
    # Convert non-serializable types
    if isinstance(d.get("id"), UUID):
        d["id"] = str(d["id"])
    if isinstance(d.get("date"), datetime):
        d["date"] = d["date"].isoformat() if d["date"] else None
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
    # Always drop these
    d.pop("embedding", None)
    d.pop("body_html", None)
    # Compute body_length from raw body_text
    raw_body = d.get("body_text")
    d["body_length"] = len(raw_body) if raw_body is not None else None
    # Apply body truncation if requested
    if body_max_chars is not None and raw_body is not None and len(raw_body) > body_max_chars:
        d["body_text"] = raw_body[:body_max_chars] + "..."
        d["body_truncated"] = True
    # Apply field selection
    if fields is not None:
        d = {k: v for k, v in d.items() if k in fields}
    else:
        # Default: exclude body_text (use body_length instead)
        d = {k: v for k, v in d.items() if k in DEFAULT_LIST_FIELDS}
    return d


def _wrap_response(
    results: list[dict[str, Any]],
    *,
    total: int,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """Wrap a list of results with pagination metadata."""
    return {"total": total, "offset": offset, "limit": limit, "results": results}


def _serialize_search_result(
    sr: Any,
    fields: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Convert a SearchResult to a JSON-serializable dict."""
    return {
        "email": _serialize_email(sr.email, fields),
        "similarity": sr.similarity,
    }


# --- Lifespan ---


@dataclass
class AppContext:
    db: MailDB


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Initialize MailDB on startup, close on shutdown."""
    db = MailDB()
    db.init_db()
    try:
        yield AppContext(db=db)
    finally:
        db.close()


# --- Server ---

mcp = FastMCP("maildb", lifespan=app_lifespan)


def _get_db(ctx: Context) -> MailDB:
    """Get MailDB instance from lifespan context."""
    return ctx.request_context.lifespan_context.db  # type: ignore[union-attr, no-any-return]


# --- Tools ---


@mcp.tool()
@log_tool
def find(
    ctx: Context,
    sender: str | None = None,
    sender_domain: str | None = None,
    recipient: str | None = None,
    after: str | None = None,
    before: str | None = None,
    has_attachment: bool | None = None,
    subject_contains: str | None = None,
    labels: list[str] | None = None,
    max_to: int | None = None,
    max_cc: int | None = None,
    max_recipients: int | None = None,
    direct_only: bool = False,
    account: str | None = None,
    limit: int = 50,
    offset: int = 0,
    order: str = "date DESC",
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Search emails by structured attribute filters.

    Parameters:
      sender: exact email address (e.g. "alice@acme.com")
      sender_domain: domain portion (e.g. "acme.com")
      recipient: address in To/CC/BCC fields
      after: ISO date string, inclusive (e.g. "2025-01-01")
      before: ISO date string, exclusive
      has_attachment: filter by attachment presence
      subject_contains: case-insensitive substring match in subject
      labels: array containment filter (AND logic, e.g. ["INBOX", "Finance"])
      max_to: max number of To recipients (e.g. 1 for direct messages)
      max_cc: max number of CC recipients (e.g. 0 for no-CC messages)
      max_recipients: max total recipients across To + CC + BCC
      direct_only: shorthand for max_to=1, max_cc=0 (cannot combine with max_to/max_cc)
      account: limit results to this source account (e.g. "you@gmail.com").
        Omit to query across all accounts.
      limit: max results (default 50)
      offset: skip first N results for pagination (default 0)
      order: "date DESC" | "date ASC" | "sender_address ASC" | "sender_address DESC"
      fields: list of field names to return. Default returns headers + body_length (no body_text).
        Pass ["body_text", ...] to include body content.

    Returns {total, offset, limit, results: [{email headers + body_length}, ...]}.

    Example: find(sender="disney@postmates.com", direct_only=True, limit=100)
    """
    db = _get_db(ctx)
    results, total = db.find(
        sender=sender,
        sender_domain=sender_domain,
        recipient=recipient,
        after=after,
        before=before,
        has_attachment=has_attachment,
        subject_contains=subject_contains,
        labels=labels,
        limit=limit,
        offset=offset,
        order=order,
        max_to=max_to,
        max_cc=max_cc,
        max_recipients=max_recipients,
        direct_only=direct_only,
        account=account,
    )
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    serialized = [_serialize_email(e, valid) for e in results]
    return _wrap_response(serialized, total=total, offset=offset, limit=limit)


@mcp.tool()
@log_tool
def search(
    ctx: Context,
    query: str,
    sender: str | None = None,
    sender_domain: str | None = None,
    recipient: str | None = None,
    after: str | None = None,
    before: str | None = None,
    has_attachment: bool | None = None,
    subject_contains: str | None = None,
    labels: list[str] | None = None,
    max_to: int | None = None,
    max_cc: int | None = None,
    max_recipients: int | None = None,
    direct_only: bool = False,
    account: str | None = None,
    limit: int = 20,
    offset: int = 0,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Semantic search for emails by natural language query. Requires Ollama running.

    Parameters:
      query: natural language search text (e.g. "budget concerns", "deployment complaints")
      sender, sender_domain, recipient, after, before, has_attachment, subject_contains, labels:
        same filters as find() — applied on top of semantic ranking
      max_to, max_cc, max_recipients, direct_only: recipient count filters (same as find)
      account: limit results to this source account (e.g. "you@gmail.com").
        Omit to query across all accounts.
      limit: max results (default 20)
      offset: skip first N results for pagination (default 0)
      fields: list of field names to return. Default returns headers + body_length (no body_text).

    Returns {total, offset, limit, results: [{email: {headers + body_length}, similarity}, ...]}.

    Example: search("complaints about deployment", sender_domain="eng.acme.com", limit=5)
    """
    db = _get_db(ctx)
    results, total = db.search(
        query,
        sender=sender,
        sender_domain=sender_domain,
        recipient=recipient,
        after=after,
        before=before,
        has_attachment=has_attachment,
        subject_contains=subject_contains,
        labels=labels,
        limit=limit,
        offset=offset,
        max_to=max_to,
        max_cc=max_cc,
        max_recipients=max_recipients,
        direct_only=direct_only,
        account=account,
    )
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    serialized = [_serialize_search_result(sr, valid) for sr in results]
    return _wrap_response(serialized, total=total, offset=offset, limit=limit)


@mcp.tool()
@log_tool
def get_thread(
    ctx: Context,
    thread_id: str,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Retrieve all emails in a conversation thread, ordered chronologically.

    Parameters:
      thread_id: the thread identifier (from an email's thread_id field)
      fields: list of field names to return (default: all)

    Returns list of email dicts ordered by date ASC.

    Example: get_thread("abc123@mail.gmail.com")
    """
    db = _get_db(ctx)
    results = db.get_thread(thread_id)
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_email(e, valid) for e in results]


@mcp.tool()
@log_tool
def get_thread_for(
    ctx: Context,
    message_id: str,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Find the full thread containing a specific email message.

    Parameters:
      message_id: the RFC 2822 Message-ID of any email in the thread
      fields: list of field names to return (default: all)

    Returns list of email dicts (the full thread) ordered by date ASC. Empty list if not found.

    Example: get_thread_for("<msg-id-123@example.com>")
    """
    db = _get_db(ctx)
    results = db.get_thread_for(message_id)
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_email(e, valid) for e in results]


@mcp.tool()
@log_tool
def top_contacts(
    ctx: Context,
    period: str | None = None,
    direction: str = "both",
    group_by: str = "address",
    exclude_domains: list[str] | None = None,
    account: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    """Find most frequent email correspondents by message count.

    Parameters:
      group_by: "address" (default) for individual addresses, "domain" for domain aggregation
      exclude_domains: list of domains to filter out (e.g. ["mycompany.com"])
      period: ISO date string — only count messages after this date
      direction: "inbound" | "outbound" | "both" (default "both")
      account: limit results to this source account (e.g. "you@gmail.com").
        Omit to query across all accounts.
      limit: max results (default 10)
      offset: skip first N results for pagination (default 0)

    Returns list of {address: str, count: int} (or {domain: str, count: int} when group_by="domain").

    Example: top_contacts(group_by="domain", exclude_domains=["mycompany.com"], direction="outbound")
    """
    db = _get_db(ctx)
    results, total = db.top_contacts(
        period=period,
        limit=limit,
        offset=offset,
        direction=direction,
        group_by=group_by,
        exclude_domains=exclude_domains,
        account=account,
    )
    return _wrap_response(results, total=total, offset=offset, limit=limit)


@mcp.tool()
@log_tool
def topics_with(
    ctx: Context,
    sender: str | None = None,
    sender_domain: str | None = None,
    limit: int = 5,
    offset: int = 0,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Find representative emails spanning different topics with a contact.

    Uses embedding-based farthest-point selection for maximum topic diversity.

    Parameters:
      sender: exact email address (e.g. "bob@acme.com")
      sender_domain: domain to match (e.g. "acme.com") — provide sender OR sender_domain
      limit: number of diverse representatives (default 5)
      offset: skip first N results for pagination (default 0)
      fields: list of field names to return (default: all)

    Returns list of email dicts maximizing topic diversity.

    Example: topics_with(sender="bob@corp.com", limit=5)
    """
    db = _get_db(ctx)
    results, total = db.topics_with(
        sender=sender, sender_domain=sender_domain, limit=limit, offset=offset
    )
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    serialized = [_serialize_email(e, valid) for e in results]
    return _wrap_response(serialized, total=total, offset=offset, limit=limit)


@mcp.tool()
@log_tool
def unreplied(
    ctx: Context,
    direction: Literal["inbound", "outbound"] = "inbound",
    recipient: str | None = None,
    after: str | None = None,
    before: str | None = None,
    sender: str | None = None,
    sender_domain: str | None = None,
    max_to: int | None = None,
    max_cc: int | None = None,
    max_recipients: int | None = None,
    direct_only: bool = False,
    account: str | None = None,
    limit: int = 100,
    offset: int = 0,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Find emails with no reply in the same thread.

    Parameters:
      direction: "inbound" (default) — messages from others where you never replied
                 "outbound" — your messages where recipient(s) never replied
      recipient: for outbound — filter to a specific recipient and check they never replied
      after, before: ISO date range filters
      sender, sender_domain: for inbound — filter by original sender
      max_to, max_cc, max_recipients, direct_only: recipient count filters (same as find)
      account: limit results to this source account (e.g. "you@gmail.com").
        Omit to query across all accounts.
      limit: max results (default 100)
      offset: skip first N results for pagination (default 0)
      fields: list of field names to return. Default returns headers + body_length (no body_text).

    Returns {total, offset, limit, results: [{email headers + body_length}, ...]}.

    Example: unreplied(direction="outbound", recipient="bob@corp.com")
    """
    db = _get_db(ctx)
    results, total = db.unreplied(
        direction=direction,
        recipient=recipient,
        after=after,
        before=before,
        sender=sender,
        sender_domain=sender_domain,
        limit=limit,
        offset=offset,
        account=account,
    )
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    serialized = [_serialize_email(e, valid) for e in results]
    return _wrap_response(serialized, total=total, offset=offset, limit=limit)


@mcp.tool()
@log_tool
def correspondence(
    ctx: Context,
    address: str,
    after: str | None = None,
    before: str | None = None,
    limit: int = 500,
    offset: int = 0,
    order: str = "date ASC",
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Get all emails exchanged with a specific person (sent by them or to them).

    Parameters:
      address: the person's email address (required)
      after, before: ISO date range filters
      limit: max results (default 500, higher for full relationship history)
      offset: skip first N results for pagination (default 0)
      order: "date ASC" (default, chronological) or "date DESC"
      fields: list of field names to return. Default returns headers + body_length (no body_text).

    Returns {total, offset, limit, results: [{email headers + body_length}, ...]}.

    Example: correspondence(address="scott@banister.com", after="2024-01-01")
    """
    db = _get_db(ctx)
    results, total = db.correspondence(
        address=address, after=after, before=before, limit=limit, offset=offset, order=order
    )
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    serialized = [_serialize_email(e, valid) for e in results]
    return _wrap_response(serialized, total=total, offset=offset, limit=limit)


@mcp.tool()
@log_tool
def mention_search(
    ctx: Context,
    text: str,
    sender: str | None = None,
    sender_domain: str | None = None,
    after: str | None = None,
    before: str | None = None,
    max_to: int | None = None,
    max_cc: int | None = None,
    max_recipients: int | None = None,
    direct_only: bool = False,
    account: str | None = None,
    limit: int = 50,
    offset: int = 0,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Search for emails containing specific text in body or subject (case-insensitive).

    Unlike search(), uses ILIKE substring matching — no Ollama needed.

    Parameters:
      text: search term (case-insensitive, e.g. "pei-chin", "chief of staff")
      sender, sender_domain: optional sender filters
      after, before: ISO date range filters
      max_to, max_cc, max_recipients, direct_only: recipient count filters (same as find)
      account: limit results to this source account (e.g. "you@gmail.com").
        Omit to query across all accounts.
      limit: max results (default 50)
      offset: skip first N results for pagination (default 0)
      fields: list of field names to return. Default returns headers + body_length (no body_text).

    Returns {total, offset, limit, results: [{email headers + body_length}, ...]}.

    Example: mention_search(text="quarterly review", sender_domain="acme.com")
    """
    db = _get_db(ctx)
    results, total = db.mention_search(
        text=text,
        sender=sender,
        sender_domain=sender_domain,
        after=after,
        before=before,
        limit=limit,
        offset=offset,
        max_to=max_to,
        max_cc=max_cc,
        max_recipients=max_recipients,
        direct_only=direct_only,
        account=account,
    )
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    serialized = [_serialize_email(e, valid) for e in results]
    return _wrap_response(serialized, total=total, offset=offset, limit=limit)


@mcp.tool()
@log_tool
def cluster(
    ctx: Context,
    where: dict[str, Any] | None = None,
    message_ids: list[str] | None = None,
    limit: int = 5,
    offset: int = 0,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Extract diverse topic representatives from an email subset using embedding similarity.

    Provide exactly one of where or message_ids (not both).

    Parameters:
      where: DSL filter dict (e.g. {"field": "sender_domain", "eq": "stripe.com"})
      message_ids: explicit list of message_id strings (for chaining with other tools)
      limit: number of diverse representatives (default 5)
      offset: skip first N results for pagination (default 0)
      fields: list of field names to return (default: all)

    Returns list of email dicts maximizing topic diversity via farthest-point selection.

    Example: cluster(where={"and": [{"field": "sender_domain", "eq": "stripe.com"}, {"field": "date", "gte": "2024-01-01"}]}, limit=5)
    """
    db = _get_db(ctx)
    results, total = db.cluster(where=where, message_ids=message_ids, limit=limit, offset=offset)
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    serialized = [_serialize_email(e, valid) for e in results]
    return _wrap_response(serialized, total=total, offset=offset, limit=limit)


@mcp.tool()
@log_tool
def long_threads(
    ctx: Context,
    min_messages: int = 5,
    after: str | None = None,
    participant: str | None = None,
    account: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Find email threads with many messages.

    Parameters:
      min_messages: minimum message count threshold (default 5)
      after: ISO date string — only count messages after this date
      participant: only threads where this address appears as sender
      account: limit results to this source account (e.g. "you@gmail.com").
        Omit to query across all accounts.
      limit: maximum number of threads to return (default 50)
      offset: skip first N results for pagination (default 0)

    Returns list of {thread_id, message_count, first_date, last_date, participants[]}.

    Example: long_threads(min_messages=10, participant="alice@example.com")
    """
    db = _get_db(ctx)
    results, total = db.long_threads(
        min_messages=min_messages,
        after=after,
        participant=participant,
        limit=limit,
        offset=offset,
        account=account,
    )
    return _wrap_response(results, total=total, offset=offset, limit=limit)


@mcp.tool()
@log_tool
def query(
    ctx: Context,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """Execute a structured query using the maildb DSL.

    Parameters:
      spec: JSON object with keys:
        from: "emails" | "sent_to" | "email_labels" (default: "emails")
        select: [{field: "col"}, {count: "*", as: "n"}, {date_trunc: "month", field: "date", as: "period"}]
        where: {field: "col", op: value} or {and/or/not: [...]}
          Operators: eq, neq, gt, gte, lt, lte, ilike, not_ilike, in, not_in, contains, is_null
        group_by: ["col1", "col2"]
        having: same syntax as where, can reference select aliases
        order_by: [{field: "col", dir: "asc|desc"}]
        limit: int (max 1000, default 50)
        offset: int

    Returns list of dicts. 5s statement timeout enforced.

    Example: query(spec={"from": "sent_to", "select": [{"field": "recipient_domain"}, {"count": "*", "as": "n"}], "group_by": ["recipient_domain"], "order_by": [{"field": "n", "dir": "desc"}], "limit": 10})
    """
    db = _get_db(ctx)
    return db.query(spec)


@mcp.tool()
@log_tool
def get_emails(
    ctx: Context,
    ids: list[str],
    body_max_chars: int | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch full email objects by message ID, with optional body truncation.

    Parameters:
      ids: list of RFC 2822 Message-ID strings
      body_max_chars: truncate body_text to N characters (None = full body).
        When truncated, body_truncated=true is added.
      fields: list of field names to return (default: all including body_text)

    Returns {total, results: [{email}, ...]}.
    Results include body_text by default. Order matches input ids list.

    Example: get_emails(ids=["abc@mail.gmail.com", "def@mail.gmail.com"], body_max_chars=500)
    """
    db = _get_db(ctx)
    results = db.get_emails(ids)
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    # For get_emails, include body_text by default (unlike list tools)
    serialized = [
        _serialize_email(
            e, fields=valid or SERIALIZABLE_EMAIL_FIELDS, body_max_chars=body_max_chars
        )
        for e in results
    ]
    return _wrap_response(serialized, total=len(serialized), offset=0, limit=len(ids))


@mcp.tool()
@log_tool
def accounts(ctx: Context) -> list[dict[str, Any]]:
    """List the email accounts present in the database with email counts.

    Returns list of {source_account, email_count, first_date, last_date, import_count}.
    Use this to discover which accounts are available before scoping queries with `account=...`.
    """
    db = _get_db(ctx)
    summaries = db.accounts()
    return [
        {
            "source_account": s.source_account,
            "email_count": s.email_count,
            "first_date": s.first_date.isoformat() if s.first_date else None,
            "last_date": s.last_date.isoformat() if s.last_date else None,
            "import_count": s.import_count,
        }
        for s in summaries
    ]


@mcp.tool()
@log_tool
def import_history(
    ctx: Context,
    account: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List ingest sessions, newest first.

    Parameters:
      account: filter to one source account (optional)
      limit: max rows (default 50)
      offset: pagination offset

    Returns list of {id, source_account, source_file, started_at, completed_at,
    messages_total, messages_inserted, messages_skipped, status}.
    """
    db = _get_db(ctx)
    records = db.import_history(account=account, limit=limit, offset=offset)
    return [
        {
            "id": str(r.id),
            "source_account": r.source_account,
            "source_file": r.source_file,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "messages_total": r.messages_total,
            "messages_inserted": r.messages_inserted,
            "messages_skipped": r.messages_skipped,
            "status": r.status,
        }
        for r in records
    ]
