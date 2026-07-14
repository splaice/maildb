# tests/test_chronicle.py
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from chronicle_server.chronicle import (
    AggregationTooFineError,
    BucketsRequest,
    choose_aggregation,
    resolve_aggregation,
)
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


# --- pure unit tests ---


def test_choose_aggregation_10y_920px() -> None:
    duration = timedelta(days=365 * 10 + 2)  # ~10y including leap days
    unit = choose_aggregation(duration, 920)
    # target_buckets=115; month≈122 > 115, quarter≈40 ≤ 115
    assert unit in ("month", "quarter")


def test_choose_aggregation_3d_920px() -> None:
    assert choose_aggregation(timedelta(days=3), 920) == "hour"


def test_choose_aggregation_40y_320px() -> None:
    assert choose_aggregation(timedelta(days=365 * 40), 320) == "year"


def test_explicit_unit_over_2000_raises() -> None:
    duration = timedelta(days=365 * 10)
    with pytest.raises(AggregationTooFineError) as exc_info:
        resolve_aggregation("hour", duration, 920)
    assert exc_info.value.requested == "hour"
    assert exc_info.value.smallest_valid_unit in (
        "day",
        "week",
        "month",
        "quarter",
        "year",
    )
    # Day still exceeds 2000 for 10y; week should fit.
    assert exc_info.value.smallest_valid_unit == "week"


def test_resolve_auto_matches_choose() -> None:
    duration = timedelta(days=3)
    assert resolve_aggregation("auto", duration, 920) == choose_aggregation(duration, 920)


def test_resolve_explicit_valid_unit() -> None:
    assert resolve_aggregation("month", timedelta(days=365), 920) == "month"


# --- auth ---


def test_buckets_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/api/chronicle/buckets",
        json={
            "scope": {},
            "viewport": {"from": "2020-01-01", "to": "2021-01-01"},
            "pixel_width": 920,
            "aggregation": "auto",
            "lanes": ["messages"],
        },
    )
    assert r.status_code == 401


def test_buckets_422_bad_lane(client: TestClient) -> None:
    login = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert login.status_code == 200
    r = client.post(
        "/api/chronicle/buckets",
        json={
            "scope": {},
            "viewport": {"from": "2020-01-01", "to": "2021-01-01"},
            "pixel_width": 920,
            "aggregation": "auto",
            "lanes": ["messages", "not-a-lane"],
        },
    )
    assert r.status_code == 422


def test_buckets_422_bad_unit(client: TestClient) -> None:
    login = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert login.status_code == 200
    r = client.post(
        "/api/chronicle/buckets",
        json={
            "scope": {},
            "viewport": {"from": "2020-01-01", "to": "2021-01-01"},
            "pixel_width": 920,
            "aggregation": "minute",
            "lanes": ["messages"],
        },
    )
    assert r.status_code == 422


def test_buckets_422_explicit_too_fine(client: TestClient) -> None:
    login = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert login.status_code == 200
    r = client.post(
        "/api/chronicle/buckets",
        json={
            "scope": {},
            "viewport": {"from": "2010-01-01", "to": "2020-01-01"},
            "pixel_width": 920,
            "aggregation": "hour",
            "lanes": ["messages"],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["smallest_valid_unit"] == "week"
    assert detail["requested"] == "hour"


# --- DB-backed ---


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def _insert_email(
    pool: ConnectionPool,
    *,
    date: datetime,
    source_account: str = "acct-a@example.com",
    sender_address: str = "alice@example.com",
    with_attachment: bool = False,
) -> None:
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
                %(id)s, %(mid)s, %(tid)s, 'bucket test',
                'Alice', %(saddr)s, 'example.com',
                '{}'::jsonb, %(date)s, 'body', NULL,
                %(has_att)s, %(labels)s, %(acct)s, now()
            )
            """,
            {
                "id": email_id,
                "mid": f"<bucket-{email_id}@example.com>",
                "tid": f"thread-{email_id}",
                "saddr": sender_address,
                "date": date,
                "has_att": with_attachment,
                "labels": ["INBOX"],
                "acct": source_account,
            },
        )
        if with_attachment:
            row = conn.execute(
                """
                INSERT INTO attachments (sha256, filename, content_type, size, storage_path)
                VALUES (%(sha)s, 'f.txt', 'text/plain', 4, %(path)s)
                RETURNING id
                """,
                {
                    "sha": hashlib.sha256(str(email_id).encode()).hexdigest(),
                    "path": f"/tmp/chronicle-bucket/{email_id}",
                },
            ).fetchone()
            assert row is not None
            conn.execute(
                """
                INSERT INTO email_attachments (email_id, attachment_id, filename)
                VALUES (%(eid)s, %(aid)s, 'f.txt')
                """,
                {"eid": email_id, "aid": row[0]},
            )
        conn.commit()


def _cleanup_bucket_emails(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            DELETE FROM email_attachments
            WHERE email_id IN (
                SELECT id FROM emails WHERE message_id LIKE '<bucket-%%@example.com>'
            )
            """
        )
        conn.execute(
            """
            DELETE FROM attachments
            WHERE storage_path LIKE '/tmp/chronicle-bucket/%%'
            """
        )
        conn.execute("DELETE FROM emails WHERE message_id LIKE '<bucket-%%@example.com>'")
        conn.commit()


def _assert_buckets_shape(body: dict[str, Any]) -> None:
    assert body["scope_fingerprint"].startswith("qs_")
    assert body["aggregation"] in (
        "hour",
        "day",
        "week",
        "month",
        "quarter",
        "year",
    )
    assert body["unit"] == body["aggregation"]
    assert "from" in body["viewport"]
    assert "to" in body["viewport"]
    assert isinstance(body["lanes"], dict)
    for points in body["lanes"].values():
        assert isinstance(points, list)
        for pt in points:
            assert "bucket" in pt
            assert isinstance(pt["count"], int)
            assert pt["count"] >= 0
    assert "unit" in body["density"]
    assert isinstance(body["density"]["buckets"], list)
    assert "from" in body["extent"]
    assert "to" in body["extent"]
    assert isinstance(body["generated_at"], str)
    assert len(body["generated_at"]) > 0


def test_buckets_endpoint_shape_and_filters(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_bucket_emails(db_pool)
    acct_a = "bucket-test-a@example.com"
    acct_b = "bucket-test-b@example.com"
    try:
        # Inside viewport
        _insert_email(
            db_pool,
            date=datetime(2020, 6, 15, 12, 0, tzinfo=UTC),
            source_account=acct_a,
            sender_address="alice@example.com",
            with_attachment=True,
        )
        _insert_email(
            db_pool,
            date=datetime(2020, 7, 1, 8, 0, tzinfo=UTC),
            source_account=acct_b,
            sender_address="bob@example.com",
        )
        # Outside viewport (should not appear in lanes; density still includes them)
        _insert_email(
            db_pool,
            date=datetime(2018, 1, 1, tzinfo=UTC),
            source_account=acct_a,
        )
        _insert_email(
            db_pool,
            date=datetime(2022, 1, 1, tzinfo=UTC),
            source_account=acct_a,
        )

        _login(db_client)
        # Scope to test accounts only so ambient DB rows cannot inflate counts.
        both_accounts = {"mailboxes": [acct_a, acct_b]}
        payload = {
            "scope": both_accounts,
            "viewport": {"from": "2020-01-01T00:00:00Z", "to": "2021-01-01T00:00:00Z"},
            "pixel_width": 920,
            "aggregation": "month",
            "lanes": ["messages", "attachments", "people"],
        }
        r = db_client.post("/api/chronicle/buckets", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        _assert_buckets_shape(body)

        msg_total = sum(p["count"] for p in body["lanes"]["messages"])
        assert msg_total == 2  # only viewport messages

        att_total = sum(p["count"] for p in body["lanes"]["attachments"])
        assert att_total == 1

        people_total = sum(p["count"] for p in body["lanes"]["people"])
        assert people_total == 2  # alice + bob

        # Density covers full extent (scope only) — includes outside-viewport messages
        density_total = sum(p["count"] for p in body["density"]["buckets"])
        assert density_total == 4
        assert density_total >= msg_total

        # Extent spans our seeded range under this scope
        assert body["extent"]["from"] is not None
        assert body["extent"]["to"] is not None
        assert body["extent"]["from"].startswith("2018")
        assert body["extent"]["to"].startswith("2022")

        # Mailbox filter reduces counts monotonically
        r_filtered = db_client.post(
            "/api/chronicle/buckets",
            json={
                **payload,
                "scope": {"mailboxes": [acct_a]},
            },
        )
        assert r_filtered.status_code == 200, r_filtered.text
        filtered = r_filtered.json()
        filtered_msg = sum(p["count"] for p in filtered["lanes"]["messages"])
        assert filtered_msg <= msg_total
        assert filtered_msg == 1

        # Fingerprint changes with scope
        assert filtered["scope_fingerprint"] != body["scope_fingerprint"]
    finally:
        _cleanup_bucket_emails(db_pool)


def test_buckets_request_model_defaults() -> None:
    req = BucketsRequest.model_validate({"viewport": {"from": "2020-01-01", "to": "2021-01-01"}})
    assert req.aggregation == "auto"
    assert req.pixel_width == 920
    assert set(req.lanes) == {"messages", "attachments", "people"}
