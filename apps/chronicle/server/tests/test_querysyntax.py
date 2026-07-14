# tests/test_querysyntax.py
from __future__ import annotations

import pytest

from chronicle_server.querysyntax import parse_query


def test_empty_and_whitespace() -> None:
    for raw in ("", "   ", "\t\n"):
        p = parse_query(raw)
        assert p.free_text == ""
        assert p.scope_updates == {} or p.scope_updates.get("free_text") in (None, "")
        assert p.unsupported == []


def test_from_operator() -> None:
    p = parse_query("from:alice@example.com")
    assert p.scope_updates["senders"] == ["alice@example.com"]
    assert p.free_text == ""


def test_to_operator() -> None:
    p = parse_query("to:bob@example.com")
    assert p.scope_updates["recipients"] == ["bob@example.com"]


def test_participant_operator() -> None:
    p = parse_query("participant:carol@example.com")
    assert p.scope_updates["participants"] == ["carol@example.com"]


def test_subject_operator() -> None:
    p = parse_query("subject:invoice")
    assert p.scope_updates["subject_contains"] == "invoice"


def test_subject_quoted() -> None:
    p = parse_query('subject:"final estimate"')
    assert p.scope_updates["subject_contains"] == "final estimate"
    assert p.free_text == ""


def test_after_before_iso() -> None:
    p = parse_query("after:2015-01-01 before:2018-12-31")
    assert p.scope_updates["date"]["from"] == "2015-01-01"
    assert p.scope_updates["date"]["to"] == "2018-12-31"


def test_on_expands_to_one_day_range() -> None:
    p = parse_query("on:2015-06-17")
    assert p.scope_updates["date"]["from"] == "2015-06-17"
    assert p.scope_updates["date"]["to"] == "2015-06-18"


def test_mailbox_operator() -> None:
    p = parse_query("mailbox:me@example.com")
    assert p.scope_updates["mailboxes"] == ["me@example.com"]


def test_filetype_and_filename() -> None:
    p = parse_query("filetype:pdf filename:invoice.pdf")
    assert p.scope_updates["file_types"] == ["pdf"]
    assert p.scope_updates["filenames"] == ["invoice.pdf"]


def test_has_attachment() -> None:
    p = parse_query("has:attachment")
    assert p.scope_updates["has_attachment"] is True


def test_has_failed_extraction_unsupported() -> None:
    p = parse_query("has:failed-extraction")
    assert "has_attachment" not in p.scope_updates
    assert any("failed-extraction" in u for u in p.unsupported)


def test_is_message_and_attachment() -> None:
    p = parse_query("is:message is:attachment")
    assert p.scope_updates["source_types"] == ["message", "attachment"]


def test_is_thread_unsupported() -> None:
    p = parse_query("is:thread")
    assert "source_types" not in p.scope_updates
    assert any("is:thread" in u for u in p.unsupported)


def test_unsupported_topic_person_organization_domain() -> None:
    p = parse_query(
        "topic:renovation person:alice organization:acme domain:example.com leftover words"
    )
    assert len(p.unsupported) == 4
    assert all(
        any(op in u for u in p.unsupported)
        for op in ("topic:", "person:", "organization:", "domain:")
    )
    assert p.free_text == "leftover words"
    assert "topic" not in p.scope_updates
    assert "person" not in p.scope_updates


def test_negated_topic_unsupported() -> None:
    p = parse_query("-topic:newsletter")
    assert p.unsupported
    assert any("topic" in u for u in p.unsupported)
    assert p.free_text == ""


def test_other_negations_unsupported() -> None:
    p = parse_query("-from:alice@example.com")
    assert p.unsupported
    assert "senders" not in p.scope_updates


def test_unknown_operator_as_plain_text() -> None:
    p = parse_query("foo:bar hello")
    assert "foo:bar" in p.free_text
    assert "hello" in p.free_text
    # not treated as unsupported — plain text
    assert p.unsupported == []


def test_combined_query_residual_free_text() -> None:
    p = parse_query("from:alice@example.com filetype:pdf after:2015-01-01 roof material decision")
    assert p.scope_updates["senders"] == ["alice@example.com"]
    assert p.scope_updates["file_types"] == ["pdf"]
    assert p.scope_updates["date"]["from"] == "2015-01-01"
    assert p.free_text == "roof material decision"
    assert p.scope_updates["free_text"] == "roof material decision"


def test_free_text_never_contains_extracted_operators() -> None:
    """Property: extracted operator tokens do not appear in free_text."""
    cases = [
        "from:a@b.com hello",
        'subject:"final estimate" world',
        "after:2015-01-01 before:2016-01-01 x",
        "on:2020-01-01 y",
        "mailbox:m@x.com z",
        "has:attachment find this",
        "is:message body words",
        "participant:p@x.com cc leftover",
        "to:t@x.com filename:a.pdf words",
        "topic:skip person:skip real text",
        "-topic:news keep me",
    ]
    operator_prefixes = (
        "from:",
        "to:",
        "participant:",
        "subject:",
        "after:",
        "before:",
        "on:",
        "mailbox:",
        "filetype:",
        "filename:",
        "has:",
        "is:",
        "topic:",
        "person:",
        "organization:",
        "domain:",
        "-topic:",
    )
    for raw in cases:
        p = parse_query(raw)
        for token in p.free_text.split():
            assert not any(token.startswith(op) and op in raw for op in operator_prefixes), (
                f"free_text still has operator token {token!r} from {raw!r}"
            )


def test_never_throws_on_garbage() -> None:
    garbage = [
        "::::",
        "from:",
        'subject:"unclosed',
        "-:-",
        "has:",
        "is:",
        "\x00\x01",
        "a" * 10_000,
        'subject:"final\\"estimate"',
    ]
    for raw in garbage:
        p = parse_query(raw)
        assert isinstance(p.free_text, str)
        assert isinstance(p.unsupported, list)
        assert isinstance(p.scope_updates, dict)


def test_multiple_from_accumulate() -> None:
    p = parse_query("from:a@x.com from:b@x.com")
    assert p.scope_updates["senders"] == ["a@x.com", "b@x.com"]


@pytest.mark.parametrize(
    ("raw", "expected_ft"),
    [
        ("plain words only", "plain words only"),
        ("from:a@b.com", ""),
        ("topic:x rest", "rest"),
    ],
)
def test_free_text_parametrized(raw: str, expected_ft: str) -> None:
    assert parse_query(raw).free_text == expected_ft
