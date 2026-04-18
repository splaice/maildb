"""Attachment extraction worker.

Claims pending rows from attachment_contents via SKIP LOCKED, runs
extract_markdown, chunks, embeds each chunk, writes markdown to disk,
and transitions status. Idempotent per-attachment; safe to crash
mid-run (watchdog reclaims 'extracting' rows older than the threshold).
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from maildb.ingest.chunking import chunk_markdown
from maildb.ingest.extraction import ExtractionFailedError, extract_markdown

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

WATCHDOG_STALE_SECONDS = 3600  # 1 hour


def ensure_pending_rows(pool: ConnectionPool) -> int:
    """Insert a 'pending' row into attachment_contents for every attachment that
    doesn't already have one. Returns count of newly inserted rows.
    """
    with pool.connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO attachment_contents (attachment_id, status)
            SELECT a.id, 'pending'
              FROM attachments a
              LEFT JOIN attachment_contents c ON c.attachment_id = a.id
             WHERE c.attachment_id IS NULL
            """
        )
        conn.commit()
        return cur.rowcount


def _reclaim_stale(pool: ConnectionPool) -> int:
    """Reset 'extracting' rows that haven't been updated in a while to 'pending'."""
    with pool.connection() as conn:
        cur = conn.execute(
            """
            UPDATE attachment_contents
               SET status = 'pending', extracted_at = NULL
             WHERE status = 'extracting'
               AND (extracted_at IS NULL OR extracted_at < now() - (%s || ' seconds')::interval)
            """,
            (WATCHDOG_STALE_SECONDS,),
        )
        conn.commit()
        return cur.rowcount


def _load_attachment(pool: ConnectionPool, attachment_id: int) -> dict[str, Any]:
    with pool.connection() as conn:
        cur = conn.execute(
            "SELECT id, sha256, filename, content_type, storage_path "
            "FROM attachments WHERE id = %s",
            (attachment_id,),
        )
        row = cur.fetchone()
    if row is None:
        msg = f"attachment {attachment_id} not found"
        raise LookupError(msg)
    return {
        "id": row[0],
        "sha256": row[1],
        "filename": row[2],
        "content_type": row[3],
        "storage_path": row[4],
    }


def _write_markdown_mirror(attachment_dir: Path, storage_path: str, markdown: str) -> None:
    mirror = attachment_dir / f"{storage_path}.md"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(markdown, encoding="utf-8")


def _embed_chunks(pool: ConnectionPool, chunks: list[dict[str, Any]]) -> None:
    """Stub embed entry point; real implementation lives in a later task.
    Kept as a function here so tests can monkeypatch it.
    """
    raise NotImplementedError  # replaced in Task 4.2


def _set_status(
    pool: ConnectionPool,
    attachment_id: int,
    *,
    status: str,
    reason: str | None = None,
    markdown: str | None = None,
    markdown_bytes: int | None = None,
    extraction_ms: int | None = None,
    extractor_version: str | None = None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE attachment_contents
               SET status = %(status)s,
                   reason = %(reason)s,
                   markdown = %(markdown)s,
                   markdown_bytes = %(markdown_bytes)s,
                   extracted_at = CASE
                        WHEN %(status)s IN ('extracted','failed','skipped') THEN now()
                        ELSE extracted_at
                   END,
                   extraction_ms = COALESCE(%(extraction_ms)s, extraction_ms),
                   extractor_version = COALESCE(%(extractor_version)s, extractor_version)
             WHERE attachment_id = %(attachment_id)s
            """,
            {
                "attachment_id": attachment_id,
                "status": status,
                "reason": reason,
                "markdown": markdown,
                "markdown_bytes": markdown_bytes,
                "extraction_ms": extraction_ms,
                "extractor_version": extractor_version,
            },
        )
        conn.commit()


def _claim_row(
    pool: ConnectionPool,
    *,
    retry_failed: bool,
    selector_sql: str = "",
    selector_params: dict[str, Any] | None = None,
) -> int | None:
    """Atomically move one row to 'extracting' and return its attachment_id."""
    states = "('pending','failed')" if retry_failed else "('pending')"
    sql = f"""
        WITH claimed AS (
            SELECT attachment_id FROM attachment_contents
             WHERE status IN {states}
               {selector_sql}
             ORDER BY attachment_id
             LIMIT 1
             FOR UPDATE SKIP LOCKED
        )
        UPDATE attachment_contents
           SET status = 'extracting', extracted_at = now(), reason = NULL
         WHERE attachment_id IN (SELECT attachment_id FROM claimed)
        RETURNING attachment_id
    """
    with pool.connection() as conn:
        cur = conn.execute(sql, selector_params or {})
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None


def process_one(pool: ConnectionPool, attachment_id: int, *, attachment_dir: Path) -> None:
    """Extract → chunk → embed → status for a single attachment row."""
    att = _load_attachment(pool, attachment_id)
    file_path = attachment_dir / Path(att["storage_path"])
    t0 = time.monotonic()
    try:
        result = extract_markdown(file_path, content_type=att["content_type"])
    except ExtractionFailedError as exc:
        # Unsupported types are skipped; Marker errors are failures.
        if "not supported" in str(exc).lower() or "requires LibreOffice" in str(exc):
            _set_status(pool, attachment_id, status="skipped", reason=str(exc))
        else:
            _set_status(pool, attachment_id, status="failed", reason=str(exc))
        return
    except Exception as exc:
        _set_status(pool, attachment_id, status="failed", reason=f"{type(exc).__name__}: {exc}")
        return

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Drop any prior chunks (re-run safety)
    with pool.connection() as conn:
        conn.execute("DELETE FROM attachment_chunks WHERE attachment_id = %s", (attachment_id,))
        conn.commit()

    chunks = chunk_markdown(result.markdown)
    chunk_rows: list[dict[str, Any]] = []
    with pool.connection() as conn:
        for c in chunks:
            conn.execute(
                """INSERT INTO attachment_chunks
                       (attachment_id, chunk_index, heading_path, page_number, token_count, text)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    attachment_id,
                    c.chunk_index,
                    c.heading_path,
                    c.page_number,
                    c.token_count,
                    c.text,
                ),
            )
            chunk_rows.append({"attachment_id": attachment_id, **c.__dict__})
        conn.commit()

    # Embed (stubbed in unit tests; real implementation attached in Task 4.2)
    with contextlib.suppress(NotImplementedError):
        _embed_chunks(pool, chunk_rows)

    # Write the on-disk markdown mirror.
    _write_markdown_mirror(attachment_dir, att["storage_path"], result.markdown)

    _set_status(
        pool,
        attachment_id,
        status="extracted",
        markdown=result.markdown,
        markdown_bytes=len(result.markdown.encode("utf-8")),
        extraction_ms=elapsed_ms,
        extractor_version=result.extractor_version,
    )


def run(
    pool: ConnectionPool,
    *,
    attachment_dir: Path,
    workers: int = 1,
    retry_failed: bool = True,
    selector_sql: str = "",
    selector_params: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Process all matching pending/failed rows using N workers.

    For workers > 1 this uses threads, which is appropriate for the
    mixed I/O + short GPU bursts Marker produces. Connection pool is
    configured for concurrent access already.
    """
    ensure_pending_rows(pool)
    _reclaim_stale(pool)

    counts = {"extracted": 0, "failed": 0, "skipped": 0}

    def _worker() -> None:
        while True:
            attachment_id = _claim_row(
                pool,
                retry_failed=retry_failed,
                selector_sql=selector_sql,
                selector_params=selector_params,
            )
            if attachment_id is None:
                return
            try:
                process_one(pool, attachment_id, attachment_dir=attachment_dir)
            except Exception as exc:
                _set_status(pool, attachment_id, status="failed", reason=str(exc))

    if workers <= 1:
        _worker()
    else:
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_worker) for _ in range(workers)]
            for f in futures:
                f.result()

    with pool.connection() as conn:
        cur = conn.execute("SELECT status, count(*) FROM attachment_contents GROUP BY status")
        for status, n in cur.fetchall():
            if status in counts:
                counts[status] = n
    return counts
