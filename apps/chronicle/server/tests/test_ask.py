# tests/test_ask.py
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from chronicle_server.ids import encode_source_id
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse hand-rolled SSE frames into (event, data) pairs."""
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
        elif line == "":
            if data_lines:
                raw = "\n".join(data_lines)
                events.append((event_name, json.loads(raw)))
            event_name = "message"
            data_lines = []
    if data_lines:
        events.append((event_name, json.loads("\n".join(data_lines))))
    return events


def test_ask_requires_auth(client: TestClient) -> None:
    r = client.post("/api/ask", json={"question": "hello", "mode": "scope"})
    assert r.status_code == 401


def test_ask_disabled_returns_json_not_sse(
    settings: Any,
    stub_pool: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from chronicle_server.app import create_app

    settings.ask_enabled = False
    monkeypatch.setattr("chronicle_server.app.create_pool", lambda _s: stub_pool)
    monkeypatch.setattr("chronicle_server.app.init_app_tables", lambda _p: None)
    monkeypatch.setattr("chronicle_server.app.ensure_user", lambda _p, _u: None)
    app = create_app(settings)
    with TestClient(app) as tc:
        _login(tc)
        r = tc.post("/api/ask", json={"question": "roof?", "mode": "scope", "scope": {}})
        assert r.status_code == 200
        assert "text/event-stream" not in (r.headers.get("content-type") or "")
        body = r.json()
        assert body["available"] is False
        assert "reason" in body


def test_ask_unavailable_returns_json(
    client: TestClient,
) -> None:
    _login(client)
    # Force model unavailable
    client.app.state.model_available = False  # type: ignore[attr-defined]
    r = client.post("/api/ask", json={"question": "roof?", "mode": "scope", "scope": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert "Model" in body["reason"] or "unavailable" in body["reason"].lower()


def _seed_message(
    pool: ConnectionPool,
    *,
    subject: str = "Re: roof",
    body_text: str = "We selected standing-seam metal roofing for the house.",
    sender_address: str = "alice@example.com",
    sender_name: str = "Alice Chen",
    source_account: str = "test@example.com",
    date: str = "2015-06-17T12:00:00+00:00",
) -> dict[str, Any]:
    email_id = uuid4()
    message_id = f"<ask-test-{email_id}@example.com>"
    tid = f"thread-ask-{email_id}"

    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO emails (
                id, message_id, thread_id, subject,
                sender_name, sender_address, sender_domain,
                recipients, date, body_text, body_html,
                has_attachment, attachments, labels, source_account, created_at
            ) VALUES (
                %(id)s, %(mid)s, %(tid)s, %(subject)s,
                %(sname)s, %(saddr)s, 'example.com',
                '{"to": ["bob@example.com"], "cc": [], "bcc": []}'::jsonb,
                %(date)s::timestamptz, %(btext)s, NULL,
                false, NULL, %(labels)s, %(acct)s, now()
            )
            """,
            {
                "id": email_id,
                "mid": message_id,
                "tid": tid,
                "subject": subject,
                "sname": sender_name,
                "saddr": sender_address,
                "date": date,
                "btext": body_text,
                "labels": ["INBOX"],
                "acct": source_account,
            },
        )
        conn.commit()

    return {
        "email_id": email_id,
        "msg_sid": encode_source_id("msg", email_id),
        "subject": subject,
        "body_text": body_text,
    }


def test_ask_sse_happy_path(
    db_client: TestClient,
    db_pool: ConnectionPool,
    db_settings: Any,
) -> None:
    seed = _seed_message(db_pool)
    try:
        _login(db_client)

        def fake_transport(
            model: str, messages: list[dict[str, str]], stream: bool
        ) -> Iterator[str]:
            assert stream is True
            assert len(messages) == 3
            yield "The house uses standing-seam metal "
            yield "roofing [S1]. Also see [S99]."

        db_client.app.state.chat_transport = fake_transport  # type: ignore[attr-defined]
        db_client.app.state.model_available = True  # type: ignore[attr-defined]

        with db_client.stream(
            "POST",
            "/api/ask",
            json={
                "question": "standing-seam metal roof",
                "mode": "scope",
                "scope": {},
            },
        ) as r:
            assert r.status_code == 200
            assert "text/event-stream" in (r.headers.get("content-type") or "")
            body = "".join(r.iter_text())

        events = _parse_sse(body)
        names = [e[0] for e in events]
        assert "retrieval" in names
        assert "token" in names
        assert "citation" in names
        assert "done" in names

        retrieval = next(d for n, d in events if n == "retrieval")
        assert "count" in retrieval
        assert "types" in retrieval
        assert retrieval["count"] >= 1

        tokens = "".join(d["text"] for n, d in events if n == "token")
        assert "standing-seam" in tokens or "metal" in tokens

        citations = [d for n, d in events if n == "citation"]
        assert len(citations) >= 1
        assert citations[0]["source_id"]
        assert citations[0]["marker"] == "[S1]"
        # Citation resolves to a real retrieved id from our seed when matched
        done = next(d for n, d in events if n == "done")
        assert "answer_id" in done
        assert done["model_route"].startswith("ollama:")
        assert done["policy_version"] == db_settings.policy_version
        assert "S99" in done.get("unmatched_markers", []) or "S99" in [
            m.lstrip("S") and m for m in done.get("unmatched_markers", [])
        ]
        assert "S99" in done["unmatched_markers"] or any(
            "99" in m for m in done["unmatched_markers"]
        )

        # Rows persisted
        with db_pool.connection() as conn:
            row = conn.execute(
                """
                SELECT status, answer_text FROM app_answers
                 WHERE id = %(id)s
                """,
                {"id": done["answer_id"]},
            ).fetchone()
            assert row is not None
            assert row[0] == "complete"
            assert row[1] is not None
            cit_count = conn.execute(
                "SELECT count(*) FROM app_citations WHERE answer_id = %(id)s",
                {"id": done["answer_id"]},
            ).fetchone()
            assert cit_count is not None
            assert cit_count[0] >= 1
    finally:
        with db_pool.connection() as conn:
            conn.execute(
                "DELETE FROM app_answers WHERE question LIKE %(q)s",
                {"q": "%standing-seam%"},
            )
            conn.execute("DELETE FROM emails WHERE id = %(id)s", {"id": seed["email_id"]})
            conn.commit()


def test_ask_midstream_exception_error_event(
    db_client: TestClient,
    db_pool: ConnectionPool,
) -> None:
    seed = _seed_message(
        db_pool,
        subject="Error path roof",
        body_text="Roof material discussion for error path test uniquephrase42.",
    )
    try:
        _login(db_client)

        def boom_transport(
            model: str, messages: list[dict[str, str]], stream: bool
        ) -> Iterator[str]:
            yield "Partial "
            raise RuntimeError("model crashed")

        db_client.app.state.chat_transport = boom_transport  # type: ignore[attr-defined]
        db_client.app.state.model_available = True  # type: ignore[attr-defined]

        with db_client.stream(
            "POST",
            "/api/ask",
            json={
                "question": "uniquephrase42 roof",
                "mode": "scope",
                "scope": {},
            },
        ) as r:
            assert r.status_code == 200
            body = "".join(r.iter_text())

        events = _parse_sse(body)
        names = [e[0] for e in events]
        assert "error" in names
        err = next(d for n, d in events if n == "error")
        assert "message" in err
        # Safe message — no stack dump required
        assert "failed" in err["message"].lower() or "error" in err["message"].lower()

        with db_pool.connection() as conn:
            row = conn.execute(
                """
                SELECT status FROM app_answers
                 WHERE question LIKE %(q)s
                 ORDER BY created_at DESC LIMIT 1
                """,
                {"q": "%uniquephrase42%"},
            ).fetchone()
            assert row is not None
            assert row[0] == "error"
    finally:
        with db_pool.connection() as conn:
            conn.execute(
                "DELETE FROM app_answers WHERE question LIKE %(q)s",
                {"q": "%uniquephrase42%"},
            )
            conn.execute("DELETE FROM emails WHERE id = %(id)s", {"id": seed["email_id"]})
            conn.commit()
