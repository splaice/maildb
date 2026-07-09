import mailbox as mb
from email.mime.text import MIMEText
from pathlib import Path
from uuid import uuid4

import pytest

from maildb.ingest import tasks as ingest_tasks
from maildb.ingest.orchestrator import run_pipeline
from maildb.ingest.parse import process_chunk
from maildb.ingest.tasks import claim_task, create_task, get_phase_status

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


def _insert_import(pool, account: str = "test@example.com"):
    """Insert an imports row and return its id. Workers look up source_account
    from the imports row keyed by ingest_tasks.import_id."""
    import_id = uuid4()
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO imports (id, source_account, source_file, status)
               VALUES (%(id)s, %(account)s, %(file)s, 'running')""",
            {"id": import_id, "account": account, "file": "test"},
        )
        conn.commit()
    return import_id


def test_process_chunk_inserts_emails(test_pool, test_settings, tmp_path):
    import_id = _insert_import(test_pool)
    attachment_dir = tmp_path / "attachments"
    create_task(
        test_pool,
        phase="parse",
        chunk_path=str(FIXTURES / "sample.mbox"),
        import_id=import_id,
    )
    process_chunk(
        database_url=test_settings.database_url,
        attachment_dir=attachment_dir,
        import_id=import_id,
    )
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        count = cur.fetchone()[0]
        cur = conn.execute("SELECT storage_path FROM attachments")
        attachment_paths = [row[0] for row in cur.fetchall()]
        cur = conn.execute("SELECT count(*) FROM email_attachments")
        email_attachment_count = cur.fetchone()[0]
    assert count == 10
    assert attachment_paths
    assert all((attachment_dir / path).exists() for path in attachment_paths)
    assert email_attachment_count == len(attachment_paths)
    status = get_phase_status(test_pool, "parse")
    assert status["completed"] == 1
    assert status["messages_total"] == 10
    assert status["messages_inserted"] == 10
    assert status["messages_skipped"] == 0


def _create_mbox_with_bad_message(tmp_path):
    """Create an mbox with good messages and one that will cause a DB error."""
    mbox_path = tmp_path / "mixed.mbox"
    mbox = mb.mbox(str(mbox_path))

    good = MIMEText("Good body text")
    good["Message-ID"] = "<good@example.com>"
    good["From"] = "alice@example.com"
    good["To"] = "bob@example.com"
    good["Subject"] = "Good message"
    good["Date"] = "Mon, 10 Mar 2025 10:00:00 +0000"
    mbox.add(good)

    bad = MIMEText("Bad body text")
    bad["Message-ID"] = "<bad@example.com>"
    bad["From"] = "alice@example.com"
    bad["To"] = "bob@example.com"
    bad["Subject"] = "Bad message"
    bad["Date"] = "Mon, 10 Mar 2025 10:00:00 +0000"
    bad.set_payload("Bad body text with a PostgreSQL poison null: \x00")
    mbox.add(bad)

    good2 = MIMEText("Also good")
    good2["Message-ID"] = "<good2@example.com>"
    good2["From"] = "alice@example.com"
    good2["To"] = "bob@example.com"
    good2["Subject"] = "Another good message"
    good2["Date"] = "Mon, 10 Mar 2025 10:00:00 +0000"
    mbox.add(good2)

    mbox.close()
    return mbox_path


def test_process_chunk_skips_bad_rows(test_pool, test_settings, tmp_path):
    import_id = _insert_import(test_pool)
    mbox_path = _create_mbox_with_bad_message(tmp_path)
    create_task(test_pool, phase="parse", chunk_path=str(mbox_path), import_id=import_id)
    process_chunk(
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        import_id=import_id,
    )
    status = get_phase_status(test_pool, "parse")
    assert status["completed"] == 1
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        count = cur.fetchone()[0]
        cur = conn.execute("SELECT count(*) FROM email_accounts")
        account_count = cur.fetchone()[0]
    assert count == 2
    assert account_count == 2
    assert status["messages_total"] == 3
    assert status["messages_inserted"] == 2
    assert status["messages_skipped"] == 1


def _create_mbox(tmp_path: Path, filename: str, message_ids: list[str]) -> Path:
    mbox_path = tmp_path / filename
    mbox = mb.mbox(str(mbox_path))
    for idx, message_id in enumerate(message_ids):
        msg = MIMEText(f"Body {idx}")
        msg["Message-ID"] = f"<{message_id}>"
        msg["From"] = "alice@example.com"
        msg["To"] = "bob@example.com"
        msg["Subject"] = f"Message {idx}"
        msg["Date"] = "Mon, 10 Mar 2025 10:00:00 +0000"
        mbox.add(msg)
    mbox.close()
    return mbox_path


def test_process_chunk_preserves_duplicate_accounting(test_pool, test_settings, tmp_path):
    import_id = _insert_import(test_pool)
    chunk_a = _create_mbox(
        tmp_path,
        "duplicates-a.mbox",
        ["dup@example.com", "dup@example.com"],
    )
    chunk_b = _create_mbox(tmp_path, "duplicates-b.mbox", ["dup@example.com"])
    create_task(test_pool, phase="parse", chunk_path=str(chunk_a), import_id=import_id)
    create_task(test_pool, phase="parse", chunk_path=str(chunk_b), import_id=import_id)

    process_chunk(
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        import_id=import_id,
    )

    status = get_phase_status(test_pool, "parse")
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        email_count = cur.fetchone()[0]
        cur = conn.execute("SELECT count(*) FROM email_accounts")
        account_count = cur.fetchone()[0]
    assert status["completed"] == 2
    assert status["messages_total"] == 3
    assert status["messages_inserted"] == 1
    assert status["messages_skipped"] == 2
    assert email_count == 1
    assert account_count == 1


def test_process_chunk_handles_failure(test_pool, test_settings, tmp_path):
    import_id = _insert_import(test_pool)
    create_task(
        test_pool,
        phase="parse",
        chunk_path="/nonexistent/path.mbox",
        import_id=import_id,
    )
    process_chunk(
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        import_id=import_id,
    )
    status = get_phase_status(test_pool, "parse")
    assert status["failed"] == 1


def test_claim_task_with_import_id_only_claims_that_import(test_pool):
    import_a = _insert_import(test_pool, account="a@example.com")
    import_b = _insert_import(test_pool, account="b@example.com")
    task_a = create_task(
        test_pool,
        phase="parse",
        chunk_path="/tmp/a.mbox",
        import_id=import_a,
    )
    task_b = create_task(
        test_pool,
        phase="parse",
        chunk_path="/tmp/b.mbox",
        import_id=import_b,
    )

    claimed = claim_task(
        test_pool,
        phase="parse",
        worker_id="worker-a",
        import_id=import_a,
    )

    assert claimed is not None
    assert claimed["id"] == task_a["id"]
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT status FROM ingest_tasks WHERE id = %(id)s",
            {"id": task_b["id"]},
        )
        assert cur.fetchone()[0] == "pending"


def test_reset_stale_in_progress_only_resets_given_import(test_pool):
    import_a = _insert_import(test_pool, account="a@example.com")
    import_b = _insert_import(test_pool, account="b@example.com")
    stale_a = create_task(
        test_pool,
        phase="parse",
        chunk_path="/tmp/a-stale.mbox",
        import_id=import_a,
    )
    stale_b = create_task(
        test_pool,
        phase="parse",
        chunk_path="/tmp/b-stale.mbox",
        import_id=import_b,
    )
    pending_a = create_task(
        test_pool,
        phase="parse",
        chunk_path="/tmp/a-pending.mbox",
        import_id=import_a,
    )
    index_a = create_task(test_pool, phase="index", import_id=import_a)

    claim_task(test_pool, phase="parse", worker_id="worker-a", import_id=import_a)
    claim_task(test_pool, phase="parse", worker_id="worker-b", import_id=import_b)
    claim_task(test_pool, phase="index", worker_id="worker-index", import_id=import_a)

    count = ingest_tasks.reset_stale_in_progress(
        test_pool,
        phase="parse",
        import_id=import_a,
    )

    assert count == 1
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT id, status, worker_id, started_at FROM ingest_tasks "
            "WHERE id = ANY(%(ids)s) ORDER BY id",
            {"ids": [stale_a["id"], stale_b["id"], pending_a["id"], index_a["id"]]},
        )
        rows = {row[0]: row[1:] for row in cur.fetchall()}
    assert rows[stale_a["id"]] == ("pending", None, None)
    assert rows[stale_b["id"]][0] == "in_progress"
    assert rows[pending_a["id"]][0] == "pending"
    assert rows[index_a["id"]][0] == "in_progress"


def test_parse_increments_reference_count(test_pool, test_settings, tmp_path):
    """Running the pipeline on an mbox whose messages share one attachment
    increments attachments.reference_count correctly."""
    fixtures = Path(__file__).parent.parent / "fixtures"
    run_pipeline(
        mbox_path=fixtures / "sample.mbox",
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,
        parse_workers=2,
        skip_embed=True,
        source_account="ref@example.com",
    )
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*), sum(reference_count) FROM attachments")
        _n_att, total_refs = cur.fetchone()
        # Every email_attachments row should map to exactly one reference_count unit.
        cur = conn.execute("SELECT count(*) FROM email_attachments")
        ea_count = cur.fetchone()[0]
    assert ea_count == total_refs, (
        f"reference_count total ({total_refs}) must equal email_attachments count ({ea_count})"
    )


def test_parse_creates_pending_attachment_contents_row(test_pool, test_settings, tmp_path):
    fixtures = Path(__file__).parent.parent / "fixtures"
    run_pipeline(
        mbox_path=fixtures / "sample.mbox",
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,
        parse_workers=2,
        skip_embed=True,
        source_account="pend@example.com",
    )
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM attachments a "
            "LEFT JOIN attachment_contents ac ON ac.attachment_id = a.id "
            "WHERE ac.attachment_id IS NULL"
        )
        missing = cur.fetchone()[0]
    assert missing == 0, (
        f"Every attachment should have a corresponding attachment_contents row; "
        f"{missing} are missing."
    )
