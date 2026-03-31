from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from maildb.models import Email, Recipients, SearchResult
from maildb.server import (
    SERIALIZABLE_EMAIL_FIELDS,
    _serialize_email,
    _serialize_search_result,
    mcp,
)


def _make_email() -> Email:
    return Email(
        id=uuid4(),
        message_id="test@example.com",
        thread_id="test@example.com",
        subject="Test Subject",
        sender_name="Alice",
        sender_address="alice@example.com",
        sender_domain="example.com",
        recipients=Recipients(to=["bob@example.com"], cc=[], bcc=[]),
        date=datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
        body_text="Hello world",
        body_html=None,
        has_attachment=False,
        attachments=[],
        labels=["INBOX"],
        in_reply_to=None,
        references=[],
        embedding=[0.1] * 768,
        created_at=datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
    )


def test_serialize_email_converts_uuid_to_str() -> None:
    email = _make_email()
    d = _serialize_email(email)
    assert isinstance(d["id"], str)


def test_serialize_email_converts_datetime_to_iso() -> None:
    email = _make_email()
    d = _serialize_email(email)
    assert d["date"] == "2025-01-15T10:00:00+00:00"
    assert d["created_at"] == "2025-01-15T10:00:00+00:00"


def test_serialize_email_drops_embedding() -> None:
    email = _make_email()
    d = _serialize_email(email)
    assert "embedding" not in d


def test_serialize_email_drops_body_html() -> None:
    email = _make_email()
    email.body_html = "<p>Hello world</p>"
    d = _serialize_email(email)
    assert "body_html" not in d


def test_serialize_email_is_json_serializable() -> None:
    email = _make_email()
    d = _serialize_email(email)
    # Should not raise
    json.dumps(d)


def test_serialize_search_result() -> None:
    email = _make_email()
    sr = SearchResult(email=email, similarity=0.95)
    d = _serialize_search_result(sr)
    assert d["similarity"] == 0.95
    assert "embedding" not in d["email"]
    json.dumps(d)  # Should not raise


def test_serialize_email_with_fields_returns_only_requested() -> None:
    email = _make_email()
    d = _serialize_email(email, fields=frozenset({"subject", "date"}))
    assert set(d.keys()) == {"subject", "date"}
    assert d["subject"] == "Test Subject"


def test_serialize_email_with_fields_none_returns_all() -> None:
    email = _make_email()
    d = _serialize_email(email, fields=None)
    # Should have all default fields (no embedding, no body_html, no body_text)
    assert "subject" in d
    assert "sender_address" in d
    assert "body_length" in d
    assert "embedding" not in d
    assert "body_html" not in d
    assert "body_text" not in d


def test_serialize_email_with_invalid_field_ignores_it() -> None:
    email = _make_email()
    d = _serialize_email(email, fields=frozenset({"subject", "nonexistent_field"}))
    assert set(d.keys()) == {"subject"}


def test_serialize_search_result_with_fields() -> None:
    email = _make_email()
    sr = SearchResult(email=email, similarity=0.95)
    d = _serialize_search_result(sr, fields=frozenset({"subject", "date"}))
    assert d["similarity"] == 0.95
    assert set(d["email"].keys()) == {"subject", "date"}


def test_serializable_email_fields_constant() -> None:
    """SERIALIZABLE_EMAIL_FIELDS contains exactly the expected fields."""
    expected = {
        "id",
        "message_id",
        "thread_id",
        "subject",
        "sender_name",
        "sender_address",
        "sender_domain",
        "recipients",
        "date",
        "body_text",
        "body_length",
        "has_attachment",
        "attachments",
        "labels",
        "in_reply_to",
        "references",
        "created_at",
    }
    assert expected == SERIALIZABLE_EMAIL_FIELDS


def test_serialize_email_default_includes_body_length() -> None:
    email = _make_email()
    email.body_text = "Hello world"
    d = _serialize_email(email)
    assert d["body_length"] == 11
    assert "body_text" not in d


def test_serialize_email_default_null_body_length() -> None:
    email = _make_email()
    email.body_text = None
    d = _serialize_email(email)
    assert d["body_length"] is None
    assert "body_text" not in d


def test_serialize_email_explicit_fields_with_body_text() -> None:
    email = _make_email()
    email.body_text = "Hello world"
    d = _serialize_email(email, fields=frozenset({"subject", "body_text"}))
    assert d["body_text"] == "Hello world"
    assert "body_length" not in d


def test_serialize_email_explicit_fields_with_body_length() -> None:
    email = _make_email()
    email.body_text = "Hello world"
    d = _serialize_email(email, fields=frozenset({"subject", "body_length"}))
    assert d["body_length"] == 11
    assert "body_text" not in d


def test_mcp_has_all_tools() -> None:
    tool_names = set(mcp._tool_manager._tools.keys())

    expected = {
        "find",
        "search",
        "get_thread",
        "get_thread_for",
        "top_contacts",
        "topics_with",
        "unreplied",
        "long_threads",
        "correspondence",
        "mention_search",
        "query",
        "cluster",
    }

    assert expected <= tool_names, f"Missing tools: {expected - tool_names}"
