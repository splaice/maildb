# tests/test_redact.py
from __future__ import annotations

import pytest

from chronicle_server.redact import (
    apply_redactions,
    detect_pii,
    redact_text,
    redact_workspace_copy,
    scan_workspace_pii,
)


@pytest.mark.parametrize(
    ("text", "kind", "must_contain"),
    [
        ("Contact me at alice@example.com please", "email", "alice@example.com"),
        ("Call +1 (415) 555-0100 today", "phone", "415"),
        ("Visit 123 Main St for pickup", "street_address", "123 Main St"),
        ("Account 123456789012 is closed", "account_number", "123456789012"),
    ],
)
def test_detect_pii_kinds(text: str, kind: str, must_contain: str) -> None:
    matches = detect_pii(text, kinds=[kind])
    assert matches, f"expected {kind} in {text!r}"
    assert all(m["kind"] == kind for m in matches)
    assert any(must_contain in m["value"] for m in matches)
    for m in matches:
        assert text[m["start"] : m["end"]] == m["value"]


def test_detect_email_and_phone_together() -> None:
    text = "bob@corp.test or +44 20 7946 0958"
    matches = detect_pii(text)
    kinds = {m["kind"] for m in matches}
    assert "email" in kinds
    assert "phone" in kinds


def test_custom_terms() -> None:
    text = "Project Codename Nightingale is secret"
    matches = detect_pii(text, kinds=[], custom_terms=["Nightingale"])
    assert len(matches) == 1
    assert matches[0]["kind"] == "custom"
    assert matches[0]["value"].lower() == "nightingale"


def test_account_false_positives_years_and_dates() -> None:
    """Years and date-like tokens must not match as account numbers."""
    samples = [
        "The year 2015 was busy",
        "Meeting on 2015-06-01",
        "ISO date 2015/06/01",
        "Compact date 20150601 in a form",
        "Short ref 1234567",  # 7 digits — below 8
    ]
    for text in samples:
        matches = detect_pii(text, kinds=["account_number"])
        assert matches == [], f"unexpected account match in {text!r}: {matches}"


def test_account_true_positive_long_digit_run() -> None:
    text = "Wire to 98765432109876 only"
    matches = detect_pii(text, kinds=["account_number"])
    assert len(matches) == 1
    assert matches[0]["value"] == "98765432109876"


def test_apply_redactions_placeholder() -> None:
    text = "Email alice@example.com now"
    matches = detect_pii(text, kinds=["email"])
    out = apply_redactions(text, matches)
    assert "alice@example.com" not in out
    assert "[REDACTED:email]" in out


def test_redact_text_roundtrip() -> None:
    text = "a@b.co and 12345678901234"
    out, matches = redact_text(text)
    assert len(matches) >= 2
    assert "a@b.co" not in out
    assert "[REDACTED:email]" in out
    assert "[REDACTED:account_number]" in out


def test_scan_and_redact_workspace_do_not_mutate_originals() -> None:
    workspace = {
        "name": "Case",
        "description": "Call alice@example.com",
    }
    blocks = [
        {
            "id": "b1",
            "block_type": "note",
            "content": {"text": "Account 11223344556677"},
        },
        {
            "id": "b2",
            "block_type": "pin",
            "content": {
                "source_id": "msg_1",
                "title": "Hi",
                "sender": "Bob",
                "excerpt": "meet at 42 Oak Ave",
            },
        },
    ]
    original_desc = workspace["description"]
    original_note = blocks[0]["content"]["text"]
    original_excerpt = blocks[1]["content"]["excerpt"]

    counts, samples, by_source = scan_workspace_pii(blocks, workspace)
    assert counts.get("email", 0) >= 1
    assert counts.get("account_number", 0) >= 1
    assert counts.get("street_address", 0) >= 1
    assert samples
    assert by_source

    # originals untouched after scan
    assert workspace["description"] == original_desc
    assert blocks[0]["content"]["text"] == original_note

    ws2, blks2, rcounts, rsrc = redact_workspace_copy(blocks, workspace)
    assert rcounts
    assert rsrc
    assert "[REDACTED:email]" in str(ws2.get("description"))
    assert "[REDACTED:account_number]" in blks2[0]["content"]["text"]
    assert "[REDACTED:street_address]" in blks2[1]["content"]["excerpt"]

    # originals still identical
    assert workspace["description"] == original_desc
    assert blocks[0]["content"]["text"] == original_note
    assert blocks[1]["content"]["excerpt"] == original_excerpt


def test_overlap_prefers_non_overlapping() -> None:
    text = "x" * 20
    # force custom overlapping terms
    matches = detect_pii(text, kinds=[], custom_terms=["xxxxx", "xx"])
    # should not stack overlapping spans
    cursor = -1
    for m in matches:
        assert m["start"] >= cursor
        cursor = m["end"]
