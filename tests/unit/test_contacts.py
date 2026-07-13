# tests/unit/test_contacts.py
from __future__ import annotations

from maildb.contacts import (
    WEIGHT_DEEP_THREADS,
    WEIGHT_ORG_NAME,
    WEIGHT_REPLIED_THREADS,
    WEIGHT_SHALLOW_THREADS,
    _compute_probability,
)


def _base(**kwargs):  # type: ignore[no-untyped-def]
    defaults = {
        "messages_from": 5,
        "messages_to": 0,
        "name_variants": [],
        "addresses": ["alice@example.com"],
    }
    defaults.update(kwargs)
    return _compute_probability(**defaults)


def test_defaults_fire_no_new_signals() -> None:
    _, signals = _base()
    assert "replied_threads" not in signals
    assert "shallow_threads" not in signals
    assert "deep_threads" not in signals
    assert "org_name" not in signals


def test_replied_threads_signal() -> None:
    _, signals = _base(replied_thread_ratio=0.3)
    assert signals["replied_threads"] == WEIGHT_REPLIED_THREADS

    _, below = _base(replied_thread_ratio=0.29)
    assert "replied_threads" not in below

    _, none = _base(replied_thread_ratio=None)
    assert "replied_threads" not in none


def test_shallow_threads_signal() -> None:
    _, signals = _base(messages_from=10, avg_thread_depth=1.4)
    assert signals["shallow_threads"] == WEIGHT_SHALLOW_THREADS

    # Needs messages_from >= 10
    _, low_volume = _base(messages_from=9, avg_thread_depth=1.0)
    assert "shallow_threads" not in low_volume

    # Depth must be < 1.5
    _, not_shallow = _base(messages_from=10, avg_thread_depth=1.5)
    assert "shallow_threads" not in not_shallow

    _, none = _base(messages_from=10, avg_thread_depth=None)
    assert "shallow_threads" not in none


def test_deep_threads_signal() -> None:
    _, signals = _base(avg_thread_depth=3.0)
    assert signals["deep_threads"] == WEIGHT_DEEP_THREADS

    _, below = _base(avg_thread_depth=2.9)
    assert "deep_threads" not in below

    _, none = _base(avg_thread_depth=None)
    assert "deep_threads" not in none


def test_shallow_and_deep_mutually_exclusive_by_thresholds() -> None:
    """< 1.5 vs >= 3.0 — both checks run independently; mid-range fires neither."""
    _, mid = _base(messages_from=20, avg_thread_depth=2.0)
    assert "shallow_threads" not in mid
    assert "deep_threads" not in mid


def test_org_name_hits() -> None:
    for name in ("Acme Support", "no-reply", "Billing Team", "Customer Care Desk"):
        _, signals = _base(name_variants=[name])
        assert signals.get("org_name") == WEIGHT_ORG_NAME, f"expected hit for {name!r}"


def test_org_name_misses() -> None:
    for name in ("Sam Poole", "Alice Smith", "Jane"):
        _, signals = _base(name_variants=[name])
        assert "org_name" not in signals, f"unexpected hit for {name!r}"
