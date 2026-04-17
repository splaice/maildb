# tests/conftest.py
from __future__ import annotations

import os
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from maildb.config import Settings
from maildb.db import create_indexes, create_pool, init_db

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "MAILDB_TEST_DATABASE_URL",
            "postgresql://maildb_test@localhost:5432/maildb_test",
        ),
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture(scope="session")
def test_pool(test_settings: Settings):  # type: ignore[no-untyped-def]
    pool = create_pool(test_settings)
    init_db(pool)
    create_indexes(pool)  # Tests need indexes
    yield pool
    pool.close()


@pytest.fixture(autouse=True)
def _ensure_source_account_nullable(request) -> Iterator[None]:  # type: ignore[no-untyped-def]
    """Before each integration test, ensure emails.source_account is nullable.

    Tests in test_db.py tighten the constraint via init_db; other tests may
    need to insert rows without source_account. Restoring to nullable keeps
    each test deterministic regardless of ordering.
    """
    if "integration" not in [m.name for m in request.node.iter_markers()]:
        yield
        return
    pool = request.getfixturevalue("test_pool")
    with pool.connection() as conn:
        conn.execute("ALTER TABLE emails ALTER COLUMN source_account DROP NOT NULL")
        conn.commit()
    yield


@pytest.fixture(autouse=True)
def _clean_emails(request) -> Iterator[None]:  # type: ignore[no-untyped-def]
    """Delete all rows after each integration test to prevent test pollution."""
    if "integration" not in [m.name for m in request.node.iter_markers()]:
        yield
        return
    yield
    pool = request.getfixturevalue("test_pool")
    with pool.connection() as conn:
        conn.execute("DELETE FROM email_attachments")
        conn.execute("DELETE FROM attachments")
        conn.execute("DELETE FROM ingest_tasks")
        conn.execute("DELETE FROM emails")
        conn.execute("DELETE FROM imports")
        conn.commit()


@pytest.fixture
def multi_account_seed(test_pool):  # type: ignore[no-untyped-def]
    """Seed two accounts with varied data for cross-account scenarios.

    Layout:
      - account A: 3 emails, including one in a thread that crosses to B
      - account B: 2 emails, one in the cross-account thread
      - one duplicate message_id between A and B (A wins via ON CONFLICT)
    """
    iid_a = uuid4()
    iid_b = uuid4()
    with test_pool.connection() as conn:
        for iid, acct in [(iid_a, "a@example.com"), (iid_b, "b@example.com")]:
            conn.execute(
                "INSERT INTO imports (id, source_account, source_file, status, completed_at) "
                "VALUES (%(id)s, %(acct)s, 'seed', 'completed', now())",
                {"id": iid, "acct": acct},
            )

        rows = [
            # account A
            ("<a-1@example.com>", "thread-A", "alice@example.com", "a@example.com", iid_a),
            ("<a-2@example.com>", "thread-A", "alice@example.com", "a@example.com", iid_a),
            ("<cross-1@example.com>", "thread-cross", "carol@example.com", "a@example.com", iid_a),
            # account B
            ("<b-1@example.com>", "thread-B", "bob@example.com", "b@example.com", iid_b),
            ("<cross-2@example.com>", "thread-cross", "carol@example.com", "b@example.com", iid_b),
        ]
        for mid, tid, sender, acct, iid in rows:
            conn.execute(
                """INSERT INTO emails (id, message_id, thread_id, sender_address,
                       sender_domain, date, source_account, import_id, created_at)
                   VALUES (%(id)s, %(mid)s, %(tid)s, %(sender)s, %(domain)s,
                       now(), %(acct)s, %(iid)s, now())""",
                {
                    "id": uuid4(),
                    "mid": mid,
                    "tid": tid,
                    "sender": sender,
                    "domain": sender.split("@")[1],
                    "acct": acct,
                    "iid": iid,
                },
            )

        # Duplicate message_id — second insert no-ops via ON CONFLICT.
        # Insert in A first so A wins.
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, sender_address,
                   date, source_account, import_id, created_at)
               VALUES (%(id)s, '<dup@example.com>', 't-dup', 'x@example.com',
                   now(), 'a@example.com', %(iid)s, now())
               ON CONFLICT (message_id) DO NOTHING""",
            {"id": uuid4(), "iid": iid_a},
        )
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, sender_address,
                   date, source_account, import_id, created_at)
               VALUES (%(id)s, '<dup@example.com>', 't-dup', 'x@example.com',
                   now(), 'b@example.com', %(iid)s, now())
               ON CONFLICT (message_id) DO NOTHING""",
            {"id": uuid4(), "iid": iid_b},
        )
        conn.commit()
    return {"iid_a": iid_a, "iid_b": iid_b}
