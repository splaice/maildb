# tests/unit/test_models.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from maildb.models import (
    AccountSummary,
    Attachment,
    Email,
    ImportRecord,
    Recipients,
    SearchResult,
)


def test_recipients_from_dict() -> None:
    data = {"to": ["a@x.com"], "cc": ["b@x.com"], "bcc": []}
    r = Recipients(to=data["to"], cc=data["cc"], bcc=data["bcc"])
    assert r.to == ["a@x.com"]
    assert r.cc == ["b@x.com"]
    assert r.bcc == []


def test_attachment_fields() -> None:
    a = Attachment(filename="doc.pdf", content_type="application/pdf", size=1024)
    assert a.filename == "doc.pdf"
    assert a.size == 1024


def _make_row() -> dict:
    return {
        "id": uuid4(),
        "message_id": "abc@example.com",
        "thread_id": "abc@example.com",
        "subject": "Test",
        "sender_name": "Alice",
        "sender_address": "alice@example.com",
        "sender_domain": "example.com",
        "recipients": json.dumps({"to": ["bob@example.com"], "cc": [], "bcc": []}),
        "date": datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
        "body_text": "Hello",
        "body_html": None,
        "has_attachment": False,
        "attachments": json.dumps([]),
        "labels": ["INBOX"],
        "in_reply_to": None,
        "references": [],
        "embedding": None,
        "created_at": datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
    }


def test_email_from_row() -> None:
    row = _make_row()
    email = Email.from_row(row)
    assert email.message_id == "abc@example.com"
    assert email.sender_name == "Alice"
    assert email.recipients is not None
    assert email.recipients.to == ["bob@example.com"]
    assert email.attachments == []
    assert email.has_attachment is False


def test_email_from_row_with_attachments() -> None:
    row = _make_row()
    row["has_attachment"] = True
    row["attachments"] = json.dumps(
        [{"filename": "file.pdf", "content_type": "application/pdf", "size": 500}]
    )
    email = Email.from_row(row)
    assert len(email.attachments) == 1
    assert email.attachments[0].filename == "file.pdf"


def test_email_from_row_null_recipients() -> None:
    row = _make_row()
    row["recipients"] = None
    email = Email.from_row(row)
    assert email.recipients is None


def test_search_result() -> None:
    row = _make_row()
    email = Email.from_row(row)
    sr = SearchResult(email=email, similarity=0.95)
    assert sr.similarity == 0.95
    assert sr.email.subject == "Test"


def test_email_includes_source_account_and_import_id():
    eid = uuid4()
    iid = uuid4()
    row = {
        "id": eid,
        "message_id": "<msg-1@example.com>",
        "thread_id": "thread-1",
        "subject": "Hello",
        "sender_name": "Alice",
        "sender_address": "alice@example.com",
        "sender_domain": "example.com",
        "recipients": None,
        "date": datetime(2026, 4, 16, tzinfo=UTC),
        "body_text": "hi",
        "body_html": None,
        "has_attachment": False,
        "attachments": None,
        "labels": None,
        "in_reply_to": None,
        "references": None,
        "embedding": None,
        "created_at": datetime(2026, 4, 16, tzinfo=UTC),
        "source_account": "you@example.com",
        "import_id": iid,
    }
    email = Email.from_row(row)
    assert email.source_account == "you@example.com"
    assert email.import_id == iid


def test_email_defaults_when_columns_missing():
    """Backwards compat: from_row with no source_account/import_id keys."""
    row = {
        "id": uuid4(),
        "message_id": "<msg-2@example.com>",
        "thread_id": "thread-2",
        "subject": None,
        "sender_name": None,
        "sender_address": None,
        "sender_domain": None,
        "recipients": None,
        "date": None,
        "body_text": None,
        "body_html": None,
        "has_attachment": False,
        "attachments": None,
        "labels": None,
        "in_reply_to": None,
        "references": None,
        "embedding": None,
        "created_at": datetime(2026, 4, 16, tzinfo=UTC),
    }
    email = Email.from_row(row)
    assert email.source_account is None
    assert email.import_id is None


def test_account_summary_dataclass_shape():
    s = AccountSummary(
        source_account="you@example.com",
        email_count=10,
        first_date=None,
        last_date=None,
        import_count=2,
    )
    assert s.source_account == "you@example.com"
    assert s.email_count == 10


def test_import_record_dataclass_shape():
    r = ImportRecord(
        id=uuid4(),
        source_account="you@example.com",
        source_file="x.mbox",
        started_at=datetime.now(UTC),
        completed_at=None,
        messages_total=0,
        messages_inserted=0,
        messages_skipped=0,
        status="running",
    )
    assert r.status == "running"


def test_attachment_chunk_dataclass_shape() -> None:
    from maildb.models import AttachmentChunk
    c = AttachmentChunk(
        id=1,
        attachment_id=10,
        chunk_index=0,
        heading_path="Overview > Payment Terms",
        page_number=3,
        token_count=250,
        text="Late fees apply after 30 days.",
    )
    assert c.token_count == 250
    assert c.heading_path == "Overview > Payment Terms"


def test_attachment_search_result_shape() -> None:
    from maildb.models import AttachmentChunk, AttachmentSearchResult
    chunk = AttachmentChunk(
        id=1,
        attachment_id=10,
        chunk_index=0,
        heading_path=None,
        page_number=None,
        token_count=5,
        text="hi",
    )
    r = AttachmentSearchResult(
        attachment_id=10,
        filename="x.pdf",
        content_type="application/pdf",
        sha256="aa",
        chunk=chunk,
        emails=["<a@b.com>"],
        similarity=0.87,
    )
    assert r.similarity == 0.87
    assert r.emails == ["<a@b.com>"]


def test_unified_search_result_either_branch() -> None:
    from maildb.models import UnifiedSearchResult
    email_side = UnifiedSearchResult(
        source="email", similarity=0.9, email=None, attachment_result=None,
    )
    assert email_side.source == "email"
    attach_side = UnifiedSearchResult(
        source="attachment", similarity=0.7, email=None, attachment_result=None,
    )
    assert attach_side.source == "attachment"
