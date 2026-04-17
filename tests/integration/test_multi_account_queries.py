"""End-to-end multi-account query scenarios from spec §9 / issue #15."""

from __future__ import annotations

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
    assert {e.message_id for e in thread} == {
        "<cross-1@example.com>",
        "<cross-2@example.com>",
    }
    assert {e.source_account for e in thread} == {"a@example.com", "b@example.com"}


def test_duplicate_emails_surface_under_both_accounts(
    test_pool, test_settings, multi_account_seed
):
    """Same message_id ingested by A then B is visible in both account-scoped queries.

    The emails row is de-duplicated on message_id (ON CONFLICT DO UPDATE
    no-op), but email_accounts carries one row per (email_id, source_account)
    so account-scoped finds return the message from either side.
    """
    with test_pool.connection() as conn:
        # emails table de-duplicates.
        cur = conn.execute("SELECT count(*) FROM emails WHERE message_id = '<dup@example.com>'")
        assert cur.fetchone()[0] == 1
        # email_accounts has one row per account.
        cur = conn.execute(
            """SELECT ea.source_account
               FROM email_accounts ea
               JOIN emails e ON e.id = ea.email_id
               WHERE e.message_id = '<dup@example.com>'
               ORDER BY ea.source_account"""
        )
        accounts = [r[0] for r in cur.fetchall()]
    assert accounts == ["a@example.com", "b@example.com"]

    # And the query surface reflects that: find(account=B) returns the dup.
    db = _db(test_pool, test_settings)
    b_results, _ = db.find(account="b@example.com", limit=100)
    assert "<dup@example.com>" in {e.message_id for e in b_results}
    a_results, _ = db.find(account="a@example.com", limit=100)
    assert "<dup@example.com>" in {e.message_id for e in a_results}


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
    # A has 4 emails (a-1, a-2, cross-1, dup), B has 3 (b-1, cross-2, dup
    # — the duplicate is attributed to both accounts via email_accounts).
    assert by_acct["a@example.com"].email_count == 4
    assert by_acct["b@example.com"].email_count == 3


def test_import_history_filters_by_account(test_pool, test_settings, multi_account_seed):
    db = _db(test_pool, test_settings)
    a_records = db.import_history(account="a@example.com")
    assert len(a_records) == 1
    assert a_records[0].source_account == "a@example.com"
