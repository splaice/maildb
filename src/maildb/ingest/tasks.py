from __future__ import annotations

from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool


def create_task(
    pool: ConnectionPool,
    *,
    phase: str,
    chunk_path: str | None = None,
    import_id: Any = None,
) -> dict[str, Any]:
    """Insert a new task row and return it."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """INSERT INTO ingest_tasks (phase, chunk_path, import_id)
               VALUES (%(phase)s, %(chunk_path)s, %(import_id)s)
               RETURNING *""",
            {"phase": phase, "chunk_path": chunk_path, "import_id": import_id},
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row)  # type: ignore[arg-type]


def claim_task(
    pool: ConnectionPool,
    *,
    phase: str,
    worker_id: str,
) -> dict[str, Any] | None:
    """Atomically claim the next pending task for a phase. Returns None if no work."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """UPDATE ingest_tasks
               SET status = 'in_progress', worker_id = %(worker_id)s, started_at = now()
               WHERE id = (
                   SELECT id FROM ingest_tasks
                   WHERE phase = %(phase)s AND status = 'pending'
                   LIMIT 1
                   FOR UPDATE SKIP LOCKED
               )
               RETURNING *""",
            {"phase": phase, "worker_id": worker_id},
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None


def complete_task(
    pool: ConnectionPool,
    task_id: int,
    *,
    messages_total: int = 0,
    messages_inserted: int = 0,
    messages_skipped: int = 0,
    attachments_extracted: int = 0,
) -> None:
    """Mark a task as completed with stats."""
    with pool.connection() as conn:
        conn.execute(
            """UPDATE ingest_tasks
               SET status = 'completed', completed_at = now(),
                   messages_total = %(messages_total)s,
                   messages_inserted = %(messages_inserted)s,
                   messages_skipped = %(messages_skipped)s,
                   attachments_extracted = %(attachments_extracted)s
               WHERE id = %(task_id)s""",
            {
                "task_id": task_id,
                "messages_total": messages_total,
                "messages_inserted": messages_inserted,
                "messages_skipped": messages_skipped,
                "attachments_extracted": attachments_extracted,
            },
        )
        conn.commit()


def fail_task(pool: ConnectionPool, task_id: int, *, error: str) -> None:
    """Mark a task as failed, increment retry count."""
    with pool.connection() as conn:
        conn.execute(
            """UPDATE ingest_tasks
               SET status = 'failed', error_message = %(error)s,
                   retry_count = retry_count + 1
               WHERE id = %(task_id)s""",
            {"task_id": task_id, "error": error},
        )
        conn.commit()


def reset_failed_tasks(
    pool: ConnectionPool,
    *,
    phase: str,
    max_retries: int = 3,
    import_id: Any = None,
) -> int:
    """Reset failed tasks with retries remaining back to pending. Returns count.

    If `import_id` is given, only that import's tasks are touched — required
    when multiple accounts share the queue so a prior import's failures don't
    get retried under the wrong account.
    """
    with pool.connection() as conn, conn.cursor() as cur:
        sql = (
            "UPDATE ingest_tasks "
            "SET status = 'pending', worker_id = NULL, error_message = NULL "
            "WHERE phase = %(phase)s AND status = 'failed' "
            "AND retry_count < %(max_retries)s"
        )
        params: dict[str, Any] = {"phase": phase, "max_retries": max_retries}
        if import_id is not None:
            sql += " AND import_id = %(import_id)s"
            params["import_id"] = import_id
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount


def get_phase_status(
    pool: ConnectionPool,
    phase: str,
    *,
    import_id: Any = None,
) -> dict[str, int]:
    """Get counts by status for a phase.

    If `import_id` is given, counts are scoped to that import only. Required
    when the orchestrator is deciding whether the *current* import has work
    left in a phase; otherwise prior imports' completed rows mask the answer.
    """
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        sql = (
            "SELECT "
            "count(*) FILTER (WHERE status = 'pending') AS pending, "
            "count(*) FILTER (WHERE status = 'in_progress') AS in_progress, "
            "count(*) FILTER (WHERE status = 'completed') AS completed, "
            "count(*) FILTER (WHERE status = 'failed') AS failed, "
            "count(*) AS total, "
            "coalesce(sum(messages_total), 0) AS messages_total, "
            "coalesce(sum(messages_inserted), 0) AS messages_inserted, "
            "coalesce(sum(messages_skipped), 0) AS messages_skipped, "
            "coalesce(sum(attachments_extracted), 0) AS attachments_extracted "
            "FROM ingest_tasks WHERE phase = %(phase)s"
        )
        params: dict[str, Any] = {"phase": phase}
        if import_id is not None:
            sql += " AND import_id = %(import_id)s"
            params["import_id"] = import_id
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row)  # type: ignore[arg-type]
