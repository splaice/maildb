# tests/test_generate.py
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from chronicle_server.gateway import AskSource
from chronicle_server.generate import (
    detect_bursts,
    evidence_strength_for_claims,
    parse_extracted_events,
    resolve_claim_citations,
)
from chronicle_server.ids import encode_source_id
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def _src(marker: str = "S1", source_id: str = "msg_1") -> AskSource:
    return AskSource(
        marker=marker,
        source_id=source_id,
        source_type="message",
        date="2015-06-17",
        sender="alice@example.com",
        title="Roof",
        plain_text="We selected metal roofing.",
        block_text="We selected metal roofing.",
        excerpt="We selected metal roofing.",
        location={"char_start": 0, "char_end": 26},
        excerpt_hash="abc",
    )


# --- detect_bursts unit table ---


def test_detect_bursts_flat_series_none() -> None:
    buckets = [(date(2015, 1, d), 10) for d in range(1, 15)]
    assert detect_bursts(buckets) == []


def test_detect_bursts_empty_and_all_zero() -> None:
    assert detect_bursts([]) == []
    assert detect_bursts([(date(2015, 1, 1), 0)]) == []


def test_detect_bursts_single_spike() -> None:
    # Background: 5 msgs/day; one spike of 50
    buckets = [(date(2015, 6, d), 5) for d in range(1, 20)]
    buckets[9] = (date(2015, 6, 10), 50)  # day 10
    bursts = detect_bursts(buckets)
    assert len(bursts) == 1
    b = bursts[0]
    assert b.start == date(2015, 6, 9)  # pad -1
    assert b.end == date(2015, 6, 11)  # pad +1
    assert b.total >= 50


def test_detect_bursts_adjacent_merge() -> None:
    # Two spikes 2 days apart should merge; pad ±1
    buckets = [(date(2015, 3, d), 3) for d in range(1, 20)]
    buckets[4] = (date(2015, 3, 5), 40)
    buckets[6] = (date(2015, 3, 7), 40)  # within 3 days of 5
    bursts = detect_bursts(buckets)
    assert len(bursts) == 1
    assert bursts[0].start == date(2015, 3, 4)
    assert bursts[0].end == date(2015, 3, 8)
    assert bursts[0].total >= 80


def test_detect_bursts_gap_no_merge() -> None:
    # Spikes 5 days apart should not merge (gap > 3)
    buckets = [(date(2015, 4, d), 3) for d in range(1, 25)]
    buckets[4] = (date(2015, 4, 5), 40)
    buckets[9] = (date(2015, 4, 10), 40)  # 5 days after previous spike
    bursts = detect_bursts(buckets)
    assert len(bursts) == 2


def test_detect_bursts_sigma_zero_guard() -> None:
    # All non-zero days identical → σ=0 → threshold = max(mean×2, 5)
    # mean=10 → threshold=20; a day with 10 is not a burst
    flat = [(date(2015, 5, d), 10) for d in range(1, 10)]
    assert detect_bursts(flat) == []
    # Spike above 20 is a burst
    buckets = list(flat)
    buckets[3] = (date(2015, 5, 4), 25)
    bursts = detect_bursts(buckets)
    assert len(bursts) == 1
    assert bursts[0].total >= 25


def test_detect_bursts_sigma_zero_min_threshold_5() -> None:
    # All equal non-zero counts → σ=0 → threshold = max(mean×2, 5).
    # mean=1 → mean×2=2 but floor is 5; no day reaches 5 → no bursts.
    buckets = [(date(2015, 7, d), 1) for d in range(1, 10)]
    assert detect_bursts(buckets) == []
    # Inject a day at the floor (5). Values are no longer equal so σ>0, but
    # the floor case is still exercised by the all-equal series above.
    buckets[2] = (date(2015, 7, 3), 5)
    assert len(detect_bursts(buckets)) == 1


def test_detect_bursts_cap_max() -> None:
    # Dense low background + many isolated high spikes; cap at max_bursts.
    buckets: list[tuple[date, int]] = []
    d0 = date(2016, 1, 1)
    # ~200 quiet days at count 2
    for i in range(200):
        buckets.append((d0 + timedelta(days=i), 2))
    # 10 isolated spikes of 100 every 20 days (gap >> 3)
    for i in range(10):
        spike_day = d0 + timedelta(days=5 + i * 20)
        buckets.append((spike_day, 100))
    bursts = detect_bursts(buckets, max_bursts=5)
    assert len(bursts) == 5
    totals = [b.total for b in bursts]
    assert totals == sorted(totals, reverse=True)


# --- parse / citations ---


def test_resolve_markers_drops_unresolved() -> None:
    sources = [_src("S1", "msg_a"), _src("S2", "msg_b")]
    cits = resolve_claim_citations(["S1", "S99", "[S2]"], sources)
    assert len(cits) == 2
    assert {c["source_id"] for c in cits} == {"msg_a", "msg_b"}
    for c in cits:
        assert "excerpt" in c
        assert "excerpt_hash" in c
        assert "location" in c
        assert "source_type" in c


def test_parse_drops_zero_citation_claims() -> None:
    sources = [_src("S1", "msg_a")]
    content = json.dumps(
        [
            {
                "title": "Roof decision",
                "event_type": "decision",
                "date": "2015-06-17",
                "date_precision": "day",
                "summary": "Metal roof",
                "claims": [
                    {
                        "text": "Metal selected",
                        "status": "direct",
                        "source_markers": ["S99"],  # unresolved → drop claim
                    },
                    {
                        "text": "Also this",
                        "status": "supported",
                        "source_markers": ["S1"],
                    },
                ],
            }
        ]
    )
    events = parse_extracted_events(
        content,
        window_start=date(2015, 6, 1),
        window_end=date(2015, 6, 30),
        sources=sources,
    )
    assert len(events) == 1
    claims = events[0]["claims"]
    assert len(claims) == 1
    assert claims[0][0] == "Also this"
    assert len(claims[0][2]) >= 1


def test_parse_drops_event_when_all_claims_uncited() -> None:
    sources = [_src("S1")]
    content = json.dumps(
        [
            {
                "title": "Ghost",
                "event_type": "meeting",
                "date": "2015-06-17",
                "date_precision": "day",
                "summary": "x",
                "claims": [
                    {"text": "No evidence", "status": "direct", "source_markers": ["S9"]},
                ],
            }
        ]
    )
    assert (
        parse_extracted_events(
            content,
            window_start=date(2015, 6, 1),
            window_end=date(2015, 6, 30),
            sources=sources,
        )
        == []
    )


def test_parse_whitelist_and_enum_rejections() -> None:
    sources = [_src("S1", "msg_a")]
    # bad event_type, bad precision, missing title — all dropped
    content = json.dumps(
        [
            {
                "title": "Ok",
                "event_type": "not_a_type",
                "date": "2015-06-17",
                "date_precision": "day",
                "claims": [{"text": "c", "status": "direct", "source_markers": ["S1"]}],
            },
            {
                "title": "Ok2",
                "event_type": "decision",
                "date": "2015-06-17",
                "date_precision": "hour",  # not allowed in extraction
                "claims": [{"text": "c", "status": "direct", "source_markers": ["S1"]}],
            },
            {
                "event_type": "decision",
                "date": "2015-06-17",
                "date_precision": "day",
                "claims": [{"text": "c", "status": "direct", "source_markers": ["S1"]}],
            },
            {
                "title": "Good",
                "event_type": "decision",
                "date": "2015-06-17",
                "date_precision": "day",
                "evil": "drop",
                "claims": [
                    {
                        "text": "c",
                        "status": "direct",
                        "source_markers": ["S1"],
                        "extra": 1,
                    }
                ],
            },
        ]
    )
    events = parse_extracted_events(
        content,
        window_start=date(2015, 6, 1),
        window_end=date(2015, 6, 30),
        sources=sources,
    )
    assert len(events) == 1
    assert events[0]["title"] == "Good"
    assert "evil" not in events[0]


def test_parse_clamps_date_into_window() -> None:
    sources = [_src("S1", "msg_a")]
    content = json.dumps(
        [
            {
                "title": "Early",
                "event_type": "travel",
                "date": "2010-01-01",
                "date_precision": "week",
                "claims": [{"text": "went", "status": "direct", "source_markers": ["S1"]}],
            }
        ]
    )
    events = parse_extracted_events(
        content,
        window_start=date(2015, 6, 1),
        window_end=date(2015, 6, 30),
        sources=sources,
    )
    assert events[0]["date"] == date(2015, 6, 1)


def test_evidence_strength_levels() -> None:
    one = [("a", "direct", [{"source_id": "1"}])]
    two = [("a", "direct", [{"source_id": "1"}, {"source_id": "2"}])]
    mixed = [
        ("a", "direct", [{"source_id": "1"}]),
        ("b", "supported", [{"source_id": "1"}, {"source_id": "2"}]),
    ]
    assert evidence_strength_for_claims(two) == "high"
    assert evidence_strength_for_claims(one) == "medium"
    assert evidence_strength_for_claims(mixed) == "medium"
    assert evidence_strength_for_claims([]) == "low"


# --- HTTP / integration ---


def test_generate_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/api/events/generate",
        json={"scope": {}, "viewport": {"from": "2015-01-01", "to": "2016-01-01"}},
    )
    assert r.status_code == 401


def test_generate_unavailable_returns_available_false(client: TestClient) -> None:
    _login(client)
    client.app.state.model_available = False  # type: ignore[attr-defined]
    r = client.post(
        "/api/events/generate",
        json={"scope": {}, "viewport": {"from": "2015-01-01", "to": "2016-01-01"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"available": False}


# Isolate generate fixtures via distinctive account tag (cleanup only).
_GEN_ACCOUNT = "gen-burst@example.com"


def _seed_email(
    pool: ConnectionPool,
    *,
    subject: str = "Burst seed",
    body_text: str = "We selected standing-seam metal roofing.",
    sender_address: str = "alice@example.com",
    date: str = "1991-06-10T12:00:00+00:00",
    source_account: str = _GEN_ACCOUNT,
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
                'Alice', %(saddr)s, 'example.com',
                '{"to": ["bob@example.com"]}'::jsonb, %(date)s::timestamptz,
                %(body)s, null, false, %(labels)s, %(acct)s, now()
            )
            """,
            {
                "id": eid,
                "mid": f"<gen-{eid}@example.com>",
                "tid": f"thread-gen-{eid}",
                "subject": subject,
                "saddr": sender_address,
                "date": date,
                "body": body_text,
                "labels": ["INBOX"],
                "acct": source_account,
            },
        )
        # MailDB account filter joins email_accounts (not emails.source_account alone).
        conn.commit()
    return {
        "id": eid,
        "source_id": encode_source_id("msg", eid),
        "subject": subject,
        "date": date,
    }


def _cleanup_gen(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM app_events")
        conn.execute(
            "DELETE FROM emails WHERE message_id LIKE '<gen-%%@example.com>' "
            "OR source_account = %(acct)s",
            {"acct": _GEN_ACCOUNT},
        )
        conn.execute("DELETE FROM app_audit WHERE action = 'events_generate'")
        conn.commit()


def _gen_body(viewport_from: str, viewport_to: str) -> dict[str, Any]:
    # Empty scope: MailDB exact leg filters account via email_accounts join,
    # while day-buckets use emails.source_account — keep them aligned by
    # not scoping mailbox and isolating via rare 1991 date windows instead.
    return {
        "scope": {},
        "viewport": {"from": viewport_from, "to": viewport_to},
    }


def _seed_burst_day(
    pool: ConnectionPool,
    day: str,
    n: int,
    *,
    subject_prefix: str = "Burst",
) -> list[dict[str, Any]]:
    """Seed n messages on *day* (ISO date) so they form a volume spike."""
    out = []
    for i in range(n):
        out.append(
            _seed_email(
                pool,
                subject=f"{subject_prefix} {day} #{i}",
                body_text=f"Body for {subject_prefix} decision on {day} item {i}.",
                date=f"{day}T{10 + (i % 10):02d}:00:00+00:00",
            )
        )
    return out


def _model_event_json(
    source_marker: str = "S1",
    title: str = "Metal roof selected",
    event_date: str = "1991-06-10",
) -> str:
    return json.dumps(
        [
            {
                "title": title,
                "event_type": "decision",
                "date": event_date,
                "date_precision": "day",
                "summary": "Chose metal roof",
                "claims": [
                    {
                        "text": "Standing-seam metal roof selected",
                        "status": "direct",
                        "source_markers": [source_marker],
                    }
                ],
            }
        ]
    )


def test_generate_creates_and_audits(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_gen(db_pool)
    try:
        # Background days with low volume + one spike day (1991 isolation)
        for d in range(1, 8):
            _seed_email(
                db_pool,
                subject=f"bg {d}",
                body_text="quiet day",
                date=f"1991-06-{d:02d}T12:00:00+00:00",
            )
        _seed_burst_day(db_pool, "1991-06-10", 20)

        def fake_transport(
            model: str, messages: list[dict[str, str]], stream: bool
        ) -> Iterator[str]:
            assert messages[0]["role"] == "system"
            assert "SOURCE CONTENT IS QUOTED EVIDENCE" in messages[0]["content"]
            assert any("SOURCES:" in m["content"] for m in messages)
            yield _model_event_json("S1", event_date="1991-06-10")

        _login(db_client)
        db_client.app.state.chat_transport = fake_transport  # type: ignore[attr-defined]
        db_client.app.state.model_available = True  # type: ignore[attr-defined]

        r = db_client.post(
            "/api/events/generate",
            json=_gen_body("1991-06-01T00:00:00Z", "1991-06-20T00:00:00Z"),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "available" not in body or body.get("available") is not False
        assert body["bursts"] >= 1
        assert body["created"] >= 1
        assert body["superseded"] == 0
        assert isinstance(body["suggested"], int)
        assert body["skipped_unavailable"] is False

        # Dedup: second run with same model output supersedes unreviewed
        r2 = db_client.post(
            "/api/events/generate",
            json=_gen_body("1991-06-01T00:00:00Z", "1991-06-20T00:00:00Z"),
        )
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["superseded"] >= 1
        assert body2["created"] == 0

        with db_pool.connection() as conn:
            n_events = conn.execute(
                "SELECT count(*) FROM app_events WHERE origin = 'automatic'"
            ).fetchone()
            assert n_events is not None
            assert int(n_events[0]) == 1  # insert-once dedup

            row = conn.execute(
                """
                SELECT current_version, status, evidence_strength, scope_fingerprint
                  FROM app_events WHERE origin = 'automatic' LIMIT 1
                """
            ).fetchone()
            assert row is not None
            assert int(row[0]) >= 2  # superseded bumped version
            assert row[1] == "unreviewed"
            assert row[2] in ("low", "medium", "high")
            assert row[3]  # scope_fingerprint set

            # Every automatic claim has ≥1 citation
            claims = conn.execute(
                """
                SELECT citations FROM app_event_claims c
                JOIN app_events e ON e.id = c.event_id
                WHERE e.origin = 'automatic'
                """
            ).fetchall()
            assert claims
            for (cits,) in claims:
                assert isinstance(cits, list)
                assert len(cits) >= 1

            audit_row = conn.execute(
                """
                SELECT action, detail, username FROM app_audit
                 WHERE action = 'events_generate'
                 ORDER BY at DESC LIMIT 1
                """
            ).fetchone()
            assert audit_row is not None
            assert audit_row[0] == "events_generate"
            detail = audit_row[1]
            assert isinstance(detail, dict)
            assert "scope_fingerprint" in detail
            assert "bursts" in detail
            assert "created" in detail
            assert "model" in detail
            assert "policy_version" in detail
            assert audit_row[2] == USERNAME
    finally:
        _cleanup_gen(db_pool)


def test_generate_no_clobber_confirmed(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _cleanup_gen(db_pool)
    try:
        for d in range(1, 8):
            _seed_email(
                db_pool,
                subject=f"bg {d}",
                body_text="quiet",
                date=f"1991-06-{d:02d}T12:00:00+00:00",
            )
        _seed_burst_day(db_pool, "1991-06-10", 20)

        def fake_transport(
            model: str, messages: list[dict[str, str]], stream: bool
        ) -> Iterator[str]:
            yield _model_event_json("S1", title="Metal roof selected", event_date="1991-06-10")

        _login(db_client)
        db_client.app.state.chat_transport = fake_transport  # type: ignore[attr-defined]
        db_client.app.state.model_available = True  # type: ignore[attr-defined]

        # First generate creates unreviewed
        r = db_client.post(
            "/api/events/generate",
            json=_gen_body("1991-06-01T00:00:00Z", "1991-06-20T00:00:00Z"),
        )
        assert r.status_code == 200
        assert r.json()["created"] >= 1

        with db_pool.connection() as conn:
            row = conn.execute(
                "SELECT id, current_version, status FROM app_events WHERE origin='automatic'"
            ).fetchone()
            assert row is not None
            eid = row[0]
            # Analyst confirms
            conn.execute(
                """
                UPDATE app_events
                   SET status = 'confirmed', updated_at = now()
                 WHERE id = %(id)s
                """,
                {"id": eid},
            )
            conn.commit()
            before_ver = int(row[1])
            before_status = "confirmed"

        # Regen → suggested, not clobber
        r2 = db_client.post(
            "/api/events/generate",
            json=_gen_body("1991-06-01T00:00:00Z", "1991-06-20T00:00:00Z"),
        )
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["suggested"] >= 1
        assert body2["created"] == 0
        assert body2["superseded"] == 0

        with db_pool.connection() as conn:
            row = conn.execute(
                """
                SELECT current_version, status FROM app_events WHERE id = %(id)s
                """,
                {"id": eid},
            ).fetchone()
            assert row is not None
            # BOTH must be unchanged (AI-005)
            assert int(row[0]) == before_ver
            assert row[1] == before_status

            max_ver = conn.execute(
                "SELECT max(version) FROM app_event_versions WHERE event_id = %(id)s",
                {"id": eid},
            ).fetchone()
            assert max_ver is not None
            assert int(max_ver[0]) == before_ver + 1  # suggestion version appended
    finally:
        _cleanup_gen(db_pool)


def test_generate_no_clobber_edited_and_dismissed(
    db_client: TestClient, db_pool: ConnectionPool
) -> None:
    _cleanup_gen(db_pool)
    try:
        for d in range(1, 8):
            _seed_email(
                db_pool,
                subject=f"bg2 {d}",
                body_text="quiet",
                date=f"1991-07-{d:02d}T12:00:00+00:00",
            )
        _seed_burst_day(db_pool, "1991-07-10", 20, subject_prefix="July")

        titles = {"edited": "Edited roof event", "dismissed": "Dismissed roof event"}

        def make_transport(title: str):
            def fake_transport(
                model: str, messages: list[dict[str, str]], stream: bool
            ) -> Iterator[str]:
                yield _model_event_json("S1", title=title, event_date="1991-07-10")

            return fake_transport

        _login(db_client)

        for status, title in titles.items():
            db_client.app.state.chat_transport = make_transport(title)  # type: ignore[attr-defined]
            db_client.app.state.model_available = True  # type: ignore[attr-defined]

            r = db_client.post(
                "/api/events/generate",
                json=_gen_body("1991-07-01T00:00:00Z", "1991-07-20T00:00:00Z"),
            )
            assert r.status_code == 200
            assert r.json()["created"] >= 1

            with db_pool.connection() as conn:
                row = conn.execute(
                    """
                    SELECT id, current_version FROM app_events
                     WHERE origin='automatic' AND title = %(t)s
                    """,
                    {"t": title},
                ).fetchone()
                assert row is not None
                eid, ver = row[0], int(row[1])
                conn.execute(
                    "UPDATE app_events SET status = %(s)s WHERE id = %(id)s",
                    {"s": status, "id": eid},
                )
                conn.commit()

            r2 = db_client.post(
                "/api/events/generate",
                json=_gen_body("1991-07-01T00:00:00Z", "1991-07-20T00:00:00Z"),
            )
            assert r2.status_code == 200
            assert r2.json()["suggested"] >= 1

            with db_pool.connection() as conn:
                row = conn.execute(
                    "SELECT current_version, status FROM app_events WHERE id = %(id)s",
                    {"id": eid},
                ).fetchone()
                assert row is not None
                assert int(row[0]) == ver
                assert row[1] == status
    finally:
        _cleanup_gen(db_pool)


def test_health_audit_tail_includes_events_generate(
    db_client: TestClient, db_pool: ConnectionPool
) -> None:
    _cleanup_gen(db_pool)
    try:
        for d in range(1, 6):
            _seed_email(
                db_pool,
                subject=f"hbg {d}",
                body_text="quiet",
                date=f"1991-08-{d:02d}T12:00:00+00:00",
            )
        _seed_burst_day(db_pool, "1991-08-10", 20, subject_prefix="Aug")

        def fake_transport(
            model: str, messages: list[dict[str, str]], stream: bool
        ) -> Iterator[str]:
            yield _model_event_json("S1", title="Aug decision", event_date="1991-08-10")

        _login(db_client)
        db_client.app.state.chat_transport = fake_transport  # type: ignore[attr-defined]
        db_client.app.state.model_available = True  # type: ignore[attr-defined]
        db_client.post(
            "/api/events/generate",
            json=_gen_body("1991-08-01T00:00:00Z", "1991-08-20T00:00:00Z"),
        )

        from chronicle_server.health import get_archive_health

        health = get_archive_health(db_pool)
        assert "audit_tail" in health
        assert isinstance(health["audit_tail"], list)
        actions = {row["action"] for row in health["audit_tail"]}
        assert "events_generate" in actions
        for row in health["audit_tail"]:
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
    finally:
        _cleanup_gen(db_pool)
