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
    return ConnectionPool(conninfo=config.database_url, min_size=1, max_size=5)


def init_db(pool: ConnectionPool) -> None:
    """Apply idempotent table DDL from schema_tables.sql."""
    schema_sql = importlib.resources.files("maildb").joinpath("schema_tables.sql").read_text()
    with pool.connection() as conn:
        conn.execute(schema_sql)
        conn.commit()
    logger.info("database_initialized")


def create_indexes(pool: ConnectionPool) -> None:
    """Apply all non-unique indexes from schema_indexes.sql."""
    index_sql = importlib.resources.files("maildb").joinpath("schema_indexes.sql").read_text()
    with pool.connection() as conn:
        conn.execute(index_sql)
        conn.commit()
    logger.info("indexes_created")
