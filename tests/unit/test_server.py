from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from maildb import server
from maildb.models import (
    AccountSummary,
    AttachmentChunk,
    AttachmentSearchResult,
    Email,
    ImportRecord,
    Recipients,
    SearchResult,
)
from maildb.server import (
    SERIALIZABLE_EMAIL_FIELDS,
    _serialize_email,
    _serialize_search_result,
    _wrap_response,
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
        source_account=None,
        import_id=None,
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
        "body_truncated",
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


def test_serialize_email_body_max_chars_truncates() -> None:
    email = _make_email()
    email.body_text = "Hello world, this is a long email body"
    d = _serialize_email(
        email, fields=frozenset({"body_text", "body_truncated"}), body_max_chars=11
    )
    assert d["body_text"] == "Hello world..."
    assert d["body_truncated"] is True


def test_serialize_email_body_max_chars_no_truncation_needed() -> None:
    email = _make_email()
    email.body_text = "Short"
    d = _serialize_email(
        email, fields=frozenset({"body_text", "body_truncated"}), body_max_chars=100
    )
    assert d["body_text"] == "Short"
    assert "body_truncated" not in d


def test_serialize_email_body_max_chars_null_body() -> None:
    email = _make_email()
    email.body_text = None
    d = _serialize_email(email, fields=frozenset({"body_text"}), body_max_chars=10)
    assert d["body_text"] is None


def test_wrap_response() -> None:
    results = [{"a": 1}, {"a": 2}]
    wrapped = _wrap_response(results, total=10, offset=0, limit=50)
    assert wrapped == {"total": 10, "offset": 0, "limit": 50, "results": results}


def test_wrap_response_empty() -> None:
    wrapped = _wrap_response([], total=0, offset=0, limit=50)
    assert wrapped == {"total": 0, "offset": 0, "limit": 50, "results": []}


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
        "get_emails",
    }

    assert expected <= tool_names, f"Missing tools: {expected - tool_names}"


def test_find_passes_account_to_db() -> None:
    mock_db = MagicMock()
    mock_db.find.return_value = ([], 0)
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db

    server.find(ctx, account="you@example.com")
    kwargs = mock_db.find.call_args.kwargs
    assert kwargs["account"] == "you@example.com"


def test_accounts_tool_serializes_summaries() -> None:
    mock_db = MagicMock()
    mock_db.accounts.return_value = [
        AccountSummary(
            source_account="a@example.com",
            email_count=10,
            first_date=datetime(2026, 1, 1, tzinfo=UTC),
            last_date=datetime(2026, 4, 1, tzinfo=UTC),
            import_count=2,
        ),
    ]
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db

    result = server.accounts(ctx)
    assert isinstance(result, list)
    assert result[0]["source_account"] == "a@example.com"
    assert result[0]["email_count"] == 10
    assert result[0]["first_date"].startswith("2026-01")


def test_import_history_tool() -> None:
    mock_db = MagicMock()
    iid = uuid4()
    mock_db.import_history.return_value = [
        ImportRecord(
            id=iid,
            source_account="a@example.com",
            source_file="x.mbox",
            started_at=datetime(2026, 4, 16, tzinfo=UTC),
            completed_at=None,
            messages_total=0,
            messages_inserted=0,
            messages_skipped=0,
            status="running",
        ),
    ]
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db

    result = server.import_history(ctx)
    assert result[0]["id"] == str(iid)
    assert result[0]["status"] == "running"


def test_server_has_new_attachment_tools() -> None:
    names = set(mcp._tool_manager._tools.keys())
    assert {"search_attachments", "search_all", "get_attachment_markdown"} <= names


def test_search_attachments_tool_serializes() -> None:
    mock_db = MagicMock()
    mock_db.search_attachments.return_value = (
        [
            AttachmentSearchResult(
                attachment_id=1,
                filename="a.pdf",
                content_type="application/pdf",
                sha256="abc",
                chunk=AttachmentChunk(
                    id=10,
                    attachment_id=1,
                    chunk_index=0,
                    heading_path="Overview",
                    page_number=3,
                    token_count=5,
                    text="hi",
                ),
                emails=["<x@y.com>"],
                similarity=0.95,
            )
        ],
        1,
    )
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db

    result = server.search_attachments(ctx, query="anything")
    assert result["total"] == 1
    hit = result["results"][0]
    assert hit["attachment_id"] == 1
    assert hit["chunk"]["text"] == "hi"
    assert hit["emails"] == ["<x@y.com>"]


def test_get_attachment_markdown_tool_returns_null_for_missing() -> None:
    mock_db = MagicMock()
    mock_db.get_attachment_markdown.return_value = None
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db
    assert server.get_attachment_markdown(ctx, attachment_id=1) is None


def test_search_all_passes_recipient_count_filters_through() -> None:
    mock_db = MagicMock()
    mock_db.search_all.return_value = ([], 0)
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db
    server.search_all(
        ctx, query="x",
        max_to=1, max_cc=0, max_recipients=3, direct_only=True,
    )
    kwargs = mock_db.search_all.call_args.kwargs
    assert kwargs["max_to"] == 1
    assert kwargs["max_cc"] == 0
    assert kwargs["max_recipients"] == 3
    assert kwargs["direct_only"] is True
