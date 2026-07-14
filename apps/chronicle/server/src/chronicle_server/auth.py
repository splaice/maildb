# src/chronicle_server/auth.py
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, Response
from itsdangerous import BadSignature, BadTimeSignature, TimestampSigner
from pydantic import BaseModel

from chronicle_server.db import audit, update_last_login

if TYPE_CHECKING:
    from chronicle_server.config import ChronicleSettings

logger = structlog.get_logger()

router = APIRouter(tags=["auth"])

_ph = PasswordHasher()

# Session payload: ``username|auth_at`` (unix seconds). Old tokens without
# ``auth_at`` remain valid sessions but are stale for fresh-auth checks.
_SESSION_SEP = "|"


class LoginBody(BaseModel):
    username: str
    password: str


class LoginRateLimiter:
    """In-memory fixed-window limiter (no deps; single-user app).

    ≥ *max_failures* failed logins per key within *window_s* → limited.
    Success should call :meth:`reset`. Clock is injectable for unit tests.
    """

    def __init__(
        self,
        max_failures: int = 5,
        window_s: int = 900,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._clock: Callable[[], float] = clock or time.time
        self._failures: dict[str, list[float]] = {}

    def _prune(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_s
        times = [t for t in self._failures.get(key, []) if t > cutoff]
        if times:
            self._failures[key] = times
        else:
            self._failures.pop(key, None)
        return times

    def is_limited(self, *keys: str) -> tuple[bool, int]:
        """Return ``(limited, retry_after_s)`` if any key is at/over the cap."""
        now = self._clock()
        limited = False
        retry_after = 0
        for key in keys:
            if not key:
                continue
            times = self._prune(key, now)
            if len(times) >= self.max_failures:
                limited = True
                oldest = min(times)
                ra = int(self.window_s - (now - oldest)) + 1
                retry_after = max(retry_after, max(1, ra))
        return limited, retry_after

    def record_failure(self, *keys: str) -> None:
        now = self._clock()
        for key in keys:
            if not key:
                continue
            times = self._prune(key, now)
            times.append(now)
            self._failures[key] = times

    def reset(self, *keys: str) -> None:
        for key in keys:
            self._failures.pop(key, None)


def _signer(settings: ChronicleSettings) -> TimestampSigner:
    return TimestampSigner(settings.secret_key)


def sign_session(
    username: str,
    settings: ChronicleSettings,
    *,
    auth_at: float | None = None,
) -> str:
    """Return a signed session token ``username|auth_at`` for *username*."""
    ts = int(auth_at if auth_at is not None else time.time())
    payload = f"{username}{_SESSION_SEP}{ts}"
    return _signer(settings).sign(payload.encode("utf-8")).decode("utf-8")


def _parse_payload(raw: str) -> tuple[str, float | None]:
    """Split payload into (username, auth_at). Old tokens → auth_at None."""
    if _SESSION_SEP not in raw:
        return raw, None
    username, _, auth_part = raw.partition(_SESSION_SEP)
    if not username:
        return raw, None
    try:
        return username, float(auth_part)
    except ValueError:
        # Malformed auth_at: treat whole raw as username-less stale session
        return username, None


def unsign_session_parts(token: str, settings: ChronicleSettings) -> tuple[str, float | None]:
    """Verify token; return (username, auth_at). auth_at is None for legacy tokens."""
    raw = _signer(settings).unsign(token.encode("utf-8"), max_age=settings.session_max_age_s)
    return _parse_payload(raw.decode("utf-8"))


def unsign_session(token: str, settings: ChronicleSettings) -> str:
    """Verify and return the username from a session token.

    Raises BadSignature / BadTimeSignature on invalid or expired tokens.
    """
    username, _auth_at = unsign_session_parts(token, settings)
    return username


def require_user(request: Request) -> str:
    """FastAPI dependency: return authenticated username or raise 401."""
    settings: ChronicleSettings = request.app.state.settings
    cookie = request.cookies.get(settings.cookie_name)
    if not cookie:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return unsign_session(cookie, settings)
    except (BadSignature, BadTimeSignature):
        raise HTTPException(status_code=401, detail="Not authenticated") from None


def require_fresh_auth(max_age_s: int = 900) -> Callable[[Request], str]:
    """Dependency factory: session auth_at must be fresher than *max_age_s*.

    Legacy tokens without auth_at are treated as stale for fresh-auth purposes
    but remain valid sessions for :func:`require_user`.
    On failure: 401 with ``{"reason": "reauth-required"}`` in detail.
    """

    def _dependency(request: Request) -> str:
        settings: ChronicleSettings = request.app.state.settings
        cookie = request.cookies.get(settings.cookie_name)
        if not cookie:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            username, auth_at = unsign_session_parts(cookie, settings)
        except (BadSignature, BadTimeSignature):
            raise HTTPException(status_code=401, detail="Not authenticated") from None
        now = time.time()
        if auth_at is None or (now - auth_at) > max_age_s:
            raise HTTPException(
                status_code=401,
                detail={"reason": "reauth-required"},
            )
        return username

    return _dependency


def _set_session_cookie(response: Response, token: str, settings: ChronicleSettings) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_max_age_s,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def _clear_session_cookie(response: Response, settings: ChronicleSettings) -> None:
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def _client_ip(request: Request) -> str:
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def _limiter_keys(username: str, ip: str) -> tuple[str, str]:
    return (f"user:{username}", f"ip:{ip}")


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> dict[str, str]:
    """Authenticate single user; set session cookie on success."""
    settings: ChronicleSettings = request.app.state.settings
    pool = request.app.state.pool
    limiter: LoginRateLimiter = request.app.state.login_rate_limiter
    ip = _client_ip(request)
    keys = _limiter_keys(body.username, ip)

    limited, retry_after = limiter.is_limited(*keys)
    if limited:
        audit(
            pool,
            username=body.username,
            action="login_ratelimited",
            detail={"ip": ip, "retry_after": retry_after},
        )
        logger.info("login_ratelimited", username=body.username, ip=ip)
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts",
            headers={"Retry-After": str(retry_after)},
        )

    ok = False
    if body.username == settings.username:
        try:
            _ph.verify(settings.password_hash, body.password)
            ok = True
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False

    if not ok:
        limiter.record_failure(*keys)
        audit(pool, username=body.username, action="login_failed", detail={})
        logger.info("login_failed", username=body.username)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    limiter.reset(*keys)
    token = sign_session(body.username, settings)
    _set_session_cookie(response, token, settings)
    update_last_login(pool, body.username)
    audit(pool, username=body.username, action="login", detail={})
    logger.info("login_ok", username=body.username)
    return {"username": body.username}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    """Clear session cookie and audit logout when a session was present."""
    settings: ChronicleSettings = request.app.state.settings
    pool = request.app.state.pool
    username: str | None = None
    cookie = request.cookies.get(settings.cookie_name)
    if cookie:
        try:
            username = unsign_session(cookie, settings)
        except (BadSignature, BadTimeSignature):
            username = None
    _clear_session_cookie(response, settings)
    if username is not None:
        audit(pool, username=username, action="logout", detail={})
        logger.info("logout", username=username)
    return {"status": "ok"}


@router.get("/session")
def session(request: Request) -> dict[str, str]:
    """Return current session username or 401."""
    username = require_user(request)
    return {"username": username}
