from __future__ import annotations

import time
from typing import Any

import structlog
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from maildb.embeddings import EmbeddingClient, build_embedding_text
from maildb.ingest.progress import ProgressTracker

logger = structlog.get_logger()

SELECT_BATCH_SQL = """
SELECT id, subject, sender_name, body_text
FROM emails
WHERE embedding IS NULL
LIMIT %(batch_size)s
FOR UPDATE SKIP LOCKED
"""

# Zero vector used to mark emails that cannot be embedded (e.g. too long).
# This prevents them from being retried on every batch.
SKIP_SENTINEL = "[0]"


def _fetch_batch(pool: ConnectionPool, batch_size: int) -> list[dict[str, Any]]:
    """Fetch a batch of un-embedded rows. Returns empty list when no work remains."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SELECT_BATCH_SQL, {"batch_size": batch_size})
        rows = cur.fetchall()
        # Rollback to release FOR UPDATE locks — we'll update by id later
        conn.rollback()
    return [dict(r) for r in rows]


def _embed_and_update_batch(
    pool: ConnectionPool,
    client: EmbeddingClient,
    rows: list[dict[str, Any]],
    texts: list[str],
) -> int:
    """Embed a full batch and update all rows. Returns count updated."""
    embeddings = client.embed_batch(texts)
    with pool.connection() as conn:
        for row, emb in zip(rows, embeddings, strict=True):
            conn.execute(
                "UPDATE emails SET embedding = %s WHERE id = %s",
                (emb, row["id"]),
            )
        conn.commit()
    return len(rows)


def _embed_and_update_single(
    pool: ConnectionPool,
    client: EmbeddingClient,
    rows: list[dict[str, Any]],
    texts: list[str],
    dimensions: int,
) -> int:
    """Embed rows one at a time. Mark failures with a zero vector so they aren't retried."""
    updated = 0
    zero_vector = [0.0] * dimensions
    for row, text in zip(rows, texts, strict=True):
        try:
            emb = client.embed(text)
        except Exception:
            logger.warning("embed_single_skipped", email_id=str(row["id"]))
            # Mark with zero vector so this row is not picked up again
            with pool.connection() as conn:
                conn.execute(
                    "UPDATE emails SET embedding = %s WHERE id = %s",
                    (zero_vector, row["id"]),
                )
                conn.commit()
            continue
        with pool.connection() as conn:
            conn.execute(
                "UPDATE emails SET embedding = %s WHERE id = %s",
                (emb, row["id"]),
            )
            conn.commit()
        updated += 1
    return updated


def embed_worker(
    *,
    database_url: str,
    ollama_url: str,
    embedding_model: str,
    embedding_dimensions: int,
    batch_size: int = 50,
    _embedding_client: EmbeddingClient | None = None,
    _progress_total: int = 0,
) -> int:
    """Process embedding batches until no work remains. Returns total rows updated."""
    pool = ConnectionPool(conninfo=database_url, min_size=1, max_size=1, open=True)
    client = _embedding_client or EmbeddingClient(
        ollama_url=ollama_url,
        model_name=embedding_model,
        dimensions=embedding_dimensions,
    )

    total_updated = 0
    tracker = ProgressTracker(total=_progress_total) if _progress_total > 0 else None
    start_time = time.monotonic()
    last_report = start_time

    try:
        while True:
            rows = _fetch_batch(pool, batch_size)
            if not rows:
                break

            texts = [
                build_embedding_text(r["subject"], r["sender_name"], r["body_text"]) for r in rows
            ]

            try:
                batch_updated = _embed_and_update_batch(pool, client, rows, texts)
                total_updated += batch_updated
                logger.info("embed_batch_done", batch_size=batch_updated, total=total_updated)
            except Exception:
                # Batch failed — fall back to one-at-a-time
                logger.warning("embed_batch_failed_falling_back", batch_size=len(rows))
                batch_updated = _embed_and_update_single(
                    pool, client, rows, texts, embedding_dimensions
                )
                total_updated += batch_updated

            now = time.monotonic()
            if tracker is not None and now - last_report >= 60:
                tracker.update(total_updated, now - start_time)
                logger.info("embed_progress", summary=tracker.summary_line())
                last_report = now

    finally:
        pool.close()

    return total_updated
