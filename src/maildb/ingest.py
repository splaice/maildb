# src/maildb/ingest.py
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import structlog
from psycopg_pool import ConnectionPool

from maildb.embeddings import EmbeddingClient, build_embedding_text
from maildb.parsing import parse_mbox

logger = structlog.get_logger()

INSERT_SQL = """
INSERT INTO emails (
    message_id, thread_id, subject, sender_name, sender_address, sender_domain,
    recipients, date, body_text, body_html, has_attachment, attachments,
    labels, in_reply_to, "references", embedding
) VALUES (
    %(message_id)s, %(thread_id)s, %(subject)s, %(sender_name)s, %(sender_address)s,
    %(sender_domain)s, %(recipients)s, %(date)s, %(body_text)s, %(body_html)s,
    %(has_attachment)s, %(attachments)s, %(labels)s, %(in_reply_to)s,
    %(references)s, %(embedding)s
) ON CONFLICT (message_id) DO NOTHING
"""


def _prepare_row(msg: dict[str, Any], embedding: list[float] | None) -> dict[str, Any]:
    """Prepare a parsed message dict for database insertion."""
    return {
        "message_id": msg["message_id"],
        "thread_id": msg["thread_id"],
        "subject": msg["subject"],
        "sender_name": msg["sender_name"],
        "sender_address": msg["sender_address"],
        "sender_domain": msg["sender_domain"],
        "recipients": json.dumps(msg["recipients"]) if msg["recipients"] else None,
        "date": msg["date"],
        "body_text": msg["body_text"],
        "body_html": msg["body_html"],
        "has_attachment": msg["has_attachment"],
        "attachments": json.dumps(msg["attachments"]) if msg["attachments"] else None,
        "labels": msg["labels"] if msg["labels"] else None,
        "in_reply_to": msg["in_reply_to"],
        "references": msg["references"] if msg["references"] else None,
        "embedding": embedding,
    }


def ingest_mbox(
    pool: ConnectionPool,
    embedding_client: EmbeddingClient,
    mbox_path: Path | str,
    batch_size: int = 100,
) -> dict[str, int]:
    """Ingest messages from an mbox file into the database."""
    message_iter = parse_mbox(mbox_path)
    total = 0
    inserted = 0
    skipped = 0
    failed_embeddings = 0

    while True:
        batch = list(itertools.islice(message_iter, batch_size))
        if not batch:
            break
        total += len(batch)

        embed_texts = [
            build_embedding_text(m["subject"], m["sender_name"], m["body_text"]) for m in batch
        ]

        embeddings: list[list[float] | None]
        try:
            raw_embeddings = embedding_client.embed_batch(embed_texts)
            embeddings = list(raw_embeddings)
        except Exception:
            logger.warning("embedding_batch_failed", batch_size=len(batch))
            embeddings = [None] * len(batch)
            failed_embeddings += len(batch)

        with pool.connection() as conn:
            for msg, emb in zip(batch, embeddings, strict=True):
                row = _prepare_row(msg, emb)
                cur = conn.execute(INSERT_SQL, row)
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            conn.commit()

        if total % 1000 == 0:
            logger.info("ingest_progress", processed=total)

    logger.info(
        "ingest_complete",
        total=total,
        inserted=inserted,
        skipped=skipped,
        failed_embeddings=failed_embeddings,
    )
    return {
        "total": total,
        "inserted": inserted,
        "skipped": skipped,
        "failed_embeddings": failed_embeddings,
    }


def backfill_embeddings(
    pool: ConnectionPool,
    embedding_client: EmbeddingClient,
    batch_size: int = 100,
) -> int:
    """Generate embeddings for rows where embedding IS NULL."""
    updated = 0

    with pool.connection() as conn:
        cur = conn.execute(
            "SELECT id, subject, sender_name, body_text FROM emails WHERE embedding IS NULL"
        )
        rows = cur.fetchall()

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [build_embedding_text(row[1], row[2], row[3]) for row in batch]
        embeddings = embedding_client.embed_batch(texts)

        with pool.connection() as conn:
            for row, emb in zip(batch, embeddings, strict=False):
                conn.execute(
                    "UPDATE emails SET embedding = %s WHERE id = %s",
                    (emb, row[0]),
                )
            conn.commit()
        updated += len(batch)

    logger.info("backfill_complete", updated=updated)
    return updated
