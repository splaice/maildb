# src/chronicle_server/auth.py
from __future__ import annotations

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


class LoginBody(BaseModel):
    username: str
    password: str


def _signer(settings: ChronicleSettings) -> TimestampSigner:
    return TimestampSigner(settings.secret_key)


def sign_session(username: str, settings: ChronicleSettings) -> str:
    """Return a signed session token for *username*."""
    return _signer(settings).sign(username.encode("utf-8")).decode("utf-8")


def unsign_session(token: str, settings: ChronicleSettings) -> str:
    """Verify and return the username from a session token.

    Raises BadSignature / BadTimeSignature on invalid or expired tokens.
    """
    raw = _signer(settings).unsign(token.encode("utf-8"), max_age=settings.session_max_age_s)
    return raw.decode("utf-8")


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


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> dict[str, str]:
    """Authenticate single user; set session cookie on success."""
    settings: ChronicleSettings = request.app.state.settings
    pool = request.app.state.pool

    ok = False
    if body.username == settings.username:
        try:
            _ph.verify(settings.password_hash, body.password)
            ok = True
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False

    if not ok:
        audit(pool, username=body.username, action="login_failed", detail={})
        logger.info("login_failed", username=body.username)
        raise HTTPException(status_code=401, detail="Invalid credentials")

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
