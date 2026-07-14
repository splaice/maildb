# src/chronicle_server/archive.py
"""Archive summary endpoint and shared ETag / data-version helpers (§16.1).

Response fingerprints for hot aggregate endpoints:

    ETag = sha256(scope_fingerprint + viewport/params + data_version)

Data-version marker (cheap, one statement, cached on ``request.state``) lives in
:mod:`chronicle_server.cache` and is re-exported here for existing importers
(topics list, tests). Full marker includes ``max(updated_at)`` of
``app_topics`` / ``app_events`` so curation edits bust derived-content caches.

POST endpoints honor ``If-None-Match`` the same way as GET (non-standard for
POST but fine for this private API): matching ETag → 304 empty body.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, Request, Response

from chronicle_server.auth import require_user
from chronicle_server.cache import (
    cache_key,
    cached,
    data_version,
    emails_data_version,
    topics_data_version,
)

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

router = APIRouter(tags=["archive"])

API_VERSION = "0.1.0"

# Re-export data-version helpers (single implementation in cache.py).
__all__ = [
    "API_VERSION",
    "archive_summary",
    "archive_summary_etag",
    "data_version",
    "emails_data_version",
    "get_archive_summary",
    "if_none_match",
    "not_modified",
    "response_etag",
    "router",
    "topics_data_version",
]


# --- ETag helpers (shared by chronicle + topics endpoints) ---


def response_etag(*parts: str) -> str:
    """Strong ETag: quoted sha256 of joined fingerprint parts."""
    material = "\n".join(parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f'"{digest}"'


def if_none_match(request: Request, etag: str) -> bool:
    """True when the request's If-None-Match matches *etag* (or is ``*``)."""
    header = request.headers.get("if-none-match")
    if header is None or not header.strip():
        return False
    stripped = header.strip()
    if stripped == "*":
        return True
    target = etag.strip()
    for part in stripped.split(","):
        token = part.strip()
        if token.startswith("W/"):
            token = token[2:].strip()
        if token == target:
            return True
    return False


def not_modified(etag: str) -> Response:
    """304 empty body with the given ETag."""
    return Response(status_code=304, headers={"ETag": etag})


def get_archive_summary(pool: ConnectionPool) -> dict[str, Any]:
    """Read-only archive coverage summary over existing maildb tables."""
    with pool.connection() as conn:
        accounts_rows = conn.execute(
            """
            SELECT
                ea.source_account AS account,
                COUNT(DISTINCT ea.email_id)::int AS messages
            FROM email_accounts ea
            GROUP BY ea.source_account
            ORDER BY messages DESC
            """
        ).fetchall()

        date_row = conn.execute(
            """
            SELECT
                MIN(date) AS date_from,
                MAX(date) AS date_to
            FROM emails
            """
        ).fetchone()

        counts_row = conn.execute(
            """
            SELECT
                (SELECT count(*)::int FROM emails) AS messages,
                (SELECT count(DISTINCT thread_id)::int FROM emails) AS threads,
                (SELECT count(*)::int FROM attachments) AS attachments,
                (SELECT count(*)::int FROM contacts) AS contacts
            """
        ).fetchone()

        extraction_row = conn.execute(
            """
            SELECT
                count(*) FILTER (WHERE status = 'extracted')::int AS extracted,
                count(*) FILTER (WHERE status = 'failed')::int AS failed,
                count(*) FILTER (WHERE status = 'skipped')::int AS skipped,
                count(*) FILTER (WHERE status = 'pending')::int AS pending
            FROM attachment_contents
            """
        ).fetchone()

        embedding_row = conn.execute(
            """
            SELECT
                count(*) FILTER (WHERE embedding IS NOT NULL)::int AS embedded,
                count(*) FILTER (WHERE embedding IS NULL)::int AS missing
            FROM emails
            """
        ).fetchone()

    accounts = [{"account": r[0], "messages": r[1]} for r in accounts_rows]

    date_from = date_row[0] if date_row is not None else None
    date_to = date_row[1] if date_row is not None else None

    def _iso(value: Any) -> str | None:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()  # type: ignore[no-any-return]
        return str(value)

    assert counts_row is not None
    assert extraction_row is not None
    assert embedding_row is not None

    return {
        "accounts": accounts,
        "date_range": {"from": _iso(date_from), "to": _iso(date_to)},
        "counts": {
            "messages": counts_row[0],
            "threads": counts_row[1],
            "attachments": counts_row[2],
            "contacts": counts_row[3],
        },
        "extraction": {
            "extracted": extraction_row[0],
            "failed": extraction_row[1],
            "skipped": extraction_row[2],
            "pending": extraction_row[3],
        },
        "embedding": {
            "embedded": embedding_row[0],
            "missing": embedding_row[1],
        },
        "versions": {"schema": "maildb", "api": API_VERSION},
    }


def archive_summary_etag(pool: ConnectionPool, request: Request) -> str:
    """ETag for GET /api/archive/summary (no scope; params = endpoint id)."""
    data_ver = data_version(pool, request)
    return response_etag("archive/summary", "", data_ver)


@router.get("/summary", response_model=None)
def archive_summary(
    request: Request,
    response: Response,
    _user: str = Depends(require_user),
) -> dict[str, Any] | Response:
    """Authenticated archive coverage, counts, and version metadata.

    Supports ``If-None-Match`` / ``ETag`` conditional requests (§16.1).
    Whole-response cache keyed by data version (warm hits skip aggregate SQL).
    """
    pool: ConnectionPool = request.app.state.pool
    etag = archive_summary_etag(pool, request)
    if if_none_match(request, etag):
        return not_modified(etag)
    ver = data_version(pool, request)
    body = cached(
        pool,
        key=cache_key("summary", {}),
        data_version=ver,
        compute=lambda: get_archive_summary(pool),
    )
    response.headers["ETag"] = etag
    return body
