from pathlib import Path

import pytest

from maildb.ingest.parse import process_chunk
from maildb.ingest.tasks import create_task, get_phase_status

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


def test_process_chunk_inserts_emails(test_pool, test_settings, tmp_path):
    create_task(test_pool, phase="parse", chunk_path=str(FIXTURES / "sample.mbox"))
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


def test_process_chunk_handles_failure(test_pool, test_settings, tmp_path):
    create_task(test_pool, phase="parse", chunk_path="/nonexistent/path.mbox")
    process_chunk(
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
    )
    status = get_phase_status(test_pool, "parse")
    assert status["failed"] == 1
