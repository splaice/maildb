from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from maildb.maildb import MailDB

pytestmark = pytest.mark.integration


def _seed_attachment_chunk(
    test_pool,
    *,
    attachment_id: int | None = None,
    sha256: str = "s1",
    content_type: str = "application/pdf",
    filename: str = "doc.pdf",
    chunk_text: str = "Termination clause: 30 days notice.",
    embedding: list[float] | None = None,
    heading_path: str | None = "Overview > Payment Terms",
    email_ids: list[str] | None = None,
) -> tuple[int, int]:
    """Insert an attachment + one chunk + optional email linkage. Returns (att_id, chunk_id)."""
    with test_pool.connection() as conn:
        if attachment_id is None:
            cur = conn.execute(
                "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (sha256, filename, content_type, 100, f"{sha256[:2]}/{sha256[2:4]}/{sha256}"),
            )
            attachment_id = cur.fetchone()[0]
        vec = str(embedding or [0.1] * 768)
        cur = conn.execute(
            """INSERT INTO attachment_chunks
                   (attachment_id, chunk_index, heading_path, token_count, text, embedding)
               VALUES (%s, 0, %s, 8, %s, %s) RETURNING id""",
            (attachment_id, heading_path, chunk_text, vec),
        )
        chunk_id = cur.fetchone()[0]

        if email_ids:
            for mid in email_ids:
                conn.execute(
                    "INSERT INTO emails (id, message_id, thread_id, source_account) "
                    "VALUES (gen_random_uuid(), %s, 't', 'search@ex.com')",
                    (mid,),
                )
                eid = conn.execute(
                    "SELECT id FROM emails WHERE message_id = %s", (mid,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO email_attachments (email_id, attachment_id, filename) "
                    "VALUES (%s, %s, %s)",
                    (eid, attachment_id, filename),
                )
        conn.commit()
    return attachment_id, chunk_id


def test_search_attachments_returns_matching_chunk(test_pool, test_settings):
    att_id, _ = _seed_attachment_chunk(
        test_pool,
        chunk_text="Late fees accrue after 30 days.",
        email_ids=["<email-1@ex.com>"],
    )
    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.1] * 768
    results, total = db.search_attachments(query="late fees")
    assert total >= 1
    hit = next(r for r in results if r.attachment_id == att_id)
    assert hit.chunk.text == "Late fees accrue after 30 days."
    assert "<email-1@ex.com>" in hit.emails
    assert hit.similarity > 0


def test_search_attachments_filters_by_content_type(test_pool, test_settings):
    _seed_attachment_chunk(
        test_pool,
        sha256="pdf1",
        content_type="application/pdf",
        filename="a.pdf",
        chunk_text="pdf content",
    )
    _seed_attachment_chunk(
        test_pool,
        sha256="doc1",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="b.docx",
        chunk_text="docx content",
    )
    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.1] * 768
    results, _ = db.search_attachments(query="content", content_type="application/pdf")
    assert all(r.content_type == "application/pdf" for r in results)


def test_search_attachments_honors_email_level_account_filter(test_pool, test_settings):
    # Account A carries attachment X; Account B has a different one.
    iid_a = uuid4()
    with test_pool.connection() as conn:
        conn.execute(
            "INSERT INTO imports (id, source_account, source_file, status) "
            "VALUES (%s, %s, 't', 'completed')",
            (iid_a, "a@ex.com"),
        )
        conn.commit()
    att_id, _ = _seed_attachment_chunk(
        test_pool,
        sha256="accA",
        filename="only-A.pdf",
        chunk_text="unique token yyy",
        email_ids=["<a-1@ex.com>"],
    )
    # Link the email to account A via email_accounts
    with test_pool.connection() as conn:
        eid = conn.execute(
            "SELECT id FROM emails WHERE message_id = %s",
            ("<a-1@ex.com>",),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO email_accounts (email_id, source_account, import_id) VALUES (%s, %s, %s)",
            (eid, "a@ex.com", iid_a),
        )
        conn.commit()
    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.1] * 768

    # Scoped to account A: should find the chunk.
    results_a, _ = db.search_attachments(query="unique", account="a@ex.com")
    assert any(r.attachment_id == att_id for r in results_a)

    # Scoped to account B: should not find it.
    results_b, _ = db.search_attachments(query="unique", account="b@ex.com")
    assert not any(r.attachment_id == att_id for r in results_b)
