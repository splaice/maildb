# src/chronicle_server/sources.py
"""Authoritative source-access endpoints: messages, attachments, threads."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from chronicle_server.auth import require_user
from chronicle_server.ids import decode_source_id, encode_source_id, msg_key_to_uuid
from chronicle_server.sanitize import sanitize_email_html

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

router = APIRouter(tags=["sources"])

_MARKDOWN_PAGE = 50_000
_THREAD_MSG_CAP = 500


# --- response models ---


class AttachmentMeta(BaseModel):
    id: str
    filename: str
    content_type: str | None = None
    size: int | None = None


class BodyDescriptor(BaseModel):
    text: str | None
    html: str | None
    remote_resources_blocked: int = 0
    had_active_content: bool = False


class MessageEnvelope(BaseModel):
    id: str
    thread_id: str | None = None
    subject: str | None = None
    sender_name: str | None = None
    sender_address: str | None = None
    recipients: Any = None
    date: str | None = None
    mailbox: str | None = None
    labels: list[str] = Field(default_factory=list)
    has_attachment: bool = False
    attachments: list[AttachmentMeta] = Field(default_factory=list)


class MessageSource(BaseModel):
    kind: Literal["msg"] = "msg"
    envelope: MessageEnvelope
    body: BodyDescriptor


class AttachmentSource(BaseModel):
    kind: Literal["att"] = "att"
    id: str
    filename: str
    content_type: str | None = None
    size: int | None = None
    source_message_id: str | None = None
    source_envelope: MessageEnvelope | None = None
    extraction_status: str | None = None
    extraction_reason: str | None = None
    markdown: str | None = None
    truncated: bool = False
    text_offset: int = 0


class SourceContext(BaseModel):
    id: str
    start: int
    end: int
    excerpt: str
    context_before: str
    context_after: str
    sha256: str
    window: int


class ThreadParticipant(BaseModel):
    name: str | None = None
    address: str | None = None


class ThreadDateRange(BaseModel):
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None

    model_config = {"populate_by_name": True}


class ThreadMessage(BaseModel):
    id: str
    subject: str | None = None
    sender_name: str | None = None
    sender_address: str | None = None
    recipients: Any = None
    date: str | None = None
    mailbox: str | None = None
    labels: list[str] = Field(default_factory=list)
    has_attachment: bool = False


class ThreadResponse(BaseModel):
    thread_id: str
    subject: str | None = None
    date_range: ThreadDateRange
    participants: list[ThreadParticipant]
    message_count: int
    messages: list[ThreadMessage]
    truncated: bool = False


# --- helpers ---


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


def _labels(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def _404(detail: str = "Not found") -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


def _decode_or_404(sid: str) -> tuple[str, int | str]:
    try:
        return decode_source_id(sid)
    except ValueError:
        raise _404("Not found") from None


def _fetch_attachment_metas(conn: Any, email_id: UUID) -> list[AttachmentMeta]:
    rows = conn.execute(
        """
        SELECT a.id, a.filename, a.content_type, a.size
          FROM email_attachments ea
          JOIN attachments a ON a.id = ea.attachment_id
         WHERE ea.email_id = %(email_id)s
         ORDER BY a.id
        """,
        {"email_id": email_id},
    ).fetchall()
    return [
        AttachmentMeta(
            id=encode_source_id("att", r[0]),
            filename=r[1],
            content_type=r[2],
            size=r[3],
        )
        for r in rows
    ]


def _envelope_from_row(
    row: dict[str, Any],
    attachments: list[AttachmentMeta] | None = None,
) -> MessageEnvelope:
    email_id: UUID = row["id"]
    thread_raw = row.get("thread_id")
    thr_sid = encode_source_id("thr", thread_raw) if thread_raw else None
    return MessageEnvelope(
        id=encode_source_id("msg", email_id),
        thread_id=thr_sid,
        subject=row.get("subject"),
        sender_name=row.get("sender_name"),
        sender_address=row.get("sender_address"),
        recipients=row.get("recipients"),
        date=_iso(row.get("date")),
        mailbox=row.get("source_account"),
        labels=_labels(row.get("labels")),
        has_attachment=bool(row.get("has_attachment")),
        attachments=attachments if attachments is not None else [],
    )


def _row_to_dict(row: Any, columns: list[str]) -> dict[str, Any]:
    return dict(zip(columns, row, strict=True))


_EMAIL_COLS = [
    "id",
    "thread_id",
    "subject",
    "sender_name",
    "sender_address",
    "recipients",
    "date",
    "source_account",
    "labels",
    "has_attachment",
    "body_text",
    "body_html",
]


def get_message_source(pool: ConnectionPool, msg_key: int) -> MessageSource:
    email_uuid = msg_key_to_uuid(msg_key)
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, thread_id, subject, sender_name, sender_address,
                   recipients, date, source_account, labels, has_attachment,
                   body_text, body_html
              FROM emails
             WHERE id = %(id)s
            """,
            {"id": email_uuid},
        ).fetchone()
        if row is None:
            raise _404()
        data = _row_to_dict(row, _EMAIL_COLS)
        att_metas = _fetch_attachment_metas(conn, email_uuid)

    body_html_raw = data.get("body_html")
    if body_html_raw:
        sanitized = sanitize_email_html(str(body_html_raw))
        body = BodyDescriptor(
            text=data.get("body_text"),
            html=sanitized["html"],
            remote_resources_blocked=sanitized["remote_resources_blocked"],
            had_active_content=sanitized["had_active_content"],
        )
    else:
        body = BodyDescriptor(
            text=data.get("body_text"),
            html=None,
            remote_resources_blocked=0,
            had_active_content=False,
        )

    return MessageSource(
        envelope=_envelope_from_row(data, att_metas),
        body=body,
    )


def get_attachment_source(
    pool: ConnectionPool,
    att_key: int,
    text_offset: int = 0,
) -> AttachmentSource:
    if text_offset < 0:
        raise HTTPException(status_code=400, detail="text_offset must be >= 0")

    with pool.connection() as conn:
        att_row = conn.execute(
            """
            SELECT a.id, a.filename, a.content_type, a.size,
                   ac.status, ac.reason, ac.markdown
              FROM attachments a
              LEFT JOIN attachment_contents ac ON ac.attachment_id = a.id
             WHERE a.id = %(id)s
            """,
            {"id": att_key},
        ).fetchone()
        if att_row is None:
            raise _404()

        # Earliest linked source message for envelope basics.
        email_row = conn.execute(
            """
            SELECT e.id, e.thread_id, e.subject, e.sender_name, e.sender_address,
                   e.recipients, e.date, e.source_account, e.labels, e.has_attachment
              FROM email_attachments ea
              JOIN emails e ON e.id = ea.email_id
             WHERE ea.attachment_id = %(id)s
             ORDER BY e.date NULLS LAST, e.id
             LIMIT 1
            """,
            {"id": att_key},
        ).fetchone()

    filename = att_row[1]
    content_type = att_row[2]
    size = att_row[3]
    status = att_row[4]
    reason = att_row[5]
    markdown_full = att_row[6]

    source_message_id: str | None = None
    source_envelope: MessageEnvelope | None = None
    if email_row is not None:
        email_cols = [
            "id",
            "thread_id",
            "subject",
            "sender_name",
            "sender_address",
            "recipients",
            "date",
            "source_account",
            "labels",
            "has_attachment",
        ]
        edata = _row_to_dict(email_row, email_cols)
        source_envelope = _envelope_from_row(edata, attachments=[])
        source_message_id = source_envelope.id

    markdown_out: str | None = None
    truncated = False
    if status == "extracted" and markdown_full is not None:
        text = str(markdown_full)
        if text_offset > len(text):
            raise HTTPException(status_code=416, detail="text_offset out of range")
        page = text[text_offset : text_offset + _MARKDOWN_PAGE]
        truncated = text_offset + _MARKDOWN_PAGE < len(text)
        markdown_out = page

    return AttachmentSource(
        id=encode_source_id("att", att_key),
        filename=filename,
        content_type=content_type,
        size=size,
        source_message_id=source_message_id,
        source_envelope=source_envelope,
        extraction_status=status,
        extraction_reason=reason,
        markdown=markdown_out,
        truncated=truncated,
        text_offset=text_offset,
    )


def get_source_context(
    pool: ConnectionPool,
    sid: str,
    start: int,
    end: int,
    window: int = 400,
) -> SourceContext:
    kind, key = _decode_or_404(sid)
    if kind not in ("msg", "att"):
        raise _404()

    if start < 0 or end < 0 or end < start:
        raise HTTPException(status_code=416, detail="offsets out of range")

    with pool.connection() as conn:
        if kind == "msg":
            assert isinstance(key, int)
            row = conn.execute(
                "SELECT body_text FROM emails WHERE id = %(id)s",
                {"id": msg_key_to_uuid(key)},
            ).fetchone()
            if row is None:
                raise _404()
            text = row[0] if row[0] is not None else ""
        else:
            assert isinstance(key, int)
            row = conn.execute(
                """
                SELECT ac.markdown
                  FROM attachments a
                  LEFT JOIN attachment_contents ac ON ac.attachment_id = a.id
                 WHERE a.id = %(id)s
                """,
                {"id": key},
            ).fetchone()
            if row is None:
                raise _404()
            text = row[0] if row[0] is not None else ""

    text_s = str(text)
    length = len(text_s)
    if start > length or end > length:
        raise HTTPException(status_code=416, detail="offsets out of range")

    excerpt = text_s[start:end]
    context_before = text_s[max(0, start - window) : start]
    context_after = text_s[end : min(length, end + window)]
    digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()

    return SourceContext(
        id=sid if sid.startswith(("msg_", "att_")) else encode_source_id(kind, key),
        start=start,
        end=end,
        excerpt=excerpt,
        context_before=context_before,
        context_after=context_after,
        sha256=digest,
        window=window,
    )


def get_thread(pool: ConnectionPool, thread_id: str) -> ThreadResponse:
    with pool.connection() as conn:
        count_row = conn.execute(
            "SELECT count(*)::int FROM emails WHERE thread_id = %(tid)s",
            {"tid": thread_id},
        ).fetchone()
        message_count = count_row[0] if count_row is not None else 0
        if message_count == 0:
            raise _404()

        range_row = conn.execute(
            """
            SELECT MIN(date), MAX(date),
                   (array_agg(subject ORDER BY date NULLS LAST, id)
                      FILTER (WHERE subject IS NOT NULL))[1] AS first_subject
              FROM emails
             WHERE thread_id = %(tid)s
            """,
            {"tid": thread_id},
        ).fetchone()

        participants_rows = conn.execute(
            """
            SELECT DISTINCT ON (COALESCE(sender_address, ''), COALESCE(sender_name, ''))
                   sender_name, sender_address
              FROM emails
             WHERE thread_id = %(tid)s
             ORDER BY COALESCE(sender_address, ''), COALESCE(sender_name, '')
            """,
            {"tid": thread_id},
        ).fetchall()

        msg_rows = conn.execute(
            """
            SELECT id, subject, sender_name, sender_address, recipients,
                   date, source_account, labels, has_attachment
              FROM emails
             WHERE thread_id = %(tid)s
             ORDER BY date NULLS LAST, id
             LIMIT %(lim)s
            """,
            {"tid": thread_id, "lim": _THREAD_MSG_CAP},
        ).fetchall()

    date_from = _iso(range_row[0]) if range_row else None
    date_to = _iso(range_row[1]) if range_row else None
    subject = range_row[2] if range_row else None

    participants = [
        ThreadParticipant(name=r[0], address=r[1]) for r in participants_rows if r[0] or r[1]
    ]

    messages: list[ThreadMessage] = []
    for r in msg_rows:
        messages.append(
            ThreadMessage(
                id=encode_source_id("msg", r[0]),
                subject=r[1],
                sender_name=r[2],
                sender_address=r[3],
                recipients=r[4],
                date=_iso(r[5]),
                mailbox=r[6],
                labels=_labels(r[7]),
                has_attachment=bool(r[8]),
            )
        )

    return ThreadResponse(
        thread_id=encode_source_id("thr", thread_id),
        subject=subject,
        date_range=ThreadDateRange(**{"from": date_from, "to": date_to}),
        participants=participants,
        message_count=message_count,
        messages=messages,
        truncated=message_count > _THREAD_MSG_CAP,
    )


# --- routes ---


@router.get("/sources/{sid}")
def get_source(
    sid: str,
    request: Request,
    text_offset: int = Query(0, ge=0),
    _user: str = Depends(require_user),
) -> MessageSource | AttachmentSource:
    """Authoritative source metadata and safe body/preview descriptor."""
    kind, key = _decode_or_404(sid)
    pool: ConnectionPool = request.app.state.pool
    if kind == "msg":
        assert isinstance(key, int)
        return get_message_source(pool, key)
    if kind == "att":
        assert isinstance(key, int)
        return get_attachment_source(pool, key, text_offset=text_offset)
    # thr_* is not a source document
    raise _404()


@router.get("/sources/{sid}/context")
def get_context(
    sid: str,
    request: Request,
    start: int = Query(..., ge=0),
    end: int = Query(..., ge=0),
    window: int = Query(400, ge=0),
    _user: str = Depends(require_user),
) -> SourceContext:
    """Passage excerpt with surrounding context and sha256 hash."""
    pool: ConnectionPool = request.app.state.pool
    return get_source_context(pool, sid, start=start, end=end, window=window)


@router.get("/threads/{thr_sid}")
def get_thread_endpoint(
    thr_sid: str,
    request: Request,
    _user: str = Depends(require_user),
) -> ThreadResponse:
    """Thread envelope list ordered by date (no bodies)."""
    kind, key = _decode_or_404(thr_sid)
    if kind != "thr":
        raise _404()
    assert isinstance(key, str)
    pool: ConnectionPool = request.app.state.pool
    return get_thread(pool, key)
