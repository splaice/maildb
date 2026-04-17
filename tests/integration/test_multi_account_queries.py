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
