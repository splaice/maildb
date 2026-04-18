import mailbox as mb
from email.mime.text import MIMEText
from pathlib import Path
from uuid import uuid4

import pytest

from maildb.ingest.orchestrator import run_pipeline
from maildb.ingest.parse import process_chunk
from maildb.ingest.tasks import create_task, get_phase_status

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
    create_task(
        test_pool,
        phase="parse",
        chunk_path=str(FIXTURES / "sample.mbox"),
        import_id=import_id,
    )
    process_chunk(
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
    )
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        count = cur.fetchone()[0]
    assert count > 0
    status = get_phase_status(test_pool, "parse")
    assert status["completed"] == 1


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
    bad["Message-ID"] = f"<{'x' * 5000}@example.com>"
    bad["From"] = "alice@example.com"
    bad["To"] = "bob@example.com"
    bad["Subject"] = "Bad message"
    bad["Date"] = "Mon, 10 Mar 2025 10:00:00 +0000"
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
    )
    status = get_phase_status(test_pool, "parse")
    assert status["completed"] == 1
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        count = cur.fetchone()[0]
    assert count >= 1


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
    )
    status = get_phase_status(test_pool, "parse")
    assert status["failed"] == 1


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
