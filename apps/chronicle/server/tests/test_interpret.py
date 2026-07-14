# tests/test_interpret.py
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import TYPE_CHECKING
from uuid import uuid4

from chronicle_server.interpret import (
    parse_model_response,
    validate_model_extraction,
)
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


# --- unit: model JSON validation ---


def test_validate_whitelist_drops_unknown_keys() -> None:
    out = validate_model_extraction(
        {
            "senders": ["alice@example.com"],
            "evil_key": "drop me",
            "date_from": "2014-01-01",
            "prose": "nope",
        }
    )
    assert out is not None
    assert "evil_key" not in out
    assert "prose" not in out
    assert out["senders"] == ["alice@example.com"]
    assert out["date_from"] == "2014-01-01"


def test_parse_model_response_largest_json_block() -> None:
    content = 'Here is the result:\n{"senders": ["a@x.com"], "residual_text": "roof"}\nThanks'
    out = parse_model_response(content)
    assert out is not None
    assert out["senders"] == ["a@x.com"]
    assert out["residual_text"] == "roof"


def test_parse_model_response_prose_only() -> None:
    assert parse_model_response("I think you want emails from Alice about roofs.") is None


def test_parse_model_response_bad_json() -> None:
    assert parse_model_response("{senders: not valid}") is None


def test_parse_model_response_bad_dates_dropped() -> None:
    out = parse_model_response(json.dumps({"date_from": "not-a-date", "senders": ["a@x.com"]}))
    assert out is not None
    assert "date_from" not in out
    assert out["senders"] == ["a@x.com"]


# --- auth ---


def test_interpret_requires_auth(client: TestClient) -> None:
    r = client.post("/api/query/interpret", json={"text": "hello there world", "scope": {}})
    assert r.status_code == 401


# --- syntax-only (model unavailable) ---


def test_interpret_syntax_only_model_unavailable(client: TestClient) -> None:
    _login(client)
    client.app.state.model_available = False  # type: ignore[attr-defined]

    r = client.post(
        "/api/query/interpret",
        json={
            "text": "from:alice@example.com filetype:pdf roof material decision",
            "scope": {"mailboxes": ["me@example.com"]},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["model_used"] is False
    assert body["free_text"] == "roof material decision"
    assert "alice@example.com" in (body["scope"].get("senders") or [])
    assert "pdf" in (body["scope"].get("file_types") or [])
    # Request scope preserved when syntax/model don't set it
    assert "me@example.com" in (body["scope"].get("mailboxes") or [])

    kinds = {(c["kind"], c["value"], c["origin"]) for c in body["chips"]}
    assert ("sender", "alice@example.com", "syntax") in kinds
    assert ("file_type", "pdf", "syntax") in kinds


def test_interpret_unsupported_chip(client: TestClient) -> None:
    _login(client)
    client.app.state.model_available = False  # type: ignore[attr-defined]
    r = client.post(
        "/api/query/interpret",
        json={"text": "topic:renovation hello world here", "scope": {}},
    )
    assert r.status_code == 200
    body = r.json()
    unsupported = [c for c in body["chips"] if c["kind"] == "unsupported"]
    assert any(c["value"] == "topic:renovation" for c in unsupported)
    assert all(c["origin"] == "syntax" for c in unsupported)


# --- fake-transport model happy path ---


def test_interpret_model_happy_path_syntax_wins(
    client: TestClient,
) -> None:
    _login(client)

    def fake_transport(model: str, messages: list[dict[str, str]], stream: bool) -> Iterator[str]:
        assert messages[0]["role"] == "system"
        assert "extract search constraints" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        # Model tries to set a conflicting sender + a date range
        yield json.dumps(
            {
                "senders": ["model-winner@example.com"],
                "date_from": "2014-01-01",
                "date_to": "2018-12-31",
                "file_types": ["docx"],
                "residual_text": "roof material decision",
                "unknown_key": "drop",
            }
        )

    client.app.state.chat_transport = fake_transport  # type: ignore[attr-defined]
    client.app.state.model_available = True  # type: ignore[attr-defined]

    r = client.post(
        "/api/query/interpret",
        json={
            # syntax sender must win over model sender; free text ≥ 3 words
            "text": "from:syntax@example.com emails about roof material decision",
            "scope": {},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["model_used"] is True
    assert body["free_text"] == "roof material decision"
    # Syntax wins on senders
    assert body["scope"]["senders"] == ["syntax@example.com"]
    # Model supplies date when syntax has none
    assert body["scope"]["date"]["from"] == "2014-01-01"
    assert body["scope"]["date"]["to"] == "2018-12-31"
    assert body["scope"]["file_types"] == ["docx"]

    origins = {c["kind"]: c["origin"] for c in body["chips"] if c["kind"] != "unsupported"}
    assert origins.get("sender") == "syntax"
    assert origins.get("date") == "model"
    assert origins.get("file_type") == "model"


def test_interpret_malformed_model_output_no_5xx(client: TestClient) -> None:
    _login(client)

    cases = [
        "Sure, here are some constraints for you without JSON.",
        "{not valid json at all",
        json.dumps({"senders": "not-a-list", "date_from": 12345}),
        json.dumps({"totally": "unknown", "keys": True}),
    ]

    for content in cases:

        def fake_transport(
            model: str,
            messages: list[dict[str, str]],
            stream: bool,
            *,
            _content: str = content,
        ) -> Iterator[str]:
            yield _content

        client.app.state.chat_transport = fake_transport  # type: ignore[attr-defined]
        client.app.state.model_available = True  # type: ignore[attr-defined]

        r = client.post(
            "/api/query/interpret",
            json={"text": "find emails about roof material decision", "scope": {}},
        )
        assert r.status_code == 200, content
        body = r.json()
        # Malformed → behave as if model returned nothing
        assert body["model_used"] is False
        assert body["free_text"] == "find emails about roof material decision"


def test_interpret_skips_model_when_free_text_trivial(client: TestClient) -> None:
    """Fewer than 3 residual words → no model call."""
    called: list[bool] = []

    def fake_transport(model: str, messages: list[dict[str, str]], stream: bool) -> Iterator[str]:
        called.append(True)
        yield json.dumps({"residual_text": "x"})

    _login(client)
    client.app.state.chat_transport = fake_transport  # type: ignore[attr-defined]
    client.app.state.model_available = True  # type: ignore[attr-defined]

    r = client.post(
        "/api/query/interpret",
        json={"text": "from:a@x.com two words", "scope": {}},
    )
    assert r.status_code == 200
    assert r.json()["model_used"] is False
    assert called == []


# --- audit hash-only ---


def test_interpret_audit_hash_only(
    db_client: TestClient,
    db_pool: ConnectionPool,
) -> None:
    _login(db_client)
    db_client.app.state.model_available = False  # type: ignore[attr-defined]

    text = "from:alice@example.com roof material decision"
    expected_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    r = db_client.post(
        "/api/query/interpret",
        json={"text": text, "scope": {}},
    )
    assert r.status_code == 200
    assert r.json()["model_used"] is False

    with db_pool.connection() as conn:
        row = conn.execute(
            """
            SELECT detail FROM app_audit
             WHERE action = 'interpret'
             ORDER BY id DESC
             LIMIT 1
            """
        ).fetchone()
    assert row is not None
    detail = row[0]
    if isinstance(detail, str):
        detail = json.loads(detail)
    assert set(detail.keys()) == {"model_used", "text_sha256"}
    assert detail["text_sha256"] == expected_sha
    assert detail["model_used"] is False
    # Content must not appear in the audit detail
    assert text not in json.dumps(detail)


# --- contacts name resolution ---


def _seed_contact(
    pool: ConnectionPool,
    *,
    display_name: str,
    addresses: list[str],
) -> str:
    contact_id = uuid4()
    with pool.connection() as conn:
        # Clear colliding addresses from prior runs / shared test DB.
        for addr in addresses:
            conn.execute(
                "DELETE FROM contact_addresses WHERE address = %(addr)s",
                {"addr": addr},
            )
        conn.execute(
            """
            INSERT INTO contacts (id, display_name, kind, kind_source)
            VALUES (%(id)s, %(name)s, 'human', 'manual')
            """,
            {"id": contact_id, "name": display_name},
        )
        for addr in addresses:
            conn.execute(
                """
                INSERT INTO contact_addresses (
                    address, contact_id, name_variants, is_user,
                    messages_from, messages_to
                ) VALUES (
                    %(addr)s, %(cid)s, %(variants)s, false, 5, 1
                )
                """,
                {
                    "addr": addr,
                    "cid": contact_id,
                    "variants": [display_name],
                },
            )
        conn.commit()
    return str(contact_id)


def _cleanup_contacts(pool: ConnectionPool, contact_ids: list[str]) -> None:
    with pool.connection() as conn:
        for cid in contact_ids:
            conn.execute("DELETE FROM contact_addresses WHERE contact_id = %(id)s", {"id": cid})
            conn.execute("DELETE FROM contacts WHERE id = %(id)s", {"id": cid})
        conn.commit()


def test_interpret_name_resolution_single_match(
    db_client: TestClient,
    db_pool: ConnectionPool,
) -> None:
    cid = _seed_contact(
        db_pool,
        display_name="Alice Chen",
        addresses=["alice.chen.interpret@example.com"],
    )
    try:
        _login(db_client)

        def fake_transport(
            model: str, messages: list[dict[str, str]], stream: bool
        ) -> Iterator[str]:
            yield json.dumps(
                {
                    "senders": ["Alice Chen"],
                    "residual_text": "roof material decision",
                }
            )

        db_client.app.state.chat_transport = fake_transport  # type: ignore[attr-defined]
        db_client.app.state.model_available = True  # type: ignore[attr-defined]

        r = db_client.post(
            "/api/query/interpret",
            json={"text": "emails from Alice about roof material decision", "scope": {}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["model_used"] is True
        assert body["scope"]["senders"] == ["alice.chen.interpret@example.com"]
        sender_chips = [c for c in body["chips"] if c["kind"] == "sender"]
        assert len(sender_chips) == 1
        assert sender_chips[0]["value"] == "alice.chen.interpret@example.com"
        assert sender_chips[0]["origin"] == "model"
        assert sender_chips[0].get("display") == "Alice Chen"
        assert not any(c["kind"] == "unresolved_person" for c in body["chips"])
    finally:
        _cleanup_contacts(db_pool, [cid])


def test_interpret_name_resolution_ambiguous(
    db_client: TestClient,
    db_pool: ConnectionPool,
) -> None:
    c1 = _seed_contact(
        db_pool,
        display_name="Alex Smith",
        addresses=["alex1.interpret@example.com"],
    )
    c2 = _seed_contact(
        db_pool,
        display_name="Alex Jones",
        addresses=["alex2.interpret@example.com"],
    )
    try:
        _login(db_client)

        def fake_transport(
            model: str, messages: list[dict[str, str]], stream: bool
        ) -> Iterator[str]:
            # "Alex" matches both contacts
            yield json.dumps(
                {
                    "senders": ["Alex"],
                    "residual_text": "project budget numbers",
                }
            )

        db_client.app.state.chat_transport = fake_transport  # type: ignore[attr-defined]
        db_client.app.state.model_available = True  # type: ignore[attr-defined]

        r = db_client.post(
            "/api/query/interpret",
            json={"text": "messages from Alex about project budget numbers", "scope": {}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["model_used"] is True
        # Ambiguous → not applied to scope
        assert not body["scope"].get("senders")
        unresolved = [c for c in body["chips"] if c["kind"] == "unresolved_person"]
        assert len(unresolved) == 1
        assert unresolved[0]["value"] == "Alex"
        assert unresolved[0]["origin"] == "model"
    finally:
        _cleanup_contacts(db_pool, [c1, c2])


def test_interpret_model_address_no_contacts_lookup(client: TestClient) -> None:
    """Email-shaped values skip contacts and apply directly."""
    _login(client)

    def fake_transport(model: str, messages: list[dict[str, str]], stream: bool) -> Iterator[str]:
        yield json.dumps(
            {
                "participants": ["bob@example.com"],
                "has_attachment": True,
                "residual_text": "invoice copy scan",
            }
        )

    client.app.state.chat_transport = fake_transport  # type: ignore[attr-defined]
    client.app.state.model_available = True  # type: ignore[attr-defined]

    r = client.post(
        "/api/query/interpret",
        json={"text": "find the invoice copy scan please", "scope": {}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["model_used"] is True
    assert body["scope"]["participants"] == ["bob@example.com"]
    assert body["scope"]["has_attachment"] is True
