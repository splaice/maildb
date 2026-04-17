from pathlib import Path
from uuid import uuid4

import pytest

from maildb.ingest import orchestrator
from maildb.ingest.orchestrator import count_unembedded, get_status, reset_pipeline, run_pipeline
from maildb.ingest.tasks import complete_task, create_task

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


def _insert_unembedded_email(pool, message_id):
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, sender_name,
                   body_text, created_at)
               VALUES (%(id)s, %(message_id)s, 'thread-1', 'Test', 'Sender',
                   'Body text', now())""",
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
            """INSERT INTO emails (id, message_id, thread_id, subject, embedding, created_at)
               VALUES (%(id)s, 'reset-emb@example.com', 't1', 'Test', %(emb)s, now())""",
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


def test_run_pipeline_does_not_orphan_import_row_on_restart(test_pool, test_settings, tmp_path):
    """Regression: the restart path (split_status total > 0, completed == 0)
    must not leak an orphaned 'running' imports row.
    """
    # Simulate stale split state to trigger the restart branch.
    create_task(test_pool, phase="split")
    create_task(test_pool, phase="parse", chunk_path="/tmp/fake.chunk")
    # Clean imports table before our run.
    with test_pool.connection() as conn:
        conn.execute("DELETE FROM imports")
        conn.commit()

    run_pipeline(
        mbox_path=FIXTURES / "sample.mbox",
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,
        parse_workers=2,
        skip_embed=True,
        source_account="restart@example.com",
    )

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*), max(status) FROM imports")
        count, status = cur.fetchone()
        assert count == 1, f"Expected 1 imports row after restart, got {count}"
        assert status == "completed"


def test_run_pipeline_marks_import_failed_on_exception(
    test_pool, test_settings, tmp_path, monkeypatch
):
    """On exception during the pipeline, the imports row should end up
    status='failed' with completed_at set, and the exception should re-raise.
    """

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated index failure")

    monkeypatch.setattr(orchestrator, "run_index_phase", _boom)

    with test_pool.connection() as conn:
        conn.execute("DELETE FROM imports")
        conn.commit()

    with pytest.raises(RuntimeError, match="simulated index failure"):
        run_pipeline(
            mbox_path=FIXTURES / "sample.mbox",
            database_url=test_settings.database_url,
            attachment_dir=tmp_path / "attachments",
            tmp_dir=tmp_path / "chunks",
            chunk_size_bytes=50 * 1024 * 1024,
            parse_workers=2,
            skip_embed=True,
            source_account="failed@example.com",
        )

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT status, completed_at FROM imports WHERE source_account = 'failed@example.com'"
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "failed"
        assert rows[0][1] is not None


def test_run_pipeline_writes_imports_row_and_stamps_emails(test_pool, test_settings, tmp_path):
    # Clear any imports rows left over from prior tests — conftest's _clean_emails
    # fixture doesn't touch the imports table.
    with test_pool.connection() as conn:
        conn.execute("DELETE FROM imports")
        conn.commit()

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


def test_re_running_ingest_creates_new_import_but_zero_emails(test_pool, test_settings, tmp_path):
    """Idempotent ingest: second run inserts zero emails but logs a new import row.

    The first run completes, so the resume-by-key lookup finds no
    'running' row for the second invocation — it creates a new one.
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
    # Wipe ingest_tasks (but keep emails) so the second run replays the parse
    # phase and exercises the ON CONFLICT dedup path.
    with test_pool.connection() as conn:
        conn.execute("DELETE FROM ingest_tasks")
        conn.commit()
    run_pipeline(**common_kwargs)

    with test_pool.connection() as conn:
        cur = conn.execute(
            """SELECT count(*), sum(messages_skipped)
               FROM imports WHERE source_account = 're-run@example.com'"""
        )
        count, total_skipped = cur.fetchone()
        assert count == 2
        assert total_skipped > 0, "Second import should have recorded skipped duplicates"


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
    assert len(rows) == 1  # Orphan adopted, not duplicated.
    assert rows[0][0] == orphan_id
    assert rows[0][1] == "completed"


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
