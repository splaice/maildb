# tests/test_scope.py
from __future__ import annotations

import json

from chronicle_server.scope import DateRange, QueryScope, scope_filters, scope_fingerprint


def test_scope_filters_empty() -> None:
    conditions, params = scope_filters(QueryScope())
    assert conditions == []
    assert params == {}


def test_scope_filters_each_field() -> None:
    scope = QueryScope(
        date=DateRange(**{"from": "2020-01-01", "to": "2021-01-01"}),
        mailboxes=["acct@example.com"],
        senders=["alice@example.com"],
    )
    conditions, params = scope_filters(scope)

    assert conditions == [
        "date >= %(scope_from)s",
        "date < %(scope_to)s",
        "source_account = ANY(%(mailboxes)s)",
        "sender_address = ANY(%(senders)s)",
    ]
    assert params == {
        "scope_from": "2020-01-01",
        "scope_to": "2021-01-01",
        "mailboxes": ["acct@example.com"],
        "senders": ["alice@example.com"],
    }


def test_scope_filters_partial_date_from_only() -> None:
    scope = QueryScope(date=DateRange(**{"from": "2015-06-01"}))
    conditions, params = scope_filters(scope)
    assert conditions == ["date >= %(scope_from)s"]
    assert params == {"scope_from": "2015-06-01"}


def test_scope_filters_empty_lists_omit_conditions() -> None:
    scope = QueryScope(mailboxes=[], senders=[])
    conditions, params = scope_filters(scope)
    assert conditions == []
    assert params == {}


def test_scope_fingerprint_stable_under_key_reorder() -> None:
    a = QueryScope.model_validate(
        {
            "version": 1,
            "mailboxes": ["a@x.com", "b@x.com"],
            "senders": ["s@x.com"],
            "date": {"from": "2010-01-01", "to": "2020-01-01"},
        }
    )
    b = QueryScope.model_validate(
        {
            "date": {"to": "2020-01-01", "from": "2010-01-01"},
            "senders": ["s@x.com"],
            "mailboxes": ["a@x.com", "b@x.com"],
            "version": 1,
        }
    )
    assert scope_fingerprint(a) == scope_fingerprint(b)
    assert scope_fingerprint(a).startswith("qs_")
    assert len(scope_fingerprint(a)) == len("qs_") + 16


def test_scope_fingerprint_changes_when_filter_changes() -> None:
    base = QueryScope(mailboxes=["a@x.com"])
    other = QueryScope(mailboxes=["b@x.com"])
    assert scope_fingerprint(base) != scope_fingerprint(other)

    with_sender = QueryScope(mailboxes=["a@x.com"], senders=["s@x.com"])
    assert scope_fingerprint(base) != scope_fingerprint(with_sender)


# --- v2 fields ---


def test_scope_filters_recipients_gin_containment() -> None:
    scope = QueryScope(recipients=["bob@example.com"])
    conditions, params = scope_filters(scope)
    assert len(conditions) == 1
    cond = conditions[0]
    assert "recipients @> jsonb_build_object('to'" in cond
    assert "recipients @> jsonb_build_object('cc'" in cond
    assert "recipients @> jsonb_build_object('bcc'" in cond
    assert params["recipient_arr_0"] == json.dumps(["bob@example.com"])


def test_scope_filters_recipients_multiple_or() -> None:
    scope = QueryScope(recipients=["a@x.com", "b@x.com"])
    conditions, params = scope_filters(scope)
    assert len(conditions) == 1
    assert " OR " in conditions[0]
    assert "recipient_arr_0" in params
    assert "recipient_arr_1" in params


def test_scope_filters_participants_sender_or_recipient() -> None:
    scope = QueryScope(participants=["alice@example.com"])
    conditions, params = scope_filters(scope)
    assert len(conditions) == 1
    cond = conditions[0]
    assert "sender_address =" in cond
    assert "recipients @>" in cond
    assert params["participant_sender_0"] == "alice@example.com"
    assert params["participant_arr_0"] == json.dumps(["alice@example.com"])


def test_scope_filters_subject_contains_escaped() -> None:
    scope = QueryScope(subject_contains="100%_done\\yes")
    conditions, params = scope_filters(scope)
    assert conditions == ["subject ILIKE %(subject_pattern)s ESCAPE '\\'"]
    # %, _, \ escaped for LIKE
    assert params["subject_pattern"] == r"%100\%\_done\\yes%"


def test_scope_filters_has_attachment() -> None:
    scope = QueryScope(has_attachment=True)
    conditions, params = scope_filters(scope)
    assert conditions == ["has_attachment = %(has_attachment)s"]
    assert params == {"has_attachment": True}

    scope_f = QueryScope(has_attachment=False)
    conditions_f, params_f = scope_filters(scope_f)
    assert params_f["has_attachment"] is False


def test_scope_filters_v2_compose_with_v1() -> None:
    scope = QueryScope(
        mailboxes=["acct@x.com"],
        senders=["s@x.com"],
        recipients=["r@x.com"],
        has_attachment=True,
        subject_contains="invoice",
    )
    conditions, params = scope_filters(scope)
    assert "source_account = ANY(%(mailboxes)s)" in conditions
    assert "sender_address = ANY(%(senders)s)" in conditions
    assert any("recipients @>" in c for c in conditions)
    assert "has_attachment = %(has_attachment)s" in conditions
    assert any("subject ILIKE" in c for c in conditions)
    assert params["mailboxes"] == ["acct@x.com"]
    assert params["has_attachment"] is True


def test_scope_fingerprint_changes_with_v2_fields() -> None:
    base = QueryScope(mailboxes=["a@x.com"])
    with_rcpt = QueryScope(mailboxes=["a@x.com"], recipients=["r@x.com"])
    assert scope_fingerprint(base) != scope_fingerprint(with_rcpt)

    with_subj = QueryScope(mailboxes=["a@x.com"], subject_contains="hi")
    assert scope_fingerprint(base) != scope_fingerprint(with_subj)

    with_att = QueryScope(mailboxes=["a@x.com"], has_attachment=True)
    assert scope_fingerprint(base) != scope_fingerprint(with_att)

    with_ft = QueryScope(mailboxes=["a@x.com"], free_text="roof")
    assert scope_fingerprint(base) != scope_fingerprint(with_ft)


def test_scope_v2_defaults_leave_v1_callers() -> None:
    scope = QueryScope(mailboxes=["a@x.com"])
    assert scope.recipients == []
    assert scope.participants == []
    assert scope.subject_contains is None
    assert scope.has_attachment is None
    assert scope.file_types == []
    assert scope.filenames == []
    assert scope.source_types == []
    assert scope.free_text is None
