from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from maildb.models import Email, Recipients, SearchResult
from maildb.server import _serialize_email, _serialize_search_result, mcp


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
    }

    assert expected <= tool_names, f"Missing tools: {expected - tool_names}"
