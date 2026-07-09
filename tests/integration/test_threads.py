from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from maildb.config import Settings
from maildb.ingest.threads import repair_thread_ids
from maildb.maildb import MailDB

pytestmark = pytest.mark.integration


INSERT_EMAIL_SQL = """
INSERT INTO emails (
    message_id, thread_id, subject, sender_name, sender_address, sender_domain,
    recipients, date, body_text, body_html, has_attachment, attachments,
    labels, in_reply_to, "references"
) VALUES (
    %(message_id)s, %(thread_id)s, %(subject)s, %(sender_name)s, %(sender_address)s,
    %(sender_domain)s, %(recipients)s, %(date)s, %(body_text)s, %(body_html)s,
    %(has_attachment)s, %(attachments)s, %(labels)s, %(in_reply_to)s, %(references)s
)
"""


def _row(
    message_id: str,
    *,
    thread_id: str | None = None,
    sender: str = "sender@example.com",
    recipients: list[str] | None = None,
    date: datetime | None = None,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
) -> dict[str, object]:
    return {
        "message_id": message_id,
        "thread_id": thread_id or message_id,
        "subject": "Thread repair",
        "sender_name": sender.split("@", maxsplit=1)[0],
        "sender_address": sender,
        "sender_domain": sender.split("@", maxsplit=1)[1],
        "recipients": json.dumps(
            {"to": recipients or ["recipient@example.com"], "cc": [], "bcc": []}
        ),
        "date": date,
        "body_text": "body",
        "body_html": None,
        "has_attachment": False,
        "attachments": json.dumps([]),
        "labels": [],
        "in_reply_to": in_reply_to,
        "references": references,
    }


def _seed(test_pool, rows: list[dict[str, object]]) -> None:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        for row in rows:
            conn.execute(INSERT_EMAIL_SQL, row)
        conn.commit()


def _thread_ids(test_pool) -> dict[str, str]:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT message_id, thread_id FROM emails ORDER BY message_id")
        return dict(cur.fetchall())


def test_in_reply_to_only_fragmentation_heals(test_pool) -> None:  # type: ignore[no-untyped-def]
    _seed(
        test_pool,
        [
            _row(
                "thread-a@example.com",
                date=datetime(2025, 1, 1, 9, 0, tzinfo=UTC),
            ),
            _row(
                "thread-b@example.com",
                thread_id="thread-a@example.com",
                date=datetime(2025, 1, 1, 10, 0, tzinfo=UTC),
                in_reply_to="thread-a@example.com",
                references=["thread-a@example.com"],
            ),
            _row(
                "thread-c@example.com",
                thread_id="thread-b@example.com",
                date=datetime(2025, 1, 1, 11, 0, tzinfo=UTC),
                in_reply_to="thread-b@example.com",
            ),
        ],
    )

    updated = repair_thread_ids(test_pool)

    assert updated == 1
    assert set(_thread_ids(test_pool).values()) == {"thread-a@example.com"}


def test_absent_intermediate_connects_present_messages(test_pool) -> None:  # type: ignore[no-untyped-def]
    _seed(
        test_pool,
        [
            _row(
                "absent-x@example.com",
                thread_id="missing-middle@example.com",
                date=datetime(2025, 2, 1, 9, 0, tzinfo=UTC),
                in_reply_to="missing-middle@example.com",
            ),
            _row(
                "absent-y@example.com",
                thread_id="missing-middle@example.com",
                date=datetime(2025, 2, 1, 10, 0, tzinfo=UTC),
                references=["missing-middle@example.com"],
            ),
        ],
    )

    updated = repair_thread_ids(test_pool)

    assert updated == 2
    assert _thread_ids(test_pool) == {
        "absent-x@example.com": "absent-x@example.com",
        "absent-y@example.com": "absent-x@example.com",
    }


def test_repair_thread_ids_is_idempotent(test_pool) -> None:  # type: ignore[no-untyped-def]
    _seed(
        test_pool,
        [
            _row("idem-a@example.com", date=datetime(2025, 3, 1, 9, 0, tzinfo=UTC)),
            _row(
                "idem-b@example.com",
                thread_id="idem-a@example.com",
                date=datetime(2025, 3, 1, 10, 0, tzinfo=UTC),
                references=["idem-a@example.com"],
            ),
            _row(
                "idem-c@example.com",
                thread_id="idem-b@example.com",
                date=datetime(2025, 3, 1, 11, 0, tzinfo=UTC),
                in_reply_to="idem-b@example.com",
            ),
        ],
    )

    assert repair_thread_ids(test_pool) == 1
    repaired = _thread_ids(test_pool)
    assert repair_thread_ids(test_pool) == 0
    assert _thread_ids(test_pool) == repaired


def test_date_ties_choose_lexicographically_smallest_message_id(test_pool) -> None:  # type: ignore[no-untyped-def]
    same_date = datetime(2025, 4, 1, 9, 0, tzinfo=UTC)
    _seed(
        test_pool,
        [
            _row("tie-b@example.com", date=same_date),
            _row(
                "tie-a@example.com",
                thread_id="tie-b@example.com",
                date=same_date,
                in_reply_to="tie-b@example.com",
            ),
        ],
    )

    assert repair_thread_ids(test_pool) == 2
    assert _thread_ids(test_pool) == {
        "tie-a@example.com": "tie-a@example.com",
        "tie-b@example.com": "tie-a@example.com",
    }
    assert repair_thread_ids(test_pool) == 0
    assert _thread_ids(test_pool) == {
        "tie-a@example.com": "tie-a@example.com",
        "tie-b@example.com": "tie-a@example.com",
    }


def test_independent_threads_untouched_and_not_counted(test_pool) -> None:  # type: ignore[no-untyped-def]
    _seed(
        test_pool,
        [
            _row("solo@example.com", date=datetime(2025, 5, 1, 9, 0, tzinfo=UTC)),
            _row("ind-a@example.com", date=datetime(2025, 5, 2, 9, 0, tzinfo=UTC)),
            _row(
                "ind-b@example.com",
                thread_id="ind-a@example.com",
                date=datetime(2025, 5, 2, 10, 0, tzinfo=UTC),
                references=["ind-a@example.com"],
            ),
            _row(
                "ind-c@example.com",
                thread_id="ind-b@example.com",
                date=datetime(2025, 5, 2, 11, 0, tzinfo=UTC),
                in_reply_to="ind-b@example.com",
            ),
        ],
    )

    assert repair_thread_ids(test_pool) == 1
    assert _thread_ids(test_pool)["solo@example.com"] == "solo@example.com"


def test_unreplied_false_positive_disappears_after_thread_repair(test_pool) -> None:  # type: ignore[no-untyped-def]
    me = "alice@example.com"
    frank = "frank@example.com"
    _seed(
        test_pool,
        [
            _row(
                "false-f@example.com",
                sender=frank,
                recipients=[me],
                date=datetime(2025, 6, 1, 9, 0, tzinfo=UTC),
            ),
            _row(
                "false-r@example.com",
                thread_id="false-f@example.com",
                sender=me,
                recipients=[frank],
                date=datetime(2025, 6, 1, 10, 0, tzinfo=UTC),
                in_reply_to="false-f@example.com",
                references=["false-f@example.com"],
            ),
            _row(
                "false-g@example.com",
                thread_id="false-r@example.com",
                sender=frank,
                recipients=[me],
                date=datetime(2025, 6, 1, 11, 0, tzinfo=UTC),
                in_reply_to="false-r@example.com",
            ),
            _row(
                "false-h@example.com",
                thread_id="false-g@example.com",
                sender=me,
                recipients=[frank],
                date=datetime(2025, 6, 1, 12, 0, tzinfo=UTC),
                in_reply_to="false-g@example.com",
            ),
        ],
    )
    db = MailDB._from_pool(test_pool, config=Settings(user_email=me, _env_file=None))  # type: ignore[call-arg]

    before, _ = db.unreplied()
    assert "false-g@example.com" in {email.message_id for email in before}

    assert repair_thread_ids(test_pool) == 2
    assert set(_thread_ids(test_pool).values()) == {"false-f@example.com"}
    after, _ = db.unreplied()
    assert "false-g@example.com" not in {email.message_id for email in after}
