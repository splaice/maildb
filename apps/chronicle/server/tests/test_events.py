# tests/test_events.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from chronicle_server.ids import encode_source_id
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def _seed_email(
    pool: ConnectionPool,
    *,
    subject: str = "Event seed",
    sender_name: str = "Alice",
    sender_address: str = "alice@example.com",
    date: str = "2015-06-01T12:00:00+00:00",
) -> dict[str, Any]:
    eid = uuid4()
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
                %(sname)s, %(saddr)s, 'example.com',
                '{"to": ["bob@example.com"]}'::jsonb, %(date)s::timestamptz,
                'body text', null, false, %(labels)s, 'test@example.com', now()
            )
            """,
            {
                "id": eid,
                "mid": f"<evt-{eid}@example.com>",
                "tid": f"thread-{eid}",
                "subject": subject,
                "sname": sender_name,
                "saddr": sender_address,
                "date": date,
                "labels": ["INBOX"],
            },
        )
        conn.commit()
    return {
        "id": eid,
        "source_id": encode_source_id("msg", eid),
        "subject": subject,
        "sender_name": sender_name,
        "sender_address": sender_address,
        "date": date,
    }


def _create_event(
    client: TestClient,
    *,
    title: str = "Analyst event",
    time_start: str = "2015-06-15T00:00:00Z",
    time_end: str | None = None,
    time_precision: str = "day",
    event_type: str = "meeting",
    summary: str | None = "A summary",
    claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": title,
        "time_start": time_start,
        "time_precision": time_precision,
        "event_type": event_type,
    }
    if time_end is not None:
        body["time_end"] = time_end
    if summary is not None:
        body["summary"] = summary
    if claims is not None:
        body["claims"] = claims
    r = client.post("/api/events", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _insert_event_row(
    pool: ConnectionPool,
    *,
    title: str = "Seeded",
    time_start: datetime,
    time_end: datetime | None = None,
    origin: str = "automatic",
    status: str = "unreviewed",
    event_type: str = "communication",
    time_precision: str = "day",
) -> str:
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO app_events (
                title, time_start, time_end, time_precision, origin,
                event_type, status, current_version
            ) VALUES (
                %(title)s, %(ts)s, %(te)s, %(prec)s, %(origin)s,
                %(etype)s, %(status)s, 1
            )
            RETURNING id
            """,
            {
                "title": title,
                "ts": time_start,
                "te": time_end,
                "prec": time_precision,
                "origin": origin,
                "etype": event_type,
                "status": status,
            },
        ).fetchone()
        assert row is not None
        eid = row[0]
        conn.execute(
            """
            INSERT INTO app_event_versions (
                event_id, version, author, title, summary, derivation
            ) VALUES (
                %(eid)s, 1, 'automatic', %(title)s, null, '{}'::jsonb
            )
            """,
            {"eid": eid, "title": title},
        )
        conn.commit()
    return str(eid)


def _cleanup_events(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM app_events")
        conn.execute("DELETE FROM emails WHERE message_id LIKE '<evt-%%@example.com>'")
        conn.execute("DELETE FROM app_audit WHERE action LIKE 'event_%%'")
        conn.commit()


# --- auth ---


def test_events_require_auth(client: TestClient) -> None:
    create = client.post(
        "/api/events",
        json={"title": "x", "time_start": "2015-01-01"},
    )
    assert create.status_code == 401
    fake = str(uuid4())
    assert client.get(f"/api/events/{fake}").status_code == 401
    assert (
        client.patch(
            f"/api/events/{fake}",
            json={"current_version": 1, "title": "y"},
        ).status_code
        == 401
    )
    assert client.delete(f"/api/events/{fake}").status_code == 401
    assert (
        client.post(
            "/api/events/list",
            json={"viewport": {"from": "2015-01-01", "to": "2016-01-01"}},
        ).status_code
        == 401
    )


# --- CRUD + versioning ---


def test_create_get_event_versioned(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_events(db_pool)
    email = _seed_email(db_pool)
    _login(db_client)

    created = _create_event(
        db_client,
        title="Roof decision",
        claims=[{"text": "Chose metal roof", "citations": [email["source_id"]]}],
    )
    assert created["origin"] == "analyst"
    assert created["status"] == "confirmed"
    assert created["current_version"] == 1
    assert created["version"]["version"] == 1
    assert created["version"]["author"] == "analyst"
    assert created["version"]["title"] == "Roof decision"
    assert created["summary"] == "A summary"
    assert len(created["claims"]) == 1
    claim = created["claims"][0]
    assert claim["text"] == "Chose metal roof"
    assert claim["status"] == "direct"
    assert len(claim["citations"]) == 1
    cit = claim["citations"][0]
    assert cit["source_id"] == email["source_id"]
    assert cit["subject"] == "Event seed"
    assert cit["sender"] == "Alice"

    # Version row exists and is immutable baseline
    with db_pool.connection() as conn:
        vrows = conn.execute(
            "SELECT version, author, title FROM app_event_versions WHERE event_id = %(id)s",
            {"id": created["id"]},
        ).fetchall()
    assert len(vrows) == 1
    assert vrows[0][0] == 1
    assert vrows[0][1] == "analyst"

    got = db_client.get(f"/api/events/{created['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == created["id"]


def test_edit_creates_version_2_immutable_v1(
    db_client: TestClient, db_pool: ConnectionPool
) -> None:
    _cleanup_events(db_pool)
    _login(db_client)
    created = _create_event(db_client, title="V1 title", summary="v1 summary")

    r = db_client.patch(
        f"/api/events/{created['id']}",
        json={
            "current_version": 1,
            "title": "V2 title",
            "summary": "v2 summary",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_version"] == 2
    assert body["status"] == "edited"
    assert body["title"] == "V2 title"
    assert body["summary"] == "v2 summary"
    assert body["version"]["version"] == 2
    assert body["version"]["author"] == "analyst"

    with db_pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT version, title, summary
              FROM app_event_versions
             WHERE event_id = %(id)s
             ORDER BY version
            """,
            {"id": created["id"]},
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == 1 and rows[0][1] == "V1 title" and rows[0][2] == "v1 summary"
    assert rows[1][0] == 2 and rows[1][1] == "V2 title" and rows[1][2] == "v2 summary"


def test_optimistic_concurrency_409(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_events(db_pool)
    _login(db_client)
    created = _create_event(db_client)

    r = db_client.patch(
        f"/api/events/{created['id']}",
        json={"current_version": 99, "title": "stale"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "version_conflict"


def test_status_transitions(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_events(db_pool)
    _login(db_client)
    created = _create_event(db_client)
    eid = created["id"]
    ver = created["current_version"]

    # dismiss
    r = db_client.patch(
        f"/api/events/{eid}",
        json={"current_version": ver, "status": "dismissed"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "dismissed"
    assert r.json()["current_version"] == ver  # no version bump for status-only

    # restore
    r = db_client.patch(
        f"/api/events/{eid}",
        json={"current_version": ver, "status": "unreviewed"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "unreviewed"

    # confirm
    r = db_client.patch(
        f"/api/events/{eid}",
        json={"current_version": ver, "status": "confirmed"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"

    # invalid: restore from confirmed
    r = db_client.patch(
        f"/api/events/{eid}",
        json={"current_version": ver, "status": "unreviewed"},
    )
    assert r.status_code == 422


def test_citation_validation_404(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_events(db_pool)
    _login(db_client)
    r = db_client.post(
        "/api/events",
        json={
            "title": "Bad cit",
            "time_start": "2015-01-01T00:00:00Z",
            "claims": [{"text": "x", "citations": ["msg_999999999999999999999"]}],
        },
    )
    # malformed or missing → 404
    assert r.status_code == 404

    r = db_client.post(
        "/api/events",
        json={
            "title": "Unknown cit",
            "time_start": "2015-01-01T00:00:00Z",
            "claims": [
                {
                    "text": "x",
                    "citations": [encode_source_id("msg", uuid4())],
                }
            ],
        },
    )
    assert r.status_code == 404


def test_delete_guard_403_non_analyst(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_events(db_pool)
    _login(db_client)
    auto_id = _insert_event_row(
        db_pool,
        title="Auto",
        time_start=datetime(2015, 6, 1, tzinfo=UTC),
        origin="automatic",
    )
    r = db_client.delete(f"/api/events/{auto_id}")
    assert r.status_code == 403

    created = _create_event(db_client, title="Deletable")
    r = db_client.delete(f"/api/events/{created['id']}")
    assert r.status_code == 204
    assert db_client.get(f"/api/events/{created['id']}").status_code == 404


def test_list_viewport_intersection_span(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_events(db_pool)
    _login(db_client)

    # Point event inside
    _create_event(db_client, title="Inside", time_start="2015-06-15T00:00:00Z")
    # Span event that overlaps viewport [2015-06-01, 2015-07-01)
    _create_event(
        db_client,
        title="Span",
        time_start="2015-05-20T00:00:00Z",
        time_end="2015-06-10T00:00:00Z",
    )
    # Outside (ends before viewport)
    _create_event(
        db_client,
        title="Before",
        time_start="2015-01-01T00:00:00Z",
        time_end="2015-02-01T00:00:00Z",
    )
    # Dismissed excluded by default
    dismissed = _create_event(db_client, title="Dismissed", time_start="2015-06-20T00:00:00Z")
    db_client.patch(
        f"/api/events/{dismissed['id']}",
        json={"current_version": 1, "status": "dismissed"},
    )

    r = db_client.post(
        "/api/events/list",
        json={
            "scope": {},
            "viewport": {"from": "2015-06-01T00:00:00Z", "to": "2015-07-01T00:00:00Z"},
        },
    )
    assert r.status_code == 200, r.text
    titles = {item["title"] for item in r.json()["items"]}
    assert "Inside" in titles
    assert "Span" in titles
    assert "Before" not in titles
    assert "Dismissed" not in titles

    r2 = db_client.post(
        "/api/events/list",
        json={
            "scope": {},
            "viewport": {"from": "2015-06-01T00:00:00Z", "to": "2015-07-01T00:00:00Z"},
            "include_dismissed": True,
        },
    )
    titles2 = {item["title"] for item in r2.json()["items"]}
    assert "Dismissed" in titles2


def test_audit_rows(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_events(db_pool)
    _login(db_client)
    created = _create_event(db_client, title="Audited")
    db_client.patch(
        f"/api/events/{created['id']}",
        json={"current_version": 1, "title": "Edited"},
    )
    db_client.patch(
        f"/api/events/{created['id']}",
        json={"current_version": 2, "status": "dismissed"},
    )

    with db_pool.connection() as conn:
        actions = {
            row[0]
            for row in conn.execute(
                """
                SELECT action FROM app_audit
                 WHERE action LIKE 'event_%%'
                   AND detail->>'event_id' = %(eid)s
                """,
                {"eid": created["id"]},
            ).fetchall()
        }
    assert "event_create" in actions
    assert "event_edit" in actions
    assert "event_dismiss" in actions


def _insert_suggestion_version(
    pool: ConnectionPool,
    event_id: str,
    *,
    version: int,
    title: str = "Suggested title",
    summary: str = "Suggested summary",
    author: str = "automatic",
    claims: list[tuple[str, str, list[dict[str, Any]]]] | None = None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO app_event_versions (
                event_id, version, author, title, summary, derivation
            ) VALUES (
                %(eid)s, %(ver)s, %(author)s, %(title)s, %(summary)s,
                '{"process_version":"event-v1","model_route":"local"}'::jsonb
            )
            """,
            {
                "eid": event_id,
                "ver": version,
                "author": author,
                "title": title,
                "summary": summary,
            },
        )
        if claims:
            for pos, (text, status, cits) in enumerate(claims):
                conn.execute(
                    """
                    INSERT INTO app_event_claims (
                        event_id, version, position, text, status, citations
                    ) VALUES (
                        %(eid)s, %(ver)s, %(pos)s, %(text)s, %(status)s, %(cits)s::jsonb
                    )
                    """,
                    {
                        "eid": event_id,
                        "ver": version,
                        "pos": pos,
                        "text": text,
                        "status": status,
                        "cits": json.dumps(cits),
                    },
                )
        conn.commit()


# --- versions / adopt / conflicts (task 3.3) ---


def test_versions_endpoint_shape_and_suggestion_labeling(
    db_client: TestClient, db_pool: ConnectionPool
) -> None:
    _cleanup_events(db_pool)
    email = _seed_email(db_pool)
    _login(db_client)
    created = _create_event(
        db_client,
        title="V1",
        claims=[{"text": "Claim A", "citations": [email["source_id"]], "status": "direct"}],
    )
    eid = created["id"]
    # Analyst edit → v2 is current
    r = db_client.patch(
        f"/api/events/{eid}",
        json={"current_version": 1, "title": "V2 analyst"},
    )
    assert r.status_code == 200
    assert r.json()["current_version"] == 2
    assert r.json()["has_suggestions"] is False

    # Higher automatic suggestion v3 (not auto-applied)
    _insert_suggestion_version(
        db_pool,
        eid,
        version=3,
        title="V3 auto suggestion",
        claims=[
            (
                "Suggested claim",
                "supported",
                [
                    {
                        "source_id": email["source_id"],
                        "source_type": "message",
                        "excerpt": None,
                        "excerpt_hash": None,
                        "location": None,
                    }
                ],
            )
        ],
    )

    got = db_client.get(f"/api/events/{eid}")
    assert got.status_code == 200
    body = got.json()
    assert body["current_version"] == 2
    assert body["has_suggestions"] is True
    assert body["title"] == "V2 analyst"

    versions = db_client.get(f"/api/events/{eid}/versions")
    assert versions.status_code == 200, versions.text
    payload = versions.json()
    assert payload["event_id"] == eid
    assert payload["current_version"] == 2
    assert len(payload["versions"]) == 3
    by_ver = {v["version"]: v for v in payload["versions"]}
    for v in payload["versions"]:
        assert "author" in v
        assert "title" in v
        assert "summary" in v
        assert "derivation" in v
        assert "created_at" in v
        assert "claims" in v
        assert "is_suggestion" in v
    assert by_ver[1]["is_suggestion"] is False
    assert by_ver[2]["is_suggestion"] is False
    assert by_ver[3]["is_suggestion"] is True
    assert by_ver[3]["author"] == "automatic"
    assert by_ver[3]["title"] == "V3 auto suggestion"
    assert len(by_ver[3]["claims"]) == 1
    assert by_ver[3]["claims"][0]["text"] == "Suggested claim"


def test_adopt_happy_404_409_and_audit(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_events(db_pool)
    _login(db_client)
    created = _create_event(db_client, title="Base")
    eid = created["id"]
    _insert_suggestion_version(db_pool, eid, version=2, title="Adopt me")

    # 404 unknown version
    r404 = db_client.post(
        f"/api/events/{eid}/adopt/99",
        json={"current_version": 1},
    )
    assert r404.status_code == 404

    # 409 stale current_version
    r409 = db_client.post(
        f"/api/events/{eid}/adopt/2",
        json={"current_version": 99},
    )
    assert r409.status_code == 409
    assert r409.json()["detail"]["error"] == "version_conflict"

    # Happy path
    r = db_client.post(
        f"/api/events/{eid}/adopt/2",
        json={"current_version": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_version"] == 2
    assert body["status"] == "edited"
    assert body["title"] == "Adopt me"
    assert body["has_suggestions"] is False

    with db_pool.connection() as conn:
        actions = [
            row[0]
            for row in conn.execute(
                """
                SELECT action FROM app_audit
                 WHERE action = 'event_adopt'
                   AND detail->>'event_id' = %(eid)s
                """,
                {"eid": eid},
            ).fetchall()
        ]
    assert actions == ["event_adopt"]


def test_get_event_conflicts_field(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_events(db_pool)
    email = _seed_email(db_pool)
    _login(db_client)
    created = _create_event(
        db_client,
        title="Conflicted",
        claims=[
            {
                "text": "Direct claim",
                "citations": [email["source_id"]],
                "status": "direct",
            },
            {
                "text": "Conflicting claim",
                "citations": [email["source_id"]],
                "status": "conflicting",
            },
            {
                "text": "Unresolved alone",
                "citations": [],
                "status": "unresolved",
            },
        ],
    )
    got = db_client.get(f"/api/events/{created['id']}")
    assert got.status_code == 200
    body = got.json()
    assert "conflicts" in body
    assert "has_suggestions" in body
    assert body["has_suggestions"] is False
    positions = {c["claim_position"] for c in body["conflicts"]}
    # Conflicting claim + source-overlap direct/conflicting pair
    assert 1 in positions  # conflicting claim
    assert 0 in positions  # overlapping source with direct vs conflicting
    assert 2 not in positions
    for c in body["conflicts"]:
        assert "statuses" in c
        assert isinstance(c["statuses"], list)
