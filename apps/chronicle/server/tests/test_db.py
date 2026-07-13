# tests/test_db.py
from __future__ import annotations

from typing import TYPE_CHECKING

from chronicle_server.db import audit, ensure_user, init_app_tables

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool


def test_init_app_tables_idempotent(db_pool: ConnectionPool) -> None:
    init_app_tables(db_pool)
    init_app_tables(db_pool)
    with db_pool.connection() as conn:
        row = conn.execute(
            """
            SELECT count(*) FROM information_schema.tables
             WHERE table_schema = 'public'
               AND table_name IN ('app_users', 'app_audit')
            """
        ).fetchone()
    assert row is not None
    assert row[0] == 2


def test_audit_insert(db_pool: ConnectionPool) -> None:
    ensure_user(db_pool, "owner")
    audit(db_pool, username="owner", action="test_action", detail={"k": "v"})
    with db_pool.connection() as conn:
        row = conn.execute(
            """
            SELECT username, action, detail
              FROM app_audit
             WHERE action = 'test_action'
             ORDER BY id DESC
             LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert row[0] == "owner"
    assert row[1] == "test_action"
    assert row[2] == {"k": "v"}


def test_ensure_user_upsert(db_pool: ConnectionPool) -> None:
    ensure_user(db_pool, "owner")
    ensure_user(db_pool, "owner")
    with db_pool.connection() as conn:
        row = conn.execute(
            "SELECT count(*) FROM app_users WHERE username = %s",
            ("owner",),
        ).fetchone()
    assert row is not None
    assert row[0] == 1
