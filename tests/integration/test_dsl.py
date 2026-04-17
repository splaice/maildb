# tests/integration/test_dsl.py
"""Integration tests for the DSL engine against a real PostgreSQL database."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg.rows import dict_row

from maildb.dsl import parse_query
from maildb.maildb import MailDB

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixture: seed 3 emails for DSL tests
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_dsl(test_pool):  # type: ignore[no-untyped-def]
    """Insert a known set of emails for DSL query testing."""
    emails = [
        {
            "message_id": "dsl-1@example.com",
            "thread_id": "dsl-1@example.com",
            "subject": "Q1 Report",
            "sender_name": "Alice",
            "sender_address": "alice@acme.com",
            "sender_domain": "acme.com",
            "recipients": json.dumps(
                {
                    "to": ["bob@corp.com"],
                    "cc": ["carol@acme.com"],
                    "bcc": [],
                }
            ),
            "date": datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
            "body_text": "Here is the Q1 report for review.",
            "body_html": None,
            "has_attachment": True,
            "attachments": json.dumps([]),
            "labels": ["INBOX", "Reports"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.1] * 768,
        },
        {
            "message_id": "dsl-2@corp.com",
            "thread_id": "dsl-1@example.com",
            "subject": "Re: Q1 Report",
            "sender_name": "Bob",
            "sender_address": "bob@corp.com",
            "sender_domain": "corp.com",
            "recipients": json.dumps(
                {
                    "to": ["alice@acme.com"],
                    "cc": [],
                    "bcc": [],
                }
            ),
            "date": datetime(2025, 1, 16, 14, 0, tzinfo=UTC),
            "body_text": "Thanks, looks good.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": "dsl-1@example.com",
            "references": ["dsl-1@example.com"],
            "embedding": [0.2] * 768,
        },
        {
            "message_id": "dsl-3@acme.com",
            "thread_id": "dsl-3@acme.com",
            "subject": "Budget Meeting",
            "sender_name": "Alice",
            "sender_address": "alice@acme.com",
            "sender_domain": "acme.com",
            "recipients": json.dumps(
                {
                    "to": ["dave@acme.com"],
                    "cc": [],
                    "bcc": [],
                }
            ),
            "date": datetime(2025, 2, 1, 9, 0, tzinfo=UTC),
            "body_text": "Let's discuss the budget this Friday.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX", "Finance"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.3] * 768,
        },
    ]

    insert_sql = """
    INSERT INTO emails (
        message_id, thread_id, subject, sender_name, sender_address, sender_domain,
        recipients, date, body_text, body_html, has_attachment, attachments,
        labels, in_reply_to, "references", embedding
    ) VALUES (
        %(message_id)s, %(thread_id)s, %(subject)s, %(sender_name)s, %(sender_address)s,
        %(sender_domain)s, %(recipients)s, %(date)s, %(body_text)s, %(body_html)s,
        %(has_attachment)s, %(attachments)s, %(labels)s, %(in_reply_to)s,
        %(references)s, %(embedding)s
    )
    """

    with test_pool.connection() as conn:
        for e in emails:
            conn.execute(insert_sql, e)
        conn.commit()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _execute_dsl(test_pool: Any, spec: dict[str, Any]) -> list[dict[str, Any]]:
    sql, params = parse_query(spec)
    with test_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_simple_filter(test_pool, seed_dsl) -> None:  # type: ignore[no-untyped-def]
    rows = _execute_dsl(
        test_pool,
        {
            "where": {"field": "sender_domain", "op": "eq", "value": "acme.com"},
        },
    )
    assert len(rows) == 2


def test_aggregation_count_by_domain(test_pool, seed_dsl) -> None:  # type: ignore[no-untyped-def]
    rows = _execute_dsl(
        test_pool,
        {
            "select": [
                {"field": "sender_domain"},
                {"count": "*", "as": "cnt"},
            ],
            "group_by": ["sender_domain"],
            "order_by": [{"field": "cnt", "dir": "DESC"}],
        },
    )
    by_domain = {r["sender_domain"]: r["cnt"] for r in rows}
    assert by_domain["acme.com"] == 2
    assert by_domain["corp.com"] == 1


def test_date_range_filter(test_pool, seed_dsl) -> None:  # type: ignore[no-untyped-def]
    rows = _execute_dsl(
        test_pool,
        {
            "where": {
                "and": [
                    {"field": "date", "op": "gte", "value": "2025-01-15"},
                    {"field": "date", "op": "lt", "value": "2025-01-17"},
                ],
            },
        },
    )
    assert len(rows) == 2


def test_sent_to_source(test_pool, seed_dsl) -> None:  # type: ignore[no-untyped-def]
    rows = _execute_dsl(
        test_pool,
        {
            "from": "sent_to",
            "select": [
                {"field": "recipient_domain"},
                {"count": "*", "as": "cnt"},
            ],
            "group_by": ["recipient_domain"],
            "order_by": [{"field": "cnt", "dir": "DESC"}],
        },
    )
    by_domain = {r["recipient_domain"]: r["cnt"] for r in rows}
    # dsl-1: bob@corp.com (to), carol@acme.com (cc)
    # dsl-2: alice@acme.com (to)
    # dsl-3: dave@acme.com (to)
    assert by_domain["acme.com"] == 3
    assert by_domain["corp.com"] == 1


def test_email_labels_source(test_pool, seed_dsl) -> None:  # type: ignore[no-untyped-def]
    rows = _execute_dsl(
        test_pool,
        {
            "from": "email_labels",
            "select": [
                {"field": "label"},
                {"count": "*", "as": "cnt"},
            ],
            "group_by": ["label"],
            "order_by": [{"field": "cnt", "dir": "DESC"}],
        },
    )
    by_label = {r["label"]: r["cnt"] for r in rows}
    assert by_label["INBOX"] == 3
    assert by_label["Reports"] == 1
    assert by_label["Finance"] == 1


def test_having_filter(test_pool, seed_dsl) -> None:  # type: ignore[no-untyped-def]
    rows = _execute_dsl(
        test_pool,
        {
            "select": [
                {"field": "sender_domain"},
                {"count": "*", "as": "cnt"},
            ],
            "group_by": ["sender_domain"],
            "having": {"field": "cnt", "op": "gte", "value": 2},
        },
    )
    assert len(rows) == 1
    assert rows[0]["sender_domain"] == "acme.com"


def test_ilike_search(test_pool, seed_dsl) -> None:  # type: ignore[no-untyped-def]
    rows = _execute_dsl(
        test_pool,
        {
            "where": {"field": "subject", "op": "ilike", "value": "%budget%"},
        },
    )
    assert len(rows) == 1
    assert "Budget" in rows[0]["subject"]


def test_row_limit_cap(test_pool, seed_dsl) -> None:  # type: ignore[no-untyped-def]
    sql, _ = parse_query({"limit": 9999})
    assert "LIMIT 1000" in sql


def test_default_body_preview(test_pool, seed_dsl) -> None:  # type: ignore[no-untyped-def]
    rows = _execute_dsl(test_pool, {})
    assert len(rows) == 3
    # Default select should produce body_preview, not body_text
    assert "body_preview" in rows[0]


def test_emails_by_account_surfaces_duplicate_under_both_accounts(
    test_pool, test_settings, multi_account_seed
) -> None:  # type: ignore[no-untyped-def]
    """Cross-account duplicate (<dup@example.com>) is visible via either account.

    This is the correctness fix from issue #40: filtering by account on the
    plain `emails` source only returns the first-seen account; using the
    new `emails_by_account` source returns both.
    """
    # Baseline: plain `emails` source filtered by source_account misses the dup
    # for account B because emails.source_account reflects the first-import
    # (account A) attribution.
    rows_b_scalar = _execute_dsl(
        test_pool,
        {
            "from": "emails",
            "select": [{"field": "message_id"}],
            "where": {"field": "source_account", "op": "eq", "value": "b@example.com"},
            "limit": 100,
        },
    )
    b_scalar_ids = {r["message_id"] for r in rows_b_scalar}
    assert "<dup@example.com>" not in b_scalar_ids, (
        "Baseline precondition: plain `emails` source does not surface the dup under B"
    )

    # New source: emails_by_account with account=B returns the dup.
    rows_b_join = _execute_dsl(
        test_pool,
        {
            "from": "emails_by_account",
            "select": [{"field": "message_id"}, {"field": "account"}],
            "where": {"field": "account", "op": "eq", "value": "b@example.com"},
            "limit": 100,
        },
    )
    b_join_ids = {r["message_id"] for r in rows_b_join}
    assert "<dup@example.com>" in b_join_ids
    # And the account column is correctly returned.
    for r in rows_b_join:
        assert r["account"] == "b@example.com"

    # Account A also surfaces the dup — this is the cross-account property.
    rows_a_join = _execute_dsl(
        test_pool,
        {
            "from": "emails_by_account",
            "select": [{"field": "message_id"}],
            "where": {"field": "account", "op": "eq", "value": "a@example.com"},
            "limit": 100,
        },
    )
    a_join_ids = {r["message_id"] for r in rows_a_join}
    assert "<dup@example.com>" in a_join_ids


def test_emails_by_account_aggregation(test_pool, test_settings, multi_account_seed) -> None:  # type: ignore[no-untyped-def]
    """Grouping by account via the join gives true per-account counts."""
    rows = _execute_dsl(
        test_pool,
        {
            "from": "emails_by_account",
            "select": [
                {"field": "account"},
                {"count": "*", "as": "n"},
            ],
            "group_by": ["account"],
            "order_by": [{"field": "n", "dir": "DESC"}],
        },
    )
    by_account = {r["account"]: r["n"] for r in rows}
    # From multi_account_seed: A gets 4 (a-1, a-2, cross-1, dup),
    # B gets 3 (b-1, cross-2, dup).
    assert by_account["a@example.com"] == 4
    assert by_account["b@example.com"] == 3


def test_emails_by_account_matches_find_account_filter(
    test_pool, test_settings, multi_account_seed
) -> None:  # type: ignore[no-untyped-def]
    """The DSL emails_by_account source returns the same message_ids as
    MailDB.find(account=...) — this is the whole point of the fix.
    """
    db = MailDB._from_pool(test_pool, config=test_settings)

    # Via Tier 1 API (already correct — uses EXISTS on email_accounts).
    tier1, _ = db.find(account="b@example.com", limit=100)
    tier1_ids = {e.message_id for e in tier1}

    # Via Tier 2 DSL (the fix).
    dsl_rows = _execute_dsl(
        test_pool,
        {
            "from": "emails_by_account",
            "select": [{"field": "message_id"}],
            "where": {"field": "account", "op": "eq", "value": "b@example.com"},
            "limit": 100,
        },
    )
    dsl_ids = {r["message_id"] for r in dsl_rows}

    assert tier1_ids == dsl_ids
