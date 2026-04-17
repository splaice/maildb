"""End-to-end multi-account query scenarios from spec §9 / issue #15."""

from __future__ import annotations

from uuid import uuid4

import pytest

from maildb.maildb import MailDB

pytestmark = pytest.mark.integration


def _db(test_pool, test_settings) -> MailDB:
    config = test_settings.model_copy()
    config.user_emails = ["you@example.com"]
    return MailDB._from_pool(test_pool, config=config)


def test_get_thread_returns_cross_account_messages(test_pool, test_settings, multi_account_seed):
    """get_thread(...) ignores account and returns the full cross-account thread."""
    db = _db(test_pool, test_settings)
    thread = db.get_thread("thread-cross")
    assert {e.message_id for e in thread} == {"<cross-1@example.com>", "<cross-2@example.com>"}
    assert {e.source_account for e in thread} == {"a@example.com", "b@example.com"}


def test_deduplication_first_import_wins(test_pool, test_settings, multi_account_seed):
    """Duplicate message_id keeps the first import's source_account."""
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*), array_agg(source_account) FROM emails "
            "WHERE message_id = '<dup@example.com>'"
        )
        count, accounts = cur.fetchone()
    assert count == 1
    assert accounts == ["a@example.com"]


def test_find_no_account_returns_all(test_pool, test_settings, multi_account_seed):
    db = _db(test_pool, test_settings)
    results, total = db.find(limit=100)
    accounts = {e.source_account for e in results}
    assert accounts == {"a@example.com", "b@example.com"}
    assert total >= 6


def test_accounts_summary(test_pool, test_settings, multi_account_seed):
    db = _db(test_pool, test_settings)
    summaries = db.accounts()
    by_acct = {s.source_account: s for s in summaries}
    assert set(by_acct) == {"a@example.com", "b@example.com"}
    # A has 4 emails (a-1, a-2, cross-1, dup), B has 2 (b-1, cross-2)
    assert by_acct["a@example.com"].email_count == 4
    assert by_acct["b@example.com"].email_count == 2


def test_import_history_filters_by_account(test_pool, test_settings, multi_account_seed):
    db = _db(test_pool, test_settings)
    a_records = db.import_history(account="a@example.com")
    assert len(a_records) == 1
    assert a_records[0].source_account == "a@example.com"


def test_unreplied_scoped_by_account(test_pool, test_settings):
    """unreplied(account=...) filters to the given account's messages."""
    db_config = test_settings.model_copy()
    db_config.user_emails = ["you@example.com"]
    db = MailDB._from_pool(test_pool, config=db_config)

    iid_a, iid_b = uuid4(), uuid4()
    with test_pool.connection() as conn:
        for iid, acct in [(iid_a, "a@example.com"), (iid_b, "b@example.com")]:
            conn.execute(
                "INSERT INTO imports (id, source_account, source_file, status) "
                "VALUES (%(id)s, %(acct)s, 't', 'completed')",
                {"id": iid, "acct": acct},
            )
        # Inbound to A: one unreplied from alice.
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, sender_address,
                   date, source_account, import_id, created_at)
               VALUES (%(id)s, '<u-A@ex.com>', 'unrep-A', 'alice@ex.com',
                   now(), 'a@example.com', %(iid)s, now())""",
            {"id": uuid4(), "iid": iid_a},
        )
        # Inbound to B: one unreplied from bob.
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, sender_address,
                   date, source_account, import_id, created_at)
               VALUES (%(id)s, '<u-B@ex.com>', 'unrep-B', 'bob@ex.com',
                   now(), 'b@example.com', %(iid)s, now())""",
            {"id": uuid4(), "iid": iid_b},
        )
        conn.commit()

    a_results, _ = db.unreplied(direction="inbound", account="a@example.com")
    assert {e.message_id for e in a_results} == {"<u-A@ex.com>"}

    all_results, _ = db.unreplied(direction="inbound")
    assert {e.message_id for e in all_results} >= {"<u-A@ex.com>", "<u-B@ex.com>"}


def test_unreplied_inbound_reply_from_other_user_email(test_pool, test_settings):
    """A reply from ANY user_emails address excludes the original from unreplied.

    Cross-account reply semantic: message lands in account A's inbox,
    user replies from account B's address — must count as replied.
    """
    db_config = test_settings.model_copy()
    db_config.user_emails = ["a@example.com", "b@example.com"]
    db = MailDB._from_pool(test_pool, config=db_config)

    iid_a = uuid4()
    iid_b = uuid4()
    with test_pool.connection() as conn:
        for iid, acct in [(iid_a, "a@example.com"), (iid_b, "b@example.com")]:
            conn.execute(
                "INSERT INTO imports (id, source_account, source_file, status) "
                "VALUES (%(id)s, %(acct)s, 't', 'completed')",
                {"id": iid, "acct": acct},
            )
        # Original inbound from carol, in account A.
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, sender_address,
                   date, source_account, import_id, created_at)
               VALUES (%(id)s, '<orig@ex.com>', 'xr', 'carol@ex.com',
                   '2026-04-01', 'a@example.com', %(iid)s, now())""",
            {"id": uuid4(), "iid": iid_a},
        )
        # Reply from the user's OTHER address (b@example.com), recorded in account B.
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, sender_address,
                   date, source_account, import_id, created_at)
               VALUES (%(id)s, '<reply@ex.com>', 'xr', 'b@example.com',
                   '2026-04-02', 'b@example.com', %(iid)s, now())""",
            {"id": uuid4(), "iid": iid_b},
        )
        conn.commit()

    results, _ = db.unreplied(direction="inbound")
    message_ids = {e.message_id for e in results}
    assert "<orig@ex.com>" not in message_ids, (
        "Original should be considered replied because user replied from b@example.com"
    )
