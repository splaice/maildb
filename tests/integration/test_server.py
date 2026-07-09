from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from maildb import server
from maildb.maildb import MailDB

pytestmark = pytest.mark.integration


def _ctx(test_pool, test_settings):  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool, config=test_settings)
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(db=db))
    )


def _seed_email(test_pool, *, message_id: str = "server-fields@example.com") -> None:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, sender_address,
                   sender_domain, recipients, date, body_text, has_attachment,
                   attachments, labels, created_at)
               VALUES (%(id)s, %(mid)s, 'server-thread', 'Server test',
                   'alice@example.com', 'example.com', %(recipients)s, %(date)s,
                   'Visible body text', false, %(attachments)s, %(labels)s, now())""",
            {
                "id": uuid4(),
                "mid": message_id,
                "recipients": json.dumps({"to": ["bob@example.com"], "cc": [], "bcc": []}),
                "date": datetime(2025, 1, 1, 10, 0, tzinfo=UTC),
                "attachments": json.dumps([]),
                "labels": ["INBOX"],
            },
        )
        conn.commit()


def test_find_rejects_unknown_fields(test_pool, test_settings) -> None:  # type: ignore[no-untyped-def]
    _seed_email(test_pool)

    with pytest.raises(ValueError) as excinfo:
        server.find(_ctx(test_pool, test_settings), fields=["body"])

    message = str(excinfo.value)
    assert "body" in message
    assert "body_text" in message


def test_get_emails_rejects_unknown_fields(test_pool, test_settings) -> None:  # type: ignore[no-untyped-def]
    _seed_email(test_pool)

    with pytest.raises(ValueError) as excinfo:
        server.get_emails(
            _ctx(test_pool, test_settings),
            ids=["server-fields@example.com"],
            fields=["body"],
        )

    message = str(excinfo.value)
    assert "body" in message
    assert "body_text" in message


def test_empty_fields_uses_default_projection(test_pool, test_settings) -> None:  # type: ignore[no-untyped-def]
    _seed_email(test_pool)

    result = server.find(_ctx(test_pool, test_settings), fields=[])

    email = result["results"][0]
    assert "body_text" not in email
    assert email["body_length"] == len("Visible body text")


def test_get_attachment_markdown_pages_text(test_pool, test_settings) -> None:  # type: ignore[no-untyped-def]
    markdown = "abcdefghi"
    with test_pool.connection() as conn:
        cur = conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES ('server-md', 'doc.md', 'text/markdown', 9, 'server/md') RETURNING id"
        )
        attachment_id = cur.fetchone()[0]
        conn.execute(
            "INSERT INTO attachment_contents (attachment_id, status, markdown, markdown_bytes) "
            "VALUES (%s, 'extracted', %s, %s)",
            (attachment_id, markdown, len(markdown)),
        )
        conn.commit()

    full = server.get_attachment_markdown(_ctx(test_pool, test_settings), attachment_id)
    assert full == {
        "attachment_id": attachment_id,
        "text": markdown,
        "total_chars": len(markdown),
        "offset": 0,
        "truncated": False,
    }

    first_page = server.get_attachment_markdown(
        _ctx(test_pool, test_settings), attachment_id, max_chars=5
    )
    assert first_page == {
        "attachment_id": attachment_id,
        "text": "abcde",
        "total_chars": len(markdown),
        "offset": 0,
        "truncated": True,
    }

    second_page = server.get_attachment_markdown(
        _ctx(test_pool, test_settings), attachment_id, max_chars=5, offset=5
    )
    assert second_page == {
        "attachment_id": attachment_id,
        "text": "fghi",
        "total_chars": len(markdown),
        "offset": 5,
        "truncated": False,
    }
