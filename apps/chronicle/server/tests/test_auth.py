# tests/test_auth.py
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from chronicle_server.auth import LoginRateLimiter, sign_session, unsign_session_parts
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from chronicle_server.config import ChronicleSettings


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
    assert r.headers["Cache-Control"] == "no-store"


def test_api_no_store_header(client: TestClient) -> None:
    r = client.get("/api/archive/summary")
    assert r.headers.get("Cache-Control") == "no-store"


# --- rate limiting ---


def test_login_rate_limiter_window_math() -> None:
    """Fixed-window math with injectable clock."""
    clock = {"t": 1000.0}
    lim = LoginRateLimiter(max_failures=5, window_s=900, clock=lambda: clock["t"])

    for _ in range(4):
        lim.record_failure("user:owner")
    assert lim.is_limited("user:owner")[0] is False

    lim.record_failure("user:owner")
    limited, retry_after = lim.is_limited("user:owner")
    assert limited is True
    assert retry_after > 0

    # still inside window
    clock["t"] = 1000.0 + 899
    assert lim.is_limited("user:owner")[0] is True

    # window elapsed — oldest failures pruned
    clock["t"] = 1000.0 + 901
    assert lim.is_limited("user:owner")[0] is False

    # reset clears immediately
    for _ in range(5):
        lim.record_failure("user:owner")
    assert lim.is_limited("user:owner")[0] is True
    lim.reset("user:owner")
    assert lim.is_limited("user:owner")[0] is False


def test_login_rate_limit_429_and_reset_on_success(client: TestClient) -> None:
    with patch("chronicle_server.auth.audit") as mock_audit:
        for _ in range(5):
            r = client.post(
                "/api/auth/login",
                json={"username": USERNAME, "password": "wrong"},
            )
            assert r.status_code == 401

        blocked = client.post(
            "/api/auth/login",
            json={"username": USERNAME, "password": "wrong"},
        )
        assert blocked.status_code == 429
        assert "Retry-After" in blocked.headers
        assert mock_audit.call_args_list[-1].kwargs["action"] == "login_ratelimited"

    # inject a clean limiter so success path is reachable without waiting
    client.app.state.login_rate_limiter = LoginRateLimiter(max_failures=5, window_s=900)
    for _ in range(5):
        client.post("/api/auth/login", json={"username": USERNAME, "password": "wrong"})
    # success resets — after 5 failures we are limited; reset via direct call then login
    client.app.state.login_rate_limiter.reset(f"user:{USERNAME}", "ip:testclient")
    ok = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert ok.status_code == 200

    # after success, failures start fresh
    for _ in range(4):
        r = client.post("/api/auth/login", json={"username": USERNAME, "password": "wrong"})
        assert r.status_code == 401
    still_ok_path = client.post("/api/auth/login", json={"username": USERNAME, "password": "wrong"})
    # 5th failure → 401 (not yet 429); next would be 429
    assert still_ok_path.status_code == 401
    limited = client.post("/api/auth/login", json={"username": USERNAME, "password": "wrong"})
    assert limited.status_code == 429


def test_login_rate_limit_reset_on_success_clears_window(client: TestClient) -> None:
    lim = LoginRateLimiter(max_failures=5, window_s=900)
    client.app.state.login_rate_limiter = lim
    for _ in range(3):
        assert (
            client.post(
                "/api/auth/login", json={"username": USERNAME, "password": "nope"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
        ).status_code
        == 200
    )
    # counters cleared — can fail 4 more times without 429
    for _ in range(4):
        assert (
            client.post(
                "/api/auth/login", json={"username": USERNAME, "password": "nope"}
            ).status_code
            == 401
        )
    assert (
        client.post("/api/auth/login", json={"username": USERNAME, "password": "nope"}).status_code
        == 401
    )  # 5th failure still 401
    assert (
        client.post("/api/auth/login", json={"username": USERNAME, "password": "nope"}).status_code
        == 429
    )


# --- session auth_at / fresh auth ---


def test_session_token_includes_auth_at(client: TestClient, settings: ChronicleSettings) -> None:
    login = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert login.status_code == 200
    token = login.cookies.get("chronicle_session")
    assert token
    username, auth_at = unsign_session_parts(token, settings)
    assert username == USERNAME
    assert auth_at is not None
    assert abs(auth_at - time.time()) < 60


def test_legacy_token_without_auth_at_is_valid_session_but_stale_for_fresh(
    client: TestClient, settings: ChronicleSettings
) -> None:
    """Old ``username``-only tokens still authenticate but fail require_fresh_auth."""
    from itsdangerous import TimestampSigner

    legacy = TimestampSigner(settings.secret_key).sign(USERNAME.encode()).decode()
    client.cookies.set(settings.cookie_name, legacy)
    assert client.get("/api/auth/session").status_code == 200

    # export requires fresh auth
    r = client.post(
        f"/api/workspaces/{'0' * 8}-{'0' * 4}-{'0' * 4}-{'0' * 4}-{'0' * 12}/export",
        json={"format": "json"},
    )
    assert r.status_code == 401
    body = r.json()
    detail = body.get("detail") or body
    if isinstance(detail, dict):
        assert detail.get("reason") == "reauth-required"
    else:
        assert "reauth-required" in str(body)


def test_stale_auth_at_requires_reauth(client: TestClient, settings: ChronicleSettings) -> None:
    stale = sign_session(USERNAME, settings, auth_at=time.time() - 1000)
    client.cookies.set(settings.cookie_name, stale)
    assert client.get("/api/auth/session").status_code == 200
    r = client.post(
        f"/api/workspaces/{'0' * 8}-{'0' * 4}-{'0' * 4}-{'0' * 4}-{'0' * 12}/export",
        json={"format": "markdown"},
    )
    assert r.status_code == 401
    detail = r.json().get("detail")
    assert isinstance(detail, dict)
    assert detail["reason"] == "reauth-required"


def test_fresh_auth_allows_export_endpoint_auth_check(
    client: TestClient,
) -> None:
    """Fresh login satisfies require_fresh_auth (404 for missing workspace is fine)."""
    client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    r = client.post(
        f"/api/workspaces/{'0' * 8}-{'0' * 4}-{'0' * 4}-{'0' * 4}-{'0' * 12}/export",
        json={"format": "json"},
    )
    # Not 401 reauth — either 404 (no workspace in stub pool) or other non-auth error
    assert r.status_code != 401


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
