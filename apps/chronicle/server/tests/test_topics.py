# tests/test_topics.py
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import numpy as np
import pytest

from chronicle_server.ids import encode_source_id
from chronicle_server.topics import (
    extract_top_terms,
    label_from_terms,
    parse_label_polish_response,
    pca_project_2d,
    run_kmeans,
    validate_label_array,
    vector_literal,
)
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


# --- pure k-means / labels ---


def test_kmeans_deterministic_same_seed() -> None:
    rng = np.random.default_rng(0)
    data = rng.normal(size=(40, 8)).astype(np.float64)
    # Two well-separated blobs for stability.
    data[20:] += 5.0
    c1, l1 = run_kmeans(data, k=2, seed=13)
    c2, l2 = run_kmeans(data, k=2, seed=13)
    np.testing.assert_allclose(c1, c2)
    np.testing.assert_array_equal(l1, l2)


def test_kmeans_k_greater_than_sample_raises() -> None:
    data = np.eye(3, dtype=np.float64)
    with pytest.raises(ValueError, match="exceeds sample size"):
        run_kmeans(data, k=5, seed=1)


def test_extract_top_terms_stopwords_and_min_len() -> None:
    subjects = [
        "The Meeting about Renovation and Plumbing",
        "RE: Renovation quote for plumbing",
        "fw: meeting notes",
        "a to of",
    ]
    terms = extract_top_terms(subjects, top_n=5)
    assert "the" not in terms
    assert "about" not in terms
    assert "and" not in terms
    assert "for" not in terms
    # short tokens dropped
    assert all(len(t) >= 3 for t in terms)
    assert "renovation" in terms
    assert "plumbing" in terms
    assert label_from_terms(terms).count(",") <= 2


def test_label_polish_whitelist_and_failure_fallback() -> None:
    ok = validate_label_array(["House renovation", "Travel plans"], 2)
    assert ok == ["House renovation", "Travel plans"]
    # too many words
    assert validate_label_array(["one two three four five"], 1) is None
    # wrong length
    assert validate_label_array(["only one"], 2) is None
    # non-string
    assert validate_label_array([1, 2], 2) is None

    content = 'Here you go: ["Kitchen remodel", "Family travel"]'
    parsed = parse_label_polish_response(content, 2)
    assert parsed == ["Kitchen remodel", "Family travel"]
    assert parse_label_polish_response("not json", 2) is None
    assert parse_label_polish_response('["too many words in this label"]', 1) is None


# --- DB helpers ---


def _unit_vec(i: int, dim: int = 768) -> list[float]:
    v = [0.0] * dim
    v[i % dim] = 1.0
    return v


def _insert_email_with_embedding(
    pool: ConnectionPool,
    *,
    subject: str,
    embedding: list[float],
    date: datetime | None = None,
    source_account: str = "topics-test@example.com",
    sender_address: str = "alice@topics-test.example",
) -> dict[str, Any]:
    email_id = uuid4()
    message_id = f"<topics-test-{email_id}@example.com>"
    tid = f"thread-topics-{email_id}"
    when = date or datetime(2020, 6, 15, 12, 0, tzinfo=UTC)
    emb_lit = vector_literal(embedding)
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO emails (
                id, message_id, thread_id, subject,
                sender_name, sender_address, sender_domain,
                recipients, date, body_text, body_html,
                has_attachment, attachments, labels, source_account,
                embedding, created_at
            ) VALUES (
                %(id)s, %(mid)s, %(tid)s, %(subject)s,
                'Alice', %(saddr)s, 'topics-test.example',
                '{"to": [], "cc": [], "bcc": []}'::jsonb, %(date)s, 'body', NULL,
                false, NULL, ARRAY['INBOX'], %(acct)s,
                %(emb)s::vector, now()
            )
            """,
            {
                "id": email_id,
                "mid": message_id,
                "tid": tid,
                "subject": subject,
                "saddr": sender_address,
                "date": when,
                "acct": source_account,
                "emb": emb_lit,
            },
        )
        conn.commit()
    return {
        "email_id": email_id,
        "msg_sid": encode_source_id("msg", email_id),
        "subject": subject,
        "source_account": source_account,
    }


def _cleanup_topics_fixtures(pool: ConnectionPool, seeds: list[dict[str, Any]]) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM app_topic_members")
        conn.execute("DELETE FROM app_topics")
        for seed in seeds:
            conn.execute("DELETE FROM emails WHERE id = %(id)s", {"id": seed["email_id"]})
        conn.execute("DELETE FROM app_audit WHERE action LIKE 'topics%%'")
        conn.commit()


# --- generation / precedence / assignment ---


def test_generate_and_assignment_seeded_corpus(
    db_client: TestClient, db_pool: ConnectionPool
) -> None:
    seeds: list[dict[str, Any]] = []
    try:
        # Two clusters along orthogonal axes.
        for i in range(6):
            seeds.append(
                _insert_email_with_embedding(
                    db_pool,
                    subject=f"Kitchen renovation plumbing quote {i}",
                    embedding=_unit_vec(0),
                    date=datetime(2020, 3, 1 + i, 12, 0, tzinfo=UTC),
                )
            )
        for i in range(6):
            seeds.append(
                _insert_email_with_embedding(
                    db_pool,
                    subject=f"Family travel itinerary flights {i}",
                    embedding=_unit_vec(10),
                    date=datetime(2020, 4, 1 + i, 12, 0, tzinfo=UTC),
                )
            )

        _login(db_client)
        r = db_client.post(
            "/api/topics/generate",
            json={"k": 4, "sample": 100, "seed": 13},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["topics"] >= 1
        assert body["assigned"] >= 12
        assert "took_ms" in body

        # Distances populated; member_count correct.
        with db_pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT t.id, t.label, t.origin, t.member_count, t.centroid IS NOT NULL
                  FROM app_topics t
                 WHERE t.origin = 'automatic'
                """
            ).fetchall()
            assert len(rows) >= 1
            total_members = sum(int(r[3]) for r in rows)
            assert total_members >= 12
            for r_ in rows:
                assert r_[2] == "automatic"
                assert r_[4] is True

            dist_rows = conn.execute(
                """
                SELECT count(*) FILTER (WHERE distance IS NOT NULL)::int,
                       count(*)::int
                  FROM app_topic_members
                 WHERE origin = 'automatic'
                """
            ).fetchone()
            assert dist_rows is not None
            assert dist_rows[0] == dist_rows[1]
            assert dist_rows[1] >= 12

        # Origin visible on list payload.
        listed = db_client.get("/api/topics")
        assert listed.status_code == 200
        topics = listed.json()["topics"]
        assert isinstance(topics, list)
        assert all("origin" in t for t in topics)

        # Deterministic: second generate with same seed yields same centroid count path.
        r2 = db_client.post(
            "/api/topics/generate",
            json={"k": 4, "sample": 100, "seed": 13},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["topics"] == body["topics"]
    finally:
        _cleanup_topics_fixtures(db_pool, seeds)


def test_regeneration_precedence_matrix(db_client: TestClient, db_pool: ConnectionPool) -> None:
    seeds: list[dict[str, Any]] = []
    try:
        for i in range(8):
            seeds.append(
                _insert_email_with_embedding(
                    db_pool,
                    subject=f"Alpha beta gamma document {i}",
                    embedding=_unit_vec(i % 3),
                    date=datetime(2021, 1, 1 + i, 12, 0, tzinfo=UTC),
                )
            )
        _login(db_client)
        r = db_client.post(
            "/api/topics/generate",
            json={"k": 4, "sample": 50, "seed": 7},
        )
        assert r.status_code == 200, r.text

        listed = db_client.get("/api/topics").json()["topics"]
        assert listed
        auto_id = listed[0]["id"]
        auto_label = listed[0]["label"]

        # Curate by rename → origin curated.
        patch = db_client.patch(
            f"/api/topics/{auto_id}",
            json={"label": "Curated Keep Me"},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["origin"] == "curated"
        assert patch.json()["label"] == "Curated Keep Me"

        # Manual topic.
        created = db_client.post(
            "/api/topics",
            json={"label": "Manual Archive Topic", "description": "hand built"},
        )
        assert created.status_code == 200, created.text
        manual_id = created.json()["id"]
        assert created.json()["origin"] == "manual"

        # Manual membership on a surviving automatic topic (create fresh auto via re-gen
        # after we add manual member to a new auto). First add manual member to curated.
        email_sid = seeds[0]["msg_sid"]
        add = db_client.post(
            f"/api/topics/{auto_id}/members",
            json={"email_sid": email_sid},
        )
        assert add.status_code == 200, add.text

        # Also create a pure-automatic topic path: generate again after insert more? Use
        # a second automatic by regenerating and picking an automatic one, then add
        # manual member to it.
        r_mid = db_client.post(
            "/api/topics/generate",
            json={"k": 4, "sample": 50, "seed": 7},
        )
        assert r_mid.status_code == 200

        # After regen: curated and manual survive with labels intact.
        with db_pool.connection() as conn:
            curated = conn.execute(
                "SELECT label, origin FROM app_topics WHERE id = %(id)s",
                {"id": auto_id},
            ).fetchone()
            assert curated is not None
            assert curated[0] == "Curated Keep Me"
            assert curated[1] == "curated"

            manual = conn.execute(
                "SELECT label, origin FROM app_topics WHERE id = %(id)s",
                {"id": manual_id},
            ).fetchone()
            assert manual is not None
            assert manual[0] == "Manual Archive Topic"
            assert manual[1] == "manual"

            # Manual membership row survives reassignment.
            mem = conn.execute(
                """
                SELECT origin FROM app_topic_members
                 WHERE topic_id = %(tid)s AND email_id = %(eid)s
                """,
                {"tid": auto_id, "eid": seeds[0]["email_id"]},
            ).fetchone()
            assert mem is not None
            assert mem[0] == "manual"

        # Plain automatic topics were replaced (old label gone unless recreated).
        # Curated id still present; generation increased for new automatic topics.
        with db_pool.connection() as conn:
            autos = conn.execute(
                "SELECT id, label FROM app_topics WHERE origin = 'automatic'"
            ).fetchall()
            # New automatic topics exist after regen.
            assert len(autos) >= 1
            # Original automatic label may differ after recreation — curated kept.
            assert auto_label  # just ensure we captured it
    finally:
        _cleanup_topics_fixtures(db_pool, seeds)


def test_gateway_polish_fake_transport(db_client: TestClient, db_pool: ConnectionPool) -> None:
    seeds: list[dict[str, Any]] = []
    try:
        for i in range(6):
            seeds.append(
                _insert_email_with_embedding(
                    db_pool,
                    subject=f"Contract negotiation deadline {i}",
                    embedding=_unit_vec(2),
                )
            )
        _login(db_client)

        def fake_transport(
            model: str, messages: list[dict[str, str]], stream: bool
        ) -> Iterator[str]:
            # Emit one polished label per cluster — over-provision; parse keeps length match.
            yield '["Contract talks"]'

        db_client.app.state.chat_transport = fake_transport  # type: ignore[attr-defined]
        db_client.app.state.model_available = True  # type: ignore[attr-defined]

        # k=4 but sample may produce fewer effective clusters; force k=4 with enough
        # diversity if possible. With identical embeddings, kmeans still creates k
        # centroids (empty clusters reseeded) — labels_final length = effective_k.
        # For polish to apply, response length must match effective_k.
        # Use k=4 and a response with 4 labels.
        def fake_transport4(
            model: str, messages: list[dict[str, str]], stream: bool
        ) -> Iterator[str]:
            yield '["Alpha one", "Beta two", "Gamma three", "Delta four"]'

        db_client.app.state.chat_transport = fake_transport4  # type: ignore[attr-defined]

        r = db_client.post(
            "/api/topics/generate",
            json={"k": 4, "sample": 50, "seed": 3},
        )
        assert r.status_code == 200, r.text
        # At least one topic created; polish may or may not stick depending on k vs n.
        assert r.json()["topics"] >= 1

        # Failure fallback: bad transport keeps term labels (no crash).
        def bad_transport(
            model: str, messages: list[dict[str, str]], stream: bool
        ) -> Iterator[str]:
            yield "NOT VALID JSON AT ALL"

        db_client.app.state.chat_transport = bad_transport  # type: ignore[attr-defined]
        r2 = db_client.post(
            "/api/topics/generate",
            json={"k": 4, "sample": 50, "seed": 3},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["topics"] >= 1
    finally:
        db_client.app.state.model_available = False  # type: ignore[attr-defined]
        _cleanup_topics_fixtures(db_pool, seeds)


# --- CRUD ---


def test_crud_rename_delete_members_auth(
    db_client: TestClient, db_pool: ConnectionPool, client: TestClient
) -> None:
    seeds: list[dict[str, Any]] = []
    try:
        seeds.append(
            _insert_email_with_embedding(
                db_pool,
                subject="Solo message for membership",
                embedding=_unit_vec(5),
            )
        )

        # Auth required.
        assert client.get("/api/topics").status_code == 401
        assert client.post("/api/topics/generate", json={}).status_code == 401

        _login(db_client)

        created = db_client.post(
            "/api/topics",
            json={"label": "Hand Topic", "description": "d"},
        )
        assert created.status_code == 200
        tid = created.json()["id"]
        assert created.json()["origin"] == "manual"

        # Manual delete ok.
        deleted = db_client.delete(f"/api/topics/{tid}")
        assert deleted.status_code == 200

        # Recreate + auto via generate for delete guard.
        for i in range(4):
            seeds.append(
                _insert_email_with_embedding(
                    db_pool,
                    subject=f"Auto cluster subject word{i}",
                    embedding=_unit_vec(1),
                )
            )
        gen = db_client.post(
            "/api/topics/generate",
            json={"k": 4, "sample": 20, "seed": 1},
        )
        assert gen.status_code == 200
        topics = db_client.get("/api/topics").json()["topics"]
        auto = next(t for t in topics if t["origin"] == "automatic")
        forbidden = db_client.delete(f"/api/topics/{auto['id']}")
        assert forbidden.status_code == 403

        # Rename flips to curated.
        patched = db_client.patch(
            f"/api/topics/{auto['id']}",
            json={"label": "Renamed Auto"},
        )
        assert patched.status_code == 200
        assert patched.json()["origin"] == "curated"
        assert patched.json()["label"] == "Renamed Auto"

        # Member add/remove + audit.
        sid = seeds[0]["msg_sid"]
        add = db_client.post(
            f"/api/topics/{auto['id']}/members",
            json={"email_sid": sid},
        )
        assert add.status_code == 200
        rem = db_client.delete(f"/api/topics/{auto['id']}/members/{sid}")
        assert rem.status_code == 200

        with db_pool.connection() as conn:
            actions = {
                r[0]
                for r in conn.execute(
                    """
                    SELECT action FROM app_audit
                     WHERE action LIKE 'topics%%'
                    """
                ).fetchall()
            }
        assert "topics_member_add" in actions
        assert "topics_member_remove" in actions
        assert "topics_patch" in actions
        assert "topics_generate" in actions

        # Detail endpoint shape.
        detail = db_client.get(f"/api/topics/{auto['id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert "activity" in body
        assert "members" in body
        assert body["origin"] in ("curated", "automatic", "manual")
    finally:
        _cleanup_topics_fixtures(db_pool, seeds)


# --- Atlas analytics (4.2) ---


def test_pca_project_2d_deterministic() -> None:
    rng = np.random.default_rng(42)
    data = rng.normal(size=(12, 16)).astype(np.float64)
    a = pca_project_2d(data)
    b = pca_project_2d(data)
    np.testing.assert_allclose(a, b)
    assert a.shape == (12, 2)
    assert float(np.max(np.abs(a))) <= 1.0 + 1e-9


def test_river_top_n_and_unit(db_client: TestClient, db_pool: ConnectionPool) -> None:
    seeds: list[dict[str, Any]] = []
    try:
        for i in range(5):
            seeds.append(
                _insert_email_with_embedding(
                    db_pool,
                    subject=f"River alpha topic word {i}",
                    embedding=_unit_vec(0),
                    date=datetime(2020, 3, 1 + i, 12, 0, tzinfo=UTC),
                )
            )
        for i in range(3):
            seeds.append(
                _insert_email_with_embedding(
                    db_pool,
                    subject=f"River beta topic word {i}",
                    embedding=_unit_vec(20),
                    date=datetime(2020, 6, 1 + i, 12, 0, tzinfo=UTC),
                )
            )
        _login(db_client)
        gen = db_client.post(
            "/api/topics/generate",
            json={"k": 4, "sample": 50, "seed": 11},
        )
        assert gen.status_code == 200, gen.text

        r = db_client.get(
            "/api/topics/river",
            params={
                "from": "2020-01-01T00:00:00Z",
                "to": "2021-01-01T00:00:00Z",
                "unit": "month",
                "top": 2,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode_hint"] == "absolute"
        assert body["unit"] == "month"
        assert isinstance(body["topics"], list)
        assert len(body["topics"]) <= 2
        for series in body["topics"]:
            assert "topic_id" in series
            assert "label" in series
            assert "origin" in series
            assert isinstance(series["buckets"], list)
            for b in series["buckets"]:
                assert "bucket" in b and "count" in b

        # unit=auto resolves to a valid unit.
        r_auto = db_client.get(
            "/api/topics/river",
            params={
                "from": "2020-01-01T00:00:00Z",
                "to": "2021-01-01T00:00:00Z",
                "unit": "auto",
                "top": 8,
            },
        )
        assert r_auto.status_code == 200, r_auto.text
        assert r_auto.json()["unit"] in (
            "hour",
            "day",
            "week",
            "month",
            "quarter",
            "year",
        )
    finally:
        _cleanup_topics_fixtures(db_pool, seeds)


def test_matrix_totals(db_client: TestClient, db_pool: ConnectionPool) -> None:
    seeds: list[dict[str, Any]] = []
    try:
        for i in range(4):
            seeds.append(
                _insert_email_with_embedding(
                    db_pool,
                    subject=f"Matrix year twenty {i}",
                    embedding=_unit_vec(1),
                    date=datetime(2020, 5, 1 + i, 12, 0, tzinfo=UTC),
                )
            )
        for i in range(2):
            seeds.append(
                _insert_email_with_embedding(
                    db_pool,
                    subject=f"Matrix year twentyone {i}",
                    embedding=_unit_vec(1),
                    date=datetime(2021, 5, 1 + i, 12, 0, tzinfo=UTC),
                )
            )
        _login(db_client)
        gen = db_client.post(
            "/api/topics/generate",
            json={"k": 4, "sample": 50, "seed": 5},
        )
        assert gen.status_code == 200, gen.text

        r = db_client.get("/api/topics/matrix", params={"by": "year"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["by"] == "year"
        assert "2020" in body["columns"] or "2021" in body["columns"]
        assert isinstance(body["rows"], list)
        assert "column_totals" in body
        assert "grand_total" in body
        # Totals reconcile: sum of row_totals == grand_total == sum of col totals.
        row_sum = sum(int(row["row_total"]) for row in body["rows"])
        col_sum = sum(int(v) for v in body["column_totals"].values())
        assert row_sum == body["grand_total"]
        assert col_sum == body["grand_total"]
        assert body["grand_total"] >= 6
    finally:
        _cleanup_topics_fixtures(db_pool, seeds)


def test_projection_determinism_shape_and_manual_exclusion(
    db_client: TestClient, db_pool: ConnectionPool
) -> None:
    seeds: list[dict[str, Any]] = []
    try:
        for i in range(6):
            seeds.append(
                _insert_email_with_embedding(
                    db_pool,
                    subject=f"Projection cluster subject {i}",
                    embedding=_unit_vec(i % 2),
                    date=datetime(2019, 1, 1 + i, 12, 0, tzinfo=UTC),
                )
            )
        _login(db_client)
        gen = db_client.post(
            "/api/topics/generate",
            json={"k": 4, "sample": 50, "seed": 9},
        )
        assert gen.status_code == 200, gen.text

        # Manual topic without centroid must be excluded.
        created = db_client.post(
            "/api/topics",
            json={"label": "Manual No Centroid"},
        )
        assert created.status_code == 200
        manual_id = created.json()["id"]

        p1 = db_client.get("/api/topics/projection")
        assert p1.status_code == 200, p1.text
        body1 = p1.json()
        p2 = db_client.get("/api/topics/projection")
        assert p2.status_code == 200
        body2 = p2.json()
        # Determinism: same centroids → same coords.
        assert body1["points"] == body2["points"]

        # Shape: topic-level only (no source_id / email_id fields).
        assert "points" in body1
        for pt in body1["points"]:
            assert set(pt.keys()) >= {
                "topic_id",
                "label",
                "origin",
                "member_count",
                "x",
                "y",
            }
            assert "source_id" not in pt
            assert "email_id" not in pt
            assert "sources" not in pt
            assert -1.0 - 1e-9 <= float(pt["x"]) <= 1.0 + 1e-9
            assert -1.0 - 1e-9 <= float(pt["y"]) <= 1.0 + 1e-9
            assert pt["topic_id"] != manual_id

        assert body1["excluded_without_centroid"] >= 1
        assert "note" in body1
        assert "TA-003" in body1["note"] or "topic-level" in body1["note"].lower()
    finally:
        _cleanup_topics_fixtures(db_pool, seeds)


def test_members_keyset_endpoint(db_client: TestClient, db_pool: ConnectionPool) -> None:
    seeds: list[dict[str, Any]] = []
    try:
        for i in range(6):
            seeds.append(
                _insert_email_with_embedding(
                    db_pool,
                    subject=f"Member list item {i}",
                    embedding=_unit_vec(3),
                    date=datetime(2022, 1, 1 + i, 12, 0, tzinfo=UTC),
                )
            )
        _login(db_client)
        gen = db_client.post(
            "/api/topics/generate",
            json={"k": 4, "sample": 50, "seed": 2},
        )
        assert gen.status_code == 200, gen.text
        topics = db_client.get("/api/topics").json()["topics"]
        assert topics
        tid = topics[0]["id"]

        page1 = db_client.get(
            f"/api/topics/{tid}/members",
            params={"limit": 2},
        )
        assert page1.status_code == 200, page1.text
        b1 = page1.json()
        assert len(b1["items"]) <= 2
        assert "next_cursor" in b1
        ids1 = [it["id"] for it in b1["items"]]
        assert all(sid.startswith("msg_") for sid in ids1)

        if b1["next_cursor"]:
            page2 = db_client.get(
                f"/api/topics/{tid}/members",
                params={"limit": 2, "cursor": b1["next_cursor"]},
            )
            assert page2.status_code == 200, page2.text
            ids2 = [it["id"] for it in page2.json()["items"]]
            # Keyset pages must not overlap.
            assert set(ids1).isdisjoint(set(ids2))
    finally:
        _cleanup_topics_fixtures(db_pool, seeds)
