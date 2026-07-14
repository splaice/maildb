# tests/test_workspaces.py
from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

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
    subject: str = "Workspace seed",
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
                "mid": f"<ws-{eid}@example.com>",
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


def _seed_answer(
    pool: ConnectionPool,
    *,
    answer_text: str = "Metal roof [S1].",
    citations: list[dict[str, Any]] | None = None,
) -> UUID:
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO app_answers (
                question, scope_fingerprint, model_route, policy_version,
                status, answer_text, retrieval
            ) VALUES (
                'What roof?', 'qs_test', 'ollama:llama3.2', 'ask-v1',
                'complete', %(text)s, '[]'::jsonb
            )
            RETURNING id
            """,
            {"text": answer_text},
        ).fetchone()
        assert row is not None
        aid: UUID = row[0]
        for cit in citations or []:
            conn.execute(
                """
                INSERT INTO app_citations (
                    answer_id, marker, source_id, source_type,
                    location, excerpt, excerpt_hash
                ) VALUES (
                    %(aid)s, %(marker)s, %(sid)s, %(stype)s,
                    %(loc)s::jsonb, %(excerpt)s, %(ehash)s
                )
                """,
                {
                    "aid": aid,
                    "marker": cit.get("marker", "S1"),
                    "sid": cit["source_id"],
                    "stype": cit.get("source_type", "message"),
                    "loc": json.dumps(cit.get("location") or {"char_start": 0, "char_end": 1}),
                    "excerpt": cit.get("excerpt", "excerpt"),
                    "ehash": cit.get(
                        "excerpt_hash",
                        hashlib.sha256(str(cit.get("excerpt", "excerpt")).encode()).hexdigest(),
                    ),
                },
            )
        conn.commit()
    return aid


def _create_ws(
    client: TestClient,
    *,
    name: str = "Case A",
    description: str | None = "desc",
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if description is not None:
        body["description"] = description
    if scope is not None:
        body["scope"] = scope
    r = client.post("/api/workspaces", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# --- auth ---


def test_workspaces_require_auth(client: TestClient) -> None:
    assert client.get("/api/workspaces").status_code == 401
    assert client.post("/api/workspaces", json={"name": "x"}).status_code == 401
    fake = str(uuid4())
    assert client.get(f"/api/workspaces/{fake}").status_code == 401
    assert (
        client.patch(f"/api/workspaces/{fake}", json={"version": 1, "name": "y"}).status_code == 401
    )
    assert client.delete(f"/api/workspaces/{fake}").status_code == 401
    assert (
        client.post(
            f"/api/workspaces/{fake}/blocks",
            json={"block_type": "note", "content": {"text": "n"}},
        ).status_code
        == 401
    )
    assert client.post(f"/api/workspaces/{fake}/export", json={"format": "json"}).status_code == 401


# --- CRUD ---


def test_workspace_crud_and_list(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _login(db_client)
    a = _create_ws(db_client, name="Alpha", scope={"senders": ["a@example.com"]})
    assert a["name"] == "Alpha"
    assert a["version"] == 1
    assert a["scope"]["senders"] == ["a@example.com"]
    assert a["blocks"] == []

    b = _create_ws(db_client, name="Beta")
    listing = db_client.get("/api/workspaces")
    assert listing.status_code == 200
    items = listing.json()["items"]
    names = [i["name"] for i in items]
    assert "Alpha" in names and "Beta" in names
    # newest first — Beta created after Alpha
    beta_idx = next(i for i, it in enumerate(items) if it["name"] == "Beta")
    alpha_idx = next(i for i, it in enumerate(items) if it["name"] == "Alpha")
    assert beta_idx < alpha_idx
    alpha_item = next(it for it in items if it["name"] == "Alpha")
    assert "counts" in alpha_item
    assert alpha_item["counts"]["blocks"] == 0

    got = db_client.get(f"/api/workspaces/{a['id']}")
    assert got.status_code == 200
    assert got.json()["name"] == "Alpha"

    patched = db_client.patch(
        f"/api/workspaces/{a['id']}",
        json={"version": 1, "name": "Alpha2", "description": "updated"},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["name"] == "Alpha2"
    assert body["version"] == 2

    deleted = db_client.delete(f"/api/workspaces/{b['id']}")
    assert deleted.status_code == 204
    assert db_client.get(f"/api/workspaces/{b['id']}").status_code == 404

    with db_pool.connection() as conn:
        row = conn.execute(
            """
            SELECT action, detail FROM app_audit
             WHERE action IN ('workspace_create', 'workspace_delete')
             ORDER BY id DESC
             LIMIT 5
            """
        ).fetchall()
    actions = {r[0] for r in row}
    assert "workspace_create" in actions
    assert "workspace_delete" in actions


def test_optimistic_concurrency_409(db_client: TestClient) -> None:
    _login(db_client)
    ws = _create_ws(db_client, name="Conflict")
    ok = db_client.patch(
        f"/api/workspaces/{ws['id']}",
        json={"version": 1, "name": "Conflict-v2"},
    )
    assert ok.status_code == 200
    assert ok.json()["version"] == 2

    stale = db_client.patch(
        f"/api/workspaces/{ws['id']}",
        json={"version": 1, "name": "stale"},
    )
    assert stale.status_code == 409


# --- blocks ---


def test_block_validation_and_pin_404(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _login(db_client)
    ws = _create_ws(db_client)
    wid = ws["id"]

    # bad shape → 422
    bad = db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={"block_type": "heading", "content": {}},
    )
    assert bad.status_code == 422

    bad_note = db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={"block_type": "note", "content": {"wrong": 1}},
    )
    assert bad_note.status_code == 422

    bad_pin = db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={
            "block_type": "pin",
            "content": {
                "source_id": "msg_1",
                # missing title
                "source_type": "message",
            },
        },
    )
    assert bad_pin.status_code == 422

    # nonexistent pin source → 404
    missing = db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={
            "block_type": "pin",
            "content": {
                "source_id": encode_source_id("msg", uuid4()),
                "source_type": "message",
                "title": "Ghost",
                "date": "2015-01-01",
                "sender": "x@example.com",
                "excerpt": None,
            },
        },
    )
    assert missing.status_code == 404

    # nonexistent answer → 404
    missing_ans = db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={
            "block_type": "answer",
            "content": {"answer_id": str(uuid4())},
        },
    )
    assert missing_ans.status_code == 404

    # valid blocks
    email = _seed_email(db_pool, subject="Pinned mail")
    pin = db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={
            "block_type": "pin",
            "content": {
                "source_id": email["source_id"],
                "source_type": "message",
                "title": email["subject"],
                "date": email["date"],
                "sender": email["sender_name"],
                "excerpt": "snippet",
            },
        },
    )
    assert pin.status_code == 201, pin.text
    assert pin.json()["block_type"] == "pin"
    assert pin.json()["position"] == 0

    note = db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={"block_type": "note", "content": {"text": "Analyst note"}},
    )
    assert note.status_code == 201
    assert note.json()["position"] == 1

    heading = db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={"block_type": "heading", "content": {"text": "Findings"}},
    )
    assert heading.status_code == 201

    aid = _seed_answer(
        db_pool,
        citations=[
            {
                "marker": "S1",
                "source_id": email["source_id"],
                "source_type": "message",
                "excerpt": "metal",
            }
        ],
    )
    ans = db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={"block_type": "answer", "content": {"answer_id": str(aid)}},
    )
    assert ans.status_code == 201, ans.text
    assert ans.json()["answer"]["answer_text"]
    assert len(ans.json()["answer"]["citations"]) == 1

    full = db_client.get(f"/api/workspaces/{wid}").json()
    assert len(full["blocks"]) == 4
    assert full["blocks"][0]["block_type"] == "pin"


def test_block_reposition_shifts(db_client: TestClient) -> None:
    _login(db_client)
    ws = _create_ws(db_client)
    wid = ws["id"]
    ids: list[str] = []
    for text in ("A", "B", "C"):
        r = db_client.post(
            f"/api/workspaces/{wid}/blocks",
            json={"block_type": "heading", "content": {"text": text}},
        )
        assert r.status_code == 201
        ids.append(r.json()["id"])

    # Move C (pos 2) to position 0
    moved = db_client.patch(
        f"/api/workspaces/{wid}/blocks/{ids[2]}",
        json={"position": 0},
    )
    assert moved.status_code == 200
    assert moved.json()["position"] == 0

    blocks = db_client.get(f"/api/workspaces/{wid}").json()["blocks"]
    ordered = [b["content"]["text"] for b in blocks]
    assert ordered == ["C", "A", "B"]
    assert [b["position"] for b in blocks] == [0, 1, 2]


def test_block_delete_and_workspace_cascade(db_client: TestClient, db_pool: ConnectionPool) -> None:
    _login(db_client)
    ws = _create_ws(db_client)
    wid = ws["id"]
    r = db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={"block_type": "note", "content": {"text": "to delete"}},
    )
    bid = r.json()["id"]
    deleted = db_client.delete(f"/api/workspaces/{wid}/blocks/{bid}")
    assert deleted.status_code == 204
    assert db_client.get(f"/api/workspaces/{wid}").json()["blocks"] == []

    # cascade: blocks gone when workspace deleted
    r2 = db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={"block_type": "note", "content": {"text": "cascade me"}},
    )
    bid2 = r2.json()["id"]
    assert db_client.delete(f"/api/workspaces/{wid}").status_code == 204
    with db_pool.connection() as conn:
        row = conn.execute(
            "SELECT count(*) FROM app_workspace_blocks WHERE id = %(id)s",
            {"id": bid2},
        ).fetchone()
    assert row is not None
    assert row[0] == 0


# --- export ---


def test_export_markdown_json_csv_manifest_and_fingerprint(
    db_client: TestClient, db_pool: ConnectionPool
) -> None:
    _login(db_client)
    email = _seed_email(db_pool, subject="Roof quote")
    other = _seed_email(db_pool, subject="Other mail", sender_name="Bob")
    aid = _seed_answer(
        db_pool,
        answer_text="Chose metal [S1].",
        citations=[
            {
                "marker": "S1",
                "source_id": other["source_id"],
                "source_type": "message",
                "excerpt": "metal roof",
                "excerpt_hash": hashlib.sha256(b"metal roof").hexdigest(),
            }
        ],
    )
    ws = _create_ws(
        db_client,
        name="Export Case",
        description="Investigation",
        scope={"senders": ["alice@example.com"]},
    )
    wid = ws["id"]

    db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={"block_type": "heading", "content": {"text": "Evidence"}},
    )
    db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={"block_type": "note", "content": {"text": "Plain note only"}},
    )
    db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={
            "block_type": "pin",
            "content": {
                "source_id": email["source_id"],
                "source_type": "message",
                "title": email["subject"],
                "date": email["date"],
                "sender": email["sender_name"],
                "excerpt": "pin excerpt",
            },
        },
    )
    db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={"block_type": "answer", "content": {"answer_id": str(aid)}},
    )

    # markdown
    md = db_client.post(
        f"/api/workspaces/{wid}/export",
        json={"format": "markdown"},
    )
    assert md.status_code == 200
    assert "attachment" in md.headers.get("content-disposition", "").lower()
    text = md.text
    assert "# Export Case" in text
    assert "Investigation" in text
    assert "Scope:" in text
    assert "## Evidence" in text
    assert "Plain note only" in text
    assert email["source_id"] in text
    assert "pin excerpt" in text or "> pin excerpt" in text
    assert "Chose metal" in text
    assert "## Source manifest" in text
    # both pin + citation sources present
    assert email["source_id"] in text
    assert other["source_id"] in text

    # json
    js = db_client.post(
        f"/api/workspaces/{wid}/export",
        json={"format": "json"},
    )
    assert js.status_code == 200
    doc = js.json()
    assert doc["name"] == "Export Case"
    assert "blocks" in doc
    assert "manifest" in doc
    assert "export" in doc
    assert doc["export"]["policy_versions"]["ask"]
    fp1 = doc["export"]["fingerprint"]
    assert isinstance(fp1, str) and len(fp1) == 64
    manifest_ids = {m["source_id"] for m in doc["manifest"]}
    assert email["source_id"] in manifest_ids
    assert other["source_id"] in manifest_ids
    # deduplicated — at most one row per source
    assert len(doc["manifest"]) == len(manifest_ids)

    # fingerprint stable across identical exports
    js2 = db_client.post(
        f"/api/workspaces/{wid}/export",
        json={"format": "json"},
    )
    assert js2.json()["export"]["fingerprint"] == fp1

    # csv
    csv_r = db_client.post(
        f"/api/workspaces/{wid}/export",
        json={"format": "csv"},
    )
    assert csv_r.status_code == 200
    assert "text/csv" in csv_r.headers.get("content-type", "")
    reader = csv.DictReader(io.StringIO(csv_r.text))
    rows = list(reader)
    assert set(reader.fieldnames or []) >= {
        "source_id",
        "type",
        "date",
        "sender",
        "title",
        "excerpt_hash",
    }
    csv_ids = {row["source_id"] for row in rows}
    assert email["source_id"] in csv_ids
    assert other["source_id"] in csv_ids
    assert len(rows) == len(csv_ids)

    # audit
    with db_pool.connection() as conn:
        audits = conn.execute(
            """
            SELECT detail FROM app_audit
             WHERE action = 'workspace_export'
             ORDER BY id DESC
             LIMIT 3
            """
        ).fetchall()
    assert audits
    detail = audits[0][0]
    assert detail["workspace_id"] == wid
    assert detail["format"] in ("markdown", "json", "csv")
    assert detail["source_count"] == len(manifest_ids)
    assert detail["fingerprint"] == fp1 or len(detail["fingerprint"]) == 64


def test_export_fingerprint_matches_manifest_hash(
    db_client: TestClient, db_pool: ConnectionPool
) -> None:
    _login(db_client)
    email = _seed_email(db_pool)
    ws = _create_ws(db_client, name="Fp")
    wid = ws["id"]
    db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={
            "block_type": "pin",
            "content": {
                "source_id": email["source_id"],
                "source_type": "message",
                "title": "T",
                "date": email["date"],
                "sender": "Alice",
                "excerpt": None,
            },
        },
    )
    doc = db_client.post(
        f"/api/workspaces/{wid}/export",
        json={"format": "json"},
    ).json()
    manifest = doc["manifest"]
    normalized = sorted(
        [
            {
                "source_id": m.get("source_id"),
                "source_type": m.get("source_type"),
                "date": m.get("date"),
                "sender": m.get("sender"),
                "subject_or_filename": m.get("subject_or_filename"),
                "excerpt_hash": m.get("excerpt_hash"),
            }
            for m in manifest
        ],
        key=lambda r: str(r.get("source_id") or ""),
    )
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert doc["export"]["fingerprint"] == expected


def test_export_requires_fresh_auth(db_client: TestClient, db_settings: Any) -> None:
    import time

    from chronicle_server.auth import sign_session
    from chronicle_server.config import ChronicleSettings

    settings = db_settings
    assert isinstance(settings, ChronicleSettings)
    stale = sign_session(USERNAME, settings, auth_at=time.time() - 2000)
    db_client.cookies.set(settings.cookie_name, stale)
    ws = _create_ws(db_client, name="StaleExport")
    # require_user still ok for create — but export needs fresh auth.
    # Create used the stale cookie; sessions without fresh auth_at fail export.
    r = db_client.post(
        f"/api/workspaces/{ws['id']}/export",
        json={"format": "json"},
    )
    assert r.status_code == 401
    detail = r.json().get("detail")
    assert isinstance(detail, dict)
    assert detail["reason"] == "reauth-required"

    # reauth via login → export succeeds
    _login(db_client)
    ok = db_client.post(
        f"/api/workspaces/{ws['id']}/export",
        json={"format": "json"},
    )
    assert ok.status_code == 200


def test_export_redaction_review_then_confirm(
    db_client: TestClient, db_pool: ConnectionPool
) -> None:
    _login(db_client)
    email = _seed_email(db_pool, subject="Secret deal")
    ws = _create_ws(db_client, name="Redact Me", description="Contact alice@example.com")
    wid = ws["id"]
    note_text = "Wire 99887766554433 to 123 Main St"
    db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={"block_type": "note", "content": {"text": note_text}},
    )
    db_client.post(
        f"/api/workspaces/{wid}/blocks",
        json={
            "block_type": "pin",
            "content": {
                "source_id": email["source_id"],
                "source_type": "message",
                "title": email["subject"],
                "date": email["date"],
                "sender": "alice@example.com",
                "excerpt": "Call +1 415 555 0199",
            },
        },
    )

    # review (enabled, not confirmed) → no file
    review = db_client.post(
        f"/api/workspaces/{wid}/export",
        json={
            "format": "json",
            "redact": {
                "enabled": True,
                "kinds": ["email", "phone", "street_address", "account_number"],
                "custom_terms": [],
                "confirmed": False,
            },
        },
    )
    assert review.status_code == 200
    body = review.json()
    assert body["review"] is True
    assert body["counts"].get("email", 0) >= 1
    assert body["samples"]
    assert (
        "Content-Disposition" not in review.headers
        or "attachment" not in (review.headers.get("Content-Disposition") or "").lower()
    )

    # confirm → redacted file
    confirmed = db_client.post(
        f"/api/workspaces/{wid}/export",
        json={
            "format": "json",
            "redact": {
                "enabled": True,
                "kinds": ["email", "phone", "street_address", "account_number"],
                "custom_terms": [],
                "confirmed": True,
            },
        },
    )
    assert confirmed.status_code == 200
    assert "attachment" in confirmed.headers.get("content-disposition", "").lower()
    doc = confirmed.json()
    assert doc["export"]["redactions"]
    assert sum(doc["export"]["redactions"].values()) >= 1
    blob = json.dumps(doc)
    assert "alice@example.com" not in blob
    assert "[REDACTED:email]" in blob
    assert "99887766554433" not in blob
    assert "[REDACTED:account_number]" in blob or "[REDACTED:phone]" in blob

    # originals untouched: re-fetch workspace
    full = db_client.get(f"/api/workspaces/{wid}").json()
    assert full["description"] == "Contact alice@example.com"
    note = next(b for b in full["blocks"] if b["block_type"] == "note")
    assert note["content"]["text"] == note_text
    pin = next(b for b in full["blocks"] if b["block_type"] == "pin")
    assert pin["content"]["sender"] == "alice@example.com"

    # export twice → sources still identical (invariant)
    full2 = db_client.get(f"/api/workspaces/{wid}").json()
    assert full2["description"] == full["description"]
    assert full2["blocks"][0]["content"] == full["blocks"][0]["content"]

    with db_pool.connection() as conn:
        audits = conn.execute(
            """
            SELECT detail FROM app_audit
             WHERE action = 'workspace_export'
             ORDER BY id DESC
             LIMIT 1
            """
        ).fetchone()
    assert audits is not None
    detail = audits[0]
    assert detail.get("redact_enabled") is True
    assert detail.get("redactions")
