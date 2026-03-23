from __future__ import annotations

import importlib.resources
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

DROP_INDEXES = [
    "idx_email_sender_address",
    "idx_email_sender_domain",
    "idx_email_date",
    "idx_email_thread_id",
    "idx_email_in_reply_to",
    "idx_email_has_attachment",
    "idx_email_labels",
    "idx_email_recipients",
    "idx_email_embedding",
    "idx_email_thread_sender_date",
    "idx_email_attachments_email_id",
    "idx_email_attachments_attachment_id",
]


def drop_non_unique_indexes(pool: ConnectionPool) -> None:
    """Drop all non-unique indexes to prepare for bulk rebuild."""
    with pool.connection() as conn:
        for idx_name in DROP_INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {idx_name}")
        conn.commit()
    logger.info("indexes_dropped", count=len(DROP_INDEXES))


def run_index_phase(pool: ConnectionPool, *, include_hnsw: bool = False) -> None:
    """Create all non-unique indexes from schema_indexes.sql."""
    index_sql = importlib.resources.files("maildb").joinpath("schema_indexes.sql").read_text()
    with pool.connection() as conn:
        conn.execute(index_sql)
        if include_hnsw:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_email_embedding "
                "ON emails USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            )
        conn.execute("ANALYZE emails")
        conn.execute("ANALYZE attachments")
        conn.execute("ANALYZE email_attachments")
        conn.commit()
    logger.info("indexes_created", include_hnsw=include_hnsw)


def create_hnsw_index(pool: ConnectionPool) -> None:
    """Create the HNSW index on embeddings. Called after embed phase."""
    with pool.connection() as conn:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_email_embedding "
            "ON emails USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )
        conn.execute("ANALYZE emails")
        conn.commit()
    logger.info("hnsw_index_created")
