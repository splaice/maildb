# tests/test_scope.py
from __future__ import annotations

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
