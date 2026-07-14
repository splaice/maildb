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


def test_buckets_request_accepts_top_people_lane() -> None:
    req = BucketsRequest.model_validate(
        {
            "viewport": {"from": "2020-01-01", "to": "2021-01-01"},
            "lanes": ["messages", "top_people"],
        }
    )
    assert "top_people" in req.lanes


def _insert_contact(
    pool: ConnectionPool,
    *,
    contact_id: Any,
    display_name: str | None,
    addresses: list[tuple[str, bool]],
) -> None:
    """Insert a contact and its addresses. addresses: (address, is_user)."""
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO contacts (id, display_name)
            VALUES (%(id)s, %(name)s)
            ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name
            """,
            {"id": contact_id, "name": display_name},
        )
        for address, is_user in addresses:
            conn.execute(
                """
                INSERT INTO contact_addresses (address, contact_id, is_user)
                VALUES (%(addr)s, %(cid)s, %(is_user)s)
                ON CONFLICT (address) DO UPDATE
                  SET contact_id = EXCLUDED.contact_id,
                      is_user = EXCLUDED.is_user
                """,
                {"addr": address, "cid": contact_id, "is_user": is_user},
            )
        conn.commit()


def _cleanup_top_people_fixtures(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            DELETE FROM contact_addresses
            WHERE address LIKE '%%@top-people-test.example'
               OR address LIKE 'user-only@%%'
            """
        )
        conn.execute(
            """
            DELETE FROM contacts
            WHERE display_name LIKE 'TopPeople%%'
               OR display_name = 'UserOnly'
            """
        )
        conn.commit()
    _cleanup_bucket_emails(pool)


def test_top_people_lane_shape_and_rules(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_top_people_fixtures(db_pool)
    acct = "top-people-acct@example.com"
    # 9 external contacts + 1 user-only; top-8 should exclude user and lowest volume.
    contact_ids = [uuid4() for _ in range(9)]
    user_contact = uuid4()
    try:
        for i, cid in enumerate(contact_ids):
            addr = f"person{i}@top-people-test.example"
            _insert_contact(
                db_pool,
                contact_id=cid,
                display_name=f"TopPeople {i}" if i % 2 == 0 else None,
                addresses=[(addr, False)],
            )
            # Volume = i+1 messages so person8 has most, person0 least.
            for n in range(i + 1):
                _insert_email(
                    db_pool,
                    date=datetime(2020, 3, 1 + (n % 20), 12, 0, tzinfo=UTC),
                    source_account=acct,
                    sender_address=addr,
                )
        # User-only contact with high volume must be excluded.
        user_addr = "user-only@top-people-test.example"
        _insert_contact(
            db_pool,
            contact_id=user_contact,
            display_name="UserOnly",
            addresses=[(user_addr, True)],
        )
        for _ in range(50):
            _insert_email(
                db_pool,
                date=datetime(2020, 4, 1, 12, 0, tzinfo=UTC),
                source_account=acct,
                sender_address=user_addr,
            )

        _login(db_client)
        payload = {
            "scope": {"mailboxes": [acct]},
            "viewport": {"from": "2020-01-01T00:00:00Z", "to": "2021-01-01T00:00:00Z"},
            "pixel_width": 920,
            "aggregation": "month",
            "lanes": ["top_people"],
        }
        r = db_client.post("/api/chronicle/buckets", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        tp = body["lanes"]["top_people"]
        assert isinstance(tp, dict)
        assert "contacts" in tp
        contacts = tp["contacts"]
        assert isinstance(contacts, list)
        assert len(contacts) <= 8
        assert len(contacts) == 8  # 9 external, lowest dropped

        ids = {c["contact_id"] for c in contacts}
        assert str(user_contact) not in ids
        assert str(contact_ids[0]) not in ids  # lowest volume excluded

        # Ranked by volume desc: person8 .. person1
        totals = []
        for c in contacts:
            assert "contact_id" in c
            assert "display_name" in c
            assert isinstance(c["buckets"], list)
            for pt in c["buckets"]:
                assert "bucket" in pt
                assert isinstance(pt["count"], int)
                assert pt["count"] >= 0
            totals.append(sum(pt["count"] for pt in c["buckets"]))
        assert totals == sorted(totals, reverse=True)

        # Null display_name falls back to address
        odd = next(c for c in contacts if c["contact_id"] == str(contact_ids[7]))
        assert odd["display_name"] == "person7@top-people-test.example"
        even = next(c for c in contacts if c["contact_id"] == str(contact_ids[8]))
        assert even["display_name"] == "TopPeople 8"

        # Scope filter is monotonic (narrower mailbox → fewer or equal totals)
        other_acct = "top-people-other@example.com"
        r_empty = db_client.post(
            "/api/chronicle/buckets",
            json={**payload, "scope": {"mailboxes": [other_acct]}},
        )
        assert r_empty.status_code == 200, r_empty.text
        empty_contacts = r_empty.json()["lanes"]["top_people"]["contacts"]
        empty_total = sum(sum(pt["count"] for pt in c["buckets"]) for c in empty_contacts)
        full_total = sum(totals)
        assert empty_total <= full_total
        assert empty_total == 0
    finally:
        _cleanup_top_people_fixtures(db_pool)


def test_buckets_request_accepts_events_lane() -> None:
    req = BucketsRequest.model_validate(
        {
            "viewport": {"from": "2020-01-01", "to": "2021-01-01"},
            "lanes": ["messages", "events"],
        }
    )
    assert "events" in req.lanes


def _cleanup_events_for_lane(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM app_events")
        conn.commit()


def test_events_lane_cap_and_dismissed_exclusion(
    db_client: TestClient, db_pool: ConnectionPool
) -> None:
    """Events lane returns sparse marks (not bucket counts), excludes dismissed, caps 500."""
    from chronicle_server.chronicle import EVENTS_LANE_CAP

    _cleanup_events_for_lane(db_pool)
    try:
        with db_pool.connection() as conn:
            # One dismissed inside viewport — must not appear
            conn.execute(
                """
                INSERT INTO app_events (
                    title, time_start, time_end, time_precision, origin,
                    event_type, status, current_version
                ) VALUES (
                    'Dismissed mark', '2020-06-15T00:00:00Z', null, 'day', 'analyst',
                    'meeting', 'dismissed', 1
                )
                """
            )
            # Span event overlapping viewport
            conn.execute(
                """
                INSERT INTO app_events (
                    title, time_start, time_end, time_precision, origin,
                    event_type, status, current_version
                ) VALUES (
                    'Span mark', '2020-05-20T00:00:00Z', '2020-06-10T00:00:00Z', 'day',
                    'analyst', 'travel', 'confirmed', 1
                )
                """
            )
            # Point event inside
            conn.execute(
                """
                INSERT INTO app_events (
                    title, time_start, time_end, time_precision, origin,
                    event_type, status, current_version
                ) VALUES (
                    'Inside mark', '2020-07-01T00:00:00Z', null, 'day', 'source',
                    'document', 'unreviewed', 1
                )
                """
            )
            # Outside viewport
            conn.execute(
                """
                INSERT INTO app_events (
                    title, time_start, time_end, time_precision, origin,
                    event_type, status, current_version
                ) VALUES (
                    'Outside mark', '2019-01-01T00:00:00Z', null, 'day', 'analyst',
                    'meeting', 'confirmed', 1
                )
                """
            )
            conn.commit()

        _login(db_client)
        r = db_client.post(
            "/api/chronicle/buckets",
            json={
                "scope": {},
                "viewport": {"from": "2020-01-01T00:00:00Z", "to": "2021-01-01T00:00:00Z"},
                "pixel_width": 920,
                "aggregation": "month",
                "lanes": ["events"],
            },
        )
        assert r.status_code == 200, r.text
        lane = r.json()["lanes"]["events"]
        assert isinstance(lane, dict)
        assert "events" in lane
        assert "truncated" in lane
        assert lane["truncated"] is False
        titles = {m["title"] for m in lane["events"]}
        assert "Inside mark" in titles
        assert "Span mark" in titles
        assert "Dismissed mark" not in titles
        assert "Outside mark" not in titles
        for m in lane["events"]:
            assert "event_id" in m
            assert "time_start" in m
            assert "time_precision" in m
            assert "origin" in m
            assert "event_type" in m
            assert "status" in m
            assert "count" not in m  # not bucket counts

        # Cap + truncated flag
        _cleanup_events_for_lane(db_pool)
        with db_pool.connection() as conn:
            for i in range(EVENTS_LANE_CAP + 5):
                conn.execute(
                    """
                    INSERT INTO app_events (
                        title, time_start, time_end, time_precision, origin,
                        event_type, status, current_version
                    ) VALUES (
                        %(title)s, %(ts)s, null, 'day', 'analyst',
                        'communication', 'confirmed', 1
                    )
                    """,
                    {
                        "title": f"Cap {i}",
                        "ts": datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=i),
                    },
                )
            conn.commit()

        r2 = db_client.post(
            "/api/chronicle/buckets",
            json={
                "scope": {},
                "viewport": {"from": "2020-01-01T00:00:00Z", "to": "2021-01-01T00:00:00Z"},
                "pixel_width": 920,
                "aggregation": "month",
                "lanes": ["events"],
            },
        )
        assert r2.status_code == 200, r2.text
        lane2 = r2.json()["lanes"]["events"]
        assert lane2["truncated"] is True
        assert len(lane2["events"]) == EVENTS_LANE_CAP
    finally:
        _cleanup_events_for_lane(db_pool)
