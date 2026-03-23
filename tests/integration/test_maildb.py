# tests/integration/test_maildb.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from maildb.config import Settings
from maildb.maildb import MailDB
from maildb.models import SearchResult

pytestmark = pytest.mark.integration


@pytest.fixture
def seed_emails(test_pool):  # type: ignore[no-untyped-def]
    """Insert a known set of emails for query testing."""
    emails = [
        {
            "message_id": "find-test-1@example.com",
            "thread_id": "find-test-1@example.com",
            "subject": "Budget Discussion",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["bob@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
            "body_text": "Let's discuss the Q1 budget numbers.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.1] * 768,
        },
        {
            "message_id": "find-test-2@example.com",
            "thread_id": "find-test-1@example.com",
            "subject": "Re: Budget Discussion",
            "sender_name": "Bob",
            "sender_address": "bob@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 16, 14, 0, tzinfo=UTC),
            "body_text": "Sounds good, I'll prepare the spreadsheet.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": "find-test-1@example.com",
            "references": ["find-test-1@example.com"],
            "embedding": [0.2] * 768,
        },
        {
            "message_id": "find-test-3@stripe.com",
            "thread_id": "find-test-3@stripe.com",
            "subject": "Invoice #1234",
            "sender_name": "Stripe Billing",
            "sender_address": "billing@stripe.com",
            "sender_domain": "stripe.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 2, 1, 8, 0, tzinfo=UTC),
            "body_text": "Your invoice for January is ready.",
            "body_html": None,
            "has_attachment": True,
            "attachments": json.dumps(
                [{"filename": "invoice.pdf", "content_type": "application/pdf", "size": 2048}]
            ),
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


def test_find_by_sender(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(sender="alice@example.com")
    assert len(results) == 1
    assert results[0].sender_address == "alice@example.com"


def test_find_by_sender_domain(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(sender_domain="stripe.com")
    assert len(results) == 1
    assert results[0].sender_domain == "stripe.com"


def test_find_by_date_range(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(after="2025-01-16", before="2025-02-02")
    assert len(results) == 2  # Bob's reply and Stripe invoice


def test_find_by_attachment(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(has_attachment=True)
    assert len(results) == 1
    assert results[0].has_attachment is True


def test_find_by_subject_contains(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(subject_contains="budget")
    assert len(results) == 2  # Both budget messages


def test_find_by_labels(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(labels=["Finance"])
    assert len(results) == 1
    assert "Finance" in results[0].labels


def test_find_by_recipient(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(recipient="bob@example.com")
    assert len(results) == 1
    assert results[0].message_id == "find-test-1@example.com"


def test_find_with_limit(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(limit=1)
    assert len(results) == 1


def test_find_order_validation(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="Invalid order"):
        db.find(order="DROP TABLE emails")


def test_find_order_date_asc(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(order="date ASC")
    assert results[0].date <= results[-1].date


def test_get_thread(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    thread = db.get_thread("find-test-1@example.com")
    assert len(thread) == 2
    assert thread[0].date <= thread[1].date


def test_get_thread_for(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    thread = db.get_thread_for("find-test-2@example.com")
    assert len(thread) == 2  # Should find the full thread
    assert any(e.message_id == "find-test-1@example.com" for e in thread)


def test_search(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    mock_ec = MagicMock()
    mock_ec.embed.return_value = [0.1] * 768
    db = MailDB._from_pool(test_pool, embedding_client=mock_ec)
    results = db.search("budget discussion")
    assert len(results) > 0
    assert isinstance(results[0], SearchResult)
    assert results[0].similarity > 0


def test_search_with_filters(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    mock_ec = MagicMock()
    mock_ec.embed.return_value = [0.1] * 768
    db = MailDB._from_pool(test_pool, embedding_client=mock_ec)
    results = db.search("budget", sender_domain="example.com")
    assert all(r.email.sender_domain == "example.com" for r in results)


# Additional seed data for advanced queries
@pytest.fixture
def seed_advanced(test_pool):  # type: ignore[no-untyped-def]
    """Seed data for advanced query tests. Includes user_email=alice@example.com as context."""
    emails = [
        # Alice sends to Bob
        {
            "message_id": "adv-1@example.com",
            "thread_id": "adv-1@example.com",
            "subject": "Project Alpha",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["bob@corp.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 10, 10, 0, tzinfo=UTC),
            "body_text": "Let's start project alpha.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["Sent"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.9, 0.1] + [0.0] * 766,
        },
        # Bob replies to Alice (inbound)
        {
            "message_id": "adv-2@corp.com",
            "thread_id": "adv-1@example.com",
            "subject": "Re: Project Alpha",
            "sender_name": "Bob",
            "sender_address": "bob@corp.com",
            "sender_domain": "corp.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 11, 10, 0, tzinfo=UTC),
            "body_text": "Great, let's do it.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": "adv-1@example.com",
            "references": ["adv-1@example.com"],
            "embedding": [0.1, 0.9] + [0.0] * 766,
        },
        # Bob sends another message (unreplied by Alice)
        {
            "message_id": "adv-3@corp.com",
            "thread_id": "adv-3@corp.com",
            "subject": "Need help",
            "sender_name": "Bob",
            "sender_address": "bob@corp.com",
            "sender_domain": "corp.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
            "body_text": "Can you help me with this?",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.5, 0.5] + [0.0] * 766,
        },
        # Carol sends to Alice (different domain, inbound)
        {
            "message_id": "adv-4@other.com",
            "thread_id": "adv-4@other.com",
            "subject": "Meeting invite",
            "sender_name": "Carol",
            "sender_address": "carol@other.com",
            "sender_domain": "other.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 20, 10, 0, tzinfo=UTC),
            "body_text": "Let's meet next week.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.3, 0.7] + [0.0] * 766,
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


def test_top_contacts_inbound(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(limit=5, direction="inbound")
    # Bob sent 2 messages to Alice, Carol sent 1
    assert len(contacts) >= 2
    assert contacts[0]["address"] == "bob@corp.com"
    assert contacts[0]["count"] == 2


def test_top_contacts_outbound(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(limit=5, direction="outbound")
    assert len(contacts) >= 1
    assert contacts[0]["address"] == "bob@corp.com"


def test_top_contacts_requires_user_email(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="user_email"):
        db.top_contacts()


def test_unreplied(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    unreplied = db.unreplied()
    # adv-3 and adv-4 are unreplied inbound messages
    message_ids = [e.message_id for e in unreplied]
    assert "adv-3@corp.com" in message_ids
    assert "adv-4@other.com" in message_ids


def test_unreplied_requires_user_email(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="user_email"):
        db.unreplied()


def test_long_threads(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    threads = db.long_threads(min_messages=2)
    assert len(threads) >= 1
    assert threads[0]["thread_id"] == "adv-1@example.com"
    assert threads[0]["message_count"] >= 2


def test_topics_with_sender(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    topics = db.topics_with(sender="bob@corp.com", limit=5)
    assert len(topics) >= 1
    assert all(e.sender_address == "bob@corp.com" for e in topics)
