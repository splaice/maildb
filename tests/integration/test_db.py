# tests/integration/test_db.py
from __future__ import annotations

from uuid import uuid4

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
    with test_pool.connection() as conn:
        conn.execute("ALTER TABLE emails ALTER COLUMN source_account DROP NOT NULL")
        conn.execute("DELETE FROM email_attachments")
        conn.execute("DELETE FROM attachments")
        conn.execute("DELETE FROM ingest_tasks")
        conn.execute("DELETE FROM email_accounts")
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


def test_email_accounts_table_exists(test_pool) -> None:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'email_accounts' ORDER BY column_name"
        )
        cols = {row[0] for row in cur.fetchall()}
    assert cols == {"email_id", "source_account", "import_id", "first_seen_at"}


def test_email_accounts_primary_key_on_email_id_source_account(test_pool) -> None:  # type: ignore[no-untyped-def]
    """Same (email_id, source_account) cannot appear twice; different accounts can."""
    iid_a = uuid4()
    iid_b = uuid4()
    eid = uuid4()
    with test_pool.connection() as conn:
        for iid, acct in [(iid_a, "a@example.com"), (iid_b, "b@example.com")]:
            conn.execute(
                "INSERT INTO imports (id, source_account, source_file, status) "
                "VALUES (%(id)s, %(acct)s, 't', 'completed')",
                {"id": iid, "acct": acct},
            )
        conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, source_account, import_id) "
            "VALUES (%(id)s, '<pk@example.com>', 't', 'a@example.com', %(iid)s)",
            {"id": eid, "iid": iid_a},
        )
        # Two distinct accounts → two rows OK.
        conn.execute(
            "INSERT INTO email_accounts (email_id, source_account, import_id) "
            "VALUES (%(eid)s, 'a@example.com', %(iid)s)",
            {"eid": eid, "iid": iid_a},
        )
        conn.execute(
            "INSERT INTO email_accounts (email_id, source_account, import_id) "
            "VALUES (%(eid)s, 'b@example.com', %(iid)s)",
            {"eid": eid, "iid": iid_b},
        )
        conn.commit()

        cur = conn.execute(
            "SELECT count(*) FROM email_accounts WHERE email_id = %(eid)s",
            {"eid": eid},
        )
        assert cur.fetchone()[0] == 2


def test_indexes_for_email_accounts(test_pool) -> None:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'email_accounts' "
            "AND indexname IN ('idx_email_accounts_source_account', 'idx_email_accounts_import_id')"
        )
        names = {row[0] for row in cur.fetchall()}
    assert names == {"idx_email_accounts_source_account", "idx_email_accounts_import_id"}


def test_init_db_backfills_email_accounts_from_emails(test_pool) -> None:  # type: ignore[no-untyped-def]
    """Rows with legacy emails.source_account get mirrored into email_accounts."""
    iid = uuid4()
    eid = uuid4()
    with test_pool.connection() as conn:
        conn.execute("DELETE FROM email_accounts")
        conn.execute("DELETE FROM emails")
        conn.execute("DELETE FROM imports")
        conn.execute(
            "INSERT INTO imports (id, source_account, source_file, status) "
            "VALUES (%(id)s, 'legacy@example.com', 't', 'completed')",
            {"id": iid},
        )
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, source_account, import_id)
               VALUES (%(id)s, '<legacy@example.com>', 't', 'legacy@example.com', %(iid)s)""",
            {"id": eid, "iid": iid},
        )
        conn.commit()

    init_db(test_pool)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT source_account, import_id FROM email_accounts WHERE email_id = %(eid)s",
            {"eid": eid},
        )
        rows = cur.fetchall()
    assert rows == [("legacy@example.com", iid)]
