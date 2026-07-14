"""Attachment browser, sandboxed preview, and download endpoints."""

from __future__ import annotations

import difflib
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
_DIFF_HUNK_CAP = 200

# Trailing version/copy tokens stripped from filename stems (Table 25 / FI-004).
_STEM_VERSION_SUFFIX = re.compile(
    r"(?:[-_ ]?(?:v?\d+|final|draft|copy|\(\d+\)))+$",
    re.IGNORECASE,
)
# Currency / number patterns on changed lines (spec §A.2 amount changes).
_AMOUNT_RE = re.compile(r"[$€£]?\s?\d[\d,.]*")

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
    # Probable version-family size (self included). Cheap flag for UI "N versions".
    family_count: int = 1


class AttachmentListResponse(BaseModel):
    items: list[AttachmentListItem]
    next_cursor: str | None = None
    scope_fingerprint: str


class PreviewDenied(BaseModel):
    preview: bool = False
    reason: str


class FamilyCandidate(BaseModel):
    id: str
    filename: str
    date: str | None = None
    sender: str | None = None
    size: int | None = None
    sha256: str
    confidence: Literal["exact-duplicate", "probable-version"]
    signals: list[str]


class AttachmentFamilyResponse(BaseModel):
    id: str
    stem: str
    candidates: list[FamilyCandidate]


class DiffLine(BaseModel):
    kind: Literal["same", "add", "del"]
    text: str


class DiffHunk(BaseModel):
    a_start: int
    b_start: int
    lines: list[DiffLine]


class AmountChange(BaseModel):
    kind: Literal["add", "del"]
    text: str
    amounts: list[str]


class AttachmentMeta(BaseModel):
    id: str
    filename: str
    content_type: str | None = None
    size: int | None = None
    date: str | None = None
    sender: str | None = None
    sha256: str
    source_message_id: str | None = None


class AttachmentCompareResponse(BaseModel):
    a: AttachmentMeta
    b: AttachmentMeta
    hunks: list[DiffHunk]
    truncated: bool
    amount_changes: list[AmountChange]


# --- pure helpers (version families + compare) ---


def filename_stem(filename: str) -> str:
    """Normalize filename to a version-family stem (Table 25 / FI-004).

    Lowercase, strip extension, strip trailing version/copy tokens:
    ``[-_ ]?(v?\\d+|final|draft|copy|(\\d+))+``.
    """
    name = (filename or "").lower().strip()
    if not name:
        return ""
    # Strip last extension only when it looks like one (short alphanumeric).
    if "." in name:
        base, ext = name.rsplit(".", 1)
        if base and ext and all(c.isalnum() or c in "+-" for c in ext):
            name = base
    # Repeatedly strip trailing version tokens (handles stacked suffixes).
    while True:
        stripped = _STEM_VERSION_SUFFIX.sub("", name)
        if stripped == name:
            break
        name = stripped
    return name


def diff_lines(a: str, b: str, context: int = 2) -> dict[str, Any]:
    """Line-level LCS diff via difflib; return hunks + truncated flag.

    Each hunk: ``{a_start, b_start, lines: [{kind: same|add|del, text}]}``.
    Cap at 200 hunks.
    """
    a_lines = (a or "").splitlines()
    b_lines = (b or "").splitlines()
    matcher = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)
    hunks: list[dict[str, Any]] = []
    truncated = False
    for group in matcher.get_grouped_opcodes(n=max(0, context)):
        if len(hunks) >= _DIFF_HUNK_CAP:
            truncated = True
            break
        a_start = group[0][1]
        b_start = group[0][3]
        lines: list[dict[str, str]] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for line in a_lines[i1:i2]:
                    lines.append({"kind": "same", "text": line})
            elif tag == "delete":
                for line in a_lines[i1:i2]:
                    lines.append({"kind": "del", "text": line})
            elif tag == "insert":
                for line in b_lines[j1:j2]:
                    lines.append({"kind": "add", "text": line})
            elif tag == "replace":
                for line in a_lines[i1:i2]:
                    lines.append({"kind": "del", "text": line})
                for line in b_lines[j1:j2]:
                    lines.append({"kind": "add", "text": line})
        hunks.append({"a_start": a_start, "b_start": b_start, "lines": lines})
    return {"hunks": hunks, "truncated": truncated}


def amount_changes_from_hunks(hunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect add/del lines that contain currency/number patterns."""
    out: list[dict[str, Any]] = []
    for hunk in hunks:
        for line in hunk.get("lines") or []:
            kind = line.get("kind")
            if kind not in ("add", "del"):
                continue
            text = line.get("text") or ""
            amounts = _AMOUNT_RE.findall(text)
            if amounts:
                out.append({"kind": kind, "text": text, "amounts": amounts})
    return out


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

        family_counts = _family_counts_for_page(conn, page)

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
            family_count=family_counts.get(int(att_id), 1),
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


def _family_counts_for_page(
    conn: Any,
    page: list[Any],
) -> dict[int, int]:
    """Cheap per-page family_count (self included) via one candidate query."""
    if not page:
        return {}
    att_ids = [int(r[0]) for r in page]
    # Seed contexts: one row per attachment id (prefer newest email).
    seeds = conn.execute(
        """
        SELECT DISTINCT ON (a.id)
               a.id, a.filename, e.sender_address, e.thread_id
          FROM attachments a
          JOIN email_attachments ea ON ea.attachment_id = a.id
          JOIN emails e ON e.id = ea.email_id
         WHERE a.id = ANY(%(ids)s)
         ORDER BY a.id, e.date DESC NULLS LAST, e.id DESC
        """,
        {"ids": att_ids},
    ).fetchall()
    if not seeds:
        return {aid: 1 for aid in att_ids}

    senders = list({r[2] for r in seeds if r[2]})
    threads = list({r[3] for r in seeds if r[3]})
    if not senders and not threads:
        return {aid: 1 for aid in att_ids}

    conds: list[str] = []
    params: dict[str, Any] = {}
    if senders:
        conds.append("e.sender_address = ANY(%(senders)s)")
        params["senders"] = senders
    if threads:
        conds.append("e.thread_id = ANY(%(threads)s)")
        params["threads"] = threads
    where = " OR ".join(conds)

    cand_rows = conn.execute(
        f"""
        SELECT DISTINCT ON (a.id)
               a.id, a.filename, e.sender_address, e.thread_id
          FROM attachments a
          JOIN email_attachments ea ON ea.attachment_id = a.id
          JOIN emails e ON e.id = ea.email_id
         WHERE {where}
         ORDER BY a.id, e.date DESC NULLS LAST, e.id DESC
        """,
        params,
    ).fetchall()

    # Precompute stem + (sender, thread) for candidates.
    cand_info: list[tuple[int, str, str | None, str | None]] = [
        (int(r[0]), filename_stem(str(r[1] or "")), r[2], r[3]) for r in cand_rows
    ]

    counts: dict[int, int] = {}
    for seed in seeds:
        sid = int(seed[0])
        stem = filename_stem(str(seed[1] or ""))
        s_sender = seed[2]
        s_thread = seed[3]
        n = 0
        for cid, c_stem, c_sender, c_thread in cand_info:
            if c_stem != stem or not stem:
                continue
            same_sender = bool(s_sender and c_sender and s_sender == c_sender)
            same_thread = bool(s_thread and c_thread and s_thread == c_thread)
            if same_sender or same_thread or cid == sid:
                n += 1
        counts[sid] = max(n, 1)
    return counts


def _decode_att_key(att_sid: str) -> int:
    try:
        kind, key = decode_source_id(att_sid)
    except ValueError as exc:
        raise _404() from exc
    if kind != "att" or not isinstance(key, int):
        raise _404()
    return key


def get_attachment_family(pool: ConnectionPool, att_sid: str) -> AttachmentFamilyResponse:
    """Probable version family for an attachment (stem + same sender/thread)."""
    att_key = _decode_att_key(att_sid)

    with pool.connection() as conn:
        seed = conn.execute(
            """
            SELECT DISTINCT ON (a.id)
                   a.id, a.filename, a.size, a.sha256,
                   e.date, e.sender_name, e.sender_address, e.thread_id
              FROM attachments a
              JOIN email_attachments ea ON ea.attachment_id = a.id
              JOIN emails e ON e.id = ea.email_id
             WHERE a.id = %(id)s
             ORDER BY a.id, e.date DESC NULLS LAST, e.id DESC
            """,
            {"id": att_key},
        ).fetchone()
        if seed is None:
            raise _404("attachment not found")

        stem = filename_stem(str(seed[1] or ""))
        seed_sender = seed[6]
        seed_thread = seed[7]
        seed_sha = str(seed[3])

        conds: list[str] = []
        params: dict[str, Any] = {}
        if seed_sender:
            conds.append("e.sender_address = %(sender)s")
            params["sender"] = seed_sender
        if seed_thread:
            conds.append("e.thread_id = %(thread)s")
            params["thread"] = seed_thread
        if not conds:
            # No sender/thread anchor — family is just the seed itself.
            cand_rows = [seed]
        else:
            # One statement: candidates sharing sender OR thread (stem filtered in Python).
            where = " OR ".join(conds)
            cand_rows = conn.execute(
                f"""
                SELECT DISTINCT ON (a.id)
                       a.id, a.filename, a.size, a.sha256,
                       e.date, e.sender_name, e.sender_address, e.thread_id
                  FROM attachments a
                  JOIN email_attachments ea ON ea.attachment_id = a.id
                  JOIN emails e ON e.id = ea.email_id
                 WHERE {where}
                 ORDER BY a.id, e.date DESC NULLS LAST, e.id DESC
                """,
                params,
            ).fetchall()

    candidates: list[FamilyCandidate] = []
    for r in cand_rows:
        rid = int(r[0])
        c_stem = filename_stem(str(r[1] or ""))
        # Empty stem: only the seed itself is a family member.
        if not stem:
            if rid != att_key:
                continue
        elif c_stem != stem:
            continue
        c_sender = r[6]
        c_thread = r[7]
        c_sha = str(r[3])
        signals: list[str] = ["stem"] if stem else []
        same_sender = bool(seed_sender and c_sender and seed_sender == c_sender)
        same_thread = bool(seed_thread and c_thread and seed_thread == c_thread)
        if same_sender:
            signals.append("sender")
        if same_thread:
            signals.append("thread")
        # Self always included; others need sender or thread signal.
        if rid != att_key and not (same_sender or same_thread):
            continue
        if c_sha == seed_sha:
            confidence: Literal["exact-duplicate", "probable-version"] = "exact-duplicate"
            if "sha256" not in signals:
                signals.append("sha256")
        else:
            confidence = "probable-version"
        sender_label = r[5] or r[6]
        candidates.append(
            FamilyCandidate(
                id=encode_source_id("att", rid),
                filename=str(r[1] or ""),
                date=_iso(r[4]),
                sender=sender_label,
                size=r[2],
                sha256=c_sha,
                confidence=confidence,
                signals=signals,
            )
        )

    # Stable order: date asc, then id.
    candidates.sort(key=lambda c: (c.date or "", c.id))

    return AttachmentFamilyResponse(
        id=att_sid,
        stem=stem,
        candidates=candidates,
    )


def get_attachment_compare(
    pool: ConnectionPool,
    a_sid: str,
    b_sid: str,
) -> AttachmentCompareResponse:
    """Metadata side-by-side + extracted-text line diff + amount changes."""
    a_key = _decode_att_key(a_sid)
    b_key = _decode_att_key(b_sid)

    with pool.connection() as conn:

        def load_one(key: int) -> tuple[Any, ...] | None:
            return conn.execute(
                """
                SELECT DISTINCT ON (a.id)
                       a.id, a.filename, a.content_type, a.size, a.sha256,
                       e.date, e.sender_name, e.sender_address, e.id AS email_id,
                       ac.status AS ext_status, ac.markdown, ac.reason AS ext_reason
                  FROM attachments a
                  JOIN email_attachments ea ON ea.attachment_id = a.id
                  JOIN emails e ON e.id = ea.email_id
                  LEFT JOIN attachment_contents ac ON ac.attachment_id = a.id
                 WHERE a.id = %(id)s
                 ORDER BY a.id, e.date DESC NULLS LAST, e.id DESC
                """,
                {"id": key},
            ).fetchone()

        row_a = load_one(a_key)
        row_b = load_one(b_key)

    if row_a is None:
        raise _404(f"attachment not found: {a_sid}")
    if row_b is None:
        raise _404(f"attachment not found: {b_sid}")

    def meta(row: tuple[Any, ...], sid: str) -> AttachmentMeta:
        sender = row[6] or row[7]
        email_id = row[8]
        return AttachmentMeta(
            id=sid,
            filename=str(row[1] or ""),
            content_type=row[2],
            size=row[3],
            date=_iso(row[5]),
            sender=sender,
            sha256=str(row[4]),
            source_message_id=encode_source_id("msg", email_id) if email_id else None,
        )

    md_a = row_a[10]
    md_b = row_b[10]
    status_a = row_a[9]
    status_b = row_b[9]
    if md_a is None or status_a != "extracted":
        reason = row_a[11] or status_a or "no extraction"
        raise HTTPException(
            status_code=404,
            detail=f"missing extraction for {a_sid}: {reason}",
        )
    if md_b is None or status_b != "extracted":
        reason = row_b[11] or status_b or "no extraction"
        raise HTTPException(
            status_code=404,
            detail=f"missing extraction for {b_sid}: {reason}",
        )

    diff = diff_lines(str(md_a), str(md_b), context=2)
    amounts = amount_changes_from_hunks(diff["hunks"])

    return AttachmentCompareResponse(
        a=meta(row_a, a_sid),
        b=meta(row_b, b_sid),
        hunks=[DiffHunk.model_validate(h) for h in diff["hunks"]],
        truncated=bool(diff["truncated"]),
        amount_changes=[AmountChange.model_validate(x) for x in amounts],
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


@router.get("/attachments/compare", response_model=AttachmentCompareResponse)
def get_attachments_compare(
    request: Request,
    a: str = Query(..., description="Attachment source id (att_…)"),
    b: str = Query(..., description="Attachment source id (att_…)"),
    _user: str = Depends(require_user),
) -> AttachmentCompareResponse:
    """Side-by-side metadata + extracted-text diff (version-family compare)."""
    pool: ConnectionPool = request.app.state.pool
    return get_attachment_compare(pool, a, b)


@router.get("/attachments/{att_sid}/family", response_model=AttachmentFamilyResponse)
def get_attachments_family(
    att_sid: str,
    request: Request,
    _user: str = Depends(require_user),
) -> AttachmentFamilyResponse:
    """Probable version family for an attachment (Table 25 / FI-004)."""
    pool: ConnectionPool = request.app.state.pool
    return get_attachment_family(pool, att_sid)


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
