# src/maildb/db.py
from __future__ import annotations

import importlib.resources

import structlog
from psycopg_pool import ConnectionPool

from maildb.config import Settings

logger = structlog.get_logger()


def create_pool(config: Settings) -> ConnectionPool:
    """Create a psycopg3 connection pool."""
    pool = ConnectionPool(conninfo=config.database_url, min_size=1, max_size=5)
    return pool


def init_db(pool: ConnectionPool) -> None:
    """Apply idempotent DDL from schema.sql."""
    schema_sql = importlib.resources.files("maildb").joinpath("schema.sql").read_text()
    with pool.connection() as conn:
        conn.execute(schema_sql)
        conn.commit()
    logger.info("database_initialized")
