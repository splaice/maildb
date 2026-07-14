# tests/test_settings.py — app settings whitelist, merge, audit, per-action gates
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from chronicle_server.app import create_app
from chronicle_server.settings_api import (
    defaults_document,
    merge_document,
    validate_and_merge_patch,
)
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from chronicle_server.config import ChronicleSettings


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def _clear_settings(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM app_settings")
        conn.commit()


@pytest.fixture(autouse=True)
def _isolate_app_settings(request: pytest.FixtureRequest) -> Any:
    """Wipe app_settings around DB-backed tests so flags do not leak to peers."""
    if "db_pool" not in request.fixturenames:
        yield
        return
    pool: ConnectionPool = request.getfixturevalue("db_pool")
    _clear_settings(pool)
    yield
    _clear_settings(pool)


# --- pure unit: defaults / merge / validate ---


def test_defaults_document_includes_whitelist_groups(settings: ChronicleSettings) -> None:
    doc = defaults_document(settings)
    assert set(doc.keys()) == {"ai", "privacy", "search", "chronicle"}
    assert doc["ai"]["ask_enabled"] is True
    assert doc["ai"]["interpret_enabled"] is True
    assert doc["ai"]["generate_enabled"] is True
    assert doc["ai"]["answer_model"] == settings.answer_model
    assert "retention_note" in doc["ai"]
    assert 900 <= doc["privacy"]["session_max_age_s"] <= 86400
    assert doc["search"]["default_mode"] == "hybrid"
    assert isinstance(doc["chronicle"]["default_lanes"], list)


def test_merge_document_stored_overrides_defaults(settings: ChronicleSettings) -> None:
    stored = {
        "ai": {"ask_enabled": False, "answer_model": "should-be-ignored"},
        "search": {"default_mode": "exact"},
    }
    doc = merge_document(settings, stored)
    assert doc["ai"]["ask_enabled"] is False
    # answer_model always from env (read-only display)
    assert doc["ai"]["answer_model"] == settings.answer_model
    assert doc["search"]["default_mode"] == "exact"
    assert doc["privacy"]["session_max_age_s"] == settings.session_max_age_s


def test_validate_unknown_top_key_422(settings: ChronicleSettings) -> None:
    with pytest.raises(Exception) as ei:
        validate_and_merge_patch(settings, {}, {"evil": {"x": 1}})
    assert ei.value.status_code == 422  # type: ignore[attr-defined]


def test_validate_session_bounds(settings: ChronicleSettings) -> None:
    with pytest.raises(Exception) as ei:
        validate_and_merge_patch(settings, {}, {"privacy": {"session_max_age_s": 60}})
    assert ei.value.status_code == 422  # type: ignore[attr-defined]
    with pytest.raises(Exception) as ei2:
        validate_and_merge_patch(settings, {}, {"privacy": {"session_max_age_s": 999_999}})
    assert ei2.value.status_code == 422  # type: ignore[attr-defined]


def test_validate_shallow_merge_changed_keys(settings: ChronicleSettings) -> None:
    new_stored, changed = validate_and_merge_patch(
        settings,
        {},
        {
            "ai": {"ask_enabled": False},
            "search": {"default_mode": "semantic"},
        },
    )
    assert "ai" in changed
    assert "search" in changed
    assert new_stored["ai"]["ask_enabled"] is False
    assert new_stored["search"]["default_mode"] == "semantic"


# --- API (db-backed) ---


def test_get_settings_defaults(db_client: TestClient) -> None:
    _login(db_client)
    r = db_client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert "ai" in body and "privacy" in body and "search" in body and "chronicle" in body
    assert body["ai"]["answer_model"]
    assert body["search"]["default_mode"] == "hybrid"


def test_put_settings_merge_and_audit(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _login(db_client)
    r = db_client.put(
        "/api/settings",
        json={
            "ai": {"ask_enabled": False, "retention_note": "Local only"},
            "privacy": {"session_max_age_s": 1800},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ai"]["ask_enabled"] is False
    assert body["ai"]["retention_note"] == "Local only"
    assert body["privacy"]["session_max_age_s"] == 1800
    # other groups still present
    assert body["search"]["default_mode"] == "hybrid"

    # Shallow merge: only touch search; ai stays
    r2 = db_client.put("/api/settings", json={"search": {"default_mode": "exact"}})
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["search"]["default_mode"] == "exact"
    assert b2["ai"]["ask_enabled"] is False

    with db_pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT action, detail FROM app_audit
             WHERE action = 'settings_update'
             ORDER BY id DESC
             LIMIT 2
            """
        ).fetchall()
    assert rows
    # Audit carries changed keys, not values
    detail = rows[0][1]
    assert "changed_keys" in detail
    assert "search" in detail["changed_keys"]
    assert "ask_enabled" not in str(detail.get("changed_keys"))


def test_put_unknown_key_422(db_client: TestClient) -> None:
    _login(db_client)
    r = db_client.put("/api/settings", json={"not_a_group": {"x": 1}})
    assert r.status_code == 422


def test_put_answer_model_ignored(
    db_client: TestClient,
    db_settings: ChronicleSettings,
) -> None:
    _login(db_client)
    r = db_client.put(
        "/api/settings",
        json={"ai": {"answer_model": "gpt-whatever", "ask_enabled": True}},
    )
    assert r.status_code == 200
    assert r.json()["ai"]["answer_model"] == db_settings.answer_model


def test_session_max_age_applied_to_settings(
    db_client: TestClient,
) -> None:
    _login(db_client)
    r = db_client.put("/api/settings", json={"privacy": {"session_max_age_s": 3600}})
    assert r.status_code == 200
    assert db_client.app.state.settings.session_max_age_s == 3600  # type: ignore[attr-defined]


# --- per-action gating (disable one, others live) ---


def test_disable_ask_returns_unavailable_json(
    db_client: TestClient,
) -> None:
    _login(db_client)
    db_client.put("/api/settings", json={"ai": {"ask_enabled": False}})
    db_client.app.state.model_available = True  # type: ignore[attr-defined]
    r = db_client.post(
        "/api/ask",
        json={"question": "roof?", "mode": "scope", "scope": {}},
    )
    assert r.status_code == 200
    assert "text/event-stream" not in (r.headers.get("content-type") or "")
    body = r.json()
    assert body["available"] is False
    assert "disabled" in body["reason"].lower() or "Ask" in body["reason"]


def test_disable_interpret_parse_only(
    db_client: TestClient,
) -> None:
    _login(db_client)
    db_client.put("/api/settings", json={"ai": {"interpret_enabled": False}})
    # Model forced available — still must not use it
    db_client.app.state.model_available = True  # type: ignore[attr-defined]

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("model transport must not be called when interpret disabled")

    db_client.app.state.chat_transport = boom  # type: ignore[attr-defined]
    r = db_client.post(
        "/api/query/interpret",
        json={"text": "from:alice after:2015 about the roof renovation project"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["model_used"] is False
    # Syntax still runs (chips or free_text present)
    assert "scope" in body


def test_disable_generate_available_false(
    db_client: TestClient,
) -> None:
    _login(db_client)
    db_client.put("/api/settings", json={"ai": {"generate_enabled": False}})
    db_client.app.state.model_available = True  # type: ignore[attr-defined]
    r = db_client.post(
        "/api/events/generate",
        json={
            "scope": {},
            "viewport": {"from": "2015-01-01", "to": "2016-01-01"},
        },
    )
    assert r.status_code == 200
    assert r.json() == {"available": False}


def test_disable_ask_leaves_interpret_and_generate_live(
    db_client: TestClient,
) -> None:
    """Independence: ask off does not block interpret/generate."""
    _login(db_client)
    db_client.put(
        "/api/settings",
        json={
            "ai": {
                "ask_enabled": False,
                "interpret_enabled": True,
                "generate_enabled": True,
            }
        },
    )
    db_client.app.state.model_available = False  # type: ignore[attr-defined]

    # Interpret: parse-only path works (model unavailable still OK)
    r_i = db_client.post(
        "/api/query/interpret",
        json={"text": "from:bob"},
    )
    assert r_i.status_code == 200
    assert r_i.json()["model_used"] is False

    # Generate: available:false because model down — but not because of ask flag
    r_g = db_client.post(
        "/api/events/generate",
        json={
            "scope": {},
            "viewport": {"from": "2015-01-01", "to": "2016-01-01"},
        },
    )
    assert r_g.status_code == 200
    assert r_g.json() == {"available": False}

    # Ask still gated by flag even if we flip model on
    db_client.app.state.model_available = True  # type: ignore[attr-defined]
    r_a = db_client.post(
        "/api/ask",
        json={"question": "x", "mode": "scope", "scope": {}},
    )
    assert r_a.json()["available"] is False


def test_env_ask_disabled_still_gates_without_db_row(
    settings: ChronicleSettings,
    stub_pool: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.ask_enabled = False
    monkeypatch.setattr("chronicle_server.app.create_pool", lambda _s: stub_pool)
    monkeypatch.setattr("chronicle_server.app.init_app_tables", lambda _p: None)
    monkeypatch.setattr("chronicle_server.app.ensure_user", lambda _p, _u: None)
    app = create_app(settings)
    with TestClient(app) as tc:
        _login(tc)
        r = tc.post("/api/ask", json={"question": "x", "mode": "scope", "scope": {}})
        assert r.status_code == 200
        assert r.json()["available"] is False
