# src/maildb/db.py
from __future__ import annotations

import importlib.resources
from typing import TYPE_CHECKING

import structlog
from psycopg_pool import ConnectionPool

if TYPE_CHECKING:
    from maildb.config import Settings

logger = structlog.get_logger()


def create_pool(config: Settings) -> ConnectionPool:
    """Create a psycopg3 connection pool."""
    return ConnectionPool(conninfo=config.database_url, min_size=1, max_size=5, open=True)


def init_db(pool: ConnectionPool) -> None:
    """Apply idempotent table DDL from schema_tables.sql.

    - Backfills email_accounts from legacy emails.source_account/import_id rows.
    - Self-tightens emails.source_account to NOT NULL once every row is tagged.
    """
    schema_sql = importlib.resources.files("maildb").joinpath("schema_tables.sql").read_text()
    with pool.connection() as conn:
        conn.execute(schema_sql)

        # Backfill attachments.reference_count from email_attachments.
        # Safe to run every init_db: only rewrites rows where count differs.
        conn.execute(
            """
            UPDATE attachments a
               SET reference_count = sub.n
              FROM (
                  SELECT attachment_id, count(*) AS n
                    FROM email_attachments
                   GROUP BY attachment_id
              ) sub
             WHERE a.id = sub.attachment_id
               AND a.reference_count != sub.n
            """
        )

        # Mirror any legacy (emails.source_account, emails.import_id) pairs
        # into email_accounts. Safe to re-run — ON CONFLICT DO NOTHING.
        conn.execute(
            """INSERT INTO email_accounts (email_id, source_account, import_id)
               SELECT id, source_account, import_id FROM emails
               WHERE source_account IS NOT NULL AND import_id IS NOT NULL
               ON CONFLICT DO NOTHING"""
        )

        cur = conn.execute("SELECT count(*) FROM emails WHERE source_account IS NULL")
        null_rows = cur.fetchone()[0]  # type: ignore[index]
        if null_rows == 0:
            try:
                conn.execute("ALTER TABLE emails ALTER COLUMN source_account SET NOT NULL")
            except Exception:
                logger.warning("source_account_not_null_constraint_skipped", exc_info=True)
        else:
            logger.info(
                "source_account_not_null_skipped",
                null_rows=null_rows,
                hint="run `maildb ingest migrate --account <addr>`",
            )
        conn.commit()
    logger.info("database_initialized")


def create_indexes(pool: ConnectionPool) -> None:
    """Apply all non-unique indexes from schema_indexes.sql."""
    index_sql = importlib.resources.files("maildb").joinpath("schema_indexes.sql").read_text()
    with pool.connection() as conn:
        conn.execute(index_sql)
        conn.commit()
    logger.info("indexes_created")
