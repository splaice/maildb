# src/chronicle_server/archive.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, Request

from chronicle_server.auth import require_user

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

router = APIRouter(tags=["archive"])

API_VERSION = "0.1.0"


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


@router.get("/summary")
def archive_summary(
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Authenticated archive coverage, counts, and version metadata."""
    pool: ConnectionPool = request.app.state.pool
    return get_archive_summary(pool)
