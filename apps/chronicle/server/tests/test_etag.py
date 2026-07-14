# tests/test_etag.py
"""ETag / If-None-Match matrix + harness unit-smoke (task 5.4)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest

from chronicle_server.archive import (
    emails_data_version,
    if_none_match,
    response_etag,
    topics_data_version,
)
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool

_PERF_HARNESS = Path(__file__).resolve().parents[1] / "perf" / "harness.py"


def _load_harness() -> Any:
    """Load perf/harness.py without requiring it to be a package under src."""
    spec = importlib.util.spec_from_file_location("chronicle_perf_harness", _PERF_HARNESS)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["chronicle_perf_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def _insert_email(pool: ConnectionPool, *, subject: str = "etag-bust") -> None:
    email_id = uuid4()
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO emails (
                id, message_id, thread_id, subject,
                sender_name, sender_address, sender_domain,
                recipients, date, body_text, body_html,
                has_attachment, labels, source_account, created_at
            ) VALUES (
                %(id)s, %(mid)s, %(tid)s, %(subject)s,
                'ETag', 'etag@example.com', 'example.com',
                '{}'::jsonb, %(date)s, 'body', NULL,
                false, %(labels)s, 'etag@example.com', now()
            )
            """,
            {
                "id": email_id,
                "mid": f"<etag-{email_id}@example.com>",
                "tid": f"thread-etag-{email_id}",
                "subject": subject,
                "date": datetime(2020, 6, 15, 12, 0, tzinfo=UTC),
                "labels": ["INBOX"],
            },
        )
        conn.commit()


def _cleanup_etag_emails(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM emails WHERE message_id LIKE '<etag-%%@example.com>'")
        conn.commit()


# --- pure unit tests ---


def test_response_etag_deterministic() -> None:
    a = response_etag("qs_abc", '{"v":1}', "10:ts")
    b = response_etag("qs_abc", '{"v":1}', "10:ts")
    c = response_etag("qs_abc", '{"v":1}', "11:ts")
    assert a == b
    assert a != c
    assert a.startswith('"') and a.endswith('"')
    assert len(a) == 66  # quotes + 64 hex


def test_if_none_match_variants() -> None:
    class _Req:
        def __init__(self, value: str | None) -> None:
            self.headers = {"if-none-match": value} if value is not None else {}

    etag = '"deadbeef"'
    assert if_none_match(_Req(etag), etag)  # type: ignore[arg-type]
    assert if_none_match(_Req(f"W/{etag}"), etag)  # type: ignore[arg-type]
    assert if_none_match(_Req(f'"other", {etag}'), etag)  # type: ignore[arg-type]
    assert if_none_match(_Req("*"), etag)  # type: ignore[arg-type]
    assert not if_none_match(_Req('"other"'), etag)  # type: ignore[arg-type]
    assert not if_none_match(_Req(None), etag)  # type: ignore[arg-type]
    assert not if_none_match(_Req(""), etag)  # type: ignore[arg-type]


# --- endpoint matrix: 200 → 304 → bust ---


def _assert_etag_cycle(
    client: TestClient,
    *,
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    bust: Any,
) -> None:
    """200 with ETag → 304 on echo → data change yields new ETag / 200."""

    def _call(headers: dict[str, str] | None = None) -> Any:
        hdrs = headers or {}
        if method == "GET":
            return client.get(path, headers=hdrs)
        return client.post(path, json=json_body, headers=hdrs)

    r1 = _call()
    assert r1.status_code == 200, r1.text
    etag = r1.headers.get("etag") or r1.headers.get("ETag")
    assert etag, f"missing ETag on {method} {path}"
    assert etag.startswith('"') and etag.endswith('"')

    headers = {"If-None-Match": etag}
    r2 = _call(headers)
    assert r2.status_code == 304, r2.text
    assert (r2.headers.get("etag") or r2.headers.get("ETag")) == etag
    assert r2.content == b"" or r2.text in ("", "null")

    bust()

    r3 = _call(headers)
    assert r3.status_code == 200, r3.text
    etag3 = r3.headers.get("etag") or r3.headers.get("ETag")
    assert etag3
    assert etag3 != etag


def test_etag_archive_summary(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_etag_emails(db_pool)
    try:
        _login(db_client)
        _assert_etag_cycle(
            db_client,
            method="GET",
            path="/api/archive/summary",
            bust=lambda: _insert_email(db_pool),
        )
    finally:
        _cleanup_etag_emails(db_pool)


def test_etag_chronicle_buckets(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_etag_emails(db_pool)
    try:
        _login(db_client)
        body = {
            "scope": {},
            "viewport": {"from": "2020-01-01T00:00:00Z", "to": "2021-01-01T00:00:00Z"},
            "pixel_width": 920,
            "aggregation": "month",
            "lanes": ["messages"],
        }
        _assert_etag_cycle(
            db_client,
            method="POST",
            path="/api/chronicle/buckets",
            json_body=body,
            bust=lambda: _insert_email(db_pool),
        )
    finally:
        _cleanup_etag_emails(db_pool)


def test_etag_chronicle_compare(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_etag_emails(db_pool)
    try:
        _login(db_client)
        body = {
            "scope": {},
            "a": {"from": "2020-01-01T00:00:00Z", "to": "2020-07-01T00:00:00Z"},
            "b": {"from": "2021-01-01T00:00:00Z", "to": "2021-07-01T00:00:00Z"},
            "pixel_width": 920,
            "lanes": ["messages"],
        }
        _assert_etag_cycle(
            db_client,
            method="POST",
            path="/api/chronicle/compare",
            json_body=body,
            bust=lambda: _insert_email(db_pool),
        )
    finally:
        _cleanup_etag_emails(db_pool)


def test_etag_topics_list(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_etag_emails(db_pool)
    try:
        _login(db_client)
        _assert_etag_cycle(
            db_client,
            method="GET",
            path="/api/topics",
            bust=lambda: _insert_email(db_pool),
        )
    finally:
        _cleanup_etag_emails(db_pool)


def test_emails_data_version_changes_on_insert(db_pool: ConnectionPool) -> None:
    _cleanup_etag_emails(db_pool)
    try:
        before = emails_data_version(db_pool)
        _insert_email(db_pool)
        after = emails_data_version(db_pool)
        assert before != after
    finally:
        _cleanup_etag_emails(db_pool)


def test_topics_data_version_includes_derived_marker(db_pool: ConnectionPool) -> None:
    marker = topics_data_version(db_pool)
    assert "topics:" in marker
    assert "events:" in marker
    # Base emails marker is a prefix before the derived segment.
    assert "|" in marker


# --- harness unit-smoke (mocked httpx; no live server) ---


def test_harness_percentile_and_stats() -> None:
    h = _load_harness()
    assert h.percentile([10.0, 20.0, 30.0, 40.0, 50.0], 50) == 30.0
    assert h.percentile([10.0], 95) == 10.0
    stats = h.scenario_stats([100.0, 50.0, 60.0, 55.0, 70.0])
    assert stats["cold_ms"] == 100.0
    assert stats["n"] == 5
    assert stats["warm_p50_ms"] is not None
    assert stats["warm_p50_ms"] <= 70.0


def test_harness_evaluate_pass_fail_and_hard_floor() -> None:
    h = _load_harness()
    targets = {"archive_summary": 1000.0, "search_exact": 2000.0}

    ok_stats = h.scenario_stats([900.0, 800.0, 850.0, 820.0, 810.0])
    ok = h.evaluate_scenario("archive_summary", ok_stats, targets=targets)
    assert ok["pass"] is True
    assert ok["hard_floor_fail"] is False
    assert ok["target_ms"] == 1000.0

    soft_stats = h.scenario_stats([2000.0, 1500.0, 1600.0, 1550.0, 1520.0])
    soft = h.evaluate_scenario("archive_summary", soft_stats, targets=targets)
    assert soft["pass"] is False
    assert soft["hard_floor_fail"] is False  # 1500 < 1000*2

    hard_stats = h.scenario_stats([5000.0, 3000.0, 3100.0, 3050.0, 3020.0])
    hard = h.evaluate_scenario("archive_summary", hard_stats, targets=targets)
    assert hard["pass"] is False
    assert hard["hard_floor_fail"] is True

    skipped = h.evaluate_scenario(
        "search_hybrid",
        h.scenario_stats([]),
        targets=targets,
        skipped=True,
        skip_reason="embedding down",
    )
    assert skipped["skipped"] is True
    assert skipped["pass"] is None
    assert skipped["hard_floor_fail"] is False


def test_harness_build_report_and_hard_floor_exit_math() -> None:
    h = _load_harness()
    targets = {"archive_summary": 100.0, "search_exact": 200.0}
    scenarios = [
        h.evaluate_scenario(
            "archive_summary",
            h.scenario_stats([50.0, 40.0, 45.0, 42.0, 41.0]),
            targets=targets,
        ),
        h.evaluate_scenario(
            "search_exact",
            h.scenario_stats([1000.0, 500.0, 520.0, 510.0, 505.0]),
            targets=targets,
        ),
    ]
    report = h.build_report(
        scenarios,
        environment={"row_counts": {"messages": 42}},
        targets=targets,
    )
    assert "generated_at" in report
    assert report["targets_ms"] == targets
    assert report["environment"]["row_counts"]["messages"] == 42
    assert "search_exact" in report["failures"]
    assert "search_exact" in report["hard_floor_failures"]
    assert "archive_summary" not in report["failures"]
    assert h.hard_floor_failed(report) is True

    soft_only = [
        h.evaluate_scenario(
            "archive_summary",
            h.scenario_stats([200.0, 150.0, 160.0, 155.0, 152.0]),
            targets=targets,
        )
    ]
    soft_report = h.build_report(
        soft_only,
        environment={},
        targets=targets,
    )
    assert soft_report["failures"] == ["archive_summary"]
    assert soft_report["hard_floor_failures"] == []
    assert h.hard_floor_failed(soft_report) is False


def test_harness_runner_mocked_httpx(tmp_path: Path) -> None:
    """Scenario runner with mocked httpx transport produces report JSON shape."""
    h = _load_harness()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            return httpx.Response(200, json={"ok": True})
        if path == "/api/archive/summary":
            return httpx.Response(
                200,
                json={
                    "accounts": [],
                    "date_range": {
                        "from": "2010-01-01T00:00:00Z",
                        "to": "2020-01-01T00:00:00Z",
                    },
                    "counts": {
                        "messages": 100,
                        "threads": 50,
                        "attachments": 10,
                        "contacts": 5,
                    },
                    "extraction": {
                        "extracted": 0,
                        "failed": 0,
                        "skipped": 0,
                        "pending": 0,
                    },
                    "embedding": {"embedded": 0, "missing": 100},
                    "versions": {"schema": "maildb", "api": "0.1.0"},
                },
            )
        if path == "/api/chronicle/buckets":
            return httpx.Response(
                200,
                json={
                    "scope_fingerprint": "qs_test",
                    "aggregation": "year",
                    "unit": "year",
                    "viewport": {"from": "2010-01-01", "to": "2020-01-01"},
                    "lanes": {"messages": []},
                    "density": {"unit": "year", "buckets": []},
                    "extent": {"from": "2010-01-01", "to": "2020-01-01"},
                    "generated_at": "2020-01-01T00:00:00Z",
                },
            )
        if path == "/api/search":
            body = json.loads(request.content.decode() or "{}")
            mode = body.get("mode", "hybrid")
            if mode == "hybrid":
                # Simulate embedding unavailable via degraded marker.
                return httpx.Response(
                    200,
                    json={
                        "results": [],
                        "scope": {},
                        "scope_fingerprint": "qs_x",
                        "mode": "hybrid",
                        "took_ms": 1,
                        "degraded": {"embedding": "unavailable"},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [],
                    "scope": {},
                    "scope_fingerprint": "qs_x",
                    "mode": "exact",
                    "took_ms": 1,
                },
            )
        if path == "/api/sources/list":
            return httpx.Response(
                200,
                json={
                    "items": [{"id": "msg_1", "date": "2015-01-01T00:00:00Z"}],
                    "next_cursor": None,
                    "scope_fingerprint": "qs_x",
                },
            )
        if path == "/api/topics":
            return httpx.Response(200, json={"topics": []})
        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, base_url="http://test") as client:
        report = h.run_harness(
            base_url="http://test",
            username="owner",
            password="secret",
            n=3,
            out_dir=tmp_path,
            client=client,
        )

    assert "scenarios" in report
    names = [s["name"] for s in report["scenarios"]]
    assert names == [
        "archive_summary",
        "buckets_full_extent",
        "buckets_1y_month",
        "search_exact",
        "search_hybrid",
        "sources_list",
        "topics_list",
    ]
    hybrid = next(s for s in report["scenarios"] if s["name"] == "search_hybrid")
    assert hybrid["skipped"] is True
    assert report["environment"]["row_counts"]["messages"] == 100
    assert "targets_ms" in report
    assert "failures" in report
    assert "hard_floor_failures" in report
    results_path = Path(report["results_path"])
    assert results_path.is_file()
    on_disk = json.loads(results_path.read_text(encoding="utf-8"))
    assert on_disk["scenarios"][0]["name"] == "archive_summary"


def test_harness_main_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = _load_harness()

    soft_report = {
        "results_path": str(tmp_path / "r.json"),
        "scenarios": [
            {
                "name": "archive_summary",
                "skipped": False,
                "pass": False,
                "hard_floor_fail": False,
                "warm_p50_ms": 1500.0,
                "target_ms": 1000.0,
            }
        ],
        "failures": ["archive_summary"],
        "hard_floor_failures": [],
    }
    hard_report = {
        **soft_report,
        "scenarios": [
            {
                "name": "archive_summary",
                "skipped": False,
                "pass": False,
                "hard_floor_fail": True,
                "warm_p50_ms": 3000.0,
                "target_ms": 1000.0,
            }
        ],
        "hard_floor_failures": ["archive_summary"],
    }

    monkeypatch.setattr(
        h,
        "run_harness",
        lambda **_kw: soft_report,
    )
    assert h.main(["--user", "u", "--password", "p"]) == 0

    monkeypatch.setattr(
        h,
        "run_harness",
        lambda **_kw: hard_report,
    )
    assert h.main(["--user", "u", "--password", "p"]) == 1
