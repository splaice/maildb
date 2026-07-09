# tests/integration/test_contacts.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from maildb.contacts import build_contacts, classify_contact, classify_contacts
from maildb.ingest.orchestrator import run_pipeline

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures"

INSERT_EMAIL = """
INSERT INTO emails (
    message_id, thread_id, subject, sender_name, sender_address, sender_domain,
    recipients, date, body_text, body_html, has_attachment, attachments,
    labels, in_reply_to, "references", import_id, source_account
) VALUES (
    %(message_id)s, %(thread_id)s, %(subject)s, %(sender_name)s, %(sender_address)s,
    %(sender_domain)s, %(recipients)s, %(date)s, %(body_text)s, NULL,
    FALSE, '[]'::jsonb, %(labels)s, NULL, %(references)s, %(import_id)s, %(source_account)s
)
"""


def _clean_contacts(pool) -> None:  # type: ignore[no-untyped-def]
    with pool.connection() as conn:
        conn.execute("DELETE FROM contact_addresses")
        conn.execute("DELETE FROM contacts")
        conn.commit()


def _insert_import(pool, source_account: str = "me@example.com"):  # type: ignore[no-untyped-def]
    import_id = uuid4()
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO imports (id, source_account, source_file, status, completed_at)
               VALUES (%(id)s, %(acct)s, 'seed', 'completed', now())""",
            {"id": import_id, "acct": source_account},
        )
        conn.commit()
    return import_id


def _email(  # type: ignore[no-untyped-def]
    *,
    message_id: str,
    sender_address: str,
    sender_name: str | None = None,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    date: datetime | None = None,
    import_id=None,
    source_account: str = "me@example.com",
    subject: str = "Test",
) -> dict:
    domain = sender_address.split("@")[1] if "@" in sender_address else "example.com"
    return {
        "message_id": message_id,
        "thread_id": message_id,
        "subject": subject,
        "sender_name": sender_name,
        "sender_address": sender_address,
        "sender_domain": domain,
        "recipients": json.dumps({"to": to or [], "cc": cc or [], "bcc": bcc or []}),
        "date": date or datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
        "body_text": "hello",
        "labels": ["INBOX"],
        "references": [],
        "import_id": import_id,
        "source_account": source_account,
    }


def _snapshot_contact_addresses(pool) -> dict[str, tuple]:  # type: ignore[no-untyped-def]
    """Stats snapshot keyed by address (excludes contact_id UUIDs)."""
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT address, name_variants, is_user, first_seen, last_seen,
                      messages_from, messages_to
                 FROM contact_addresses
                ORDER BY address"""
        ).fetchall()
    return {r[0]: (tuple(r[1] or []), r[2], r[3], r[4], r[5], r[6]) for r in rows}


def _seed(pool, emails: list[dict]) -> None:  # type: ignore[no-untyped-def]
    with pool.connection() as conn:
        for e in emails:
            conn.execute(INSERT_EMAIL, e)
        conn.commit()


def test_build_contacts_aggregates_addresses_and_counts(test_pool) -> None:  # type: ignore[no-untyped-def]
    _clean_contacts(test_pool)
    import_id = _insert_import(test_pool, "me@example.com")
    _seed(
        test_pool,
        [
            _email(
                message_id="c-alice-1@x.com",
                sender_address="alice@x.com",
                sender_name="Alice A",
                to=["me@example.com"],
                date=datetime(2025, 1, 10, 9, 0, tzinfo=UTC),
                import_id=import_id,
            ),
            _email(
                message_id="c-alice-2@x.com",
                sender_address="alice@x.com",
                sender_name="Alice",
                to=["me@example.com"],
                date=datetime(2025, 1, 20, 11, 0, tzinfo=UTC),
                import_id=import_id,
            ),
            _email(
                message_id="c-me-to-bob@x.com",
                sender_address="me@example.com",
                sender_name="Me",
                to=["bob@y.com"],
                date=datetime(2025, 1, 15, 12, 0, tzinfo=UTC),
                import_id=import_id,
            ),
        ],
    )

    result = build_contacts(test_pool)
    assert result["addresses"] >= 3
    assert result["contacts_created"] >= 3

    with test_pool.connection() as conn:
        alice = conn.execute(
            "SELECT name_variants, messages_from, messages_to, first_seen, last_seen, is_user "
            "FROM contact_addresses WHERE address = 'alice@x.com'"
        ).fetchone()
        assert alice is not None
        name_variants, messages_from, messages_to, first_seen, last_seen, is_user = alice
        assert set(name_variants) == {"Alice A", "Alice"}
        assert messages_from == 2
        assert messages_to == 0
        assert first_seen == datetime(2025, 1, 10, 9, 0, tzinfo=UTC)
        assert last_seen == datetime(2025, 1, 20, 11, 0, tzinfo=UTC)
        assert is_user is False

        bob = conn.execute(
            "SELECT messages_from, messages_to, is_user FROM contact_addresses "
            "WHERE address = 'bob@y.com'"
        ).fetchone()
        assert bob is not None
        assert bob[0] == 0  # messages_from
        assert bob[1] == 1  # messages_to (from user)
        assert bob[2] is False

        me = conn.execute(
            "SELECT is_user, messages_from FROM contact_addresses WHERE address = 'me@example.com'"
        ).fetchone()
        assert me is not None
        assert me[0] is True
        assert me[1] == 1

        # Singleton contacts: one contact per address
        cur = conn.execute(
            """SELECT ca.address, c.id, c.kind, c.display_name
               FROM contact_addresses ca JOIN contacts c ON c.id = ca.contact_id
               WHERE ca.address IN ('alice@x.com', 'bob@y.com', 'me@example.com')"""
        )
        rows = cur.fetchall()
        assert len(rows) == 3
        contact_ids = {r[1] for r in rows}
        assert len(contact_ids) == 3
        alice_display = next(r[3] for r in rows if r[0] == "alice@x.com")
        assert alice_display in ("Alice A", "Alice")


def test_build_contacts_preserves_manual_curation(test_pool) -> None:  # type: ignore[no-untyped-def]
    _clean_contacts(test_pool)
    import_id = _insert_import(test_pool)
    _seed(
        test_pool,
        [
            _email(
                message_id="curate-1@x.com",
                sender_address="alice@x.com",
                sender_name="Alice",
                to=["me@example.com"],
                date=datetime(2025, 1, 10, tzinfo=UTC),
                import_id=import_id,
            ),
        ],
    )
    build_contacts(test_pool)

    with test_pool.connection() as conn:
        contact_id = conn.execute(
            "SELECT contact_id FROM contact_addresses WHERE address = 'alice@x.com'"
        ).fetchone()[0]
        conn.execute(
            """UPDATE contacts
               SET kind = 'human', kind_source = 'manual',
                   tags = ARRAY['vip'], notes = 'friend',
                   metadata = '{"curated": true}'::jsonb,
                   display_name = 'Alice Manual'
               WHERE id = %(id)s""",
            {"id": contact_id},
        )
        conn.commit()

    # Add another message and refresh
    _seed(
        test_pool,
        [
            _email(
                message_id="curate-2@x.com",
                sender_address="alice@x.com",
                sender_name="Alice New",
                to=["me@example.com"],
                date=datetime(2025, 2, 1, tzinfo=UTC),
                import_id=import_id,
            ),
        ],
    )
    build_contacts(test_pool)

    with test_pool.connection() as conn:
        row = conn.execute(
            """SELECT c.kind, c.kind_source, c.tags, c.notes, c.metadata, c.display_name,
                      ca.messages_from, ca.name_variants
               FROM contacts c
               JOIN contact_addresses ca ON ca.contact_id = c.id
               WHERE ca.address = 'alice@x.com'"""
        ).fetchone()
        kind, kind_source, tags, notes, metadata, display_name, messages_from, name_variants = row
        assert kind == "human"
        assert kind_source == "manual"
        assert tags == ["vip"]
        assert notes == "friend"
        assert metadata == {"curated": True}
        assert display_name == "Alice Manual"
        assert messages_from == 2
        assert set(name_variants) == {"Alice", "Alice New"}


def test_classifier_signal_ordering_and_never_sets_kind(test_pool) -> None:  # type: ignore[no-untyped-def]
    _clean_contacts(test_pool)
    import_id = _insert_import(test_pool, "me@example.com")

    # noreply bulk: 25 inbound, 0 outbound
    emails = [
        _email(
            message_id=f"noreply-{i}@shop.com",
            sender_address="noreply@shop.com",
            sender_name="Shop",
            to=["me@example.com"],
            date=datetime(2025, 1, 1 + (i % 28), tzinfo=UTC),
            import_id=import_id,
        )
        for i in range(25)
    ]
    # Bidirectional personal-named contact
    emails.extend(
        [
            _email(
                message_id="person-from@p.com",
                sender_address="jane@p.com",
                sender_name="Jane Smith",
                to=["me@example.com"],
                date=datetime(2025, 3, 1, tzinfo=UTC),
                import_id=import_id,
            ),
            _email(
                message_id="person-to@p.com",
                sender_address="me@example.com",
                sender_name="Me",
                to=["jane@p.com"],
                date=datetime(2025, 3, 2, tzinfo=UTC),
                import_id=import_id,
            ),
            # Unknown one-message sender
            _email(
                message_id="unknown-1@z.com",
                sender_address="stranger@z.com",
                sender_name="x",
                to=["me@example.com"],
                date=datetime(2025, 4, 1, tzinfo=UTC),
                import_id=import_id,
            ),
        ]
    )
    _seed(test_pool, emails)
    build_contacts(test_pool)
    n = classify_contacts(test_pool)
    assert n >= 3

    with test_pool.connection() as conn:
        noreply = conn.execute(
            """SELECT c.human_probability, c.classification_signals, c.classified_at, c.kind
               FROM contacts c JOIN contact_addresses ca ON ca.contact_id = c.id
               WHERE ca.address = 'noreply@shop.com'"""
        ).fetchone()
        assert noreply[0] < 0.2
        assert "automated_pattern" in noreply[1]
        assert "one_way_bulk" in noreply[1]
        assert noreply[2] is not None
        assert noreply[3] == "unknown"

        person = conn.execute(
            """SELECT c.human_probability, c.classification_signals, c.kind
               FROM contacts c JOIN contact_addresses ca ON ca.contact_id = c.id
               WHERE ca.address = 'jane@p.com'"""
        ).fetchone()
        assert person[0] > 0.85
        assert "bidirectional" in person[1]
        assert "personal_name" in person[1]
        assert person[2] == "unknown"

        unknown = conn.execute(
            """SELECT c.human_probability, c.classification_signals, c.kind
               FROM contacts c JOIN contact_addresses ca ON ca.contact_id = c.id
               WHERE ca.address = 'stranger@z.com'"""
        ).fetchone()
        assert 0.4 <= unknown[0] <= 0.7
        assert unknown[2] == "unknown"

        user = conn.execute(
            """SELECT c.human_probability
               FROM contacts c JOIN contact_addresses ca ON ca.contact_id = c.id
               WHERE ca.address = 'me@example.com'"""
        ).fetchone()
        assert user[0] is None


def test_classify_contact_matches_batch(test_pool) -> None:  # type: ignore[no-untyped-def]
    _clean_contacts(test_pool)
    import_id = _insert_import(test_pool)
    _seed(
        test_pool,
        [
            _email(
                message_id="single-1@x.com",
                sender_address="alice@x.com",
                sender_name="Alice Smith",
                to=["me@example.com"],
                import_id=import_id,
            ),
            _email(
                message_id="single-2@x.com",
                sender_address="me@example.com",
                sender_name="Me",
                to=["alice@x.com"],
                import_id=import_id,
            ),
        ],
    )
    build_contacts(test_pool)
    with test_pool.connection() as conn:
        contact_id = conn.execute(
            "SELECT contact_id FROM contact_addresses WHERE address = 'alice@x.com'"
        ).fetchone()[0]

    classify_contacts(test_pool, contact_ids=[contact_id])
    with test_pool.connection() as conn:
        batch_prob = conn.execute(
            "SELECT human_probability FROM contacts WHERE id = %(id)s",
            {"id": contact_id},
        ).fetchone()[0]
        # Clear so single path rewrites
        conn.execute(
            """UPDATE contacts
               SET human_probability = NULL, classification_signals = NULL, classified_at = NULL
               WHERE id = %(id)s""",
            {"id": contact_id},
        )
        conn.commit()

    single_prob = classify_contact(test_pool, contact_id)
    assert single_prob == pytest.approx(batch_prob)
    with test_pool.connection() as conn:
        row = conn.execute(
            "SELECT human_probability FROM contacts WHERE id = %(id)s",
            {"id": contact_id},
        ).fetchone()
        assert row[0] == pytest.approx(batch_prob)


def test_build_and_classify_idempotent(test_pool) -> None:  # type: ignore[no-untyped-def]
    _clean_contacts(test_pool)
    import_id = _insert_import(test_pool)
    _seed(
        test_pool,
        [
            _email(
                message_id="idemp-1@x.com",
                sender_address="alice@x.com",
                sender_name="Alice Smith",
                to=["me@example.com"],
                import_id=import_id,
            ),
            _email(
                message_id="idemp-2@x.com",
                sender_address="me@example.com",
                sender_name="Me",
                to=["alice@x.com"],
                import_id=import_id,
            ),
        ],
    )
    r1 = build_contacts(test_pool)
    c1 = classify_contacts(test_pool)
    with test_pool.connection() as conn:
        addr_count_1 = conn.execute("SELECT count(*) FROM contact_addresses").fetchone()[0]
        contact_count_1 = conn.execute("SELECT count(*) FROM contacts").fetchone()[0]
        probs_1 = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT ca.address, c.human_probability "
                "FROM contact_addresses ca JOIN contacts c ON c.id = ca.contact_id"
            ).fetchall()
        }

    r2 = build_contacts(test_pool)
    c2 = classify_contacts(test_pool)
    with test_pool.connection() as conn:
        addr_count_2 = conn.execute("SELECT count(*) FROM contact_addresses").fetchone()[0]
        contact_count_2 = conn.execute("SELECT count(*) FROM contacts").fetchone()[0]
        probs_2 = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT ca.address, c.human_probability "
                "FROM contact_addresses ca JOIN contacts c ON c.id = ca.contact_id"
            ).fetchall()
        }

    assert addr_count_1 == addr_count_2
    assert contact_count_1 == contact_count_2
    assert r2["contacts_created"] == 0
    assert c1 == c2
    assert probs_1 == probs_2
    assert r1["addresses"] == r2["addresses"]


def test_full_and_incremental_build_equivalence(test_pool) -> None:  # type: ignore[no-untyped-def]
    """Full one-pass build must match per-import incremental stats exactly.

    Seeds multi-arm (same address in to AND cc of one user-sent email counts
    once), sender-only, recipient-only, and mixed addresses across imports.
    """
    _clean_contacts(test_pool)
    import_a = _insert_import(test_pool, "me@example.com")
    import_b = _insert_import(test_pool, "me@example.com")

    _seed(
        test_pool,
        [
            # Multi-arm: multi@z.com in both to and cc of a single user-sent email
            # → messages_to must be 1 (not 2).
            _email(
                message_id="eq-multi-arm@x.com",
                sender_address="me@example.com",
                sender_name="Me",
                to=["multi@z.com"],
                cc=["multi@z.com", "other-cc@z.com"],
                date=datetime(2025, 1, 5, 10, 0, tzinfo=UTC),
                import_id=import_a,
            ),
            # Sender-only: never a recipient of a user-sent email
            _email(
                message_id="eq-sender-only@x.com",
                sender_address="senderonly@x.com",
                sender_name="Sender Only",
                to=["me@example.com"],
                date=datetime(2025, 1, 10, 9, 0, tzinfo=UTC),
                import_id=import_a,
            ),
            # Recipient-only (import B): user wrote to them; they never sent
            _email(
                message_id="eq-recip-only@x.com",
                sender_address="me@example.com",
                sender_name="Me",
                to=["reciponly@y.com"],
                date=datetime(2025, 1, 12, 11, 0, tzinfo=UTC),
                import_id=import_b,
            ),
            # Mixed: alice sends and user replies
            _email(
                message_id="eq-alice-from@x.com",
                sender_address="alice@x.com",
                sender_name="Alice A",
                to=["me@example.com"],
                date=datetime(2025, 1, 15, 8, 0, tzinfo=UTC),
                import_id=import_b,
            ),
            _email(
                message_id="eq-alice-to@x.com",
                sender_address="me@example.com",
                sender_name="Me",
                to=["alice@x.com"],
                bcc=["bcc-only@z.com"],
                date=datetime(2025, 1, 16, 14, 0, tzinfo=UTC),
                import_id=import_b,
            ),
            # Second name variant for alice (name_variants / top_name)
            _email(
                message_id="eq-alice-from-2@x.com",
                sender_address="alice@x.com",
                sender_name="Alice",
                to=["me@example.com"],
                date=datetime(2025, 1, 20, 8, 0, tzinfo=UTC),
                import_id=import_a,
            ),
        ],
    )

    build_contacts(test_pool)  # full path
    full_snapshot = _snapshot_contact_addresses(test_pool)

    # Prove multi-arm dedup: one user-sent email with address in to+cc → 1
    assert full_snapshot["multi@z.com"][5] == 1  # messages_to
    assert full_snapshot["senderonly@x.com"][4] == 1  # messages_from
    assert full_snapshot["senderonly@x.com"][5] == 0  # messages_to
    assert full_snapshot["reciponly@y.com"][4] == 0  # messages_from
    assert full_snapshot["reciponly@y.com"][5] == 1  # messages_to
    assert full_snapshot["alice@x.com"][4] == 2  # messages_from
    assert full_snapshot["alice@x.com"][5] == 1  # messages_to

    with test_pool.connection() as conn:
        conn.execute("TRUNCATE contact_addresses, contacts")
        conn.commit()

    # Per-import incremental builds over the same data
    for iid in (import_a, import_b):
        build_contacts(test_pool, import_id=iid)

    incremental_snapshot = _snapshot_contact_addresses(test_pool)
    assert incremental_snapshot == full_snapshot


def test_orchestrator_builds_and_classifies_contacts(test_pool, test_settings, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _clean_contacts(test_pool)
    run_pipeline(
        mbox_path=FIXTURES / "sample.mbox",
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,
        parse_workers=2,
        skip_embed=True,
        source_account="test@example.com",
    )
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM contact_addresses")
        assert cur.fetchone()[0] > 0
        # Fixture senders should be present
        for addr in (
            "alice@example.com",
            "bob@example.com",
            "carol@example.com",
            "dave@example.com",
            "noreply@notifications.example.com",
        ):
            row = conn.execute(
                """SELECT c.human_probability, c.kind
                   FROM contact_addresses ca
                   JOIN contacts c ON c.id = ca.contact_id
                   WHERE ca.address = %(addr)s""",
                {"addr": addr},
            ).fetchone()
            assert row is not None, f"missing contact for {addr}"
            assert row[0] is not None, f"missing probability for {addr}"
            assert row[1] == "unknown"

        user = conn.execute(
            """SELECT c.human_probability
               FROM contact_addresses ca
               JOIN contacts c ON c.id = ca.contact_id
               WHERE ca.address = 'test@example.com'"""
        ).fetchone()
        # User identity contact may or may not exist depending on whether the
        # address appears in the mbox; if it does, probability stays NULL.
        if user is not None:
            assert user[0] is None
