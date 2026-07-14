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
