# tests/integration/test_contacts.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from maildb.contacts import build_contacts, classify_contact, classify_contacts
from maildb.ingest.orchestrator import run_pipeline
from maildb.maildb import MailDB

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
        conn.execute("DELETE FROM contact_merges")
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
            """SELECT c.human_probability, c.classification_signals, c.classified_at,
                      c.kind, c.kind_source
               FROM contacts c JOIN contact_addresses ca ON ca.contact_id = c.id
               WHERE ca.address = 'noreply@shop.com'"""
        ).fetchone()
        assert noreply[0] < 0.2
        assert "automated_pattern" in noreply[1]
        assert "one_way_bulk" in noreply[1]
        assert noreply[2] is not None
        assert noreply[3] == "unknown"
        assert noreply[4] == "heuristic"  # default; classifier never writes kind_source

        person = conn.execute(
            """SELECT c.human_probability, c.classification_signals, c.kind, c.kind_source
               FROM contacts c JOIN contact_addresses ca ON ca.contact_id = c.id
               WHERE ca.address = 'jane@p.com'"""
        ).fetchone()
        assert person[0] > 0.85
        assert "bidirectional" in person[1]
        assert "personal_name" in person[1]
        assert person[2] == "unknown"
        assert person[3] == "heuristic"

        unknown = conn.execute(
            """SELECT c.human_probability, c.classification_signals, c.kind, c.kind_source
               FROM contacts c JOIN contact_addresses ca ON ca.contact_id = c.id
               WHERE ca.address = 'stranger@z.com'"""
        ).fetchone()
        assert 0.4 <= unknown[0] <= 0.7
        assert unknown[2] == "unknown"
        assert unknown[3] == "heuristic"

        user = conn.execute(
            """SELECT c.human_probability
               FROM contacts c JOIN contact_addresses ca ON ca.contact_id = c.id
               WHERE ca.address = 'me@example.com'"""
        ).fetchone()
        assert user[0] is None

        # Invariant: classify never writes kind/kind_source — manual curation survives
        stranger_id = conn.execute(
            "SELECT contact_id FROM contact_addresses WHERE address = 'stranger@z.com'"
        ).fetchone()[0]
        conn.execute(
            """UPDATE contacts
                  SET kind = 'human', kind_source = 'manual'
                WHERE id = %(id)s""",
            {"id": stranger_id},
        )
        conn.commit()

    classify_contacts(test_pool)
    with test_pool.connection() as conn:
        preserved = conn.execute(
            "SELECT kind, kind_source FROM contacts WHERE id = %(id)s",
            {"id": stranger_id},
        ).fetchone()
        assert preserved[0] == "human"
        assert preserved[1] == "manual"


def test_classifier_thread_stats_signals(test_pool) -> None:  # type: ignore[no-untyped-def]
    """Deep conversational partner vs one-way bulk (depth-1) threads."""
    _clean_contacts(test_pool)
    import_id = _insert_import(test_pool, "me@example.com")

    deep_thread = "thread-deep-conv"
    emails: list[dict] = []
    # Sam and user share a deep thread (depth >= 3)
    for i, (sender, mid) in enumerate(
        [
            ("sam@p.com", "deep-sam-1"),
            ("me@example.com", "deep-me-1"),
            ("sam@p.com", "deep-sam-2"),
            ("me@example.com", "deep-me-2"),
            ("sam@p.com", "deep-sam-3"),
        ]
    ):
        e = _email(
            message_id=f"{mid}@p.com",
            sender_address=sender,
            sender_name="Sam Poole" if sender == "sam@p.com" else "Me",
            to=["me@example.com"] if sender == "sam@p.com" else ["sam@p.com"],
            date=datetime(2025, 5, 1, 10, i, tzinfo=UTC),
            import_id=import_id,
        )
        e["thread_id"] = deep_thread
        emails.append(e)

    # Bulk sender: 12 one-message threads (avg depth 1), never shared with user
    for i in range(12):
        e = _email(
            message_id=f"bulk-{i}@news.com",
            sender_address="bulk@news.com",
            sender_name="News Blast",
            to=["me@example.com"],
            date=datetime(2025, 6, 1 + (i % 28), tzinfo=UTC),
            import_id=import_id,
        )
        e["thread_id"] = f"bulk-thread-{i}"
        emails.append(e)

    _seed(test_pool, emails)
    build_contacts(test_pool)
    n = classify_contacts(test_pool)
    assert n >= 2

    with test_pool.connection() as conn:
        sam = conn.execute(
            """SELECT c.human_probability, c.classification_signals, c.kind, c.kind_source
               FROM contacts c JOIN contact_addresses ca ON ca.contact_id = c.id
               WHERE ca.address = 'sam@p.com'"""
        ).fetchone()
        bulk = conn.execute(
            """SELECT c.human_probability, c.classification_signals, c.kind, c.kind_source
               FROM contacts c JOIN contact_addresses ca ON ca.contact_id = c.id
               WHERE ca.address = 'bulk@news.com'"""
        ).fetchone()

        assert "replied_threads" in sam[1]
        assert "deep_threads" in sam[1]
        assert "shallow_threads" in bulk[1]
        assert sam[0] > bulk[0]
        assert sam[2] == "unknown"
        assert bulk[2] == "unknown"
        assert sam[3] == "heuristic"
        assert bulk[3] == "heuristic"


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


def test_contacts_search_needs_review_queue(test_pool) -> None:  # type: ignore[no-untyped-def]
    """needs_review returns only kind=unknown, ranked by volume x human_probability."""
    _clean_contacts(test_pool)
    import_id = _insert_import(test_pool)
    # high: 4 msgs * 0.9 = 3.6
    # mid:  2 msgs * 0.8 = 1.6
    # low:  10 msgs * 0.1 = 1.0
    emails: list[dict] = [
        _email(
            message_id=f"high-{i}@p.com",
            sender_address="high@p.com",
            sender_name="High Vol",
            to=["me@example.com"],
            date=datetime(2025, 1, 1 + i, tzinfo=UTC),
            import_id=import_id,
        )
        for i in range(4)
    ]
    emails.extend(
        _email(
            message_id=f"mid-{i}@p.com",
            sender_address="mid@p.com",
            sender_name="Mid Vol",
            to=["me@example.com"],
            date=datetime(2025, 2, 1 + i, tzinfo=UTC),
            import_id=import_id,
        )
        for i in range(2)
    )
    emails.extend(
        _email(
            message_id=f"low-{i}@bulk.com",
            sender_address="low@bulk.com",
            sender_name="Low Prob",
            to=["me@example.com"],
            date=datetime(2025, 3, 1 + (i % 28), tzinfo=UTC),
            import_id=import_id,
        )
        for i in range(10)
    )
    _seed(test_pool, emails)
    build_contacts(test_pool)

    with test_pool.connection() as conn:
        for addr, prob in (("high@p.com", 0.9), ("mid@p.com", 0.8), ("low@bulk.com", 0.1)):
            conn.execute(
                """UPDATE contacts c
                      SET human_probability = %(prob)s
                     FROM contact_addresses ca
                    WHERE ca.contact_id = c.id AND ca.address = %(addr)s""",
                {"prob": prob, "addr": addr},
            )
        conn.commit()

    db = MailDB._from_pool(test_pool)
    results, total = db.contacts_search(needs_review=True, include_total=True, limit=100)
    assert total is not None and total >= 3
    assert all(r["kind"] == "unknown" for r in results)

    addrs = [r["addresses"][0] for r in results if r["addresses"]]
    high_i = addrs.index("high@p.com")
    mid_i = addrs.index("mid@p.com")
    low_i = addrs.index("low@bulk.com")
    assert high_i < mid_i < low_i

    # Manually kind a contact → it leaves the review queue
    db.set_kind_bulk(kind="human", address="high@p.com")
    after, _ = db.contacts_search(needs_review=True, limit=100)
    after_addrs = {a for r in after for a in (r["addresses"] or [])}
    assert "high@p.com" not in after_addrs
    assert "mid@p.com" in after_addrs


def test_set_kind_bulk_by_domain_and_invariants(test_pool) -> None:  # type: ignore[no-untyped-def]
    """set_kind_bulk by domain updates matches with kind_source=manual; classify no-clobber."""
    _clean_contacts(test_pool)
    import_id = _insert_import(test_pool)
    emails = [
        _email(
            message_id="a1@corp.com",
            sender_address="alice@corp.com",
            sender_name="Alice",
            to=["me@example.com"],
            import_id=import_id,
        ),
        _email(
            message_id="b1@corp.com",
            sender_address="bob@corp.com",
            sender_name="Bob",
            to=["me@example.com"],
            import_id=import_id,
        ),
        _email(
            message_id="o1@other.com",
            sender_address="other@other.com",
            sender_name="Other",
            to=["me@example.com"],
            import_id=import_id,
        ),
    ]
    _seed(test_pool, emails)
    build_contacts(test_pool)
    db = MailDB._from_pool(test_pool)

    dry = db.set_kind_bulk(kind="organization", domain="corp.com", dry_run=True)
    assert dry["matched"] == 2
    assert dry["updated"] == 0
    assert len(dry["sample"]) == 2
    with test_pool.connection() as conn:
        still_unknown = conn.execute(
            "SELECT count(*) FROM contacts WHERE kind = 'unknown'"
        ).fetchone()[0]
    assert still_unknown >= 2

    result = db.set_kind_bulk(kind="organization", domain="corp.com")
    assert result["matched"] == 2
    assert result["updated"] == 2

    with test_pool.connection() as conn:
        rows = conn.execute(
            """SELECT ca.address, c.kind, c.kind_source
                 FROM contact_addresses ca
                 JOIN contacts c ON c.id = ca.contact_id
                WHERE ca.address IN ('alice@corp.com', 'bob@corp.com', 'other@other.com')
                ORDER BY ca.address"""
        ).fetchall()
    by_addr = {r[0]: (r[1], r[2]) for r in rows}
    assert by_addr["alice@corp.com"] == ("organization", "manual")
    assert by_addr["bob@corp.com"] == ("organization", "manual")
    assert by_addr["other@other.com"] == ("unknown", "heuristic")

    classify_contacts(test_pool)
    with test_pool.connection() as conn:
        preserved = conn.execute(
            """SELECT c.kind, c.kind_source
                 FROM contacts c JOIN contact_addresses ca ON ca.contact_id = c.id
                WHERE ca.address = 'alice@corp.com'"""
        ).fetchone()
    assert preserved[0] == "organization"
    assert preserved[1] == "manual"


def test_set_kind_bulk_validation(test_pool) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="Invalid kind"):
        db.set_kind_bulk(kind="not-a-kind", domain="x.com")
    with pytest.raises(ValueError, match="Exactly one"):
        db.set_kind_bulk(kind="human")
    with pytest.raises(ValueError, match="Exactly one"):
        db.set_kind_bulk(kind="human", domain="x.com", address="a@x.com")


def _two_contacts_seed(pool):  # type: ignore[no-untyped-def]
    """Seed two singleton contacts with distinct addresses; return (db, source_id, target_id)."""
    _clean_contacts(pool)
    import_id = _insert_import(pool)
    emails = [
        _email(
            message_id="merge-src-1@x.com",
            sender_address="sam.a@x.com",
            sender_name="Sam Poole",
            to=["me@example.com"],
            date=datetime(2025, 1, 1, tzinfo=UTC),
            import_id=import_id,
        ),
        _email(
            message_id="merge-src-2@x.com",
            sender_address="sam.a@x.com",
            sender_name="Sam Poole",
            to=["me@example.com"],
            date=datetime(2025, 1, 2, tzinfo=UTC),
            import_id=import_id,
        ),
        _email(
            message_id="merge-tgt-1@x.com",
            sender_address="sam.b@x.com",
            sender_name="Sam.Poole",
            to=["me@example.com"],
            date=datetime(2025, 1, 3, tzinfo=UTC),
            import_id=import_id,
        ),
        _email(
            message_id="merge-tgt-2@x.com",
            sender_address="sam.b@x.com",
            sender_name="Sam.Poole",
            to=["me@example.com"],
            date=datetime(2025, 1, 4, tzinfo=UTC),
            import_id=import_id,
        ),
    ]
    _seed(pool, emails)
    build_contacts(pool)
    db = MailDB._from_pool(pool)
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT ca.address, ca.contact_id FROM contact_addresses ca
                WHERE ca.address IN ('sam.a@x.com', 'sam.b@x.com')
                ORDER BY ca.address"""
        ).fetchall()
    by_addr = {r[0]: r[1] for r in rows}
    source_id = by_addr["sam.a@x.com"]
    target_id = by_addr["sam.b@x.com"]
    with pool.connection() as conn:
        conn.execute(
            """UPDATE contacts SET tags = ARRAY['src-tag'], notes = 'source notes'
                WHERE id = %(id)s""",
            {"id": source_id},
        )
        conn.execute(
            """UPDATE contacts SET tags = ARRAY['tgt-tag'], notes = 'target notes',
                       display_name = 'Target Sam'
                WHERE id = %(id)s""",
            {"id": target_id},
        )
        conn.commit()
    return db, source_id, target_id


def test_merge_contacts_addresses_tags_snapshot_reclassify(test_pool) -> None:  # type: ignore[no-untyped-def]
    db, source_id, target_id = _two_contacts_seed(test_pool)

    with test_pool.connection() as conn:
        before_cls = conn.execute(
            "SELECT classified_at FROM contacts WHERE id = %(id)s",
            {"id": target_id},
        ).fetchone()[0]

    result = db.merge_contacts(source_id=source_id, target_id=target_id)
    assert result["id"] == str(target_id)
    assert "merge_id" in result
    assert set(result["addresses"]) == {"sam.a@x.com", "sam.b@x.com"}
    assert set(result["tags"]) == {"tgt-tag", "src-tag"}
    assert result["notes"] == "target notes\n---\nsource notes"
    assert result["display_name"] == "Target Sam"

    with test_pool.connection() as conn:
        src_gone = conn.execute(
            "SELECT 1 FROM contacts WHERE id = %(id)s", {"id": source_id}
        ).fetchone()
        assert src_gone is None

        merge = conn.execute(
            """SELECT source_id, target_id, snapshot
                 FROM contact_merges WHERE id = %(id)s""",
            {"id": result["merge_id"]},
        ).fetchone()
        assert merge is not None
        assert str(merge[0]) == str(source_id)
        assert str(merge[1]) == str(target_id)
        snap = merge[2]
        assert str(snap["contact"]["id"]) == str(source_id)
        assert any(a["address"] == "sam.a@x.com" for a in snap["addresses"])

        after_cls = conn.execute(
            "SELECT classified_at FROM contacts WHERE id = %(id)s",
            {"id": target_id},
        ).fetchone()[0]
        assert after_cls is not None
        if before_cls is not None:
            assert after_cls >= before_cls


def test_merge_contacts_unknown_target_adopts_source_kind(test_pool) -> None:  # type: ignore[no-untyped-def]
    db, source_id, target_id = _two_contacts_seed(test_pool)
    with test_pool.connection() as conn:
        conn.execute(
            """UPDATE contacts SET kind = 'human', kind_source = 'manual'
                WHERE id = %(id)s""",
            {"id": source_id},
        )
        conn.execute(
            """UPDATE contacts SET kind = 'unknown', kind_source = 'heuristic'
                WHERE id = %(id)s""",
            {"id": target_id},
        )
        conn.commit()

    result = db.merge_contacts(source_id=source_id, target_id=target_id)
    assert result["kind"] == "human"
    assert result["kind_source"] == "manual"


def test_merge_contacts_validation(test_pool) -> None:  # type: ignore[no-untyped-def]
    db, source_id, target_id = _two_contacts_seed(test_pool)
    with pytest.raises(ValueError, match="must be different"):
        db.merge_contacts(source_id=source_id, target_id=source_id)
    with pytest.raises(ValueError, match="does not exist"):
        db.merge_contacts(source_id=uuid4(), target_id=target_id)
    with pytest.raises(ValueError, match="does not exist"):
        db.merge_contacts(source_id=source_id, target_id=uuid4())


def test_merge_survives_build_contacts_refresh(test_pool) -> None:  # type: ignore[no-untyped-def]
    db, source_id, target_id = _two_contacts_seed(test_pool)
    db.merge_contacts(source_id=source_id, target_id=target_id)

    build_contacts(test_pool)

    with test_pool.connection() as conn:
        src = conn.execute(
            "SELECT 1 FROM contacts WHERE id = %(id)s", {"id": source_id}
        ).fetchone()
        assert src is None
        rows = conn.execute(
            """SELECT address, contact_id FROM contact_addresses
                WHERE address IN ('sam.a@x.com', 'sam.b@x.com')"""
        ).fetchall()
    assert len(rows) == 2
    assert all(str(r[1]) == str(target_id) for r in rows)


def test_unmerge_contacts_restores_source(test_pool) -> None:  # type: ignore[no-untyped-def]
    db, source_id, target_id = _two_contacts_seed(test_pool)
    merged = db.merge_contacts(source_id=source_id, target_id=target_id)
    merge_id = merged["merge_id"]

    result = db.unmerge_contacts(merge_id=merge_id)
    assert result["source"]["id"] == str(source_id)
    assert "sam.a@x.com" in result["source"]["addresses"]
    assert result["target"]["id"] == str(target_id)
    assert "sam.b@x.com" in result["target"]["addresses"]
    assert "sam.a@x.com" not in result["target"]["addresses"]

    with test_pool.connection() as conn:
        gone = conn.execute(
            "SELECT 1 FROM contact_merges WHERE id = %(id)s", {"id": merge_id}
        ).fetchone()
        assert gone is None

    with pytest.raises(ValueError, match="does not exist"):
        db.unmerge_contacts(merge_id=merge_id)


def test_merge_candidates_reports_shared_name(test_pool) -> None:  # type: ignore[no-untyped-def]
    db, _source_id, _target_id = _two_contacts_seed(test_pool)

    # Low-volume contact with same name — should be excluded
    import_id = _insert_import(test_pool)
    _seed(
        test_pool,
        [
            _email(
                message_id="low-vol@x.com",
                sender_address="once@x.com",
                sender_name="Sam Poole",
                to=["me@example.com"],
                import_id=import_id,
            ),
        ],
    )
    build_contacts(test_pool)

    # User-only contact with high volume — should be excluded
    with test_pool.connection() as conn:
        user_cid = conn.execute(
            "SELECT contact_id FROM contact_addresses WHERE address = 'me@example.com'"
        ).fetchone()
        if user_cid is None:
            # ensure user address exists as user-only
            pass
        else:
            conn.execute(
                """UPDATE contact_addresses
                      SET messages_from = 10, messages_to = 10, is_user = TRUE,
                          name_variants = ARRAY['Sam Poole']
                    WHERE address = 'me@example.com'"""
            )
            conn.commit()

    pairs = db.merge_candidates(limit=50)
    assert pairs
    norms = {p["norm_name"] for p in pairs}
    assert "sampoole" in norms
    pair = next(p for p in pairs if p["norm_name"] == "sampoole")
    ids = {pair["a"]["contact_id"], pair["b"]["contact_id"]}
    # The two high-volume Sam contacts
    with test_pool.connection() as conn:
        sam_ids = {
            str(r[0])
            for r in conn.execute(
                """SELECT ca.contact_id FROM contact_addresses ca
                    WHERE ca.address IN ('sam.a@x.com', 'sam.b@x.com')"""
            ).fetchall()
        }
    assert ids == sam_ids
    # Low-volume once@x.com must not appear
    all_addrs = {pair["a"]["primary_address"], pair["b"]["primary_address"]}
    for p in pairs:
        all_addrs.add(p["a"]["primary_address"])
        all_addrs.add(p["b"]["primary_address"])
    assert "once@x.com" not in all_addrs
