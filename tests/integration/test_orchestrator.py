from pathlib import Path
from uuid import uuid4

import pytest

from maildb.ingest.orchestrator import count_unembedded, get_status, reset_pipeline, run_pipeline
from maildb.ingest.tasks import complete_task, create_task

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


def _insert_unembedded_email(pool, message_id):
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, sender_name,
                   body_text, source_account, created_at)
               VALUES (%(id)s, %(message_id)s, 'thread-1', 'Test', 'Sender',
                   'Body text', 'test@example.com', now())""",
            {"id": uuid4(), "message_id": message_id},
        )
        conn.commit()


def test_run_pipeline_split_and_parse(test_pool, test_settings, tmp_path):
    """Pipeline should split, parse, and index a small mbox."""
    result = run_pipeline(
        mbox_path=FIXTURES / "sample.mbox",
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,
        parse_workers=2,
        skip_embed=True,
        source_account="test@example.com",
    )
    assert result["parse"]["completed"] > 0
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        assert cur.fetchone()[0] > 0


def test_run_pipeline_writes_imports_row_and_stamps_emails(test_pool, test_settings, tmp_path):
    run_pipeline(
        mbox_path=FIXTURES / "sample.mbox",
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,
        parse_workers=2,
        skip_embed=True,
        source_account="you@example.com",
    )
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT source_account, status, messages_inserted FROM imports")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "you@example.com"
        assert rows[0][1] == "completed"
        assert rows[0][2] > 0

        cur = conn.execute(
            "SELECT count(*) FROM emails WHERE source_account IS NULL OR import_id IS NULL"
        )
        assert cur.fetchone()[0] == 0


def test_embed_resumes_after_completion(test_pool):
    embed_task = create_task(test_pool, phase="embed")
    complete_task(test_pool, embed_task["id"], messages_total=0)
    _insert_unembedded_email(test_pool, "resume-test@example.com")

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails WHERE embedding IS NULL")
        assert cur.fetchone()[0] == 1

    assert count_unembedded(test_pool) == 1


def test_get_status(test_pool):
    status = get_status(test_pool)
    assert "split" in status
    assert "parse" in status
    assert "index" in status
    assert "embed" in status


def test_reset_full(test_pool):
    task = create_task(test_pool, phase="split")
    complete_task(test_pool, task["id"])
    _insert_unembedded_email(test_pool, "reset-test@example.com")
    reset_pipeline(test_pool, phase=None)
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM ingest_tasks")
        assert cur.fetchone()[0] == 0
        cur = conn.execute("SELECT count(*) FROM emails")
        assert cur.fetchone()[0] == 0


def test_reset_embed_phase(test_pool):
    parse_task = create_task(test_pool, phase="parse")
    complete_task(test_pool, parse_task["id"])
    embed_task = create_task(test_pool, phase="embed")
    complete_task(test_pool, embed_task["id"])
    with test_pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, embedding,
                   source_account, created_at)
               VALUES (%(id)s, 'reset-emb@example.com', 't1', 'Test', %(emb)s,
                   'test@example.com', now())""",
            {"id": uuid4(), "emb": [0.1] * 768},
        )
        conn.commit()
    reset_pipeline(test_pool, phase="embed")
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM ingest_tasks WHERE phase = 'embed'")
        assert cur.fetchone()[0] == 0
        cur = conn.execute("SELECT count(*) FROM ingest_tasks WHERE phase = 'parse'")
        assert cur.fetchone()[0] == 1
        cur = conn.execute("SELECT count(*) FROM emails WHERE embedding IS NOT NULL")
        assert cur.fetchone()[0] == 0


def test_reset_parse_phase(test_pool):
    split_task = create_task(test_pool, phase="split")
    complete_task(test_pool, split_task["id"])
    parse_task = create_task(test_pool, phase="parse")
    complete_task(test_pool, parse_task["id"])
    embed_task = create_task(test_pool, phase="embed")
    complete_task(test_pool, embed_task["id"])
    _insert_unembedded_email(test_pool, "reset-parse@example.com")
    reset_pipeline(test_pool, phase="parse")
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM ingest_tasks WHERE phase = 'split'")
        assert cur.fetchone()[0] == 1
        cur = conn.execute(
            "SELECT count(*) FROM ingest_tasks WHERE phase IN ('parse', 'index', 'embed')"
        )
        assert cur.fetchone()[0] == 0
        cur = conn.execute("SELECT count(*) FROM emails")
        assert cur.fetchone()[0] == 0


def test_re_running_completed_ingest_creates_second_import(test_pool, test_settings, tmp_path):
    """Second ingest after the first *completes* creates a new import row.

    Resume-by-(source_account, source_file) only reuses rows that are
    still status='running'. A completed one is left alone.
    """
    common_kwargs = dict(  # noqa: C408
        mbox_path=FIXTURES / "sample.mbox",
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,
        parse_workers=2,
        skip_embed=True,
        source_account="re-run@example.com",
    )
    run_pipeline(**common_kwargs)
    reset_pipeline(test_pool, phase="parse")
    run_pipeline(**common_kwargs)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM imports WHERE source_account = 're-run@example.com'"
        )
        assert cur.fetchone()[0] == 2


def test_run_pipeline_adopts_orphan_running_import(test_pool, test_settings, tmp_path):
    """An orphaned status='running' row is resumed, not duplicated."""
    mbox = FIXTURES / "sample.mbox"
    orphan_id = uuid4()
    with test_pool.connection() as conn:
        conn.execute(
            """INSERT INTO imports (id, source_account, source_file, status)
               VALUES (%(id)s, 'resume@example.com', %(file)s, 'running')""",
            {"id": orphan_id, "file": str(mbox)},
        )
        conn.commit()

    run_pipeline(
        mbox_path=mbox,
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,
        parse_workers=2,
        skip_embed=True,
        source_account="resume@example.com",
    )

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT id, status FROM imports WHERE source_account = 'resume@example.com'"
        )
        rows = cur.fetchall()
        assert len(rows) == 1  # Orphan was adopted, not duplicated.
        assert rows[0][0] == orphan_id
        assert rows[0][1] == "completed"


def test_run_pipeline_same_mbox_two_accounts_creates_join_rows(test_pool, test_settings, tmp_path):
    """Ingesting the same mbox under two different accounts produces one
    emails row per message but two email_accounts rows per message.
    """
    mbox = FIXTURES / "sample.mbox"
    common = dict(  # noqa: C408
        mbox_path=mbox,
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,
        parse_workers=2,
        skip_embed=True,
    )
    run_pipeline(source_account="a@example.com", **common)
    # Wipe parse tasks so the second account reprocesses every chunk.
    reset_pipeline(test_pool, phase="parse")
    run_pipeline(source_account="b@example.com", **common)

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        email_count = cur.fetchone()[0]
        cur = conn.execute("SELECT count(*) FROM email_accounts")
        ea_count = cur.fetchone()[0]
        cur = conn.execute(
            "SELECT count(DISTINCT email_id) FROM email_accounts "
            "WHERE source_account = 'b@example.com'"
        )
        b_tagged = cur.fetchone()[0]
    assert ea_count == email_count * 2  # Every email tagged under both.
    assert b_tagged == email_count


def test_run_pipeline_force_new_import(test_pool, test_settings, tmp_path):
    """force_new_import=True bypasses resume and creates a fresh import row."""
    mbox = FIXTURES / "sample.mbox"
    orphan_id = uuid4()
    with test_pool.connection() as conn:
        conn.execute(
            """INSERT INTO imports (id, source_account, source_file, status)
               VALUES (%(id)s, 'force@example.com', %(file)s, 'running')""",
            {"id": orphan_id, "file": str(mbox)},
        )
        conn.commit()

    run_pipeline(
        mbox_path=mbox,
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,
        parse_workers=2,
        skip_embed=True,
        source_account="force@example.com",
        force_new_import=True,
    )

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM imports WHERE source_account = 'force@example.com'"
        )
        assert cur.fetchone()[0] == 2  # Orphan left alone; new row created.
