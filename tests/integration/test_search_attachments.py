from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from maildb.maildb import MailDB

pytestmark = pytest.mark.integration

_DIM = 768
# Query embedding used by the mock client in search_all RRF tests: unit e0.
_QUERY_VEC = [1.0] + [0.0] * (_DIM - 1)


def _vec_with_cosine(sim: float, dim: int = _DIM) -> list[float]:
    """Unit vector with cosine similarity `sim` against [1, 0, 0, ...]."""
    orth = math.sqrt(max(0.0, 1.0 - sim * sim))
    return [sim, orth] + [0.0] * (dim - 2)


def _seed_email_with_embedding(
    test_pool,
    *,
    message_id: str,
    subject: str,
    embedding: list[float],
) -> None:
    with test_pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, sender_address,
                   date, embedding, source_account, created_at)
               VALUES (gen_random_uuid(), %s, 't', %s, 'ceo@acme.com',
                       %s, %s, 'sa@ex.com', now())""",
            (message_id, subject, datetime(2025, 1, 1, tzinfo=UTC), str(embedding)),
        )
        conn.commit()


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
            conn.execute(
                "INSERT INTO attachment_contents (attachment_id, status) "
                "VALUES (%s, 'extracted') "
                "ON CONFLICT (attachment_id) DO UPDATE SET status = 'extracted'",
                (attachment_id,),
            )
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


def test_search_attachments_total_counts_seen_results(test_pool, test_settings):
    for i in range(3):
        _seed_attachment_chunk(
            test_pool,
            sha256=f"total{i}",
            filename=f"total-{i}.pdf",
            chunk_text=f"Total semantics chunk {i}",
            embedding=[0.1] * 768,
        )
    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.1] * 768

    results, total = db.search_attachments(query="total semantics", limit=1, offset=1)

    assert len(results) == 1
    assert total == 1 + len(results)


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


def test_search_attachments_filters_by_cc_recipient(test_pool, test_settings):
    att_id, _ = _seed_attachment_chunk(
        test_pool,
        sha256="rcptcc",
        filename="cc.pdf",
        chunk_text="Visible through cc recipient.",
        email_ids=["<att-cc@ex.com>"],
    )
    with test_pool.connection() as conn:
        conn.execute(
            "UPDATE emails SET recipients = %s WHERE message_id = '<att-cc@ex.com>'",
            (json.dumps({"to": [], "cc": ["cc-recipient@example.com"], "bcc": []}),),
        )
        conn.commit()

    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.1] * 768
    results, _ = db.search_attachments(
        query="cc recipient",
        recipient="cc-recipient@example.com",
    )
    assert any(result.attachment_id == att_id for result in results)


def test_search_attachments_direct_only_conflicts_with_max_to(test_pool, test_settings):
    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.1] * 768

    with pytest.raises(ValueError, match="direct_only"):
        db.search_attachments(query="anything", direct_only=True, max_to=2)


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


def test_get_attachment_markdown_returns_full_text(test_pool, test_settings):
    with test_pool.connection() as conn:
        cur = conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES ('gm', 'full.pdf', 'application/pdf', 100, 'gm/gm/gm') RETURNING id"
        )
        att_id = cur.fetchone()[0]
        conn.execute(
            "INSERT INTO attachment_contents (attachment_id, status, markdown, markdown_bytes) "
            "VALUES (%s, 'extracted', %s, %s)",
            (att_id, "# Full document text", 20),
        )
        conn.commit()
    db = MailDB._from_pool(test_pool, config=test_settings)
    assert db.get_attachment_markdown(att_id) == "# Full document text"


def test_get_attachment_markdown_returns_none_when_not_extracted(test_pool, test_settings):
    with test_pool.connection() as conn:
        cur = conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES ('gm2', 'pending.pdf', 'application/pdf', 100, 'gm2/gm2/gm2') RETURNING id"
        )
        att_id = cur.fetchone()[0]
        conn.execute(
            "INSERT INTO attachment_contents (attachment_id, status) VALUES (%s, 'pending')",
            (att_id,),
        )
        conn.commit()
    db = MailDB._from_pool(test_pool, config=test_settings)
    assert db.get_attachment_markdown(att_id) is None


def test_get_attachment_markdown_honors_account_scope(test_pool, test_settings):
    """With account set, markdown is returned only when the attachment is linked to
    an email attributed to that account."""
    iid_a = uuid4()
    with test_pool.connection() as conn:
        conn.execute(
            "INSERT INTO imports (id, source_account, source_file, status) "
            "VALUES (%s, 'a@ex.com', 't', 'completed')",
            (iid_a,),
        )
        cur = conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES ('gm3', 'scoped.pdf', 'application/pdf', 100, 'gm3/gm3/gm3') RETURNING id"
        )
        att_id = cur.fetchone()[0]
        conn.execute(
            "INSERT INTO attachment_contents (attachment_id, status, markdown, markdown_bytes) "
            "VALUES (%s, 'extracted', '# scoped', 8)",
            (att_id,),
        )
        conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, source_account) "
            "VALUES (gen_random_uuid(), '<scoped-1@ex.com>', 't', 'a@ex.com')"
        )
        eid = conn.execute(
            "SELECT id FROM emails WHERE message_id = '<scoped-1@ex.com>'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO email_attachments (email_id, attachment_id, filename) "
            "VALUES (%s, %s, 'scoped.pdf')",
            (eid, att_id),
        )
        conn.execute(
            "INSERT INTO email_accounts (email_id, source_account, import_id) "
            "VALUES (%s, 'a@ex.com', %s)",
            (eid, iid_a),
        )
        conn.commit()
    db = MailDB._from_pool(test_pool, config=test_settings)
    assert db.get_attachment_markdown(att_id) == "# scoped"
    assert db.get_attachment_markdown(att_id, account="a@ex.com") == "# scoped"
    assert db.get_attachment_markdown(att_id, account="b@ex.com") is None


def test_search_attachments_excludes_failed_extractions(test_pool, test_settings):
    """Chunks whose parent attachment_contents.status is not 'extracted' must not appear
    in search results even if the chunks have valid embeddings on disk."""
    att_id, _ = _seed_attachment_chunk(
        test_pool,
        sha256="orphan1",
        filename="orphan.pdf",
        chunk_text="orphaned failure chunk text",
        email_ids=["<orphan-1@ex.com>"],
    )
    with test_pool.connection() as conn:
        conn.execute(
            "UPDATE attachment_contents SET status = 'failed', "
            "reason = 'simulated mid-pipeline crash' WHERE attachment_id = %s",
            (att_id,),
        )
        conn.commit()
    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.1] * 768
    results, _ = db.search_attachments(query="orphaned")
    assert not any(r.attachment_id == att_id for r in results)


def test_search_all_merges_email_and_attachment_hits(test_pool, test_settings):
    """Seed one email with an embedding + one attachment chunk. search_all returns both."""
    # Seed one email with an embedding close to our query.
    vec = [0.5] * 768
    with test_pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, sender_address,
                   date, embedding, source_account, created_at)
               VALUES (gen_random_uuid(), %s, 't', 'Budget', 'ceo@acme.com',
                       %s, %s, 'sa@ex.com', now())""",
            ("<email-sa-1@ex.com>", datetime(2025, 1, 1, tzinfo=UTC), str(vec)),
        )
        conn.commit()

    _seed_attachment_chunk(
        test_pool,
        sha256="sa1",
        chunk_text="A chunk about quarterly budget.",
        embedding=[0.5] * 768,
        email_ids=["<email-sa-2@ex.com>"],
    )

    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.5] * 768
    results, total = db.search_all(query="budget")
    assert total >= 2
    sources = {r.source for r in results}
    assert sources == {"email", "attachment"}


def test_search_all_rank_fusion_prevents_crowding(test_pool, test_settings):
    """Attachments with uniformly higher cosine scores must not crowd out emails.

    Live failure mode: short dense chunks all outscore whole-email embeddings, so
    raw-similarity merge returns only attachments. RRF interleaves by own-rank;
    with limit=4 the top-ranked email lands at position 2 (1-indexed).
    """
    # Four attachment chunks, all higher similarity than both emails.
    for i, sim in enumerate([0.99, 0.95, 0.90, 0.85]):
        _seed_attachment_chunk(
            test_pool,
            sha256=f"crowd-att-{i}",
            filename=f"crowd-{i}.pdf",
            chunk_text=f"Boilerplate lease clause chunk {i}",
            embedding=_vec_with_cosine(sim),
        )
    # Two emails with lower similarities.
    _seed_email_with_embedding(
        test_pool,
        message_id="<crowd-email-1@ex.com>",
        subject="My apartment lease terms",
        embedding=_vec_with_cosine(0.50),
    )
    _seed_email_with_embedding(
        test_pool,
        message_id="<crowd-email-2@ex.com>",
        subject="Lease renewal discussion",
        embedding=_vec_with_cosine(0.40),
    )

    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = list(_QUERY_VEC)

    results, _ = db.search_all(query="lease agreement terms", limit=4)

    assert len(results) == 4
    # Top-ranked email at 1-indexed position 2 (equal RRF rank, lower raw sim loses
    # the first slot to the top attachment).
    assert results[1].source == "email"
    assert results[1].email is not None
    assert results[1].email.message_id == "<crowd-email-1@ex.com>"
    assert any(r.source == "email" for r in results)

    # Per-result similarity remains raw cosine from its source, not the fusion score.
    # Fusion scores are ~1/61 ≈ 0.016; raw cosines here are ≥ 0.40.
    for r in results:
        assert r.similarity > 0.3
        # Fusion score upper bound for rank 1: 1/61
        assert r.similarity != pytest.approx(1.0 / 61)


def test_search_all_empty_source_is_noop(test_pool, test_settings):
    """With no attachment chunks, fusion order equals pure email search order."""
    _seed_email_with_embedding(
        test_pool,
        message_id="<noop-email-1@ex.com>",
        subject="High match email",
        embedding=_vec_with_cosine(0.90),
    )
    _seed_email_with_embedding(
        test_pool,
        message_id="<noop-email-2@ex.com>",
        subject="Medium match email",
        embedding=_vec_with_cosine(0.70),
    )
    _seed_email_with_embedding(
        test_pool,
        message_id="<noop-email-3@ex.com>",
        subject="Lower match email",
        embedding=_vec_with_cosine(0.50),
    )

    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = list(_QUERY_VEC)

    email_only, _ = db.search(query="match email")
    fused, _ = db.search_all(query="match email")

    assert [r.email.message_id for r in email_only] == [
        r.email.message_id for r in fused if r.email is not None
    ]
    assert all(r.source == "email" for r in fused)
    # Raw similarities preserved (not fusion scores).
    for r in fused:
        assert r.similarity > 0.3


def test_search_all_deterministic(test_pool, test_settings):
    """Same fused search twice yields identical result sequences."""
    for i, sim in enumerate([0.95, 0.80, 0.70]):
        _seed_attachment_chunk(
            test_pool,
            sha256=f"det-att-{i}",
            filename=f"det-{i}.pdf",
            chunk_text=f"Deterministic chunk {i}",
            embedding=_vec_with_cosine(sim),
        )
    _seed_email_with_embedding(
        test_pool,
        message_id="<det-email-1@ex.com>",
        subject="Deterministic email high",
        embedding=_vec_with_cosine(0.88),
    )
    _seed_email_with_embedding(
        test_pool,
        message_id="<det-email-2@ex.com>",
        subject="Deterministic email low",
        embedding=_vec_with_cosine(0.55),
    )

    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = list(_QUERY_VEC)

    first, _ = db.search_all(query="deterministic", limit=10)
    second, _ = db.search_all(query="deterministic", limit=10)

    def _key(r):
        if r.source == "email":
            assert r.email is not None
            return (r.source, r.similarity, r.email.message_id)
        assert r.attachment_result is not None
        return (r.source, r.similarity, r.attachment_result.chunk.id)

    assert [_key(r) for r in first] == [_key(r) for r in second]
