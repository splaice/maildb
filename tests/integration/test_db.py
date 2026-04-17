# tests/integration/test_db.py
from __future__ import annotations

import pytest

from maildb.db import init_db

pytestmark = pytest.mark.integration


def test_init_db_creates_table(test_pool) -> None:  # type: ignore[no-untyped-def]
    """init_db() should create the emails table."""
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'emails')"
        )
        assert cur.fetchone()[0] is True


def test_init_db_is_idempotent(test_pool) -> None:  # type: ignore[no-untyped-def]
    """Calling init_db() twice should not raise."""
    init_db(test_pool)  # second call (first was in fixture)
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'emails')"
        )
        assert cur.fetchone()[0] is True


def test_pool_connection(test_pool) -> None:  # type: ignore[no-untyped-def]
    """Pool should provide working connections."""
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT 1")
        assert cur.fetchone()[0] == 1


def test_imports_table_exists(test_pool) -> None:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'imports' ORDER BY column_name"
        )
        cols = {row[0] for row in cur.fetchall()}
    assert cols == {
        "id",
        "source_account",
        "source_file",
        "started_at",
        "completed_at",
        "messages_total",
        "messages_inserted",
        "messages_skipped",
        "status",
    }


def test_emails_has_source_account_and_import_id(test_pool) -> None:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'emails' AND column_name IN ('source_account', 'import_id')"
        )
        cols = {row[0] for row in cur.fetchall()}
    assert cols == {"source_account", "import_id"}


def test_ingest_tasks_has_import_id(test_pool) -> None:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ingest_tasks' AND column_name = 'import_id'"
        )
        rows = cur.fetchall()
    assert len(rows) == 1


def test_indexes_for_multi_account_columns(test_pool) -> None:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename IN ('emails', 'imports') "
            "AND indexname IN ("
            "  'idx_email_source_account', 'idx_email_import_id',"
            "  'idx_imports_source_account', 'idx_imports_started_at')"
        )
        names = {row[0] for row in cur.fetchall()}
    assert names == {
        "idx_email_source_account",
        "idx_email_import_id",
        "idx_imports_source_account",
        "idx_imports_started_at",
    }


def test_init_db_tightens_source_account_when_no_nulls(test_pool) -> None:  # type: ignore[no-untyped-def]
    from uuid import uuid4

    with test_pool.connection() as conn:
        conn.execute("ALTER TABLE emails ALTER COLUMN source_account DROP NOT NULL")
        conn.execute("DELETE FROM email_attachments")
        conn.execute("DELETE FROM attachments")
        conn.execute("DELETE FROM ingest_tasks")
        conn.execute("DELETE FROM emails")
        conn.execute("DELETE FROM imports")
        conn.execute(
            "INSERT INTO imports (id, source_account, source_file, status) "
            "VALUES (%(id)s, 'you@example.com', 'test', 'completed')",
            {"id": uuid4()},
        )
        cur = conn.execute("SELECT id FROM imports LIMIT 1")
        iid = cur.fetchone()[0]
        conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, source_account, import_id) "
            "VALUES (%(id)s, '<x@example.com>', 't', 'you@example.com', %(iid)s)",
            {"id": uuid4(), "iid": iid},
        )
        conn.commit()

    init_db(test_pool)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'emails' AND column_name = 'source_account'"
        )
        assert cur.fetchone()[0] == "NO"


def test_init_db_leaves_nullable_when_some_nulls(test_pool) -> None:  # type: ignore[no-untyped-def]
    from uuid import uuid4

    with test_pool.connection() as conn:
        conn.execute("ALTER TABLE emails ALTER COLUMN source_account DROP NOT NULL")
        conn.execute("DELETE FROM email_attachments")
        conn.execute("DELETE FROM attachments")
        conn.execute("DELETE FROM ingest_tasks")
        conn.execute("DELETE FROM emails")
        conn.execute("DELETE FROM imports")
        conn.execute(
            "INSERT INTO emails (id, message_id, thread_id) "
            "VALUES (%(id)s, '<y@example.com>', 't')",
            {"id": uuid4()},
        )
        conn.commit()

    init_db(test_pool)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'emails' AND column_name = 'source_account'"
        )
        assert cur.fetchone()[0] == "YES"
