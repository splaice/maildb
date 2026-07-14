# tests/test_auth.py
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def test_login_success(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200
    assert r.json() == {"username": USERNAME}
    assert "chronicle_session" in r.cookies


def test_login_wrong_password(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": "wrong"})
    assert r.status_code == 401
    assert "chronicle_session" not in r.cookies


def test_login_wrong_username(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": "other", "password": PASSWORD})
    assert r.status_code == 401


def test_session_cookie_roundtrip(client: TestClient) -> None:
    login = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert login.status_code == 200
    r = client.get("/api/auth/session")
    assert r.status_code == 200
    assert r.json() == {"username": USERNAME}


def test_session_missing_cookie(client: TestClient) -> None:
    r = client.get("/api/auth/session")
    assert r.status_code == 401


def test_session_invalid_cookie(client: TestClient) -> None:
    client.cookies.set("chronicle_session", "not-a-valid-token")
    r = client.get("/api/auth/session")
    assert r.status_code == 401


def test_session_expired_cookie(short_session_client: TestClient) -> None:
    login = short_session_client.post(
        "/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    assert login.status_code == 200
    time.sleep(1.5)
    r = short_session_client.get("/api/auth/session")
    assert r.status_code == 401


def test_logout_clears_session(client: TestClient) -> None:
    client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert client.get("/api/auth/session").status_code == 200
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    assert client.get("/api/auth/session").status_code == 401


def test_logout_without_session(client: TestClient) -> None:
    r = client.post("/api/auth/logout")
    assert r.status_code == 200


def test_security_headers_present(client: TestClient) -> None:
    r = client.get("/api/auth/session")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["Content-Security-Policy"] == "default-src 'none'"


def test_login_audits_success(client: TestClient) -> None:
    with (
        patch("chronicle_server.auth.audit") as mock_audit,
        patch("chronicle_server.auth.update_last_login"),
    ):
        client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert mock_audit.call_args_list[-1].kwargs["action"] == "login"


def test_login_audits_failure(client: TestClient) -> None:
    with patch("chronicle_server.auth.audit") as mock_audit:
        client.post("/api/auth/login", json={"username": USERNAME, "password": "nope"})
    assert mock_audit.call_args_list[-1].kwargs["action"] == "login_failed"
    detail = mock_audit.call_args_list[-1].kwargs.get("detail") or {}
    assert "password" not in detail


def test_logout_audits(client: TestClient) -> None:
    client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    with patch("chronicle_server.auth.audit") as mock_audit:
        client.post("/api/auth/logout")
    assert mock_audit.call_args_list[-1].kwargs["action"] == "logout"


def test_protected_route_requires_auth(client: TestClient) -> None:
    r = client.get("/api/archive/summary")
    assert r.status_code == 401


def _summary_stub_connection() -> Any:
    """Connection stub returning empty archive summary rows."""
    conn = MagicMock()

    def execute(sql: str, params: object = None) -> MagicMock:
        result = MagicMock()
        sql_l = sql.lower()
        if "email_accounts" in sql_l:
            result.fetchall = MagicMock(return_value=[])
        elif "min(date)" in sql_l:
            result.fetchone = MagicMock(return_value=(None, None))
        elif "embedding" in sql_l:
            result.fetchone = MagicMock(return_value=(0, 0))
        elif "attachment_contents" in sql_l or "count" in sql_l:
            result.fetchone = MagicMock(return_value=(0, 0, 0, 0))
        else:
            result.fetchall = MagicMock(return_value=[])
            result.fetchone = MagicMock(return_value=None)
        return result

    conn.execute = execute
    conn.commit = MagicMock()
    return conn


def test_login_summary_logout_e2e(client: TestClient) -> None:
    """Login → summary → logout via TestClient with real argon2 hash."""
    conn = _summary_stub_connection()

    @contextmanager
    def connection() -> Iterator[MagicMock]:
        yield conn

    client.app.state.pool.connection = connection  # type: ignore[method-assign]

    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200

    summary = client.get("/api/archive/summary")
    assert summary.status_code == 200
    body = summary.json()
    assert "accounts" in body
    assert "counts" in body
    assert body["versions"]["api"] == "0.1.0"

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
