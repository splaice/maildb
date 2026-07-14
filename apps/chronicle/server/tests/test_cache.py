# tests/test_cache.py
"""Data-versioned response cache unit + endpoint integration (task 5.4b)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from chronicle_server.cache import (
    CACHE_MAX_ROWS,
    cache_key,
    cached,
    data_version,
    emails_data_version,
)
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def _clear_cache(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM app_cache")
        conn.commit()


def _insert_email(pool: ConnectionPool, *, subject: str = "cache-bust") -> None:
    email_id = uuid4()
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO emails (
                id, message_id, thread_id, subject,
                sender_name, sender_address, sender_domain,
                recipients, date, body_text, body_html,
                has_attachment, labels, source_account, created_at
            ) VALUES (
                %(id)s, %(mid)s, %(tid)s, %(subject)s,
                'Cache', 'cache@example.com', 'example.com',
                '{}'::jsonb, %(date)s, 'body', NULL,
                false, %(labels)s, 'cache@example.com', now()
            )
            """,
            {
                "id": email_id,
                "mid": f"<cache-{email_id}@example.com>",
                "tid": f"thread-cache-{email_id}",
                "subject": subject,
                "date": datetime(2020, 6, 15, 12, 0, tzinfo=UTC),
                "labels": ["INBOX"],
            },
        )
        conn.commit()


def _cleanup_cache_emails(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM emails WHERE message_id LIKE '<cache-%%@example.com>'")
        conn.commit()


# --- unit: cache primitives ---


def test_cache_key_stability() -> None:
    a = cache_key("buckets", {"viewport": {"from": "2020-01-01", "to": "2021-01-01"}, "n": 1})
    b = cache_key("buckets", {"n": 1, "viewport": {"to": "2021-01-01", "from": "2020-01-01"}})
    c = cache_key("buckets", {"viewport": {"from": "2020-01-01", "to": "2021-01-01"}, "n": 2})
    assert a == b
    assert a != c
    assert a.startswith("buckets:")
    assert len(a) == len("buckets:") + 64


def test_cached_miss_compute_hit(db_pool: ConnectionPool) -> None:
    _clear_cache(db_pool)
    calls = {"n": 0}

    def compute() -> dict[str, Any]:
        calls["n"] += 1
        return {"v": calls["n"]}

    key = cache_key("unit", {"k": "miss-hit"})
    r1 = cached(db_pool, key=key, data_version="v1", compute=compute)
    r2 = cached(db_pool, key=key, data_version="v1", compute=compute)
    assert r1 == {"v": 1}
    assert r2 == {"v": 1}
    assert calls["n"] == 1


def test_cached_version_change_recomputes(db_pool: ConnectionPool) -> None:
    _clear_cache(db_pool)
    calls = {"n": 0}

    def compute() -> dict[str, Any]:
        calls["n"] += 1
        return {"v": calls["n"]}

    key = cache_key("unit", {"k": "version"})
    r1 = cached(db_pool, key=key, data_version="v1", compute=compute)
    r2 = cached(db_pool, key=key, data_version="v2", compute=compute)
    r3 = cached(db_pool, key=key, data_version="v2", compute=compute)
    assert r1 == {"v": 1}
    assert r2 == {"v": 2}
    assert r3 == {"v": 2}
    assert calls["n"] == 2


def test_cached_size_bound_evicts_oldest(db_pool: ConnectionPool) -> None:
    _clear_cache(db_pool)
    # Insert more than the cap; newest keys must remain.
    for i in range(CACHE_MAX_ROWS + 25):
        cached(
            db_pool,
            key=f"unit:size-{i}",
            data_version="v",
            compute=lambda i=i: {"i": i},
        )
    with db_pool.connection() as conn:
        count_row = conn.execute("SELECT count(*)::int FROM app_cache").fetchone()
        assert count_row is not None
        assert count_row[0] <= CACHE_MAX_ROWS
        # Newest keys should still be present.
        newest = conn.execute(
            "SELECT 1 FROM app_cache WHERE key = %(k)s",
            {"k": f"unit:size-{CACHE_MAX_ROWS + 24}"},
        ).fetchone()
        assert newest is not None
        # Oldest batch should have been evicted.
        oldest = conn.execute(
            "SELECT 1 FROM app_cache WHERE key = %(k)s",
            {"k": "unit:size-0"},
        ).fetchone()
        assert oldest is None


def test_data_version_includes_derived_marker(db_pool: ConnectionPool) -> None:
    marker = data_version(db_pool)
    assert "topics:" in marker
    assert "events:" in marker
    assert "|" in marker
    # Emails component is the prefix.
    emails_only = emails_data_version(db_pool)
    assert marker.startswith(emails_only)


# --- endpoint integration ---


def test_buckets_identical_requests_compute_once(
    db_client: TestClient,
    db_pool: ConnectionPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_cache(db_pool)
    calls = {"n": 0}
    import chronicle_server.chronicle as chronicle_mod

    real_get_buckets = chronicle_mod.get_buckets

    def counting_get_buckets(pool: ConnectionPool, body: Any) -> Any:
        calls["n"] += 1
        return real_get_buckets(pool, body)

    monkeypatch.setattr(chronicle_mod, "get_buckets", counting_get_buckets)

    _login(db_client)
    body = {
        "scope": {},
        "viewport": {"from": "2020-01-01T00:00:00Z", "to": "2021-01-01T00:00:00Z"},
        "pixel_width": 920,
        "aggregation": "month",
        "lanes": ["messages"],
    }
    r1 = db_client.post("/api/chronicle/buckets", json=body)
    r2 = db_client.post("/api/chronicle/buckets", json=body)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json() == r2.json()
    assert calls["n"] == 1


def test_buckets_data_version_bust_recomputes(
    db_client: TestClient,
    db_pool: ConnectionPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_cache(db_pool)
    _cleanup_cache_emails(db_pool)
    calls = {"n": 0}
    import chronicle_server.chronicle as chronicle_mod

    real_get_buckets = chronicle_mod.get_buckets

    def counting_get_buckets(pool: ConnectionPool, body: Any) -> Any:
        calls["n"] += 1
        return real_get_buckets(pool, body)

    monkeypatch.setattr(chronicle_mod, "get_buckets", counting_get_buckets)

    try:
        _login(db_client)
        body = {
            "scope": {},
            "viewport": {"from": "2020-01-01T00:00:00Z", "to": "2021-01-01T00:00:00Z"},
            "pixel_width": 920,
            "aggregation": "month",
            "lanes": ["messages"],
        }
        r1 = db_client.post("/api/chronicle/buckets", json=body)
        assert r1.status_code == 200, r1.text
        assert calls["n"] == 1

        _insert_email(db_pool)

        r2 = db_client.post("/api/chronicle/buckets", json=body)
        assert r2.status_code == 200, r2.text
        assert calls["n"] == 2
    finally:
        _cleanup_cache_emails(db_pool)
        _clear_cache(db_pool)


def test_search_facets_cached_results_live(
    db_client: TestClient,
    db_pool: ConnectionPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Facets compute once per data version; results always re-query (live)."""
    _clear_cache(db_pool)
    token = f"facet-cache-{uuid4().hex[:8]}"
    seed_id = uuid4()
    with db_pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO emails (
                id, message_id, thread_id, subject,
                sender_name, sender_address, sender_domain,
                recipients, date, body_text, body_html,
                has_attachment, labels, source_account, created_at
            ) VALUES (
                %(id)s, %(mid)s, %(tid)s, %(subject)s,
                'Facet', 'facet@example.com', 'example.com',
                '{}'::jsonb, %(date)s, %(body)s, NULL,
                false, %(labels)s, 'facet-cache@example.com', now()
            )
            """,
            {
                "id": seed_id,
                "mid": f"<facet-{seed_id}@example.com>",
                "tid": f"thread-facet-{seed_id}",
                "subject": f"Subject {token}",
                "date": datetime(2020, 6, 15, 12, 0, tzinfo=UTC),
                "body": f"Body mentions {token} once",
                "labels": ["INBOX"],
            },
        )
        conn.commit()

    facet_calls = {"n": 0}
    import chronicle_server.search as search_mod

    real_compute_facets = search_mod.compute_facets

    def counting_facets(pool: ConnectionPool, scope: Any, free_text: str | None) -> Any:
        facet_calls["n"] += 1
        return real_compute_facets(pool, scope, free_text)

    monkeypatch.setattr(search_mod, "compute_facets", counting_facets)

    try:
        _login(db_client)
        payload = {
            "query": token,
            "mode": "exact",
            "limit": 25,
            "include_facets": True,
        }
        r1 = db_client.post("/api/search", json=payload)
        r2 = db_client.post("/api/search", json=payload)
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        b1 = r1.json()
        b2 = r2.json()
        assert b1["facets"] is not None
        assert b2["facets"] == b1["facets"]
        # Facets computed once across identical requests (cache hit).
        assert facet_calls["n"] == 1

        # Insert a matching row — results must reflect it immediately (live).
        new_id = uuid4()
        with db_pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO emails (
                    id, message_id, thread_id, subject,
                    sender_name, sender_address, sender_domain,
                    recipients, date, body_text, body_html,
                    has_attachment, labels, source_account, created_at
                ) VALUES (
                    %(id)s, %(mid)s, %(tid)s, %(subject)s,
                    'Facet', 'facet2@example.com', 'example.com',
                    '{}'::jsonb, %(date)s, %(body)s, NULL,
                    false, %(labels)s, 'facet-cache@example.com', now()
                )
                """,
                {
                    "id": new_id,
                    "mid": f"<facet-{new_id}@example.com>",
                    "tid": f"thread-facet-{new_id}",
                    "subject": f"Second {token}",
                    "date": datetime(2020, 7, 1, 12, 0, tzinfo=UTC),
                    "body": f"Another body with {token}",
                    "labels": ["INBOX"],
                },
            )
            conn.commit()

        r3 = db_client.post("/api/search", json=payload)
        assert r3.status_code == 200, r3.text
        b3 = r3.json()
        result_ids = {c["id"] for c in b3["results"]}
        from chronicle_server.ids import encode_source_id

        assert encode_source_id("msg", new_id) in result_ids
        # Data-version bust forces facet recompute.
        assert facet_calls["n"] == 2
        assert b3["facets"] is not None
    finally:
        with db_pool.connection() as conn:
            conn.execute("DELETE FROM emails WHERE message_id LIKE '<facet-%%@example.com>'")
            conn.commit()
        _clear_cache(db_pool)
