# tests/test_sources.py
from __future__ import annotations

import hashlib
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


# --- auth guards (stub pool is fine) ---


def test_sources_require_auth(client: TestClient) -> None:
    assert client.get("/api/sources/msg_1").status_code == 401
    assert client.get("/api/sources/msg_1/context?start=0&end=1").status_code == 401
    assert client.get("/api/threads/thr_YQ").status_code == 401


def test_malformed_id_404_authenticated(client: TestClient) -> None:
    _login(client)
    # Stub pool: route-layer 404 for malformed, before DB.
    r = client.get("/api/sources/not-a-valid-id")
    assert r.status_code == 404
    r2 = client.get("/api/sources/msg_notint")
    assert r2.status_code == 404
    r3 = client.get("/api/threads/msg_1")
    assert r3.status_code == 404


# --- DB-backed ---


def _seed_message(
    pool: ConnectionPool,
    *,
    subject: str = "Test subject",
    body_text: str = "Hello plain body content here.",
    body_html: str | None = '<p>Hello <script>alert(1)</script><img src="http://x/t.gif">world</p>',
    thread_id: str = "thread-test-sources",
    sender_name: str = "Alice",
    sender_address: str = "alice@example.com",
    with_attachment: bool = False,
    markdown: str | None = None,
) -> dict[str, Any]:
    """Insert minimal email (+ optional attachment) rows; caller cleans up."""
    email_id = uuid4()
    message_id = f"<src-test-{email_id}@example.com>"
    att_id: int | None = None

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
                %(recip)s::jsonb, now(), %(btext)s, %(bhtml)s,
                %(has_att)s, %(labels)s, 'test@example.com', now()
            )
            """,
            {
                "id": email_id,
                "mid": message_id,
                "tid": thread_id,
                "subject": subject,
                "sname": sender_name,
                "saddr": sender_address,
                "recip": '{"to": ["bob@example.com"], "cc": [], "bcc": []}',
                "btext": body_text,
                "bhtml": body_html,
                "has_att": with_attachment,
                "labels": ["INBOX"],
            },
        )
        if with_attachment:
            row = conn.execute(
                """
                INSERT INTO attachments (sha256, filename, content_type, size, storage_path)
                VALUES (%(sha)s, %(fn)s, 'text/plain', 12, %(path)s)
                RETURNING id
                """,
                {
                    "sha": hashlib.sha256(str(email_id).encode()).hexdigest(),
                    "fn": "note.txt",
                    "path": f"/tmp/chronicle-test/{email_id}",
                },
            ).fetchone()
            assert row is not None
            att_id = row[0]
            conn.execute(
                """
                INSERT INTO email_attachments (email_id, attachment_id, filename)
                VALUES (%(eid)s, %(aid)s, 'note.txt')
                """,
                {"eid": email_id, "aid": att_id},
            )
            status = "extracted" if markdown is not None else "pending"
            conn.execute(
                """
                INSERT INTO attachment_contents (attachment_id, status, markdown, reason)
                VALUES (%(aid)s, %(status)s, %(md)s, %(reason)s)
                """,
                {
                    "aid": att_id,
                    "status": status,
                    "md": markdown,
                    "reason": None if status == "extracted" else "not-yet",
                },
            )
        conn.commit()

    return {
        "email_id": email_id,
        "msg_sid": encode_source_id("msg", email_id),
        "thread_id": thread_id,
        "thr_sid": encode_source_id("thr", thread_id),
        "att_id": att_id,
        "att_sid": encode_source_id("att", att_id) if att_id is not None else None,
        "body_text": body_text,
    }


def _cleanup(pool: ConnectionPool, seed: dict[str, Any]) -> None:
    with pool.connection() as conn:
        if seed.get("att_id") is not None:
            conn.execute(
                "DELETE FROM email_attachments WHERE attachment_id = %(aid)s",
                {"aid": seed["att_id"]},
            )
            conn.execute(
                "DELETE FROM attachment_contents WHERE attachment_id = %(aid)s",
                {"aid": seed["att_id"]},
            )
            conn.execute(
                "DELETE FROM attachments WHERE id = %(aid)s",
                {"aid": seed["att_id"]},
            )
        conn.execute("DELETE FROM emails WHERE id = %(id)s", {"id": seed["email_id"]})
        conn.commit()


def test_unknown_msg_404(db_client: TestClient) -> None:
    _login(db_client)
    # Well-formed but missing UUID.int
    sid = encode_source_id("msg", uuid4())
    r = db_client.get(f"/api/sources/{sid}")
    assert r.status_code == 404


def test_message_envelope_and_sanitized_html(
    db_pool: ConnectionPool, db_client: TestClient
) -> None:
    seed = _seed_message(db_pool, with_attachment=True, markdown="# extracted\n\nhello")
    try:
        _login(db_client)
        r = db_client.get(f"/api/sources/{seed['msg_sid']}")
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "msg"
        env = body["envelope"]
        assert env["id"] == seed["msg_sid"]
        assert env["subject"] == "Test subject"
        assert env["sender_name"] == "Alice"
        assert env["sender_address"] == "alice@example.com"
        assert env["mailbox"] == "test@example.com"
        assert env["has_attachment"] is True
        assert isinstance(env["attachments"], list)
        assert len(env["attachments"]) == 1
        assert env["attachments"][0]["id"].startswith("att_")
        assert env["thread_id"] == seed["thr_sid"]

        b = body["body"]
        assert b["text"] == seed["body_text"]
        assert b["html"] is not None
        assert "<script" not in b["html"].lower()
        assert "<img" not in b["html"].lower()
        assert "Hello" in b["html"] or "world" in b["html"]
        assert b["had_active_content"] is True
        assert b["remote_resources_blocked"] >= 1
    finally:
        _cleanup(db_pool, seed)


def test_message_context_offsets_and_hash(db_pool: ConnectionPool, db_client: TestClient) -> None:
    text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    seed = _seed_message(db_pool, body_text=text, body_html=None)
    try:
        _login(db_client)
        r = db_client.get(
            f"/api/sources/{seed['msg_sid']}/context",
            params={"start": 5, "end": 10, "window": 3},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["excerpt"] == "FGHIJ"
        assert body["context_before"] == "CDE"
        assert body["context_after"] == "KLM"
        assert body["sha256"] == hashlib.sha256(b"FGHIJ").hexdigest()
        assert body["window"] == 3

        bad = db_client.get(
            f"/api/sources/{seed['msg_sid']}/context",
            params={"start": 0, "end": 9999},
        )
        assert bad.status_code == 416
    finally:
        _cleanup(db_pool, seed)


def test_attachment_source_extracted(db_pool: ConnectionPool, db_client: TestClient) -> None:
    md = "x" * 100 + "MARKDOWN_BODY"
    seed = _seed_message(db_pool, with_attachment=True, markdown=md)
    try:
        _login(db_client)
        assert seed["att_sid"] is not None
        r = db_client.get(f"/api/sources/{seed['att_sid']}")
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "att"
        assert body["filename"] == "note.txt"
        assert body["extraction_status"] == "extracted"
        assert body["markdown"] == md
        assert body["truncated"] is False
        assert body["source_message_id"] == seed["msg_sid"]
        assert body["source_envelope"] is not None
        assert body["source_envelope"]["subject"] == "Test subject"

        # Paging via text_offset
        r2 = db_client.get(f"/api/sources/{seed['att_sid']}", params={"text_offset": 100})
        assert r2.status_code == 200
        assert r2.json()["markdown"] == "MARKDOWN_BODY"
    finally:
        _cleanup(db_pool, seed)


def test_thread_envelope_only_ordered(db_pool: ConnectionPool, db_client: TestClient) -> None:
    tid = f"thread-ord-{uuid4()}"
    seeds: list[dict[str, Any]] = []
    try:
        # Two messages, different senders; insert second subject first by time via sequential now()
        seeds.append(
            _seed_message(
                db_pool,
                thread_id=tid,
                subject="First",
                sender_name="Alice",
                sender_address="alice@example.com",
                body_html=None,
            )
        )
        seeds.append(
            _seed_message(
                db_pool,
                thread_id=tid,
                subject="Second",
                sender_name="Bob",
                sender_address="bob@example.com",
                body_html=None,
            )
        )
        _login(db_client)
        thr_sid = encode_source_id("thr", tid)
        r = db_client.get(f"/api/threads/{thr_sid}")
        assert r.status_code == 200
        body = r.json()
        assert body["thread_id"] == thr_sid
        assert body["message_count"] == 2
        assert body["truncated"] is False
        assert body["subject"] is not None
        assert len(body["messages"]) == 2
        # Envelope-only: no body fields
        for m in body["messages"]:
            assert "body" not in m
            assert "body_text" not in m
            assert "body_html" not in m
            assert m["id"].startswith("msg_")
        # Participants include both senders
        addrs = {p["address"] for p in body["participants"]}
        assert "alice@example.com" in addrs
        assert "bob@example.com" in addrs
        # Ordered by date then id
        ids = [m["id"] for m in body["messages"]]
        assert ids[0] == seeds[0]["msg_sid"]
        assert ids[1] == seeds[1]["msg_sid"]
    finally:
        for s in seeds:
            _cleanup(db_pool, s)


def test_unknown_thread_404(db_client: TestClient) -> None:
    _login(db_client)
    sid = encode_source_id("thr", f"missing-thread-{uuid4()}")
    r = db_client.get(f"/api/threads/{sid}")
    assert r.status_code == 404
