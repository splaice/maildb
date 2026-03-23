# tests/unit/test_models.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from maildb.models import Attachment, Email, Recipients, SearchResult


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
    row["attachments"] = json.dumps([
        {"filename": "file.pdf", "content_type": "application/pdf", "size": 500}
    ])
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
