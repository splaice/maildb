# tests/unit/test_parsing.py
from __future__ import annotations

from pathlib import Path

from maildb.parsing import parse_mbox

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
