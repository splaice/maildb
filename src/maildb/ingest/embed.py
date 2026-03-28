from __future__ import annotations

import time

import structlog
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from maildb.embeddings import EmbeddingClient, build_embedding_text

logger = structlog.get_logger()

SELECT_BATCH_SQL = """
SELECT id, subject, sender_name, body_text
FROM emails
WHERE embedding IS NULL
LIMIT %(batch_size)s
FOR UPDATE SKIP LOCKED
"""


def embed_worker(
    *,
    database_url: str,
    ollama_url: str,
    embedding_model: str,
    embedding_dimensions: int,
    batch_size: int = 50,
    _embedding_client: EmbeddingClient | None = None,
) -> int:
    """Process embedding batches until no work remains. Returns total rows updated."""
    pool = ConnectionPool(conninfo=database_url, min_size=1, max_size=1, open=True)
    client = _embedding_client or EmbeddingClient(
        ollama_url=ollama_url,
        model_name=embedding_model,
        dimensions=embedding_dimensions,
    )

    total_updated = 0
    consecutive_failures = 0
    max_failures = 3

    try:
        while True:
            with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SELECT_BATCH_SQL, {"batch_size": batch_size})
                rows = cur.fetchall()

                if not rows:
                    break

                texts = [
                    build_embedding_text(r["subject"], r["sender_name"], r["body_text"])
                    for r in rows
                ]

                try:
                    embeddings = client.embed_batch(texts)
                    consecutive_failures = 0
                except Exception:
                    consecutive_failures += 1
                    logger.warning(
                        "embed_batch_failed",
                        attempt=consecutive_failures,
                        max=max_failures,
                    )
                    conn.rollback()
                    if consecutive_failures >= max_failures:
                        logger.exception("embed_worker_giving_up")
                        break
                    time.sleep(2**consecutive_failures)
                    continue

                for row, emb in zip(rows, embeddings, strict=True):
                    conn.execute(
                        "UPDATE emails SET embedding = %s WHERE id = %s",
                        (emb, row["id"]),
                    )
                conn.commit()
                total_updated += len(rows)
                logger.info("embed_batch_done", batch_size=len(rows), total=total_updated)

    finally:
        pool.close()

    return total_updated
