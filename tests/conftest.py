# tests/conftest.py
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from maildb.config import Settings
from maildb.db import create_indexes, create_pool, init_db

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "MAILDB_TEST_DATABASE_URL",
            "postgresql://maildb_test@localhost:5432/maildb_test",
        ),
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture(scope="session")
def test_pool(test_settings: Settings):  # type: ignore[no-untyped-def]
    pool = create_pool(test_settings)
    init_db(pool)
    create_indexes(pool)  # Tests need indexes
    # Relax source_account NOT NULL — tests legitimately insert legacy rows
    # without source_account. Prod init_db tightens when all rows are tagged.
    with pool.connection() as conn:
        conn.execute("ALTER TABLE emails ALTER COLUMN source_account DROP NOT NULL")
        conn.commit()
    yield pool
    pool.close()


@pytest.fixture(autouse=True)
def _clean_emails(request) -> Iterator[None]:  # type: ignore[no-untyped-def]
    """Delete all rows after each integration test to prevent test pollution."""
    if "integration" not in [m.name for m in request.node.iter_markers()]:
        yield
        return
    yield
    pool = request.getfixturevalue("test_pool")
    with pool.connection() as conn:
        conn.execute("DELETE FROM email_attachments")
        conn.execute("DELETE FROM attachments")
        conn.execute("DELETE FROM ingest_tasks")
        conn.execute("DELETE FROM emails")
        conn.execute("DELETE FROM imports")
        # Tests like test_init_db_tightens_* may re-tighten this column;
        # relax again so subsequent tests can legacy-insert.
        conn.execute("ALTER TABLE emails ALTER COLUMN source_account DROP NOT NULL")
        conn.commit()
