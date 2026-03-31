# tests/integration/test_dsl.py
"""Integration tests for the DSL engine against a real PostgreSQL database."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg.rows import dict_row

from maildb.dsl import parse_query

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
