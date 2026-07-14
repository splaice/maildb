# tests/conftest.py
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from chronicle_server.app import create_app
from chronicle_server.config import ChronicleSettings

PASSWORD = "test-password-correct"
USERNAME = "owner"


@pytest.fixture
def password_hash() -> str:
    return PasswordHasher().hash(PASSWORD)


@pytest.fixture
def settings(password_hash: str) -> ChronicleSettings:
    return ChronicleSettings(
        database_url="postgresql://localhost/maildb",
        secret_key="test-secret-key-not-for-production",
        password_hash=password_hash,
        username=USERNAME,
        session_max_age_s=43200,
        cookie_secure=False,
        cookie_name="chronicle_session",
        _env_file=None,  # type: ignore[call-arg]
    )


def _make_stub_pool() -> MagicMock:
    """Pool that no-ops on connection/execute/commit for unit tests."""
    conn = MagicMock()
    empty = MagicMock(
        fetchall=MagicMock(return_value=[]),
        fetchone=MagicMock(return_value=None),
    )
    conn.execute = MagicMock(return_value=empty)
    conn.commit = MagicMock()

    @contextmanager
    def connection() -> Iterator[MagicMock]:
        yield conn

    pool = MagicMock()
    pool.connection = connection
    pool.close = MagicMock()
    return pool


@pytest.fixture
def stub_pool() -> MagicMock:
    return _make_stub_pool()


@pytest.fixture
def client(
    settings: ChronicleSettings,
    stub_pool: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setattr("chronicle_server.app.create_pool", lambda _s: stub_pool)
    monkeypatch.setattr("chronicle_server.app.init_app_tables", lambda _p: None)
    monkeypatch.setattr("chronicle_server.app.ensure_user", lambda _p, _u: None)
    app = create_app(settings)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def short_session_client(
    password_hash: str,
    stub_pool: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """Client with 1s session max age for expiry tests."""
    settings = ChronicleSettings(
        database_url="postgresql://localhost/maildb",
        secret_key="test-secret-key-not-for-production",
        password_hash=password_hash,
        username=USERNAME,
        session_max_age_s=1,
        cookie_secure=False,
        cookie_name="chronicle_session",
        _env_file=None,  # type: ignore[call-arg]
    )
    monkeypatch.setattr("chronicle_server.app.create_pool", lambda _s: stub_pool)
    monkeypatch.setattr("chronicle_server.app.init_app_tables", lambda _p: None)
    monkeypatch.setattr("chronicle_server.app.ensure_user", lambda _p, _u: None)
    app = create_app(settings)
    with TestClient(app) as tc:
        yield tc


# --- DB-backed fixtures (skip if PostgreSQL unreachable) ---


@pytest.fixture(scope="session")
def db_settings() -> ChronicleSettings:
    url = os.environ.get(
        "MAILDB_TEST_DATABASE_URL",
        "postgresql://maildb_test@localhost:5432/maildb_test",
    )
    return ChronicleSettings(
        database_url=url,
        secret_key="db-test-secret",
        password_hash=PasswordHasher().hash(PASSWORD),
        username=USERNAME,
        cookie_secure=False,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture(scope="session")
def db_pool(db_settings: ChronicleSettings) -> Iterator[Any]:
    import psycopg

    from chronicle_server.db import create_pool, ensure_user, init_app_tables

    try:
        with psycopg.connect(db_settings.database_url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as exc:
        pytest.skip(f"PostgreSQL test database unavailable: {exc}")

    pool = create_pool(db_settings)
    init_app_tables(pool)
    ensure_user(pool, db_settings.username)
    yield pool
    pool.close()


@pytest.fixture
def db_client(
    db_settings: ChronicleSettings,
    db_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setattr("chronicle_server.app.create_pool", lambda _s: db_pool)
    monkeypatch.setattr("chronicle_server.app.init_app_tables", lambda _p: None)
    monkeypatch.setattr("chronicle_server.app.ensure_user", lambda _p, _u: None)
    # Prevent TestClient lifespan from closing the session-scoped pool.
    monkeypatch.setattr(db_pool, "close", lambda: None)
    app = create_app(db_settings)
    with TestClient(app) as tc:
        yield tc
