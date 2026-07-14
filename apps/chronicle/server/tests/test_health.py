# tests/test_health.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chronicle_server.health import get_archive_health
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


def _assert_nonneg_int(value: object) -> None:
    assert isinstance(value, int)
    assert value >= 0


def _assert_health_shape(body: dict[str, Any]) -> None:
    assert "coverage" in body
    cov = body["coverage"]
    assert isinstance(cov["accounts"], list)
    for acct in cov["accounts"]:
        assert "account" in acct
        _assert_nonneg_int(acct["messages"])
    assert "date_range" in cov
    assert "from" in cov["date_range"]
    assert "to" in cov["date_range"]
    for key in ("messages", "threads", "attachments", "contacts"):
        _assert_nonneg_int(cov[key])

    assert "threading" in body
    thr = body["threading"]
    for key in ("single_message_threads", "max_thread_size", "null_date_messages"):
        _assert_nonneg_int(thr[key])

    assert "extraction" in body
    ext = body["extraction"]
    for key in ("extracted", "failed", "skipped", "pending"):
        _assert_nonneg_int(ext["by_status"][key])
    assert isinstance(ext["top_failure_reasons"], list)
    for item in ext["top_failure_reasons"]:
        assert "reason" in item
        _assert_nonneg_int(item["count"])
        assert len(item["reason"]) <= 120
    assert isinstance(ext["by_content_type"], list)
    assert len(ext["by_content_type"]) <= 15
    for item in ext["by_content_type"]:
        assert "content_type" in item
        for key in ("extracted", "failed", "skipped"):
            _assert_nonneg_int(item[key])

    assert "embeddings" in body
    emb = body["embeddings"]
    for bucket in ("emails", "attachment_chunks"):
        _assert_nonneg_int(emb[bucket]["embedded"])
        _assert_nonneg_int(emb[bucket]["missing"])

    assert "topics" in body
    topics = body["topics"]
    _assert_nonneg_int(topics["topics"])
    assert isinstance(topics["coverage"], (int, float))
    assert topics["coverage"] >= 0
    assert "last_generated" in topics  # may be null

    assert "imports" in body
    assert isinstance(body["imports"], list)
    assert len(body["imports"]) <= 20
    for rec in body["imports"]:
        assert "started_at" in rec
        assert "source_account" in rec
        assert "status" in rec
        _assert_nonneg_int(rec["messages_inserted"])
        _assert_nonneg_int(rec["messages_skipped"])

    assert "audit_tail" in body
    assert isinstance(body["audit_tail"], list)
    assert len(body["audit_tail"]) <= 25
    for row in body["audit_tail"]:
        assert "at" in row
        assert "username" in row
        assert "action" in row
        assert "detail" in row
        assert row["action"] in (
            "ask",
            "events_generate",
            "workspace_export",
            "download",
        )

    assert "generated_at" in body
    assert isinstance(body["generated_at"], str)
    assert len(body["generated_at"]) > 0


def test_archive_health_requires_auth(client: TestClient) -> None:
    r = client.get("/api/health/archive")
    assert r.status_code == 401


def test_archive_health_shape_db(db_pool: ConnectionPool) -> None:
    body = get_archive_health(db_pool)
    _assert_health_shape(body)


def test_archive_health_endpoint_authenticated(db_client: TestClient) -> None:
    login = db_client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    assert login.status_code == 200
    r = db_client.get("/api/health/archive")
    assert r.status_code == 200
    _assert_health_shape(r.json())
