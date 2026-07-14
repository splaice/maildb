# tests/test_people.py
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

from chronicle_server.cursor import encode_cursor
from chronicle_server.ids import encode_source_id
from chronicle_server.people import (
    _activity_buckets,
    _ego_graph,
    _enrich_card,
    _top_topics,
)
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def _sample_card(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "display_name": "Alice Example",
        "kind": "human",
        "kind_source": "manual",
        "tags": ["vip"],
        "human_probability": 0.85,
        "addresses": ["alice@example.com"],
        "name_variants": ["Alice"],
        "messages_from": 10,
        "messages_to": 3,
        "first_seen": datetime(2015, 1, 1, tzinfo=UTC),
        "last_seen": datetime(2018, 6, 1, tzinfo=UTC),
        "notes": "notes",
        "metadata": {},
        "classification_signals": {"bidirectional": 0.15, "personal_name": 0.1},
        "classified_at": datetime(2020, 1, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return base


# --- auth ---


def test_people_list_requires_auth(client: TestClient) -> None:
    r = client.get("/api/people")
    assert r.status_code == 401


def test_people_get_requires_auth(client: TestClient) -> None:
    r = client.get(f"/api/people/{uuid4()}")
    assert r.status_code == 401


def test_people_merge_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/api/people/merge",
        json={"source_id": str(uuid4()), "target_id": str(uuid4())},
    )
    assert r.status_code == 401


# --- proxy shapes (stub pool + mocked MailDB) ---


def test_people_list_proxy_shape(client: TestClient, stub_pool: MagicMock) -> None:
    card = _sample_card()
    mock_db = MagicMock()
    mock_db.contacts_search.return_value = ([card], 1)

    with patch("chronicle_server.people._maildb", return_value=mock_db):
        _login(client)
        r = client.get("/api/people?q=Alice&limit=10")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    assert body["total"] == 1
    assert body["offset"] == 0
    assert body["limit"] == 10
    assert body["items"][0]["display_name"] == "Alice Example"
    assert body["items"][0]["first_seen"].endswith("Z") or "2015" in body["items"][0]["first_seen"]
    kwargs = mock_db.contacts_search.call_args.kwargs
    assert kwargs["query"] == "Alice"
    assert kwargs["include_total"] is True
    assert kwargs["limit"] == 10
    assert kwargs["offset"] == 0


def test_people_list_cursor_offset_and_no_total_on_later_page(
    client: TestClient,
) -> None:
    card = _sample_card()
    mock_db = MagicMock()
    mock_db.contacts_search.return_value = ([card], None)
    secret = "test-secret-key-not-for-production"
    cursor = encode_cursor({"offset": 50}, secret)

    with patch("chronicle_server.people._maildb", return_value=mock_db):
        _login(client)
        r = client.get(f"/api/people?cursor={cursor}&limit=50")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] is None
    assert body["offset"] == 50
    kwargs = mock_db.contacts_search.call_args.kwargs
    assert kwargs["offset"] == 50
    assert kwargs["include_total"] is False


def test_people_list_invalid_kind_422(client: TestClient) -> None:
    with patch("chronicle_server.people._maildb", return_value=MagicMock()):
        _login(client)
        r = client.get("/api/people?kind=robot")
    assert r.status_code == 422


def test_people_list_needs_review(client: TestClient) -> None:
    mock_db = MagicMock()
    mock_db.contacts_search.return_value = ([], 0)
    with patch("chronicle_server.people._maildb", return_value=mock_db):
        _login(client)
        r = client.get("/api/people?needs_review=true")
    assert r.status_code == 200
    assert mock_db.contacts_search.call_args.kwargs["needs_review"] is True


def test_people_get_enriches_address_classes_and_activity(client: TestClient) -> None:
    cid = uuid4()
    card = _sample_card(id=str(cid), addresses=["alice@example.com", "me@owner.com"])
    mock_db = MagicMock()
    mock_db.get_contact.return_value = card

    addr_rows = [
        ("alice@example.com", False, 10, 2, None, None),
        ("me@owner.com", True, 0, 5, None, None),
    ]
    activity_rows = [
        (datetime(2016, 3, 1, tzinfo=UTC), 4),
        (datetime(2016, 4, 1, tzinfo=UTC), 2),
    ]

    def fake_execute(sql: str, params: dict[str, Any] | None = None) -> MagicMock:
        result = MagicMock()
        sql_l = sql.lower()
        if "contact_addresses" in sql_l:
            result.fetchall.return_value = addr_rows
            result.fetchone.return_value = None
        elif "date_trunc" in sql_l:
            result.fetchall.return_value = activity_rows
            result.fetchone.return_value = None
        elif "app_topic_members" in sql_l:
            result.fetchall.return_value = []
            result.fetchone.return_value = None
        elif "count(distinct" in sql_l or "thread_id" in sql_l:
            result.fetchone.return_value = (7,)
            result.fetchall.return_value = []
        elif "contact_merges" in sql_l:
            result.fetchall.return_value = []
            result.fetchone.return_value = None
        else:
            result.fetchall.return_value = []
            result.fetchone.return_value = None
        return result

    conn = MagicMock()
    conn.execute = fake_execute

    from contextlib import contextmanager

    @contextmanager
    def connection() -> Any:
        yield conn

    pool = MagicMock()
    pool.connection = connection

    with (
        patch("chronicle_server.people._maildb", return_value=mock_db),
        patch.object(client.app.state, "pool", pool, create=True),
    ):
        # Ensure pool is used from request.app.state
        client.app.state.pool = pool
        _login(client)
        r = client.get(f"/api/people/{cid}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["address_classes"]["alice@example.com"] == "external"
    assert body["address_classes"]["me@owner.com"] == "owner"
    assert len(body["activity"]) == 2
    assert body["activity"][0]["count"] == 4
    assert body["topics"] == []
    assert body["thread_count"] == 7
    assert "address_details" in body
    assert any(d["is_user"] for d in body["address_details"])


def test_people_get_404(client: TestClient) -> None:
    mock_db = MagicMock()
    mock_db.get_contact.return_value = None
    with patch("chronicle_server.people._maildb", return_value=mock_db):
        _login(client)
        r = client.get(f"/api/people/{uuid4()}")
    assert r.status_code == 404


def test_people_patch_calls_update_and_audits(client: TestClient, stub_pool: MagicMock) -> None:
    cid = uuid4()
    card = _sample_card(id=str(cid), kind="human", kind_source="manual")
    mock_db = MagicMock()
    mock_db.update_contact.return_value = card

    # Enrichment queries return empty.
    conn = MagicMock()
    empty = MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=(0,)))
    conn.execute = MagicMock(return_value=empty)

    from contextlib import contextmanager

    @contextmanager
    def connection() -> Any:
        yield conn

    # audit() opens its own connection on the real pool path — use stub + track INSERT.
    stub_pool.connection = connection

    with patch("chronicle_server.people._maildb", return_value=mock_db):
        _login(client)
        r = client.patch(
            f"/api/people/{cid}",
            json={"kind": "human", "display_name": "Alice Renamed", "tags": ["a"]},
        )
    assert r.status_code == 200, r.text
    kwargs = mock_db.update_contact.call_args.kwargs
    assert kwargs["kind"] == "human"
    assert kwargs["display_name"] == "Alice Renamed"
    assert kwargs["tags"] == ["a"]
    # audit INSERT should have been attempted
    assert conn.execute.called


def test_people_merge_unmerge_proxy(client: TestClient) -> None:
    src = str(uuid4())
    tgt = str(uuid4())
    mid = str(uuid4())
    card = _sample_card(id=tgt, merge_id=mid)
    mock_db = MagicMock()
    mock_db.merge_contacts.return_value = card
    mock_db.unmerge_contacts.return_value = {
        "source": _sample_card(id=src),
        "target": _sample_card(id=tgt),
    }

    conn = MagicMock()
    empty = MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=(0,)))
    conn.execute = MagicMock(return_value=empty)

    from contextlib import contextmanager

    @contextmanager
    def connection() -> Any:
        yield conn

    client.app.state.pool.connection = connection

    with patch("chronicle_server.people._maildb", return_value=mock_db):
        _login(client)
        r = client.post("/api/people/merge", json={"source_id": src, "target_id": tgt})
        assert r.status_code == 200, r.text
        assert mock_db.merge_contacts.called

        r2 = client.post("/api/people/unmerge", json={"merge_id": mid})
        assert r2.status_code == 200, r2.text
        assert mock_db.unmerge_contacts.called
        body = r2.json()
        assert "source" in body and "target" in body


def test_people_merge_candidates(client: TestClient) -> None:
    mock_db = MagicMock()
    mock_db.merge_candidates.return_value = [
        {
            "norm_name": "alice",
            "a": {
                "display_name": "Alice",
                "primary_address": "a@x.com",
                "msg_count": 5,
                "contact_id": str(uuid4()),
            },
            "b": {
                "display_name": "A. Example",
                "primary_address": "b@x.com",
                "msg_count": 3,
                "contact_id": str(uuid4()),
            },
        }
    ]
    with patch("chronicle_server.people._maildb", return_value=mock_db):
        _login(client)
        r = client.get("/api/people/merge-candidates?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    mock_db.merge_candidates.assert_called_once_with(limit=5)


def test_activity_buckets_one_statement() -> None:
    """_activity_buckets issues a single date_trunc statement."""
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [
        (datetime(2016, 1, 1, tzinfo=UTC), 3),
    ]

    from contextlib import contextmanager

    @contextmanager
    def connection() -> Any:
        yield conn

    pool = MagicMock()
    pool.connection = connection
    buckets = _activity_buckets(pool, ["alice@example.com"])
    assert len(buckets) == 1
    assert buckets[0]["count"] == 3
    assert conn.execute.call_count == 1
    sql = conn.execute.call_args[0][0]
    assert "date_trunc" in sql.lower()


def test_topics_empty_tolerance() -> None:
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("relation app_topics does not exist")

    from contextlib import contextmanager

    @contextmanager
    def connection() -> Any:
        yield conn

    pool = MagicMock()
    pool.connection = connection
    assert _top_topics(pool, ["alice@example.com"]) == []


def test_owner_class_derivation() -> None:
    cid = uuid4()
    card = _sample_card(id=str(cid), addresses=["a@x.com", "me@x.com"])
    addr_rows = [
        ("a@x.com", False, 1, 0, None, None),
        ("me@x.com", True, 0, 1, None, None),
    ]

    def fake_execute(sql: str, params: dict[str, Any] | None = None) -> MagicMock:
        result = MagicMock()
        sql_l = sql.lower()
        if "contact_addresses" in sql_l:
            result.fetchall.return_value = addr_rows
        elif "date_trunc" in sql_l or "app_topic" in sql_l or "contact_merges" in sql_l:
            result.fetchall.return_value = []
        elif "thread" in sql_l:
            result.fetchone.return_value = (0,)
        else:
            result.fetchall.return_value = []
            result.fetchone.return_value = (0,)
        return result

    conn = MagicMock()
    conn.execute = fake_execute

    from contextlib import contextmanager

    @contextmanager
    def connection() -> Any:
        yield conn

    pool = MagicMock()
    pool.connection = connection
    out = _enrich_card(pool, card)
    assert out["address_classes"]["a@x.com"] == "external"
    assert out["address_classes"]["me@x.com"] == "owner"


# --- DB-backed merge round-trip + audit ---


def _insert_throwaway_contact(
    pool: ConnectionPool,
    *,
    display_name: str,
    address: str,
    is_user: bool = False,
) -> str:
    cid = uuid4()
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO contacts (id, display_name, kind, kind_source, tags, notes)
            VALUES (%(id)s, %(name)s, 'unknown', 'heuristic', '{}', NULL)
            """,
            {"id": cid, "name": display_name},
        )
        conn.execute(
            """
            INSERT INTO contact_addresses (
                address, contact_id, name_variants, is_user,
                first_seen, last_seen, messages_from, messages_to
            ) VALUES (
                %(addr)s, %(cid)s, %(variants)s, %(is_user)s,
                now(), now(), 5, 1
            )
            """,
            {
                "addr": address,
                "cid": cid,
                "variants": [display_name],
                "is_user": is_user,
            },
        )
        conn.commit()
    return str(cid)


def _cleanup_contacts(pool: ConnectionPool, contact_ids: list[str], addresses: list[str]) -> None:
    with pool.connection() as conn:
        for cid in contact_ids:
            conn.execute(
                """
                DELETE FROM contact_merges
                 WHERE source_id = %(id)s OR target_id = %(id)s
                """,
                {"id": cid},
            )
            conn.execute(
                "DELETE FROM contact_addresses WHERE contact_id = %(id)s",
                {"id": cid},
            )
            conn.execute("DELETE FROM contacts WHERE id = %(id)s", {"id": cid})
        for addr in addresses:
            conn.execute(
                "DELETE FROM contact_addresses WHERE address = %(a)s",
                {"a": addr},
            )
        conn.execute("DELETE FROM app_audit WHERE action LIKE 'people%%'")
        conn.commit()


def test_merge_unmerge_roundtrip_and_audit(db_client: TestClient, db_pool: ConnectionPool) -> None:
    suffix = uuid4().hex[:8]
    addr_a = f"throwaway-a-{suffix}@people-test.example"
    addr_b = f"throwaway-b-{suffix}@people-test.example"
    name_a = f"ThrowA {suffix}"
    name_b = f"ThrowB {suffix}"
    ids: list[str] = []
    try:
        id_a = _insert_throwaway_contact(db_pool, display_name=name_a, address=addr_a)
        id_b = _insert_throwaway_contact(db_pool, display_name=name_b, address=addr_b)
        ids = [id_a, id_b]

        _login(db_client)
        r = db_client.post(
            "/api/people/merge",
            json={"source_id": id_a, "target_id": id_b},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == id_b
        merge_id = body.get("merge_id")
        assert merge_id
        # Address classes present after enrich
        assert addr_a in body["address_classes"] or any(
            d["address"] == addr_a for d in body.get("address_details", [])
        )
        assert "merges" in body or merge_id

        with db_pool.connection() as conn:
            audit_rows = conn.execute(
                """
                SELECT action, username, detail FROM app_audit
                 WHERE action = 'people_merge'
                 ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        assert audit_rows is not None
        assert audit_rows[0] == "people_merge"
        assert audit_rows[1] == USERNAME

        r2 = db_client.post("/api/people/unmerge", json={"merge_id": merge_id})
        assert r2.status_code == 200, r2.text
        restored = r2.json()
        assert restored["source"]["id"] == id_a
        assert restored["target"]["id"] == id_b

        with db_pool.connection() as conn:
            unmerge_rows = conn.execute(
                """
                SELECT action FROM app_audit
                 WHERE action = 'people_unmerge'
                 ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        assert unmerge_rows is not None
        assert unmerge_rows[0] == "people_unmerge"

        # Owner class derivation on DB card with is_user address
        owner_addr = f"throwaway-owner-{suffix}@people-test.example"
        id_owner = _insert_throwaway_contact(
            db_pool,
            display_name=f"Owner {suffix}",
            address=owner_addr,
            is_user=True,
        )
        ids.append(id_owner)
        r3 = db_client.get(f"/api/people/{id_owner}")
        assert r3.status_code == 200
        card = r3.json()
        assert card["address_classes"].get(owner_addr) == "owner"
    finally:
        _cleanup_contacts(
            db_pool,
            ids,
            [addr_a, addr_b, f"throwaway-owner-{suffix}@people-test.example"],
        )


def test_people_list_db_search(db_client: TestClient, db_pool: ConnectionPool) -> None:
    suffix = uuid4().hex[:8]
    addr = f"list-search-{suffix}@people-test.example"
    name = f"ListSearch {suffix}"
    cid = _insert_throwaway_contact(db_pool, display_name=name, address=addr)
    try:
        _login(db_client)
        r = db_client.get(f"/api/people?q={suffix}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert any(item["id"] == cid for item in body["items"])
        assert body["total"] is not None
    finally:
        _cleanup_contacts(db_pool, [cid], [addr])


# --- ego graph ---


def test_people_graph_requires_auth(client: TestClient) -> None:
    r = client.get(f"/api/people/{uuid4()}/graph")
    assert r.status_code == 401


def test_people_graph_depth_not_one_422(client: TestClient) -> None:
    mock_db = MagicMock()
    mock_db.get_contact.return_value = _sample_card()
    with patch("chronicle_server.people._maildb", return_value=mock_db):
        _login(client)
        r = client.get(f"/api/people/{uuid4()}/graph?depth=2")
    assert r.status_code == 422
    assert "depth=1" in r.json()["detail"]


def test_people_graph_404(client: TestClient) -> None:
    mock_db = MagicMock()
    mock_db.get_contact.return_value = None
    with patch("chronicle_server.people._maildb", return_value=mock_db):
        _login(client)
        r = client.get(f"/api/people/{uuid4()}/graph")
    assert r.status_code == 404


def _graph_row(
    node_id: str,
    label: str,
    kind: str,
    shared: int,
    first: datetime,
    last: datetime,
    threads: list[str],
    total: int,
) -> tuple[Any, ...]:
    return (node_id, label, kind, shared, first, last, threads, total)


def test_people_graph_shape_two_coparticipants(client: TestClient) -> None:
    """Graph shape: ego + 2 co-participants, edges with evidence thr_* ids."""
    ego_id = uuid4()
    bob_id = str(uuid4())
    card = _sample_card(id=str(ego_id), display_name="Ego Person", kind="human")
    mock_db = MagicMock()
    mock_db.get_contact.return_value = card

    thr_a, thr_b, thr_c = "thread-a", "thread-b", "thread-c"
    rows = [
        _graph_row(
            bob_id,
            "Bob Co",
            "human",
            3,
            datetime(2014, 1, 1, tzinfo=UTC),
            datetime(2016, 6, 1, tzinfo=UTC),
            [thr_a, thr_b, thr_c],
            2,
        ),
        _graph_row(
            "addr:stranger@example.com",
            "stranger@example.com",
            "address",
            1,
            datetime(2015, 3, 1, tzinfo=UTC),
            datetime(2015, 3, 2, tzinfo=UTC),
            ["thread-x"],
            2,
        ),
    ]

    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows

    from contextlib import contextmanager

    @contextmanager
    def connection() -> Any:
        yield conn

    pool = MagicMock()
    pool.connection = connection

    with (
        patch("chronicle_server.people._maildb", return_value=mock_db),
        patch.object(client.app.state, "pool", pool, create=True),
    ):
        client.app.state.pool = pool
        _login(client)
        r = client.get(f"/api/people/{ego_id}/graph?max_nodes=25")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["truncated"] is False
    assert body["total_coparticipants"] == 2
    assert len(body["nodes"]) == 3  # ego + 2
    ego_nodes = [n for n in body["nodes"] if n["is_ego"]]
    assert len(ego_nodes) == 1
    assert ego_nodes[0]["id"] == str(ego_id)
    assert ego_nodes[0]["label"] == "Ego Person"
    assert any(n["id"] == bob_id and n["kind"] == "human" for n in body["nodes"])
    assert any(
        n["id"] == "addr:stranger@example.com" and n["kind"] == "address" for n in body["nodes"]
    )
    assert len(body["edges"]) == 2
    for edge in body["edges"]:
        assert edge["source"] == str(ego_id)
        assert edge["kind"] == "thread_co_participation"
        assert "shared_threads" in edge
        assert "first" in edge and "last" in edge
        thr_ids = edge["evidence"]["thread_ids"]
        assert all(tid.startswith("thr_") for tid in thr_ids)
        # Encoded form matches encode_source_id
        if edge["target"] == bob_id:
            assert edge["shared_threads"] == 3
            assert thr_ids == [
                encode_source_id("thr", thr_a),
                encode_source_id("thr", thr_b),
                encode_source_id("thr", thr_c),
            ]
            assert edge["first"] == "2014-01-01"
            assert edge["last"] == "2016-06-01"


def test_people_graph_ranking_cap_truncated() -> None:
    """Ranking by shared_threads desc + cap sets truncated and total honesty."""
    ego_id = uuid4()
    # Simulate SQL already capped to max_nodes=2 but total=5
    rows = [
        _graph_row(
            str(uuid4()),
            "Top",
            "human",
            10,
            datetime(2010, 1, 1, tzinfo=UTC),
            datetime(2011, 1, 1, tzinfo=UTC),
            ["t1"],
            5,
        ),
        _graph_row(
            str(uuid4()),
            "Second",
            "human",
            5,
            datetime(2010, 1, 1, tzinfo=UTC),
            datetime(2011, 1, 1, tzinfo=UTC),
            ["t2"],
            5,
        ),
    ]
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows

    from contextlib import contextmanager

    @contextmanager
    def connection() -> Any:
        yield conn

    pool = MagicMock()
    pool.connection = connection

    out = _ego_graph(
        pool,
        contact_id=ego_id,
        ego_label="Ego",
        ego_kind="human",
        max_nodes=2,
        date_from=None,
        date_to=None,
    )
    assert out["truncated"] is True
    assert out["total_coparticipants"] == 5
    assert len(out["edges"]) == 2
    assert out["edges"][0]["shared_threads"] >= out["edges"][1]["shared_threads"]
    # SQL receives max_nodes
    params = conn.execute.call_args[0][1]
    assert params["max_nodes"] == 2


def test_people_graph_evidence_thread_ids_bounded() -> None:
    """Evidence thread ids are thr_* and capped at 20."""
    ego_id = uuid4()
    raw_threads = [f"thread-{i}" for i in range(25)]
    rows = [
        _graph_row(
            "addr:x@y.com",
            "x@y.com",
            "address",
            25,
            datetime(2012, 1, 1, tzinfo=UTC),
            datetime(2013, 1, 1, tzinfo=UTC),
            raw_threads,  # server still slices to 20
            1,
        )
    ]
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows

    from contextlib import contextmanager

    @contextmanager
    def connection() -> Any:
        yield conn

    pool = MagicMock()
    pool.connection = connection

    out = _ego_graph(
        pool,
        contact_id=ego_id,
        ego_label="Ego",
        ego_kind="human",
        max_nodes=25,
        date_from=None,
        date_to=None,
    )
    thr_ids = out["edges"][0]["evidence"]["thread_ids"]
    assert len(thr_ids) == 20
    assert all(t.startswith("thr_") for t in thr_ids)
    params = conn.execute.call_args[0][1]
    assert params["ev_cap"] == 20


def test_people_graph_date_filtering_passed_to_sql(client: TestClient) -> None:
    ego_id = uuid4()
    card = _sample_card(id=str(ego_id))
    mock_db = MagicMock()
    mock_db.get_contact.return_value = card

    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []

    from contextlib import contextmanager

    @contextmanager
    def connection() -> Any:
        yield conn

    pool = MagicMock()
    pool.connection = connection

    with (
        patch("chronicle_server.people._maildb", return_value=mock_db),
        patch.object(client.app.state, "pool", pool, create=True),
    ):
        client.app.state.pool = pool
        _login(client)
        r = client.get(
            f"/api/people/{ego_id}/graph?date_from=2014-01-01&date_to=2018-12-31&max_nodes=10"
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["nodes"][0]["is_ego"] is True
    assert body["edges"] == []
    assert body["total_coparticipants"] == 0
    assert body["truncated"] is False
    params = conn.execute.call_args[0][1]
    assert params["date_from"] is not None
    assert params["date_to"] is not None
    assert params["date_from"].year == 2014
    assert params["date_to"].year == 2018
    assert params["max_nodes"] == 10
    sql = conn.execute.call_args[0][0].lower()
    assert "ego_threads" in sql or "ego_addrs" in sql
    assert "thread_co_participation" not in sql  # edge kind is response-only


def test_people_graph_invalid_date_422(client: TestClient) -> None:
    mock_db = MagicMock()
    mock_db.get_contact.return_value = _sample_card()
    with patch("chronicle_server.people._maildb", return_value=mock_db):
        _login(client)
        r = client.get(f"/api/people/{uuid4()}/graph?date_from=not-a-date")
    assert r.status_code == 422


def test_people_graph_max_nodes_hard_cap(client: TestClient) -> None:
    mock_db = MagicMock()
    mock_db.get_contact.return_value = _sample_card()
    with patch("chronicle_server.people._maildb", return_value=mock_db):
        _login(client)
        r = client.get(f"/api/people/{uuid4()}/graph?max_nodes=100")
    # FastAPI Query le=50 → 422
    assert r.status_code == 422
