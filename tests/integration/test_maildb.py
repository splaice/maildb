# tests/integration/test_maildb.py
from __future__ import annotations

import json
import json as json_mod
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from maildb.config import Settings
from maildb.maildb import MailDB
from maildb.models import Email, SearchResult

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


def test_find_offset(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    all_results = db.find(limit=10)
    offset_results = db.find(limit=10, offset=2)
    assert len(offset_results) == len(all_results) - 2
    assert offset_results[0].message_id == all_results[2].message_id


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


def test_top_contacts_requires_user_email(test_pool, seed_advanced, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("MAILDB_USER_EMAIL", raising=False)
    config = Settings(user_email=None, _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
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


def test_unreplied_outbound_with_seed_advanced(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """Verify outbound direction works when user_email matches sender address."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    # Insert an outbound message from Alice to Carol with no reply
    with test_pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO emails (
                message_id, thread_id, subject, sender_name, sender_address, sender_domain,
                recipients, date, body_text, body_html, has_attachment, attachments,
                labels, in_reply_to, "references", embedding
            ) VALUES (
                'outbound-1@example.com', 'outbound-1@example.com',
                'Follow up', 'Alice', 'alice@example.com', 'example.com',
                %(recipients)s, '2025-01-25 10:00:00+00', 'Following up on our chat.', NULL,
                false, '{}', '{Sent}', NULL, '{}', %(embedding)s
            )
            """,
            {
                "recipients": json_mod.dumps({"to": ["carol@other.com"], "cc": [], "bcc": []}),
                "embedding": [0.0] * 768,
            },
        )
        conn.commit()

    unreplied_emails = db.unreplied(direction="outbound")
    message_ids = [e.message_id for e in unreplied_emails]
    assert "outbound-1@example.com" in message_ids


def test_unreplied_requires_user_email(test_pool, seed_advanced, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("MAILDB_USER_EMAIL", raising=False)
    config = Settings(user_email=None, _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    with pytest.raises(ValueError, match="user_email"):
        db.unreplied()


def test_unreplied_excludes_null_date(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """Emails with null date should not appear in unreplied results."""
    # Insert a null-date email (simulating a Google Chat transcript)
    with test_pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO emails (
                message_id, thread_id, subject, sender_name, sender_address, sender_domain,
                recipients, date, body_text, body_html, has_attachment, attachments,
                labels, in_reply_to, "references", embedding
            ) VALUES (
                'null-date@chat.google.com', 'null-date@chat.google.com',
                'Chat with Team', 'Bot', 'bot@chat.google.com', 'chat.google.com',
                %(recipients)s, NULL, 'chat transcript', NULL,
                false, '{}', '{Chat}', NULL, '{}', %(embedding)s
            )
            """,
            {
                "recipients": json_mod.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
                "embedding": [0.0] * 768,
            },
        )
        conn.commit()

    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    unreplied_emails = db.unreplied()
    message_ids = [e.message_id for e in unreplied_emails]
    assert "null-date@chat.google.com" not in message_ids


def test_long_threads(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    threads = db.long_threads(min_messages=2)
    assert len(threads) >= 1
    assert threads[0]["thread_id"] == "adv-1@example.com"
    assert threads[0]["message_count"] >= 2


def test_long_threads_respects_limit(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    threads = db.long_threads(min_messages=2, limit=1)
    assert len(threads) == 1


def test_topics_with_sender(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    topics = db.topics_with(sender="bob@corp.com", limit=5)
    assert len(topics) >= 1
    assert all(e.sender_address == "bob@corp.com" for e in topics)


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


def test_get_thread_for_nonexistent(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    """get_thread_for with a message_id that doesn't exist should return []."""
    db = MailDB._from_pool(test_pool)
    result = db.get_thread_for("nonexistent@x.com")
    assert result == []


def test_topics_with_sender_domain(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """topics_with(sender_domain=...) should return emails from that domain."""
    db = MailDB._from_pool(test_pool)
    topics = db.topics_with(sender_domain="corp.com", limit=5)
    assert len(topics) >= 1
    assert all(e.sender_domain == "corp.com" for e in topics)
    # Bob is the only sender at corp.com
    assert all(e.sender_address == "bob@corp.com" for e in topics)


def test_topics_with_no_args_raises(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """topics_with() with neither sender nor sender_domain should raise ValueError."""
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="sender or sender_domain"):
        db.topics_with()


def test_long_threads_with_after(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """long_threads filters individual rows before grouping."""
    db = MailDB._from_pool(test_pool)

    # after="2025-01-12" excludes both adv-1 (Jan 10) and adv-2 (Jan 11)
    threads = db.long_threads(min_messages=2, after="2025-01-12")
    assert len(threads) == 0

    # after="2025-01-09" includes both messages in adv-1 thread
    threads = db.long_threads(min_messages=2, after="2025-01-09")
    assert len(threads) >= 1
    assert threads[0]["thread_id"] == "adv-1@example.com"
    assert threads[0]["message_count"] >= 2


def test_long_threads_with_participant(test_pool, seed_advanced) -> None:
    """long_threads with participant filters to threads with that sender."""
    db = MailDB._from_pool(test_pool)
    threads = db.long_threads(min_messages=2, participant="bob@corp.com")
    assert len(threads) >= 1
    assert threads[0]["thread_id"] == "adv-1@example.com"


def test_long_threads_participant_no_match(test_pool, seed_advanced) -> None:
    """Participant not in any long thread returns empty."""
    db = MailDB._from_pool(test_pool)
    threads = db.long_threads(min_messages=2, participant="nobody@nowhere.com")
    assert len(threads) == 0


def test_long_threads_no_participant_unchanged(test_pool, seed_advanced) -> None:
    """Without participant, current behavior preserved."""
    db = MailDB._from_pool(test_pool)
    threads = db.long_threads(min_messages=2)
    assert len(threads) >= 1


def test_long_threads_participant_cc_only_no_match(test_pool, seed_advanced) -> None:
    """Participant who is only in CC (not as sender) should NOT match."""
    db = MailDB._from_pool(test_pool)
    threads = db.long_threads(min_messages=2, participant="carol@other.com")
    assert len(threads) == 0


def test_unreplied_with_sender_filter(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """unreplied(sender=...) should only return unreplied from that sender."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    unreplied = db.unreplied(sender="bob@corp.com")
    message_ids = [e.message_id for e in unreplied]
    assert "adv-3@corp.com" in message_ids
    assert "adv-4@other.com" not in message_ids


def test_top_contacts_both(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """top_contacts(direction='both') should combine inbound + outbound."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(direction="both")
    # Bob: 2 inbound (adv-2, adv-3) + 1 outbound (adv-1 sent to bob) = 3
    bob = next(c for c in contacts if c["address"] == "bob@corp.com")
    assert bob["count"] == 3
    # Carol: 1 inbound (adv-4) = 1
    carol = next(c for c in contacts if c["address"] == "carol@other.com")
    assert carol["count"] == 1
    # Bob should be first (highest count)
    assert contacts[0]["address"] == "bob@corp.com"


def test_top_contacts_with_period(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """top_contacts with period should only count messages after that date."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(period="2025-01-14", direction="inbound")
    # After Jan 14: Bob sent adv-3 (Jan 15), Carol sent adv-4 (Jan 20)
    addresses = {c["address"] for c in contacts}
    assert "bob@corp.com" in addresses
    assert "carol@other.com" in addresses
    bob = next(c for c in contacts if c["address"] == "bob@corp.com")
    carol = next(c for c in contacts if c["address"] == "carol@other.com")
    assert bob["count"] == 1
    assert carol["count"] == 1


def test_top_contacts_domain_grouping_outbound(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """group_by='domain' with direction='outbound' groups recipients by domain."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(direction="outbound", group_by="domain")
    assert len(contacts) >= 1
    domains = {c["domain"] for c in contacts}
    assert "corp.com" in domains


def test_top_contacts_domain_grouping_inbound(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """group_by='domain' with direction='inbound' groups senders by domain."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(direction="inbound", group_by="domain")
    domains = {c["domain"] for c in contacts}
    assert "corp.com" in domains
    assert "other.com" in domains


def test_top_contacts_exclude_domains(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """exclude_domains filters out contacts from specified domains."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(direction="inbound", exclude_domains=["corp.com"])
    addresses = {c["address"] for c in contacts}
    assert "bob@corp.com" not in addresses
    assert "carol@other.com" in addresses


def test_top_contacts_domain_grouping_with_exclusion(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """group_by='domain' combined with exclude_domains."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(
        direction="inbound", group_by="domain", exclude_domains=["other.com"]
    )
    domains = {c["domain"] for c in contacts}
    assert "corp.com" in domains
    assert "other.com" not in domains


def test_top_contacts_domain_grouping_both(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """group_by='domain' with direction='both' combines inbound and outbound domains."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(direction="both", group_by="domain")
    # corp.com: 2 inbound (bob) + 1 outbound (alice->bob) = 3
    corp = next(c for c in contacts if c["domain"] == "corp.com")
    assert corp["count"] == 3
    # other.com: 1 inbound (carol) = 1
    other = next(c for c in contacts if c["domain"] == "other.com")
    assert other["count"] == 1


def test_top_contacts_exclude_multiple_domains(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """Excluding all contact domains returns empty results."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(direction="inbound", exclude_domains=["corp.com", "other.com"])
    assert contacts == []


def test_top_contacts_default_unchanged(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    """Default group_by='address' with no exclusions still works as before."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(direction="inbound")
    assert len(contacts) >= 2
    assert contacts[0]["address"] == "bob@corp.com"
    assert contacts[0]["count"] == 2


def test_unreplied_respects_limit(test_pool, seed_advanced) -> None:
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    results = db.unreplied(limit=1)
    assert len(results) <= 1


def test_search_results_ordered_by_similarity(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    """Search results should be ordered by descending similarity."""
    mock_ec = MagicMock()
    mock_ec.embed.return_value = [0.1] * 768
    db = MailDB._from_pool(test_pool, embedding_client=mock_ec)
    results = db.search("budget discussion")
    assert len(results) >= 2
    # The email with embedding [0.1]*768 should be most similar (cosine similarity = 1.0)
    assert results[0].email.message_id == "find-test-1@example.com"
    assert results[0].similarity == pytest.approx(1.0, abs=0.01)
    # Results should be in descending similarity order
    for i in range(len(results) - 1):
        assert results[i].similarity >= results[i + 1].similarity


# ---------------------------------------------------------------------------
# Unreplied outbound direction tests
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_unreplied_outbound(test_pool):  # type: ignore[no-untyped-def]
    """Seed data for outbound unreplied tests. user_email=alice@example.com."""
    emails = [
        # Alice→Dave (to), Dave never replies
        {
            "message_id": "unr-out-1@example.com",
            "thread_id": "unr-out-1@example.com",
            "subject": "Hey Dave",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["dave@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 3, 1, 10, 0, tzinfo=UTC),
            "body_text": "Hey Dave, any updates?",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["Sent"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.1] * 768,
        },
        # Alice→Eve (to), Eve replies
        {
            "message_id": "unr-out-2@example.com",
            "thread_id": "unr-out-2@example.com",
            "subject": "Hey Eve",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["eve@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 3, 2, 10, 0, tzinfo=UTC),
            "body_text": "Hey Eve, how are you?",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["Sent"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.2] * 768,
        },
        # Eve→Alice (reply to unr-out-2)
        {
            "message_id": "unr-out-3@corp.com",
            "thread_id": "unr-out-2@example.com",
            "subject": "Re: Hey Eve",
            "sender_name": "Eve",
            "sender_address": "eve@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 3, 3, 10, 0, tzinfo=UTC),
            "body_text": "Doing great!",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": "unr-out-2@example.com",
            "references": ["unr-out-2@example.com"],
            "embedding": [0.3] * 768,
        },
        # Frank→Alice, Alice never replies (inbound unreplied)
        {
            "message_id": "unr-in-1@corp.com",
            "thread_id": "unr-in-1@corp.com",
            "subject": "Question from Frank",
            "sender_name": "Frank",
            "sender_address": "frank@corp.com",
            "sender_domain": "corp.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 3, 4, 10, 0, tzinfo=UTC),
            "body_text": "Can you review this?",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.4] * 768,
        },
        # Alice→Eve (to) + Dave (cc), nobody replies
        {
            "message_id": "unr-out-4@example.com",
            "thread_id": "unr-out-4@example.com",
            "subject": "Team update",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps(
                {"to": ["eve@example.com"], "cc": ["dave@example.com"], "bcc": []}
            ),
            "date": datetime(2025, 3, 5, 10, 0, tzinfo=UTC),
            "body_text": "Here is the team update.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["Sent"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.5] * 768,
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


def test_unreplied_outbound(test_pool, seed_unreplied_outbound) -> None:  # type: ignore[no-untyped-def]
    """Outbound unreplied: messages Alice sent where nobody replied."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    results = db.unreplied(direction="outbound")
    message_ids = [e.message_id for e in results]
    # unr-out-1 (Dave never replied) and unr-out-4 (nobody replied) should appear
    assert "unr-out-1@example.com" in message_ids
    assert "unr-out-4@example.com" in message_ids
    # unr-out-2 should NOT appear (Eve replied)
    assert "unr-out-2@example.com" not in message_ids
    # Inbound message should NOT appear
    assert "unr-in-1@corp.com" not in message_ids


def test_unreplied_outbound_with_recipient(test_pool, seed_unreplied_outbound) -> None:  # type: ignore[no-untyped-def]
    """Outbound unreplied filtered to a specific recipient."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    results = db.unreplied(direction="outbound", recipient="dave@example.com")
    message_ids = [e.message_id for e in results]
    # unr-out-1 (to Dave, no reply) and unr-out-4 (cc Dave, no reply) should appear
    assert "unr-out-1@example.com" in message_ids
    assert "unr-out-4@example.com" in message_ids
    # unr-out-2 only went to Eve, not Dave
    assert "unr-out-2@example.com" not in message_ids


def test_unreplied_inbound_default(test_pool, seed_unreplied_outbound) -> None:  # type: ignore[no-untyped-def]
    """Default direction='inbound' returns only inbound unreplied messages."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    results = db.unreplied()  # default direction="inbound"
    message_ids = [e.message_id for e in results]
    # Only Frank's message is inbound unreplied
    assert "unr-in-1@corp.com" in message_ids
    # None of Alice's outbound should appear
    assert "unr-out-1@example.com" not in message_ids
    assert "unr-out-2@example.com" not in message_ids
    assert "unr-out-4@example.com" not in message_ids


def test_unreplied_outbound_multi_recipient_partial_reply(
    test_pool, seed_unreplied_outbound
) -> None:  # type: ignore[no-untyped-def]
    """Outbound with recipient filter: Eve replied to unr-out-2 but not unr-out-4."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    results = db.unreplied(direction="outbound", recipient="eve@example.com")
    message_ids = [e.message_id for e in results]
    # unr-out-2: Eve replied → should NOT appear
    assert "unr-out-2@example.com" not in message_ids
    # unr-out-4: Eve was a to-recipient but never replied → should appear
    assert "unr-out-4@example.com" in message_ids


def test_unreplied_invalid_direction(test_pool, seed_unreplied_outbound) -> None:  # type: ignore[no-untyped-def]
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    with pytest.raises(ValueError, match="direction"):
        db.unreplied(direction="sideways")  # type: ignore[arg-type]


def test_correspondence(test_pool, seed_advanced) -> None:
    """correspondence() returns all emails exchanged with a person."""
    db = MailDB._from_pool(test_pool)
    results = db.correspondence(address="bob@corp.com")
    msg_ids = [e.message_id for e in results]
    assert "adv-1@example.com" in msg_ids  # sent TO bob
    assert "adv-2@corp.com" in msg_ids  # sent BY bob
    assert "adv-3@corp.com" in msg_ids  # sent BY bob
    assert "adv-4@other.com" not in msg_ids  # carol, not bob


def test_correspondence_chronological_order(test_pool, seed_advanced) -> None:
    """Default order is date ASC (chronological)."""
    db = MailDB._from_pool(test_pool)
    results = db.correspondence(address="bob@corp.com")
    dates = [e.date for e in results]
    assert dates == sorted(dates)


def test_correspondence_with_date_filter(test_pool, seed_advanced) -> None:
    """Date filters narrow results."""
    db = MailDB._from_pool(test_pool)
    results = db.correspondence(address="bob@corp.com", after="2025-01-12")
    msg_ids = [e.message_id for e in results]
    assert "adv-3@corp.com" in msg_ids
    assert "adv-1@example.com" not in msg_ids


def test_correspondence_limit(test_pool, seed_advanced) -> None:
    """Limit restricts result count."""
    db = MailDB._from_pool(test_pool)
    results = db.correspondence(address="bob@corp.com", limit=1)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# mention_search tests
# ---------------------------------------------------------------------------


def test_mention_search_body(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="spreadsheet")
    assert len(results) == 1
    assert results[0].message_id == "find-test-2@example.com"


def test_mention_search_subject(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="Invoice")
    assert len(results) == 1
    assert results[0].message_id == "find-test-3@stripe.com"


def test_mention_search_case_insensitive(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="BUDGET")
    assert len(results) >= 1


def test_mention_search_with_sender_filter(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="budget", sender="alice@example.com")
    assert len(results) == 1
    assert results[0].sender_address == "alice@example.com"


def test_mention_search_escapes_like_chars(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="100%_done")
    assert len(results) == 0


def test_mention_search_ordered_by_date_desc(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="budget")
    if len(results) >= 2:
        assert results[0].date >= results[1].date


def test_mention_search_limit(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="budget", limit=1)
    assert len(results) <= 1


# ---------------------------------------------------------------------------
# query() / DSL tests
# ---------------------------------------------------------------------------


def test_query_simple_filter(test_pool, seed_emails) -> None:
    db = MailDB._from_pool(test_pool)
    results = db.query({"where": {"field": "sender_domain", "eq": "stripe.com"}})
    assert len(results) == 1
    assert results[0]["sender_domain"] == "stripe.com"


def test_query_aggregation(test_pool, seed_emails) -> None:
    db = MailDB._from_pool(test_pool)
    results = db.query(
        {
            "select": [{"field": "sender_domain"}, {"count": "*", "as": "total"}],
            "group_by": ["sender_domain"],
            "order_by": [{"field": "total", "dir": "desc"}],
        }
    )
    assert len(results) >= 1
    example_row = next(r for r in results if r["sender_domain"] == "example.com")
    assert example_row["total"] == 2


def test_query_row_limit(test_pool, seed_emails) -> None:
    db = MailDB._from_pool(test_pool)
    results = db.query({"limit": 9999})
    assert len(results) <= 1000


def test_query_group_by_date_trunc_alias(test_pool, seed_emails) -> None:
    db = MailDB._from_pool(test_pool)
    results = db.query(
        {
            "select": [
                {"date_trunc": "month", "field": "date", "as": "month"},
                {"count": "*", "as": "n"},
            ],
            "group_by": ["month"],
            "order_by": [{"field": "month", "dir": "asc"}],
        }
    )
    assert len(results) >= 1
    assert "month" in results[0]
    assert "n" in results[0]


def test_query_invalid_spec(test_pool, seed_emails) -> None:
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError):
        db.query({"from": "nonexistent"})


def test_query_serialization(test_pool, seed_emails) -> None:
    db = MailDB._from_pool(test_pool)
    results = db.query({"where": {"field": "sender_domain", "eq": "stripe.com"}})
    json_mod.dumps(results, default=str)  # Should not raise


# ---------------------------------------------------------------------------
# cluster() tests
# ---------------------------------------------------------------------------


def test_cluster_with_message_ids(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.cluster(
        message_ids=["adv-1@example.com", "adv-2@corp.com", "adv-3@corp.com", "adv-4@other.com"],
        limit=2,
    )
    assert len(results) == 2
    assert all(isinstance(e, Email) for e in results)


def test_cluster_with_where(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.cluster(where={"field": "sender_domain", "eq": "corp.com"}, limit=2)
    assert len(results) >= 1
    assert all(e.sender_domain == "corp.com" for e in results)


def test_cluster_fewer_than_limit(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.cluster(where={"field": "sender_address", "eq": "carol@other.com"}, limit=10)
    assert len(results) == 1


def test_cluster_requires_where_or_ids(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match=r"where.*message_ids"):
        db.cluster()


def test_cluster_rejects_both_where_and_ids(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match=r"where.*message_ids"):
        db.cluster(
            where={"field": "sender_domain", "eq": "corp.com"},
            message_ids=["adv-1@example.com"],
        )
