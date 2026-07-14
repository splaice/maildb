# tests/test_files.py
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from chronicle_server.files import CONTENT_TYPE_FAMILY_PATTERNS, _match_magic
from chronicle_server.ids import encode_source_id
from tests.conftest import PASSWORD, USERNAME

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200


# --- auth ---


def test_attachments_require_auth(client: TestClient) -> None:
    assert client.post("/api/attachments/list", json={}).status_code == 401
    assert client.get("/api/attachments/att_1/preview").status_code == 401
    assert client.get("/api/attachments/att_1/download").status_code == 401


# --- unit: family mapping + magic ---


def test_family_mapping_constant() -> None:
    assert "pdf" in CONTENT_TYPE_FAMILY_PATTERNS
    assert any("pdf" in p for p in CONTENT_TYPE_FAMILY_PATTERNS["pdf"])
    assert any(p.startswith("image/") for p in CONTENT_TYPE_FAMILY_PATTERNS["image"])
    assert "spreadsheet" in CONTENT_TYPE_FAMILY_PATTERNS
    assert "document" in CONTENT_TYPE_FAMILY_PATTERNS
    assert "text" in CONTENT_TYPE_FAMILY_PATTERNS


def test_magic_numbers() -> None:
    assert _match_magic("image/png", b"\x89PNG\r\n\x1a\nxxxx")
    assert _match_magic("image/jpeg", b"\xff\xd8\xff\xe0xxxx")
    assert _match_magic("image/gif", b"GIF89a......")
    assert _match_magic("image/webp", b"RIFF\x00\x00\x00\x00WEBP")
    assert _match_magic("application/pdf", b"%PDF-1.4....")
    assert _match_magic("text/plain", b"hello")
    assert not _match_magic("image/png", b"not a png")
    assert not _match_magic("application/pdf", b"MZ....")


# --- seed helpers ---


def _seed_attachment(
    pool: ConnectionPool,
    *,
    filename: str = "note.txt",
    content_type: str = "text/plain",
    size: int = 12,
    storage_path: str,
    sha256: str | None = None,
    subject: str = "With attachment",
    sender_name: str = "Alice",
    sender_address: str = "alice@example.com",
    date: str = "2015-06-01T12:00:00+00:00",
    status: str | None = "extracted",
    reason: str | None = None,
    markdown: str | None = "extracted text",
    email_id: Any | None = None,
) -> dict[str, Any]:
    eid = email_id or uuid4()
    message_id = f"<files-test-{eid}@example.com>"
    sha = sha256 or hashlib.sha256(f"{eid}:{filename}:{storage_path}".encode()).hexdigest()

    with pool.connection() as conn:
        # Email may already exist when linking another attachment.
        existing = conn.execute("SELECT 1 FROM emails WHERE id = %(id)s", {"id": eid}).fetchone()
        if existing is None:
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
                    'body', null, true, %(labels)s, 'test@example.com', now()
                )
                """,
                {
                    "id": eid,
                    "mid": message_id,
                    "tid": f"thread-{eid}",
                    "subject": subject,
                    "sname": sender_name,
                    "saddr": sender_address,
                    "date": date,
                    "labels": ["INBOX"],
                },
            )

        # Reuse attachment row when same sha256 already present.
        existing_att = conn.execute(
            "SELECT id FROM attachments WHERE sha256 = %(sha)s", {"sha": sha}
        ).fetchone()
        if existing_att is not None:
            att_id = existing_att[0]
        else:
            row = conn.execute(
                """
                INSERT INTO attachments (sha256, filename, content_type, size, storage_path)
                VALUES (%(sha)s, %(fn)s, %(ct)s, %(size)s, %(path)s)
                RETURNING id
                """,
                {
                    "sha": sha,
                    "fn": filename,
                    "ct": content_type,
                    "size": size,
                    "path": storage_path,
                },
            ).fetchone()
            assert row is not None
            att_id = row[0]
            if status is not None:
                conn.execute(
                    """
                    INSERT INTO attachment_contents (attachment_id, status, markdown, reason)
                    VALUES (%(aid)s, %(status)s, %(md)s, %(reason)s)
                    ON CONFLICT (attachment_id) DO NOTHING
                    """,
                    {
                        "aid": att_id,
                        "status": status,
                        "md": markdown,
                        "reason": reason,
                    },
                )

        link = conn.execute(
            """
            SELECT 1 FROM email_attachments
             WHERE email_id = %(eid)s AND attachment_id = %(aid)s
            """,
            {"eid": eid, "aid": att_id},
        ).fetchone()
        if link is None:
            conn.execute(
                """
                INSERT INTO email_attachments (email_id, attachment_id, filename)
                VALUES (%(eid)s, %(aid)s, %(fn)s)
                """,
                {"eid": eid, "aid": att_id, "fn": filename},
            )
        conn.commit()

    return {
        "email_id": eid,
        "msg_sid": encode_source_id("msg", eid),
        "att_id": att_id,
        "att_sid": encode_source_id("att", att_id),
        "sha256": sha,
        "filename": filename,
        "storage_path": storage_path,
    }


def _cleanup_seeds(pool: ConnectionPool, seeds: list[dict[str, Any]]) -> None:
    with pool.connection() as conn:
        att_ids = {s["att_id"] for s in seeds}
        email_ids = {s["email_id"] for s in seeds}
        for aid in att_ids:
            conn.execute(
                "DELETE FROM email_attachments WHERE attachment_id = %(aid)s",
                {"aid": aid},
            )
            conn.execute(
                "DELETE FROM attachment_contents WHERE attachment_id = %(aid)s",
                {"aid": aid},
            )
            conn.execute("DELETE FROM attachments WHERE id = %(aid)s", {"aid": aid})
        for eid in email_ids:
            conn.execute("DELETE FROM emails WHERE id = %(id)s", {"id": eid})
        conn.commit()


def _write_file(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# --- list ---


def test_list_shape_and_keyset(db_pool: ConnectionPool, db_client: TestClient) -> None:
    seeds = [
        _seed_attachment(
            db_pool,
            filename=f"file-{i}.txt",
            storage_path=f"list-test/{i}.txt",
            date=f"2015-0{i + 1}-01T12:00:00+00:00",
            status="extracted",
        )
        for i in range(3)
    ]
    try:
        _login(db_client)
        r = db_client.post(
            "/api/attachments/list",
            json={"limit": 2, "filters": {}},
        )
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert "next_cursor" in body
        assert "scope_fingerprint" in body
        assert len(body["items"]) == 2
        item = body["items"][0]
        assert item["id"].startswith("att_")
        assert item["source_message_id"].startswith("msg_")
        assert "extraction" in item
        assert "status" in item["extraction"]
        assert "sha256" in item
        assert "duplicate_count" in item
        assert body["next_cursor"] is not None

        r2 = db_client.post(
            "/api/attachments/list",
            json={"limit": 2, "cursor": body["next_cursor"], "filters": {}},
        )
        assert r2.status_code == 200
        body2 = r2.json()
        ids1 = {x["id"] for x in body["items"]}
        ids2 = {x["id"] for x in body2["items"]}
        assert ids1.isdisjoint(ids2)
    finally:
        _cleanup_seeds(db_pool, seeds)


def test_list_family_and_status_coalesce(db_pool: ConnectionPool, db_client: TestClient) -> None:
    seeds = [
        _seed_attachment(
            db_pool,
            filename="a.pdf",
            content_type="application/pdf",
            storage_path="fam/a.pdf",
            status="failed",
            reason="timeout",
            markdown=None,
        ),
        _seed_attachment(
            db_pool,
            filename="b.png",
            content_type="image/png",
            storage_path="fam/b.png",
            status="extracted",
        ),
        _seed_attachment(
            db_pool,
            filename="no-status.bin",
            content_type="application/octet-stream",
            storage_path="fam/c.bin",
            status=None,  # no attachment_contents row → pending
            markdown=None,
        ),
    ]
    # For the third seed with status=None we need no attachment_contents.
    # _seed_attachment with status=None still skips insert when reusing — force delete.
    with db_pool.connection() as conn:
        conn.execute(
            "DELETE FROM attachment_contents WHERE attachment_id = %(aid)s",
            {"aid": seeds[2]["att_id"]},
        )
        conn.commit()

    try:
        _login(db_client)
        r = db_client.post(
            "/api/attachments/list",
            json={"filters": {"content_type_family": "pdf"}},
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert all((it["content_type"] or "").startswith("application/pdf") for it in items)
        assert any(it["filename"] == "a.pdf" for it in items)

        r2 = db_client.post(
            "/api/attachments/list",
            json={"filters": {"status": "failed"}},
        )
        assert r2.status_code == 200
        failed = r2.json()["items"]
        assert any(it["filename"] == "a.pdf" for it in failed)
        assert all(it["extraction"]["status"] == "failed" for it in failed)
        assert any(it["extraction"].get("reason") == "timeout" for it in failed)

        r3 = db_client.post(
            "/api/attachments/list",
            json={"filters": {"status": "pending"}},
        )
        assert r3.status_code == 200
        pending = r3.json()["items"]
        assert any(it["filename"] == "no-status.bin" for it in pending)
        for it in pending:
            if it["filename"] == "no-status.bin":
                assert it["extraction"]["status"] == "pending"
    finally:
        _cleanup_seeds(db_pool, seeds)


def test_duplicate_grouping_and_occurrence_bound(
    db_pool: ConnectionPool, db_client: TestClient
) -> None:
    shared_sha = hashlib.sha256(b"dup-content-unique").hexdigest()
    seeds: list[dict[str, Any]] = []
    # 3 occurrences of same hash (exact duplicates)
    for i in range(3):
        seeds.append(
            _seed_attachment(
                db_pool,
                filename="shared.pdf",
                content_type="application/pdf",
                storage_path=f"dup/shared-{i}.pdf",
                sha256=shared_sha,
                subject=f"Copy {i}",
                date=f"2016-01-0{i + 1}T12:00:00+00:00",
                status="extracted",
            )
        )
    # Different hash, same filename — must not collapse
    seeds.append(
        _seed_attachment(
            db_pool,
            filename="shared.pdf",
            content_type="application/pdf",
            storage_path="dup/other.pdf",
            sha256=hashlib.sha256(b"other-content").hexdigest(),
            subject="Different content",
            date="2016-02-01T12:00:00+00:00",
            status="extracted",
        )
    )
    try:
        _login(db_client)
        r = db_client.post(
            "/api/attachments/list",
            json={
                "group_duplicates": True,
                "filters": {"filename": "shared.pdf"},
            },
        )
        assert r.status_code == 200
        items = r.json()["items"]
        # Two groups: shared_sha and the other hash
        assert len(items) == 2
        shared = next(it for it in items if it["sha256"] == shared_sha)
        assert shared["duplicate_count"] >= 3
        assert shared["occurrences"] is not None
        assert len(shared["occurrences"]) == 3
        for occ in shared["occurrences"]:
            assert occ["id"].startswith("msg_")
            assert "subject" in occ

        # Bound: seed 25 occurrences and check cap at 20
        bound_sha = hashlib.sha256(b"bound-dup").hexdigest()
        bound_seeds: list[dict[str, Any]] = []
        for i in range(25):
            bound_seeds.append(
                _seed_attachment(
                    db_pool,
                    filename="bound.txt",
                    content_type="text/plain",
                    storage_path=f"bound/{i}.txt",
                    sha256=bound_sha,
                    subject=f"Bound {i}",
                    date=f"2017-01-01T{i % 24:02d}:00:00+00:00",
                    status="extracted",
                )
            )
        try:
            r2 = db_client.post(
                "/api/attachments/list",
                json={
                    "group_duplicates": True,
                    "filters": {"filename": "bound.txt"},
                },
            )
            assert r2.status_code == 200
            bound_item = next(it for it in r2.json()["items"] if it["sha256"] == bound_sha)
            assert bound_item["duplicate_count"] >= 25
            assert len(bound_item["occurrences"] or []) == 20
        finally:
            _cleanup_seeds(db_pool, bound_seeds)
    finally:
        _cleanup_seeds(db_pool, seeds)


# --- preview / download ---


def test_containment_guard(
    db_pool: ConnectionPool,
    db_client: TestClient,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "attachments"
    root.mkdir()
    seed = _seed_attachment(
        db_pool,
        filename="evil.txt",
        content_type="text/plain",
        storage_path="../../etc/passwd",
        status="extracted",
    )
    # Also put a legit file for positive path
    _write_file(root, "ok/note.txt", b"hello world")
    legit = _seed_attachment(
        db_pool,
        filename="note.txt",
        content_type="text/plain",
        storage_path="ok/note.txt",
        status="extracted",
    )
    try:
        monkeypatch.setattr(db_client.app.state.settings, "attachment_root", str(root))
        _login(db_client)
        # Path escape → 404, no path leakage
        r = db_client.get(f"/api/attachments/{seed['att_sid']}/preview")
        assert r.status_code == 404
        assert "etc/passwd" not in r.text
        assert str(root) not in r.text

        r2 = db_client.get(f"/api/attachments/{seed['att_sid']}/download")
        assert r2.status_code == 404

        # Missing on disk → 404
        missing = _seed_attachment(
            db_pool,
            filename="gone.txt",
            content_type="text/plain",
            storage_path="missing/gone.txt",
            status="extracted",
        )
        try:
            r3 = db_client.get(f"/api/attachments/{missing['att_sid']}/preview")
            assert r3.status_code == 404
        finally:
            _cleanup_seeds(db_pool, [missing])

        # Legit file works
        r4 = db_client.get(f"/api/attachments/{legit['att_sid']}/preview")
        assert r4.status_code == 200
        assert r4.content == b"hello world"
    finally:
        _cleanup_seeds(db_pool, [seed, legit])


def test_preview_allowlist_and_headers(
    db_pool: ConnectionPool,
    db_client: TestClient,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setattr(db_client.app.state.settings, "attachment_root", str(root))

    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    _write_file(root, "img/a.png", png_data)
    svg_data = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    _write_file(root, "img/a.svg", svg_data)
    # Declared png but wrong magic
    _write_file(root, "img/fake.png", b"not-png-data-here")
    pdf_data = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    _write_file(root, "doc/a.pdf", pdf_data)
    _write_file(root, "doc/a.txt", b"plain text body")

    seeds = [
        _seed_attachment(
            db_pool,
            filename="a.png",
            content_type="image/png",
            storage_path="img/a.png",
            size=len(png_data),
        ),
        _seed_attachment(
            db_pool,
            filename="a.svg",
            content_type="image/svg+xml",
            storage_path="img/a.svg",
            size=len(svg_data),
        ),
        _seed_attachment(
            db_pool,
            filename="fake.png",
            content_type="image/png",
            storage_path="img/fake.png",
            size=16,
        ),
        _seed_attachment(
            db_pool,
            filename="a.pdf",
            content_type="application/pdf",
            storage_path="doc/a.pdf",
            size=len(pdf_data),
        ),
        _seed_attachment(
            db_pool,
            filename="a.txt",
            content_type="text/plain",
            storage_path="doc/a.txt",
            size=15,
        ),
    ]
    try:
        _login(db_client)
        # PNG ok
        r = db_client.get(f"/api/attachments/{seeds[0]['att_sid']}/preview")
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("image/png")
        assert "sandbox" in r.headers.get("content-security-policy", "")
        assert r.headers.get("x-content-type-options") == "nosniff"
        cd = r.headers.get("content-disposition", "")
        assert "inline" in cd
        assert "filename" in cd.lower()

        # SVG → 415
        r_svg = db_client.get(f"/api/attachments/{seeds[1]['att_sid']}/preview")
        assert r_svg.status_code == 415
        body = r_svg.json()
        assert body["preview"] is False
        assert "reason" in body

        # Mismatched magic → 415
        r_fake = db_client.get(f"/api/attachments/{seeds[2]['att_sid']}/preview")
        assert r_fake.status_code == 415
        assert r_fake.json()["preview"] is False

        # PDF ok
        r_pdf = db_client.get(f"/api/attachments/{seeds[3]['att_sid']}/preview")
        assert r_pdf.status_code == 200
        assert "pdf" in r_pdf.headers.get("content-type", "")

        # Text ok
        r_txt = db_client.get(f"/api/attachments/{seeds[4]['att_sid']}/preview")
        assert r_txt.status_code == 200
        assert "text/plain" in r_txt.headers.get("content-type", "")
        assert r_txt.text == "plain text body"
        assert "sandbox" in r_txt.headers.get("content-security-policy", "")
    finally:
        _cleanup_seeds(db_pool, seeds)


def test_download_disposition_and_audit(
    db_pool: ConnectionPool,
    db_client: TestClient,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "attachments"
    root.mkdir()
    data = b"download-me"
    _write_file(root, "dl/file.bin", data)
    monkeypatch.setattr(db_client.app.state.settings, "attachment_root", str(root))
    seed = _seed_attachment(
        db_pool,
        filename="file.bin",
        content_type="application/octet-stream",
        storage_path="dl/file.bin",
        size=len(data),
        status="failed",
        reason="unsupported",
        markdown=None,
    )
    try:
        _login(db_client)
        r = db_client.get(f"/api/attachments/{seed['att_sid']}/download")
        assert r.status_code == 200
        assert r.content == data
        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert r.headers.get("x-content-type-options") == "nosniff"

        with db_pool.connection() as conn:
            row = conn.execute(
                """
                SELECT action, detail
                  FROM app_audit
                 WHERE action = 'download'
                 ORDER BY id DESC
                 LIMIT 1
                """
            ).fetchone()
        assert row is not None
        assert row[0] == "download"
        detail = row[1]
        if isinstance(detail, str):
            import json

            detail = json.loads(detail)
        assert detail.get("attachment_id") == seed["att_sid"]
    finally:
        _cleanup_seeds(db_pool, [seed])
