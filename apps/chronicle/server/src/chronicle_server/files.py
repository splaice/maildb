"""Attachment browser, sandboxed preview, and download endpoints."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from chronicle_server.auth import require_user
from chronicle_server.cursor import decode_cursor, encode_cursor
from chronicle_server.db import audit
from chronicle_server.ids import decode_source_id, encode_source_id
from chronicle_server.scope import QueryScope, scope_filters, scope_fingerprint

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

router = APIRouter(tags=["attachments"])

_LIST_DEFAULT_LIMIT = 50
_LIST_MAX_LIMIT = 200
_OCCURRENCE_BOUND = 20

# Content-type family → ILIKE patterns (module constant per task).
# "other" is handled as NOT matching any of the named families.
CONTENT_TYPE_FAMILY_PATTERNS: dict[str, list[str]] = {
    "pdf": ["application/pdf%"],
    "image": ["image/%"],
    "spreadsheet": [
        "application/vnd.openxmlformats-officedocument.spreadsheetml%",
        "application/vnd.ms-excel%",
        "text/csv%",
    ],
    "document": [
        "application/vnd.openxmlformats-officedocument.wordprocessingml%",
        "application/msword%",
        "application/vnd.openxmlformats-officedocument.presentationml%",
        "text/html%",
    ],
    "text": ["text/plain%"],
}

_ALL_FAMILY_PATTERNS: list[str] = [
    p for patterns in CONTENT_TYPE_FAMILY_PATTERNS.values() for p in patterns
]

_PREVIEW_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_PREVIEW_PDF = "application/pdf"
_PREVIEW_TEXT = "text/plain"

# Email columns referenced by scope_filters — prefix with table alias for joins.
_SCOPE_COLS = (
    "date",
    "source_account",
    "sender_address",
    "recipients",
    "subject",
    "has_attachment",
)


# --- models ---


class AttachmentListFilters(BaseModel):
    filename: str | None = None
    content_type_family: (
        Literal["pdf", "image", "spreadsheet", "document", "text", "other"] | None
    ) = None
    status: str | None = None
    date_from: str | None = None
    date_to: str | None = None


class AttachmentListRequest(BaseModel):
    scope: QueryScope = Field(default_factory=QueryScope)
    filters: AttachmentListFilters = Field(default_factory=AttachmentListFilters)
    cursor: str | None = None
    limit: int = _LIST_DEFAULT_LIMIT
    group_duplicates: bool = False

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError("limit must be >= 1")
        return min(value, _LIST_MAX_LIMIT)


class ExtractionInfo(BaseModel):
    status: str
    reason: str | None = None


class AttachmentOccurrence(BaseModel):
    id: str
    subject: str | None = None
    sender: str | None = None
    date: str | None = None


class AttachmentListItem(BaseModel):
    id: str
    filename: str
    content_type: str | None = None
    size: int | None = None
    date: str | None = None
    sender_name: str | None = None
    sender_address: str | None = None
    source_message_id: str
    source_subject: str | None = None
    extraction: ExtractionInfo
    sha256: str
    duplicate_count: int
    occurrences: list[AttachmentOccurrence] | None = None


class AttachmentListResponse(BaseModel):
    items: list[AttachmentListItem]
    next_cursor: str | None = None
    scope_fingerprint: str


class PreviewDenied(BaseModel):
    preview: bool = False
    reason: str


# --- helpers ---


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _prefix_scope_conditions(conditions: list[str], alias: str = "e") -> list[str]:
    """Prefix bare email column names from scope_filters with table alias."""
    out: list[str] = []
    for cond in conditions:
        rewritten = cond
        for col in _SCOPE_COLS:
            rewritten = re.sub(rf"\b{col}\b", f"{alias}.{col}", rewritten)
        out.append(rewritten)
    return out


def _404(detail: str = "Not found") -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


def _content_disposition(disposition: str, filename: str) -> str:
    """RFC 5987 filename* for non-ASCII; keep a simple ASCII fallback."""
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    ascii_fallback = ascii_fallback.replace('"', "'") or "download"
    encoded = quote(filename, safe="")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def _family_condition(family: str | None, params: dict[str, Any]) -> str | None:
    if not family:
        return None
    if family == "other":
        parts: list[str] = []
        for i, pattern in enumerate(_ALL_FAMILY_PATTERNS):
            key = f"fam_other_{i}"
            parts.append(f"a.content_type NOT ILIKE %({key})s ESCAPE '\\'")
            params[key] = pattern
        # NULL content_type counts as other
        return f"(a.content_type IS NULL OR ({' AND '.join(parts)}))"
    patterns = CONTENT_TYPE_FAMILY_PATTERNS.get(family)
    if not patterns:
        return None
    parts = []
    for i, pattern in enumerate(patterns):
        key = f"fam_{family}_{i}"
        parts.append(f"a.content_type ILIKE %({key})s ESCAPE '\\'")
        params[key] = pattern
    return f"({' OR '.join(parts)})"


def _match_magic(content_type: str, head: bytes) -> bool:
    """Tiny magic-number check for preview allowlist (no deps)."""
    ct = content_type.lower().split(";")[0].strip()
    if ct == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ct == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if ct == "image/gif":
        return head.startswith(b"GIF87a") or head.startswith(b"GIF89a")
    if ct == "image/webp":
        return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ct == "application/pdf":
        return head.startswith(b"%PDF")
    return ct == "text/plain"


def _resolve_contained(root: Path, storage_path: str) -> Path | None:
    """Resolve storage_path under root; return None if escapes or missing."""
    try:
        root_resolved = root.resolve()
        # Reject absolute storage paths and null bytes up front.
        if not storage_path or "\x00" in storage_path:
            return None
        candidate = Path(storage_path)
        if candidate.is_absolute():
            return None
        resolved = (root_resolved / storage_path).resolve()
        if not resolved.is_relative_to(root_resolved):
            return None
        if not resolved.is_file():
            return None
        return resolved
    except (OSError, ValueError, RuntimeError):
        return None


def _parse_list_cursor(token: str, secret_key: str) -> tuple[str | None, int]:
    try:
        payload = decode_cursor(token, secret_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if "id" not in payload:
        raise HTTPException(status_code=400, detail="invalid cursor")
    try:
        last_id = int(payload["id"])
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    d = payload.get("d")
    if d is not None and not isinstance(d, str):
        raise HTTPException(status_code=400, detail="invalid cursor")
    return d if isinstance(d, str) else None, last_id


def _fetch_occurrences(conn: Any, sha256_list: list[str]) -> dict[str, list[AttachmentOccurrence]]:
    """Bounded provenance list per sha256 (exact duplicates only)."""
    if not sha256_list:
        return {}
    rows = conn.execute(
        """
        SELECT a.sha256, e.id, e.subject, e.sender_name, e.sender_address, e.date
          FROM attachments a
          JOIN email_attachments ea ON ea.attachment_id = a.id
          JOIN emails e ON e.id = ea.email_id
         WHERE a.sha256 = ANY(%(shas)s)
         ORDER BY a.sha256, e.date DESC NULLS LAST, e.id DESC
        """,
        {"shas": sha256_list},
    ).fetchall()
    out: dict[str, list[AttachmentOccurrence]] = {s: [] for s in sha256_list}
    for r in rows:
        sha = r[0]
        bucket = out.setdefault(sha, [])
        if len(bucket) >= _OCCURRENCE_BOUND:
            continue
        sender = r[3] or r[4]
        bucket.append(
            AttachmentOccurrence(
                id=encode_source_id("msg", r[1]),
                subject=r[2],
                sender=sender,
                date=_iso(r[5]),
            )
        )
    return out


def list_attachments(
    pool: ConnectionPool,
    body: AttachmentListRequest,
    secret_key: str,
) -> AttachmentListResponse:
    """Keyset-paginated attachment list with optional exact-duplicate grouping."""
    scope_conds, scope_params = scope_filters(body.scope)
    scope_conds = _prefix_scope_conditions(scope_conds, "e")
    filters = body.filters
    conditions: list[str] = list(scope_conds)
    params: dict[str, Any] = {
        "lim": body.limit + 1,
        **scope_params,
    }

    if filters.filename:
        conditions.append("a.filename ILIKE %(fn_pattern)s ESCAPE '\\'")
        params["fn_pattern"] = f"%{_escape_like(filters.filename)}%"

    fam = _family_condition(filters.content_type_family, params)
    if fam:
        conditions.append(fam)

    if filters.status:
        conditions.append("COALESCE(ac.status, 'pending') = %(status)s")
        params["status"] = filters.status

    if filters.date_from:
        conditions.append("e.date >= %(filt_from)s")
        params["filt_from"] = filters.date_from
    if filters.date_to:
        conditions.append("e.date < %(filt_to)s")
        params["filt_to"] = filters.date_to

    if body.cursor:
        cursor_d, cursor_id = _parse_list_cursor(body.cursor, secret_key)
        params["cursor_id"] = cursor_id
        if cursor_d is not None:
            params["cursor_d"] = cursor_d
            conditions.append(
                "("
                " (e.date IS NOT NULL AND (e.date > %(cursor_d)s"
                "  OR (e.date = %(cursor_d)s AND a.id > %(cursor_id)s)))"
                " OR e.date IS NULL"
                ")"
            )
        else:
            conditions.append("e.date IS NULL AND a.id > %(cursor_id)s")

    where_sql = " AND ".join(conditions) if conditions else "TRUE"

    # Precompute duplicate counts (window-free CTE).
    if body.group_duplicates:
        # One row per sha256: latest occurrence as representative.
        sql = f"""
            WITH dup_counts AS (
                SELECT a2.sha256, COUNT(*)::int AS duplicate_count
                  FROM attachments a2
                  JOIN email_attachments ea2 ON ea2.attachment_id = a2.id
                 GROUP BY a2.sha256
            ),
            ranked AS (
                SELECT DISTINCT ON (a.sha256)
                       a.id AS att_id, a.filename, a.content_type, a.size,
                       a.sha256, a.storage_path,
                       e.id AS email_id, e.subject, e.sender_name, e.sender_address,
                       e.date,
                       COALESCE(ac.status, 'pending') AS ext_status,
                       ac.reason AS ext_reason,
                       COALESCE(dc.duplicate_count, 1) AS duplicate_count
                  FROM attachments a
                  JOIN email_attachments ea ON ea.attachment_id = a.id
                  JOIN emails e ON e.id = ea.email_id
                  LEFT JOIN attachment_contents ac ON ac.attachment_id = a.id
                  LEFT JOIN dup_counts dc ON dc.sha256 = a.sha256
                 WHERE {where_sql}
                 ORDER BY a.sha256, e.date DESC NULLS LAST, a.id DESC
            )
            SELECT att_id, filename, content_type, size, sha256, storage_path,
                   email_id, subject, sender_name, sender_address, date,
                   ext_status, ext_reason, duplicate_count
              FROM ranked
             ORDER BY date ASC NULLS LAST, att_id ASC
             LIMIT %(lim)s
        """
    else:
        sql = f"""
            WITH dup_counts AS (
                SELECT a2.sha256, COUNT(*)::int AS duplicate_count
                  FROM attachments a2
                  JOIN email_attachments ea2 ON ea2.attachment_id = a2.id
                 GROUP BY a2.sha256
            )
            SELECT a.id AS att_id, a.filename, a.content_type, a.size,
                   a.sha256, a.storage_path,
                   e.id AS email_id, e.subject, e.sender_name, e.sender_address,
                   e.date,
                   COALESCE(ac.status, 'pending') AS ext_status,
                   ac.reason AS ext_reason,
                   COALESCE(dc.duplicate_count, 1) AS duplicate_count
              FROM attachments a
              JOIN email_attachments ea ON ea.attachment_id = a.id
              JOIN emails e ON e.id = ea.email_id
              LEFT JOIN attachment_contents ac ON ac.attachment_id = a.id
              LEFT JOIN dup_counts dc ON dc.sha256 = a.sha256
             WHERE {where_sql}
             ORDER BY e.date ASC NULLS LAST, a.id ASC
             LIMIT %(lim)s
        """

    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        page = rows[: body.limit]
        has_more = len(rows) > body.limit

        occurrences_map: dict[str, list[AttachmentOccurrence]] = {}
        if body.group_duplicates and page:
            shas = list({r[4] for r in page})
            occurrences_map = _fetch_occurrences(conn, shas)

    items: list[AttachmentListItem] = []
    for r in page:
        (
            att_id,
            filename,
            content_type,
            size,
            sha256,
            _storage_path,
            email_id,
            subject,
            sender_name,
            sender_address,
            date,
            ext_status,
            ext_reason,
            duplicate_count,
        ) = r
        email_uuid: UUID = email_id
        item = AttachmentListItem(
            id=encode_source_id("att", int(att_id)),
            filename=filename,
            content_type=content_type,
            size=size,
            date=_iso(date),
            sender_name=sender_name,
            sender_address=sender_address,
            source_message_id=encode_source_id("msg", email_uuid),
            source_subject=subject,
            extraction=ExtractionInfo(
                status=ext_status or "pending",
                reason=ext_reason,
            ),
            sha256=sha256,
            duplicate_count=int(duplicate_count),
            occurrences=(occurrences_map.get(sha256) if body.group_duplicates else None),
        )
        items.append(item)

    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        payload = {"d": _iso(last[10]), "id": int(last[0])}
        next_cursor = encode_cursor(payload, secret_key)

    return AttachmentListResponse(
        items=items,
        next_cursor=next_cursor,
        scope_fingerprint=scope_fingerprint(body.scope),
    )


def _load_attachment_row(pool: ConnectionPool, att_key: int) -> tuple[str, str | None, str] | None:
    """Return (storage_path, content_type, filename) or None."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT storage_path, content_type, filename
              FROM attachments
             WHERE id = %(id)s
            """,
            {"id": att_key},
        ).fetchone()
    if row is None:
        return None
    return str(row[0]), row[1], str(row[2])


def _preview_media_type(declared: str | None, head: bytes) -> str | None:
    """Return media type if previewable, else None."""
    if not declared:
        return None
    ct = declared.lower().split(";")[0].strip()
    # SVG never previewable
    if ct == "image/svg+xml" or ct.endswith("+xml") and "svg" in ct:
        return None
    if ct in _PREVIEW_IMAGE_TYPES:
        if not _match_magic(ct, head):
            return None
        return ct
    if ct == _PREVIEW_PDF:
        if not _match_magic(ct, head):
            return None
        return ct
    if ct == _PREVIEW_TEXT or ct.startswith("text/plain"):
        return "text/plain; charset=utf-8"
    return None


# --- routes ---


@router.post("/attachments/list")
def post_attachments_list(
    body: AttachmentListRequest,
    request: Request,
    _user: str = Depends(require_user),
) -> AttachmentListResponse:
    pool: ConnectionPool = request.app.state.pool
    secret_key: str = request.app.state.settings.secret_key
    return list_attachments(pool, body, secret_key)


@router.get("/attachments/{att_sid}/preview", response_model=None)
def get_attachment_preview(
    att_sid: str,
    request: Request,
    _user: str = Depends(require_user),
) -> FileResponse | JSONResponse | Response:
    try:
        kind, key = decode_source_id(att_sid)
    except ValueError:
        raise _404() from None
    if kind != "att" or not isinstance(key, int):
        raise _404()

    pool: ConnectionPool = request.app.state.pool
    row = _load_attachment_row(pool, key)
    if row is None:
        raise _404()
    storage_path, content_type, filename = row

    root = Path(request.app.state.settings.attachment_root)
    resolved = _resolve_contained(root, storage_path)
    if resolved is None:
        raise _404()

    try:
        head = resolved.read_bytes()[:16]
    except OSError:
        raise _404() from None

    media = _preview_media_type(content_type, head)
    if media is None:
        reason = "type not previewable"
        if content_type and "svg" in content_type.lower():
            reason = "svg is not previewable"
        elif content_type and content_type.lower().split(";")[0].strip() in (
            *_PREVIEW_IMAGE_TYPES,
            _PREVIEW_PDF,
        ):
            reason = "magic number does not match declared content type"
        return JSONResponse(
            status_code=415,
            content=PreviewDenied(preview=False, reason=reason).model_dump(),
            headers={
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )

    headers = {
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": _content_disposition("inline", filename),
    }

    # Plain text: re-encode with errors=replace so clients always get valid UTF-8.
    if media.startswith("text/plain"):
        try:
            raw = resolved.read_bytes()
        except OSError:
            raise _404() from None
        text = raw.decode("utf-8", errors="replace")
        return Response(
            content=text.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers=headers,
        )

    return FileResponse(
        path=resolved,
        media_type=media,
        headers=headers,
    )


@router.get("/attachments/{att_sid}/download", response_model=None)
def get_attachment_download(
    att_sid: str,
    request: Request,
    user: str = Depends(require_user),
) -> FileResponse:
    try:
        kind, key = decode_source_id(att_sid)
    except ValueError:
        raise _404() from None
    if kind != "att" or not isinstance(key, int):
        raise _404()

    pool: ConnectionPool = request.app.state.pool
    row = _load_attachment_row(pool, key)
    if row is None:
        raise _404()
    storage_path, content_type, filename = row

    root = Path(request.app.state.settings.attachment_root)
    resolved = _resolve_contained(root, storage_path)
    if resolved is None:
        raise _404()

    audit(
        pool,
        username=user,
        action="download",
        detail={"attachment_id": att_sid},
    )

    media = content_type or "application/octet-stream"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": _content_disposition("attachment", filename),
    }
    return FileResponse(
        path=resolved,
        media_type=media,
        headers=headers,
        filename=filename,
        content_disposition_type="attachment",
    )
