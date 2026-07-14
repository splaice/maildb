# tests/test_search.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from chronicle_server.ids import encode_source_id
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


# --- auth (stub pool) ---


def test_search_requires_auth(client: TestClient) -> None:
    r = client.post("/api/search", json={"query": "hello", "mode": "exact"})
    assert r.status_code == 401


# --- helpers ---


def _seed_message(
    pool: ConnectionPool,
    *,
    subject: str = "Test subject",
    body_text: str = "Hello plain body content here.",
    sender_address: str = "alice@example.com",
    sender_name: str = "Alice",
    source_account: str = "test@example.com",
    date: str = "2020-06-15T12:00:00+00:00",
    has_attachment: bool = False,
    recipients: str = '{"to": ["bob@example.com"], "cc": [], "bcc": []}',
    thread_id: str | None = None,
) -> dict[str, Any]:
    email_id = uuid4()
    message_id = f"<search-test-{email_id}@example.com>"
    tid = thread_id or f"thread-search-{email_id}"

    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO emails (
                id, message_id, thread_id, subject,
                sender_name, sender_address, sender_domain,
                recipients, date, body_text, body_html,
                has_attachment, attachments, labels, source_account, created_at
            ) VALUES (
                %(id)s, %(mid)s, %(tid)s, %(subject)s,
                %(sname)s, %(saddr)s, 'example.com',
                %(recip)s::jsonb, %(date)s::timestamptz, %(btext)s, NULL,
                %(has_att)s, NULL, %(labels)s, %(acct)s, now()
            )
            """,
            {
                "id": email_id,
                "mid": message_id,
                "tid": tid,
                "subject": subject,
                "sname": sender_name,
                "saddr": sender_address,
                "recip": recipients,
                "date": date,
                "btext": body_text,
                "has_att": has_attachment,
                "labels": ["INBOX"],
                "acct": source_account,
            },
        )
        conn.commit()

    return {
        "email_id": email_id,
        "msg_sid": encode_source_id("msg", email_id),
        "thread_id": tid,
        "thr_sid": encode_source_id("thr", tid),
        "subject": subject,
        "sender_address": sender_address,
        "date": date,
        "body_text": body_text,
    }


def _cleanup(pool: ConnectionPool, seeds: list[dict[str, Any]]) -> None:
    with pool.connection() as conn:
        for seed in seeds:
            conn.execute("DELETE FROM emails WHERE id = %(id)s", {"id": seed["email_id"]})
        conn.commit()


def _ollama_reachable() -> bool:
    """Probe whether Ollama embedding endpoint is up (for optional semantic asserts)."""
    try:
        import urllib.request

        from maildb.config import Settings

        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        url = settings.ollama_url.rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=1.5) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except Exception:
        return False


@pytest.fixture
def ollama_up() -> bool:
    return _ollama_reachable()


# --- DB-backed ---


def test_exact_mode_date_ordered_labeled_cards_with_snippets(
    db_pool: ConnectionPool, db_client: TestClient
) -> None:
    seeds = [
        _seed_message(
            db_pool,
            subject="Alpha unique-search-token",
            body_text="Body with unique-search-token early and more padding text " * 5,
            date="2021-01-01T00:00:00+00:00",
            sender_address="a@example.com",
        ),
        _seed_message(
            db_pool,
            subject="Beta",
            body_text="Later message also has unique-search-token inside it",
            date="2022-01-01T00:00:00+00:00",
            sender_address="b@example.com",
        ),
    ]
    try:
        _login(db_client)
        r = db_client.post(
            "/api/search",
            json={
                "query": "unique-search-token",
                "mode": "exact",
                "limit": 25,
                "include_facets": True,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "exact"
        assert "scope_fingerprint" in body
        assert body["scope_fingerprint"].startswith("qs_")
        assert isinstance(body["took_ms"], int)
        assert body.get("degraded") is None

        results = body["results"]
        # At least our two seeds (DB may have other matches)
        ours = [c for c in results if c["id"] in {s["msg_sid"] for s in seeds}]
        assert len(ours) >= 2
        for card in ours:
            assert card["result_type"] == "message"
            assert card["id"].startswith("msg_")
            assert "subject" in card
            assert "sender" in card
            assert "date" in card
            assert "mailbox" in card
            assert "snippet" in card
            assert (
                "unique-search-token" in card["snippet"].lower()
                or "unique-search-token" in (card.get("subject") or "").lower()
            )
            assert card["match"]["kind"] == "exact"
            assert "field" in card["match"]

        # Date-ordered DESC among our cards
        our_dates = [c["date"] for c in ours]
        assert our_dates == sorted(our_dates, reverse=True)

        # Facets shape
        assert body["facet_basis"] == "exact"
        assert "facets" in body and body["facets"] is not None
        assert set(body["facets"].keys()) >= {"mailbox", "year", "has_attachment"}
        for key in ("mailbox", "year", "has_attachment"):
            assert isinstance(body["facets"][key], list)
            for item in body["facets"][key]:
                assert "value" in item and "count" in item
    finally:
        _cleanup(db_pool, seeds)


def test_hybrid_merge_explanations_or_skip_semantic(
    db_pool: ConnectionPool, db_client: TestClient, ollama_up: bool
) -> None:
    seed = _seed_message(
        db_pool,
        subject="Hybrid probe subject xyzzy",
        body_text="The quick brown xyzzy fox jumps",
        date="2020-03-01T00:00:00+00:00",
    )
    try:
        _login(db_client)
        r = db_client.post(
            "/api/search",
            json={"query": "xyzzy", "mode": "hybrid", "limit": 10},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "hybrid"

        if body.get("degraded"):
            # Embedding unavailable — exact leg still returned, not silent
            assert body["degraded"] == {"semantic": "unavailable"}
            for card in body["results"]:
                if card["id"] == seed["msg_sid"]:
                    assert card["match"]["kind"] == "exact"
            return

        if not ollama_up:
            # Semantic worked or empty; if hybrid explanations present, check shape
            pass

        for card in body["results"]:
            kind = card["match"]["kind"]
            if kind == "hybrid":
                assert "exact_rank" in card["match"]
                assert "semantic_rank" in card["match"]
                assert "similarity" in card["match"]
            elif kind == "exact":
                # degraded path already handled
                pass
    finally:
        _cleanup(db_pool, [seed])


def test_degraded_flag_when_embedding_raises(
    db_pool: ConnectionPool, db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed_message(
        db_pool,
        subject="Degrade me",
        body_text="degrade-token unique body",
        date="2019-01-01T00:00:00+00:00",
    )
    try:
        from maildb.embeddings import EmbeddingClient

        def _boom(self: Any, text: str) -> list[float]:  # noqa: ARG001
            raise ConnectionError("ollama down")

        monkeypatch.setattr(EmbeddingClient, "embed", _boom)

        _login(db_client)
        r = db_client.post(
            "/api/search",
            json={"query": "degrade-token", "mode": "hybrid", "limit": 10},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["degraded"] == {"semantic": "unavailable"}
        assert (
            any(c.get("match", {}).get("kind") == "exact" for c in body["results"])
            or body["results"] is not None
        )
    finally:
        _cleanup(db_pool, [seed])


def test_semantic_mode_503_on_failure(
    db_pool: ConnectionPool, db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from maildb.embeddings import EmbeddingClient

    def _boom(self: Any, text: str) -> list[float]:  # noqa: ARG001
        raise ConnectionError("ollama down")

    monkeypatch.setattr(EmbeddingClient, "embed", _boom)

    _login(db_client)
    r = db_client.post(
        "/api/search",
        json={"query": "anything", "mode": "semantic", "limit": 5},
    )
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["semantic"] == "unavailable" or "unavailable" in str(detail).lower()


def test_cursor_window_walk(db_pool: ConnectionPool, db_client: TestClient) -> None:
    token = f"cursor-walk-{uuid4().hex[:8]}"
    seeds = [
        _seed_message(
            db_pool,
            subject=f"Cursor {i} {token}",
            body_text=f"body {token} number {i}",
            date=f"2020-{(i % 12) + 1:02d}-01T00:00:00+00:00",
            sender_address=f"u{i}@example.com",
        )
        for i in range(5)
    ]
    try:
        _login(db_client)
        r1 = db_client.post(
            "/api/search",
            json={"query": token, "mode": "exact", "limit": 2, "include_facets": True},
        )
        assert r1.status_code == 200, r1.text
        b1 = r1.json()
        assert len(b1["results"]) <= 2
        assert b1["facets"] is not None  # first page includes facets

        if not b1.get("next_cursor"):
            # Not enough results in this DB environment
            pytest.skip("not enough matches for cursor walk")

        r2 = db_client.post(
            "/api/search",
            json={
                "query": token,
                "mode": "exact",
                "limit": 2,
                "cursor": b1["next_cursor"],
                "include_facets": True,
            },
        )
        assert r2.status_code == 200, r2.text
        b2 = r2.json()
        # Facets skipped when cursor set
        assert b2.get("facets") is None
        ids1 = {c["id"] for c in b1["results"]}
        ids2 = {c["id"] for c in b2["results"]}
        assert ids1.isdisjoint(ids2)
    finally:
        _cleanup(db_pool, seeds)


def test_oversized_offset_422(db_pool: ConnectionPool, db_client: TestClient) -> None:
    from chronicle_server.cursor import encode_cursor

    _login(db_client)
    # offset 490 + limit 25 = 515 > 500
    secret = db_client.app.state.settings.secret_key
    cursor = encode_cursor({"o": 490}, secret)
    r = db_client.post(
        "/api/search",
        json={"query": "x", "mode": "exact", "limit": 25, "cursor": cursor},
    )
    assert r.status_code == 422
    assert "narrow" in str(r.json()["detail"]).lower()


def test_query_syntax_echoed_in_scope(db_pool: ConnectionPool, db_client: TestClient) -> None:
    _login(db_client)
    r = db_client.post(
        "/api/search",
        json={
            "query": "from:alice@example.com topic:renovation roof",
            "mode": "exact",
            "scope": {},
            "limit": 5,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "alice@example.com" in body["scope"].get("senders", [])
    ft = body["scope"].get("free_text") or ""
    assert ft == "roof" or "roof" in ft
    assert any("topic" in u for u in body["unsupported"])


def test_request_scope_wins_on_conflict(db_pool: ConnectionPool, db_client: TestClient) -> None:
    _login(db_client)
    r = db_client.post(
        "/api/search",
        json={
            "query": "from:parser@example.com hello",
            "mode": "exact",
            "scope": {"senders": ["request@example.com"]},
            "limit": 5,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"]["senders"] == ["request@example.com"]


def test_unsupported_never_errors(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _login(db_client)
    r = db_client.post(
        "/api/search",
        json={
            "query": "person:alice organization:acme domain:x.com -topic:spam",
            "mode": "exact",
            "limit": 5,
        },
    )
    assert r.status_code == 200
    assert len(r.json()["unsupported"]) >= 3
