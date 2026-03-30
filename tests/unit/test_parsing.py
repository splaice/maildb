# tests/unit/test_parsing.py
from __future__ import annotations

import mailbox as mb
from email.mime.text import MIMEText
from pathlib import Path

from maildb.parsing import parse_mbox, parse_message

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_parse_mbox_yields_all_messages() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    assert len(messages) == 10


def test_parse_message_extracts_message_id() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    assert messages[0]["message_id"] == "msg001@example.com"


def test_parse_message_strips_angle_brackets() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    assert "<" not in messages[0]["message_id"]
    assert ">" not in messages[0]["message_id"]


def test_parse_sender_fields() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[0]
    assert msg["sender_name"] == "Alice Smith"
    assert msg["sender_address"] == "alice@example.com"
    assert msg["sender_domain"] == "example.com"


def test_parse_recipients() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[0]
    assert "bob@example.com" in msg["recipients"]["to"]
    assert "carol@example.com" in msg["recipients"]["to"]
    assert "dave@example.com" in msg["recipients"]["cc"]


def test_parse_date_utc() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[0]
    assert msg["date"].year == 2025
    assert msg["date"].month == 1
    assert msg["date"].tzinfo is not None


def test_threading_root_message() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[0]
    assert msg["thread_id"] == msg["message_id"]


def test_threading_with_references() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[2]
    assert msg["thread_id"] == "msg001@example.com"


def test_threading_in_reply_to_only() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[7]
    assert msg["thread_id"] == "msg-proposal@example.com"


def test_html_only_body_extraction() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[3]
    assert msg["body_html"] is not None
    assert "Welcome" in msg["body_text"]
    assert "<html>" not in msg["body_text"]


def test_attachment_metadata() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[4]
    assert msg["has_attachment"] is True
    assert len(msg["attachments"]) == 1
    assert msg["attachments"][0]["filename"] == "q1-report.pdf"
    assert msg["attachments"][0]["content_type"] == "application/pdf"


def test_missing_subject() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[5]
    assert msg["subject"] is None


def test_multipart_alternative_prefers_plain() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[9]
    assert "This week in tech" in msg["body_text"]
    assert "<html>" not in msg["body_text"]


def test_body_cleaning_applied() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[1]
    assert ">" not in msg["body_text"]
    assert "Bob Jones" not in msg["body_text"]
    assert "spreadsheet" in msg["body_text"]


def test_timezone_naive_date_becomes_utc() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[8]
    assert msg["date"].tzinfo is not None


def test_in_reply_to_stripped() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[1]
    assert msg["in_reply_to"] == "msg001@example.com"


def test_references_parsed_as_list() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[2]
    assert isinstance(msg["references"], list)
    assert len(msg["references"]) == 2
    assert msg["references"][0] == "msg001@example.com"


def test_attachment_metadata_includes_bytes() -> None:
    """_extract_attachments should return raw bytes in a 'data' key."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[4]  # msg005 — has PDF attachment
    # The _attachments_with_data should have bytes
    assert "_attachments_with_data" in msg
    for att in msg["_attachments_with_data"]:
        assert "data" in att
        assert isinstance(att["data"], bytes)
        assert len(att["data"]) == att["size"]


def test_attachments_metadata_no_bytes() -> None:
    """The 'attachments' key should NOT contain raw data bytes."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[4]  # msg005 — has PDF attachment
    for att in msg["attachments"]:
        assert "data" not in att


def test_gmail_labels_extraction() -> None:
    """Messages with X-Gmail-Labels header should have labels extracted."""
    msg = MIMEText("Test body")
    msg["Message-ID"] = "<gmail-labels-test@example.com>"
    msg["From"] = "test@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Gmail Labels Test"
    msg["Date"] = "Mon, 10 Mar 2025 10:00:00 +0000"
    msg["X-Gmail-Labels"] = "Inbox,Important,Starred"

    mbox_msg = mb.mboxMessage(msg)
    result = parse_message(mbox_msg)
    assert result is not None
    assert result["labels"] == ["Inbox", "Important", "Starred"]


def test_no_gmail_labels_returns_empty() -> None:
    """Messages without X-Gmail-Labels should have empty labels list."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[0]  # msg001 — no Gmail labels
    assert msg["labels"] == []


def test_recipients_filters_empty_addresses() -> None:
    msg = MIMEText("body")
    msg["Message-ID"] = "<filter-empty@example.com>"
    msg["From"] = "test@example.com"
    msg["To"] = "valid@example.com, , "
    msg["Date"] = "Mon, 10 Mar 2025 10:00:00 +0000"
    result = parse_message(mb.mboxMessage(msg))
    assert result is not None
    assert "" not in result["recipients"]["to"]
    assert all(isinstance(addr, str) for addr in result["recipients"]["to"])


def test_recipients_structure_always_has_keys() -> None:
    msg = MIMEText("body")
    msg["Message-ID"] = "<struct-test@example.com>"
    msg["From"] = "test@example.com"
    msg["Date"] = "Mon, 10 Mar 2025 10:00:00 +0000"
    result = parse_message(mb.mboxMessage(msg))
    assert result is not None
    r = result["recipients"]
    assert isinstance(r, dict)
    assert set(r.keys()) == {"to", "cc", "bcc"}
    assert isinstance(r["to"], list)
    assert isinstance(r["cc"], list)
    assert isinstance(r["bcc"], list)


def test_recipients_filters_none_like_addresses() -> None:
    msg = MIMEText("body")
    msg["Message-ID"] = "<none-addr@example.com>"
    msg["From"] = "test@example.com"
    msg["To"] = "valid@example.com"
    msg["Cc"] = "   ,  "
    msg["Date"] = "Mon, 10 Mar 2025 10:00:00 +0000"
    result = parse_message(mb.mboxMessage(msg))
    assert result is not None
    assert all(addr.strip() for addr in result["recipients"]["cc"])
