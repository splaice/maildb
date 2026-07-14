# src/chronicle_server/health.py
"""Data Health: archive coverage, threading, extraction, embeddings, imports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, Request
from maildb import MailDB

from chronicle_server.archive import get_archive_summary
from chronicle_server.auth import require_user
from chronicle_server.cache import cache_row_count

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

router = APIRouter(tags=["health"])


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


_AUDIT_TAIL_ACTIONS = ("ask", "events_generate", "workspace_export", "download")
_AUDIT_TAIL_LIMIT = 25


def get_archive_health(pool: ConnectionPool) -> dict[str, Any]:
    """Read-only archive health aggregates over existing maildb tables."""
    summary = get_archive_summary(pool)

    coverage = {
        "accounts": summary["accounts"],
        "date_range": summary["date_range"],
        "messages": summary["counts"]["messages"],
        "threads": summary["counts"]["threads"],
        "attachments": summary["counts"]["attachments"],
        "contacts": summary["counts"]["contacts"],
    }

    with pool.connection() as conn:
        threading_row = conn.execute(
            """
            SELECT
                count(*) FILTER (WHERE cnt = 1)::int AS single_message_threads,
                COALESCE(max(cnt), 0)::int AS max_thread_size,
                (
                    SELECT count(*)::int FROM emails WHERE date IS NULL
                ) AS null_date_messages
            FROM (
                SELECT count(*) AS cnt FROM emails GROUP BY thread_id
            ) t
            """
        ).fetchone()

        failure_rows = conn.execute(
            """
            SELECT
                left(coalesce(reason, ''), 120) AS reason,
                count(*)::int AS count
            FROM attachment_contents
            WHERE status = 'failed'
            GROUP BY left(coalesce(reason, ''), 120)
            ORDER BY count DESC
            LIMIT 10
            """
        ).fetchall()

        content_type_rows = conn.execute(
            """
            SELECT
                coalesce(a.content_type, '') AS content_type,
                count(*) FILTER (WHERE ac.status = 'extracted')::int AS extracted,
                count(*) FILTER (WHERE ac.status = 'failed')::int AS failed,
                count(*) FILTER (WHERE ac.status = 'skipped')::int AS skipped
            FROM attachment_contents ac
            JOIN attachments a ON a.id = ac.attachment_id
            GROUP BY a.content_type
            ORDER BY count(*) DESC
            LIMIT 15
            """
        ).fetchall()

        chunk_emb_row = conn.execute(
            """
            SELECT
                count(*) FILTER (WHERE embedding IS NOT NULL)::int AS embedded,
                count(*) FILTER (WHERE embedding IS NULL)::int AS missing
            FROM attachment_chunks
            """
        ).fetchone()

        audit_rows = conn.execute(
            """
            SELECT at, username, action, detail
              FROM app_audit
             WHERE action = ANY(%(actions)s)
             ORDER BY at DESC
             LIMIT %(limit)s
            """,
            {
                "actions": list(_AUDIT_TAIL_ACTIONS),
                "limit": _AUDIT_TAIL_LIMIT,
            },
        ).fetchall()

        # Topics coverage (tables may be empty pre-generation).
        try:
            topics_count_row = conn.execute("SELECT count(*)::int FROM app_topics").fetchone()
            assigned_row = conn.execute(
                "SELECT count(DISTINCT email_id)::int FROM app_topic_members"
            ).fetchone()
            last_gen_row = conn.execute(
                """
                SELECT at FROM app_audit
                 WHERE action = 'topics_generate'
                 ORDER BY at DESC
                 LIMIT 1
                """
            ).fetchone()
            topics_n = int(topics_count_row[0]) if topics_count_row else 0
            assigned_n = int(assigned_row[0]) if assigned_row else 0
            last_generated = _iso(last_gen_row[0]) if last_gen_row else None
        except Exception:
            # Tables not yet present (pre-migration) — report zeros.
            topics_n = 0
            assigned_n = 0
            last_generated = None

    assert threading_row is not None
    assert chunk_emb_row is not None

    embedded_n = int(summary["embedding"]["embedded"])
    coverage_ratio = (assigned_n / embedded_n) if embedded_n > 0 else 0.0

    db = MailDB._from_pool(pool)
    import_records = db.import_history(limit=20)
    imports = [
        {
            "started_at": _iso(rec.started_at),
            "source_account": rec.source_account,
            "status": rec.status,
            "messages_inserted": rec.messages_inserted,
            "messages_skipped": rec.messages_skipped,
        }
        for rec in import_records
    ]

    audit_tail = [
        {
            "at": _iso(r[0]),
            "username": r[1],
            "action": r[2],
            "detail": r[3] if isinstance(r[3], dict) else {},
        }
        for r in audit_rows
    ]

    return {
        "coverage": coverage,
        "threading": {
            "single_message_threads": threading_row[0],
            "max_thread_size": threading_row[1],
            "null_date_messages": threading_row[2],
        },
        "extraction": {
            "by_status": {
                "extracted": summary["extraction"]["extracted"],
                "failed": summary["extraction"]["failed"],
                "skipped": summary["extraction"]["skipped"],
                "pending": summary["extraction"]["pending"],
            },
            "top_failure_reasons": [{"reason": r[0], "count": r[1]} for r in failure_rows],
            "by_content_type": [
                {
                    "content_type": r[0],
                    "extracted": r[1],
                    "failed": r[2],
                    "skipped": r[3],
                }
                for r in content_type_rows
            ],
        },
        "embeddings": {
            "emails": {
                "embedded": summary["embedding"]["embedded"],
                "missing": summary["embedding"]["missing"],
            },
            "attachment_chunks": {
                "embedded": chunk_emb_row[0],
                "missing": chunk_emb_row[1],
            },
        },
        "topics": {
            "topics": topics_n,
            "coverage": coverage_ratio,
            "last_generated": last_generated,
        },
        "imports": imports,
        "audit_tail": audit_tail,
        "cache": {
            "rows": cache_row_count(pool),
            "hit_info": None,
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/archive")
def archive_health(
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Authenticated archive health: coverage, threading, extraction, embeddings, imports."""
    pool: ConnectionPool = request.app.state.pool
    return get_archive_health(pool)
