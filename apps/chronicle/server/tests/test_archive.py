# tests/test_archive.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chronicle_server.archive import get_archive_summary
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


def _assert_summary_shape(body: dict[str, Any]) -> None:
    assert "accounts" in body
    assert isinstance(body["accounts"], list)
    for acct in body["accounts"]:
        assert "account" in acct
        assert "messages" in acct
        assert isinstance(acct["messages"], int)
        assert acct["messages"] >= 0

    assert "date_range" in body
    assert "from" in body["date_range"]
    assert "to" in body["date_range"]

    assert "counts" in body
    for key in ("messages", "threads", "attachments", "contacts"):
        assert key in body["counts"]
        assert isinstance(body["counts"][key], int)
        assert body["counts"][key] >= 0

    assert "extraction" in body
    for key in ("extracted", "failed", "skipped", "pending"):
        assert key in body["extraction"]
        assert isinstance(body["extraction"][key], int)
        assert body["extraction"][key] >= 0

    assert "embedding" in body
    for key in ("embedded", "missing"):
        assert key in body["embedding"]
        assert isinstance(body["embedding"][key], int)
        assert body["embedding"][key] >= 0

    assert body["versions"]["schema"] == "maildb"
    assert body["versions"]["api"] == "0.1.0"


def test_archive_summary_shape_db(db_pool: ConnectionPool) -> None:
    body = get_archive_summary(db_pool)
    _assert_summary_shape(body)


def test_archive_summary_endpoint_authenticated(db_client: TestClient) -> None:
    login = db_client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert login.status_code == 200
    r = db_client.get("/api/archive/summary")
    assert r.status_code == 200
    _assert_summary_shape(r.json())
