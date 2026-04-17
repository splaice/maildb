from __future__ import annotations

from uuid import uuid4

import pytest

from maildb.ingest.orchestrator import backfill_source_account

pytestmark = pytest.mark.integration


def _insert_untagged_email(pool, message_id: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, sender_name,
                   body_text, created_at)
               VALUES (%(id)s, %(mid)s, 'thread-1', 'T', 'S', 'b', now())""",
            {"id": uuid4(), "mid": message_id},
        )
        conn.commit()


def _clean_imports_for_account(pool, account: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "DELETE FROM imports WHERE source_account = %(acct)s",
            {"acct": account},
        )
        conn.commit()


def test_backfill_tags_null_rows(test_pool):
    _clean_imports_for_account(test_pool, "you@example.com")
    _insert_untagged_email(test_pool, "<a@example.com>")
    _insert_untagged_email(test_pool, "<b@example.com>")

    result = backfill_source_account(test_pool, account="you@example.com")
    assert result["rows_updated"] == 2

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM emails "
            "WHERE source_account = 'you@example.com' AND import_id IS NOT NULL"
        )
        assert cur.fetchone()[0] == 2

        cur = conn.execute(
            "SELECT source_file, status, messages_inserted FROM imports "
            "WHERE source_account = 'you@example.com'"
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "migration"
        assert rows[0][1] == "completed"
        assert rows[0][2] == 2


def test_backfill_is_idempotent(test_pool):
    # Ensure no pre-existing migration rows for this account.
    _clean_imports_for_account(test_pool, "you@example.com")
    _insert_untagged_email(test_pool, "<c@example.com>")

    first = backfill_source_account(test_pool, account="you@example.com")
    second = backfill_source_account(test_pool, account="you@example.com")

    assert first["rows_updated"] == 1
    assert second["rows_updated"] == 0
    # Second call still creates an imports row, but with messages_inserted=0
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM imports "
            "WHERE source_file = 'migration' AND source_account = 'you@example.com'"
        )
        assert cur.fetchone()[0] == 2


def test_backfill_does_not_overwrite_tagged_rows(test_pool):
    _clean_imports_for_account(test_pool, "you@example.com")
    _clean_imports_for_account(test_pool, "other@example.com")
    # Pre-tagged row
    with test_pool.connection() as conn:
        conn.execute(
            "INSERT INTO imports (id, source_account, source_file, status) "
            "VALUES (%(id)s, %(acct)s, 'preexisting', 'completed')",
            {"id": uuid4(), "acct": "other@example.com"},
        )
        cur = conn.execute("SELECT id FROM imports WHERE source_account = 'other@example.com'")
        existing_iid = cur.fetchone()[0]
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject,
                   source_account, import_id, created_at)
               VALUES (%(id)s, '<tagged@example.com>', 't', 'T',
                   'other@example.com', %(iid)s, now())""",
            {"id": uuid4(), "iid": existing_iid},
        )
        conn.commit()

    # Add an untagged row too
    _insert_untagged_email(test_pool, "<untagged@example.com>")

    result = backfill_source_account(test_pool, account="you@example.com")
    assert result["rows_updated"] == 1

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT source_account FROM emails WHERE message_id = '<tagged@example.com>'"
        )
        assert cur.fetchone()[0] == "other@example.com"
