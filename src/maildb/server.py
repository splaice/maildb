from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP

from maildb.maildb import MailDB

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# --- Serialization ---


def _serialize_email(email: Any) -> dict[str, Any]:
    """Convert an Email dataclass to a JSON-serializable dict."""
    d = asdict(email)
    # Convert non-serializable types
    if isinstance(d.get("id"), UUID):
        d["id"] = str(d["id"])
    if isinstance(d.get("date"), datetime):
        d["date"] = d["date"].isoformat() if d["date"] else None
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
    # Drop embedding from serialized output (too large, not useful for agents)
    d.pop("embedding", None)
    return d


def _serialize_search_result(sr: Any) -> dict[str, Any]:
    """Convert a SearchResult to a JSON-serializable dict."""
    return {
        "email": _serialize_email(sr.email),
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
    limit: int = 50,
    order: str = "date DESC",
) -> list[dict[str, Any]]:
    """Search emails by structured filters: sender, domain, date range, attachments, subject, labels."""
    db = _get_db(ctx)
    results = db.find(
        sender=sender,
        sender_domain=sender_domain,
        recipient=recipient,
        after=after,
        before=before,
        has_attachment=has_attachment,
        subject_contains=subject_contains,
        labels=labels,
        limit=limit,
        order=order,
    )
    return [_serialize_email(e) for e in results]


@mcp.tool()
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
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Semantic search for emails by natural language query, with optional structured filters."""
    db = _get_db(ctx)
    results = db.search(
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
    )
    return [_serialize_search_result(sr) for sr in results]


@mcp.tool()
def get_thread(ctx: Context, thread_id: str) -> list[dict[str, Any]]:
    """Get all emails in a conversation thread, ordered chronologically."""
    db = _get_db(ctx)
    results = db.get_thread(thread_id)
    return [_serialize_email(e) for e in results]


@mcp.tool()
def get_thread_for(ctx: Context, message_id: str) -> list[dict[str, Any]]:
    """Find the full thread containing a specific email message."""
    db = _get_db(ctx)
    results = db.get_thread_for(message_id)
    return [_serialize_email(e) for e in results]


@mcp.tool()
def top_contacts(
    ctx: Context,
    period: str | None = None,
    limit: int = 10,
    direction: str = "both",
) -> list[dict[str, Any]]:
    """Find most frequent email correspondents. Direction: 'inbound', 'outbound', or 'both'."""
    db = _get_db(ctx)
    return db.top_contacts(period=period, limit=limit, direction=direction)


@mcp.tool()
def topics_with(
    ctx: Context,
    sender: str | None = None,
    sender_domain: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Find representative emails spanning different topics with a contact."""
    db = _get_db(ctx)
    results = db.topics_with(sender=sender, sender_domain=sender_domain, limit=limit)
    return [_serialize_email(e) for e in results]


@mcp.tool()
def unreplied(
    ctx: Context,
    after: str | None = None,
    before: str | None = None,
    sender: str | None = None,
    sender_domain: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Find inbound emails that have no outbound reply in the same thread."""
    db = _get_db(ctx)
    results = db.unreplied(
        after=after, before=before, sender=sender, sender_domain=sender_domain, limit=limit
    )
    return [_serialize_email(e) for e in results]


@mcp.tool()
def long_threads(
    ctx: Context,
    min_messages: int = 5,
    after: str | None = None,
) -> list[dict[str, Any]]:
    """Find email threads with many messages."""
    db = _get_db(ctx)
    return db.long_threads(min_messages=min_messages, after=after)
