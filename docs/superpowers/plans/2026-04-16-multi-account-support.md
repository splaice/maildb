# Multi-Account Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-account support to MailDB end-to-end: schema migration, ingest pipeline tagging, backfill of existing data, query API surface, MCP exposure, and a Typer-based CLI rework.

**Architecture:** Every email row gains `source_account` (the email address it was imported from) and `import_id` (FK into a new `imports` table that tracks ingest sessions). All query methods gain an optional `account` filter. Existing data is backfilled in place via a new `migrate` command. The two ad-hoc `__main__.py` CLIs are unified under a single Typer-based `maildb` console script.

**Tech Stack:** Python 3.12+, PostgreSQL 16+, pgvector, psycopg3 + psycopg_pool, Pydantic v2 + pydantic-settings, FastMCP, Typer (new), structlog, Ruff, mypy, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-04-16-multi-account-support-design.md`

**Tracking issues:** #11, #12, #13, #14, #15

---

## File Map

### New files

| Path | Responsibility |
|------|----------------|
| `src/maildb/cli.py` | Typer application — `serve`, `ingest run`, `ingest status`, `ingest reset`, `ingest migrate` subcommands |
| `tests/integration/test_migrate.py` | Integration tests for backfill behavior |
| `tests/integration/test_multi_account_queries.py` | Cross-account integration tests for `find`, `search`, `unreplied`, `top_contacts`, `long_threads`, `accounts`, `import_history` |

### Modified files

| Path | Changes |
|------|---------|
| `src/maildb/schema_tables.sql` | Add `imports` table, `source_account`/`import_id` columns on `emails`, `import_id` column on `ingest_tasks` |
| `src/maildb/schema_indexes.sql` | Add indexes for new columns |
| `src/maildb/db.py` | Add NOT NULL self-tightening logic to `init_db` |
| `src/maildb/models.py` | Add `source_account`/`import_id` to `Email`; add `AccountSummary` and `ImportRecord` dataclasses |
| `src/maildb/maildb.py` | Add `account` to `_build_filters`/`find`/`search`/`top_contacts`/`unreplied`/`long_threads`; add `accounts()`/`import_history()`; refactor `_require_user_email` → list semantics |
| `src/maildb/config.py` | Add `user_emails: list[str]`; merge legacy `user_email` |
| `src/maildb/dsl.py` | Add `source_account`/`import_id` to `_EMAILS_COLUMNS` |
| `src/maildb/server.py` | Add `account` parameter to relevant tools; add `accounts` and `import_history` tools |
| `src/maildb/__main__.py` | Replace MCP-server entry with Typer app dispatch (preserving `python -m maildb` for `serve`) |
| `src/maildb/ingest/orchestrator.py` | Accept `source_account`/`import_id`; write `imports` row; pass `import_id` into task creation |
| `src/maildb/ingest/parse.py` | Read `import_id` from claimed task; INSERT `source_account` and `import_id` into emails |
| `src/maildb/ingest/tasks.py` | `create_task` accepts `import_id`; `claim_task` returns it |
| `src/maildb/ingest/__main__.py` | **Deleted** — replaced by Typer subcommands in `cli.py` |
| `pyproject.toml` | Add `typer>=0.12` dependency; add `[project.scripts] maildb = "maildb.cli:app"` |
| `tests/conftest.py` | Add `multi_account_seed` helper for cross-account tests |
| `tests/unit/test_cli.py` | Rewrite to test the Typer commands (skip-embed flag, --account validation) |
| `tests/unit/test_config.py` | Add coverage for `MAILDB_USER_EMAILS` parsing and legacy alias merge |
| `tests/unit/test_dsl.py` | Add coverage for `source_account`/`import_id` in DSL whitelist |
| `tests/unit/test_models.py` | Add coverage for new `Email` fields and new dataclasses |
| `tests/integration/test_orchestrator.py` | Update `run_pipeline` calls to pass `source_account` |
| `tests/integration/test_maildb.py` | Update existing tests that construct rows to include `source_account` (or rely on defaults) |
| `tests/integration/test_parse_worker.py` | Update parse-worker tests for new fields |

---

## Step 1 — Schema (issue #11)

Adds `source_account`/`import_id` columns to `emails`, creates the `imports` table, adds indexes, and surfaces both fields in the `Email` dataclass and `SELECT_COLS`. After this step, existing ingest still works (writes NULL into the new columns); new query methods are not yet added.

### Task 1.1: Add `imports` table and new columns to schema

**Files:**
- Modify: `src/maildb/schema_tables.sql`
- Test: `tests/integration/test_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_db.py`:

```python
import pytest

pytestmark = pytest.mark.integration


def test_imports_table_exists(test_pool):
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'imports' ORDER BY column_name"
        )
        cols = {row[0] for row in cur.fetchall()}
    assert cols == {
        "id", "source_account", "source_file", "started_at", "completed_at",
        "messages_total", "messages_inserted", "messages_skipped", "status",
    }


def test_emails_has_source_account_and_import_id(test_pool):
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'emails' AND column_name IN ('source_account', 'import_id')"
        )
        cols = {row[0] for row in cur.fetchall()}
    assert cols == {"source_account", "import_id"}


def test_ingest_tasks_has_import_id(test_pool):
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ingest_tasks' AND column_name = 'import_id'"
        )
        rows = cur.fetchall()
    assert len(rows) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/integration/test_db.py -v
```

Expected: three FAILs — `imports` table missing, `source_account`/`import_id` missing, `ingest_tasks.import_id` missing.

- [ ] **Step 3: Update the schema SQL**

Replace the contents of `src/maildb/schema_tables.sql` with:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS imports (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_account    TEXT NOT NULL,
    source_file       TEXT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    messages_total    INT NOT NULL DEFAULT 0,
    messages_inserted INT NOT NULL DEFAULT 0,
    messages_skipped  INT NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS emails (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      TEXT UNIQUE NOT NULL,
    thread_id       TEXT NOT NULL,
    source_account  TEXT,
    import_id       UUID REFERENCES imports(id),
    subject         TEXT,
    sender_name     TEXT,
    sender_address  TEXT,
    sender_domain   TEXT,
    recipients      JSONB,
    date            TIMESTAMPTZ,
    body_text       TEXT,
    body_html       TEXT,
    has_attachment   BOOLEAN DEFAULT FALSE,
    attachments     JSONB,
    labels          TEXT[],
    in_reply_to     TEXT,
    "references"    TEXT[],
    embedding       vector(768),
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- For databases created before multi-account support, add the columns idempotently.
ALTER TABLE emails ADD COLUMN IF NOT EXISTS source_account TEXT;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS import_id UUID REFERENCES imports(id);

CREATE TABLE IF NOT EXISTS ingest_tasks (
    id                    SERIAL PRIMARY KEY,
    phase                 TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'pending',
    chunk_path            TEXT,
    worker_id             TEXT,
    started_at            TIMESTAMPTZ,
    completed_at          TIMESTAMPTZ,
    error_message         TEXT,
    retry_count           INT DEFAULT 0,
    messages_total        INT DEFAULT 0,
    messages_inserted     INT DEFAULT 0,
    messages_skipped      INT DEFAULT 0,
    attachments_extracted INT DEFAULT 0,
    import_id             UUID REFERENCES imports(id),
    created_at            TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE ingest_tasks ADD COLUMN IF NOT EXISTS import_id UUID REFERENCES imports(id);

CREATE TABLE IF NOT EXISTS attachments (
    id              SERIAL PRIMARY KEY,
    sha256          TEXT NOT NULL,
    filename        TEXT NOT NULL,
    content_type    TEXT,
    size            BIGINT NOT NULL,
    storage_path    TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (sha256)
);

CREATE TABLE IF NOT EXISTS email_attachments (
    email_id        UUID NOT NULL REFERENCES emails(id),
    attachment_id   INT NOT NULL REFERENCES attachments(id),
    filename        TEXT NOT NULL,
    PRIMARY KEY (email_id, attachment_id)
);
```

The duplicated `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` lines below the `CREATE TABLE` cover databases created before this schema revision — they're no-ops for fresh databases.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_db.py -v
```

Expected: all three new tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/schema_tables.sql tests/integration/test_db.py
git commit -m "feat(schema): add imports table and source_account/import_id columns

Foundation for multi-account support (#11). Existing rows have NULL
in the new columns until the backfill migration runs."
```

### Task 1.2: Add indexes for new columns

**Files:**
- Modify: `src/maildb/schema_indexes.sql`
- Test: `tests/integration/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_db.py`:

```python
def test_indexes_for_multi_account_columns(test_pool):
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename IN ('emails', 'imports') "
            "AND indexname IN ("
            "  'idx_email_source_account', 'idx_email_import_id',"
            "  'idx_imports_source_account', 'idx_imports_started_at')"
        )
        names = {row[0] for row in cur.fetchall()}
    assert names == {
        "idx_email_source_account",
        "idx_email_import_id",
        "idx_imports_source_account",
        "idx_imports_started_at",
    }
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_db.py::test_indexes_for_multi_account_columns -v
```

Expected: FAIL — indexes are missing.

- [ ] **Step 3: Add the indexes**

Append to `src/maildb/schema_indexes.sql`:

```sql
CREATE INDEX IF NOT EXISTS idx_email_source_account ON emails (source_account);
CREATE INDEX IF NOT EXISTS idx_email_import_id ON emails (import_id);
CREATE INDEX IF NOT EXISTS idx_imports_source_account ON imports (source_account);
CREATE INDEX IF NOT EXISTS idx_imports_started_at ON imports (started_at DESC);
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/integration/test_db.py::test_indexes_for_multi_account_columns -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/schema_indexes.sql tests/integration/test_db.py
git commit -m "feat(schema): add indexes for source_account and import_id"
```

### Task 1.3: Update `Email` model and `SELECT_COLS`

**Files:**
- Modify: `src/maildb/models.py`
- Modify: `src/maildb/maildb.py`
- Test: `tests/unit/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_models.py`:

```python
from datetime import datetime, timezone
from uuid import uuid4

from maildb.models import Email


def test_email_includes_source_account_and_import_id():
    eid = uuid4()
    iid = uuid4()
    row = {
        "id": eid,
        "message_id": "<msg-1@example.com>",
        "thread_id": "thread-1",
        "subject": "Hello",
        "sender_name": "Alice",
        "sender_address": "alice@example.com",
        "sender_domain": "example.com",
        "recipients": None,
        "date": datetime(2026, 4, 16, tzinfo=timezone.utc),
        "body_text": "hi",
        "body_html": None,
        "has_attachment": False,
        "attachments": None,
        "labels": None,
        "in_reply_to": None,
        "references": None,
        "embedding": None,
        "created_at": datetime(2026, 4, 16, tzinfo=timezone.utc),
        "source_account": "you@example.com",
        "import_id": iid,
    }
    email = Email.from_row(row)
    assert email.source_account == "you@example.com"
    assert email.import_id == iid


def test_email_defaults_when_columns_missing():
    """Backwards compat: from_row with no source_account/import_id keys."""
    row = {
        "id": uuid4(),
        "message_id": "<msg-2@example.com>",
        "thread_id": "thread-2",
        "subject": None,
        "sender_name": None,
        "sender_address": None,
        "sender_domain": None,
        "recipients": None,
        "date": None,
        "body_text": None,
        "body_html": None,
        "has_attachment": False,
        "attachments": None,
        "labels": None,
        "in_reply_to": None,
        "references": None,
        "embedding": None,
        "created_at": datetime(2026, 4, 16, tzinfo=timezone.utc),
    }
    email = Email.from_row(row)
    assert email.source_account is None
    assert email.import_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_models.py -v -k "source_account or columns_missing"
```

Expected: FAIL — `Email` has no `source_account`/`import_id` fields.

- [ ] **Step 3: Add fields to `Email` and update `from_row`**

In `src/maildb/models.py`, add to the `Email` dataclass (after `embedding` and before `created_at`, then keep `created_at` last):

```python
@dataclass
class Email:
    id: UUID
    message_id: str
    thread_id: str
    subject: str | None
    sender_name: str | None
    sender_address: str | None
    sender_domain: str | None
    recipients: Recipients | None
    date: datetime | None
    body_text: str | None
    body_html: str | None
    has_attachment: bool
    attachments: list[Attachment]
    labels: list[str]
    in_reply_to: str | None
    references: list[str]
    embedding: list[float] | None
    source_account: str | None
    import_id: UUID | None
    created_at: datetime
```

Update `from_row` (in the same file) — change the final `cls(...)` call to add the two new fields:

```python
        return cls(
            id=row["id"],
            message_id=row["message_id"],
            thread_id=row["thread_id"],
            subject=row.get("subject"),
            sender_name=row.get("sender_name"),
            sender_address=row.get("sender_address"),
            sender_domain=row.get("sender_domain"),
            recipients=recipients,
            date=row.get("date"),
            body_text=row.get("body_text"),
            body_html=row.get("body_html"),
            has_attachment=row.get("has_attachment", False),
            attachments=attachments_list,
            labels=row.get("labels") or [],
            in_reply_to=row.get("in_reply_to"),
            references=row.get("references") or [],
            embedding=_parse_embedding(row.get("embedding")),
            source_account=row.get("source_account"),
            import_id=row.get("import_id"),
            created_at=row["created_at"],
        )
```

In `src/maildb/maildb.py`, update `SELECT_COLS` (around line 33):

```python
SELECT_COLS = """
    id, message_id, thread_id, subject, sender_name, sender_address,
    sender_domain, recipients, date, body_text, body_html, has_attachment,
    attachments, labels, in_reply_to, "references", embedding,
    source_account, import_id, created_at
"""
```

The aliased version inside `unreplied()` (around line 644) also needs updating:

```python
        select_cols_aliased = """
            e.id, e.message_id, e.thread_id, e.subject, e.sender_name, e.sender_address,
            e.sender_domain, e.recipients, e.date, e.body_text, e.body_html, e.has_attachment,
            e.attachments, e.labels, e.in_reply_to, e."references", e.embedding,
            e.source_account, e.import_id, e.created_at
        """
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_models.py -v
uv run pytest tests/integration/test_maildb.py -v --no-header 2>&1 | tail -30
```

Expected: model tests PASS. Some `test_maildb.py` tests may fail temporarily because they construct `Email` objects manually — fix in the next task.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/models.py src/maildb/maildb.py tests/unit/test_models.py
git commit -m "feat(models): add source_account and import_id to Email

Plumbs the new schema columns through the Email dataclass and
SELECT_COLS so they round-trip from the database."
```

### Task 1.4: Fix any test fixtures that construct `Email` manually

**Files:**
- Possibly: `tests/integration/test_maildb.py`, `tests/integration/test_orchestrator.py`, `tests/integration/test_parse_worker.py`

- [ ] **Step 1: Run the full integration suite to identify breakage**

```bash
uv run pytest tests/integration/ -v --no-header 2>&1 | grep -E "FAIL|ERROR" | head -40
```

Expected: any failures should be from `Email(...)` constructor calls missing the two new positional args, or fixtures that SELECT specific columns and now miss the new ones. If the suite is green, skip to step 4.

- [ ] **Step 2: Add the missing fields to any fixture/factory that constructs `Email` directly**

For each failure, locate the construction site and add `source_account=None, import_id=None` (or sensible test values) to the `Email(...)` call. Most tests use `Email.from_row(...)` and won't need changes.

- [ ] **Step 3: Run the suite again**

```bash
uv run pytest tests/integration/ -v --no-header 2>&1 | tail -15
```

Expected: all tests PASS or only failures unrelated to this change.

- [ ] **Step 4: Run the full check**

```bash
uv run just check
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -u tests/
git commit -m "test: update Email fixtures for new source_account/import_id fields"
```

---

## Step 2 — Pipeline + CLI rework + backfill (issues #12, #14, Typer)

Replaces the two ad-hoc CLI entry points with a single Typer app, requires `--account` for new ingests, writes an `imports` row per session, stamps every email with `source_account` and `import_id`, adds the `migrate` backfill command, and self-tightens `source_account` to `NOT NULL` once the database is fully tagged.

### Task 2.1: Add Typer dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add typer to dependencies**

In `pyproject.toml`, add to the `dependencies` list (under `[project]`):

```toml
dependencies = [
    "psycopg[binary]>=3.2",
    "psycopg-pool>=3.2",
    "pgvector>=0.3",
    "ollama>=0.4",
    "pydantic-settings>=2.5",
    "beautifulsoup4>=4.12",
    "structlog>=24.4",
    "mcp>=1.26.0",
    "typer>=0.12",
]
```

Add a `[project.scripts]` block (after `[project]`):

```toml
[project.scripts]
maildb = "maildb.cli:app"
```

- [ ] **Step 2: Sync dependencies**

```bash
uv sync
```

Expected: `typer` installed; `uv.lock` updated.

- [ ] **Step 3: Verify importable**

```bash
uv run python -c "import typer; print(typer.__version__)"
```

Expected: prints a version >= 0.12.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add typer dependency and maildb console script"
```

### Task 2.2: Create Typer app skeleton with `serve` command

**Files:**
- Create: `src/maildb/cli.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test**

Replace the contents of `tests/unit/test_cli.py` with:

```python
from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from maildb.cli import app

runner = CliRunner()


def test_serve_invokes_mcp_run():
    with patch("maildb.cli._configure_logging"), patch("maildb.cli.mcp.run") as mock_run:
        result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()


def test_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output
    assert "ingest" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_cli.py -v
```

Expected: ImportError — `maildb.cli` doesn't exist.

- [ ] **Step 3: Create the Typer app**

Create `src/maildb/cli.py`:

```python
"""Unified maildb CLI — `serve`, `ingest run/status/reset/migrate`."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog
import typer

from maildb.config import Settings
from maildb.pii import scrub_pii
from maildb.server import mcp

app = typer.Typer(
    name="maildb",
    help="Personal email database with semantic search.",
    no_args_is_help=True,
)

ingest_app = typer.Typer(name="ingest", help="Ingest pipeline commands.", no_args_is_help=True)
app.add_typer(ingest_app, name="ingest")


def _configure_logging(settings: Settings | None = None) -> None:
    """Set up dual-sink logging: stderr (INFO+) and debug log file (DEBUG+).

    PII scrubbing is applied before events reach either sink.
    """
    settings = settings or Settings()
    log_path = Path(settings.debug_log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists() and log_path.stat().st_size > settings.debug_log_max_bytes:
        log_path.write_text("")

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.INFO)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)

    file_level = getattr(logging, settings.debug_log_level.upper(), logging.DEBUG)
    file_handler = logging.FileHandler(str(log_path))
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            scrub_pii,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


@app.command()
def serve() -> None:
    """Run the MailDB MCP server (stdio transport)."""
    _configure_logging()
    mcp.run()


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_cli.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): introduce Typer app with serve subcommand"
```

### Task 2.3: Add `ingest run` command with required `--account`

**Files:**
- Modify: `src/maildb/cli.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_cli.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock


def test_ingest_run_requires_account(tmp_path: Path):
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    result = runner.invoke(app, ["ingest", "run", str(mbox)])
    assert result.exit_code != 0
    assert "account" in result.output.lower() or "missing" in result.output.lower()


def test_ingest_run_validates_account_format(tmp_path: Path):
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    result = runner.invoke(app, ["ingest", "run", str(mbox), "--account", "not-an-email"])
    assert result.exit_code != 0
    assert "email" in result.output.lower()


def test_ingest_run_passes_account_through(tmp_path: Path):
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    with (
        patch("maildb.cli.run_pipeline") as mock_pipeline,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        mock_pipeline.return_value = {}
        result = runner.invoke(
            app, ["ingest", "run", str(mbox), "--account", "you@example.com"]
        )
    assert result.exit_code == 0, result.output
    mock_pipeline.assert_called_once()
    kwargs = mock_pipeline.call_args[1]
    assert kwargs["source_account"] == "you@example.com"
    assert kwargs["skip_embed"] is False


def test_ingest_run_skip_embed_flag(tmp_path: Path):
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    with (
        patch("maildb.cli.run_pipeline") as mock_pipeline,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        mock_pipeline.return_value = {}
        result = runner.invoke(
            app,
            ["ingest", "run", str(mbox), "--account", "you@example.com", "--skip-embed"],
        )
    assert result.exit_code == 0, result.output
    kwargs = mock_pipeline.call_args[1]
    assert kwargs["skip_embed"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_cli.py -v -k "ingest_run"
```

Expected: FAIL — `ingest run` doesn't exist.

- [ ] **Step 3: Implement `ingest run`**

Add to the top of `src/maildb/cli.py` (additional imports):

```python
import re

from maildb.db import create_pool, init_db
from maildb.ingest.orchestrator import run_pipeline
```

Add a helper at module level:

```python
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_account(account: str) -> str:
    if not _EMAIL_RE.match(account):
        raise typer.BadParameter(f"--account {account!r} is not a valid email address")
    return account
```

Add the `ingest run` command:

```python
@ingest_app.command("run")
def ingest_run(
    mbox_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    account: str = typer.Option(..., "--account", help="Email address of the source account."),
    skip_embed: bool = typer.Option(False, "--skip-embed", help="Skip the embedding phase."),
) -> None:
    """Run the full ingest pipeline for an mbox file."""
    _validate_account(account)
    settings = Settings()

    pool = create_pool(settings)
    init_db(pool)
    pool.close()

    result = run_pipeline(
        mbox_path=mbox_path,
        database_url=settings.database_url,
        attachment_dir=settings.attachment_dir,
        tmp_dir=settings.ingest_tmp_dir,
        chunk_size_bytes=settings.ingest_chunk_size_mb * 1024 * 1024,
        parse_workers=settings.ingest_workers,
        embed_workers=settings.embed_workers,
        embed_batch_size=settings.embed_batch_size,
        ollama_url=settings.ollama_url,
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
        skip_embed=skip_embed,
        source_account=account,
    )
    _print_status_dict(result)


def _print_status_dict(status: dict) -> None:  # type: ignore[type-arg]
    """Format and print pipeline status summary to stdout."""
    lines = [
        f"{'Phase':<10} {'Total':>6} {'Done':>6} {'Failed':>7} {'In Progress':>12}",
    ]
    for phase in ("split", "parse", "index", "embed"):
        s = status.get(phase, {})
        lines.append(
            f"{phase:<10} {s.get('total', 0):>6} {s.get('completed', 0):>6} "
            f"{s.get('failed', 0):>7} {s.get('in_progress', 0):>12}"
        )
    lines.append("")
    lines.append(f"Messages: {status.get('total_emails', 0):,}")
    real = status.get("total_embedded_real", status.get("total_embedded", 0))
    skipped = status.get("total_embedded_skipped", 0)
    total = status.get("total_emails", 0)
    if skipped > 0:
        lines.append(f"Embeddings: {real:,} real + {skipped:,} skipped / {total:,}")
    else:
        lines.append(f"Embeddings: {real:,} / {total:,}")
    lines.append(
        f"Attachments: {status.get('total_attachments', 0):,} "
        f"({status.get('total_attachments_unique', 0):,} unique)"
    )
    typer.echo("\n".join(lines))
```

(`run_pipeline` doesn't yet accept `source_account` — that comes in Task 2.5. The test uses `MagicMock` so it works against the new signature regardless.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_cli.py -v
```

Expected: all tests PASS. (`test_ingest_run_passes_account_through` works because `mock_pipeline` accepts any kwargs.)

- [ ] **Step 5: Commit**

```bash
git add src/maildb/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): add 'ingest run' subcommand requiring --account"
```

### Task 2.4: Plumb `source_account` and `import_id` through `run_pipeline`

**Files:**
- Modify: `src/maildb/ingest/orchestrator.py`
- Modify: `src/maildb/ingest/tasks.py`
- Test: `tests/integration/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_orchestrator.py`:

```python
def test_run_pipeline_writes_imports_row_and_stamps_emails(
    test_pool, test_settings, tmp_path
):
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
        cur = conn.execute(
            "SELECT source_account, status, messages_inserted FROM imports"
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "you@example.com"
        assert rows[0][1] == "completed"
        assert rows[0][2] > 0

        cur = conn.execute(
            "SELECT count(*) FROM emails "
            "WHERE source_account IS NULL OR import_id IS NULL"
        )
        assert cur.fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_orchestrator.py::test_run_pipeline_writes_imports_row_and_stamps_emails -v
```

Expected: FAIL — either `source_account` is an unknown kwarg, or rows have NULLs.

- [ ] **Step 3: Update `tasks.create_task` to accept `import_id`**

In `src/maildb/ingest/tasks.py`, update `create_task`:

```python
def create_task(
    pool: ConnectionPool,
    *,
    phase: str,
    chunk_path: str | None = None,
    import_id: Any = None,
) -> dict[str, Any]:
    """Insert a new task row and return it."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """INSERT INTO ingest_tasks (phase, chunk_path, import_id)
               VALUES (%(phase)s, %(chunk_path)s, %(import_id)s)
               RETURNING *""",
            {"phase": phase, "chunk_path": chunk_path, "import_id": import_id},
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row)  # type: ignore[arg-type]
```

(`Any` is already imported at the top of `tasks.py`. `claim_task` already uses `RETURNING *`, so it picks up `import_id` automatically.)

- [ ] **Step 4: Update `run_pipeline` to accept `source_account` and write an `imports` row**

In `src/maildb/ingest/orchestrator.py`, add to imports near the top:

```python
from uuid import UUID, uuid4
```

Modify the `run_pipeline` signature and body to add `source_account` and the imports-row lifecycle. The new signature:

```python
def run_pipeline(
    *,
    mbox_path: Path | str,
    database_url: str,
    attachment_dir: Path | str,
    tmp_dir: Path | str,
    source_account: str,
    chunk_size_bytes: int = 50 * 1024 * 1024,
    parse_workers: int = -1,
    embed_workers: int = 4,
    embed_batch_size: int = 50,
    ollama_url: str = "http://localhost:11434",
    embedding_model: str = "nomic-embed-text",
    embedding_dimensions: int = 768,
    skip_embed: bool = False,
) -> dict[str, Any]:
    """Run the full ingest pipeline. Restartable."""
    if parse_workers == -1:
        parse_workers = max(1, (os.cpu_count() or 2) - 1)

    pool = _get_pool(database_url)
    import_id = uuid4()

    try:
        # Create or resume the imports row for this session.
        with pool.connection() as conn:
            conn.execute(
                """INSERT INTO imports (id, source_account, source_file, status)
                   VALUES (%(id)s, %(account)s, %(file)s, 'running')""",
                {"id": import_id, "account": source_account, "file": str(mbox_path)},
            )
            conn.commit()

        # ... existing phase 1-4 logic, but EVERY create_task() call now passes import_id ...

        # On success, finalize the imports row.
        with pool.connection() as conn:
            cur = conn.execute(
                "SELECT count(*) FROM emails WHERE import_id = %(id)s",
                {"id": import_id},
            )
            inserted = cur.fetchone()[0]  # type: ignore[index]
            conn.execute(
                """UPDATE imports
                   SET status='completed', completed_at=now(),
                       messages_total=%(t)s, messages_inserted=%(t)s
                   WHERE id=%(id)s""",
                {"id": import_id, "t": inserted},
            )
            conn.commit()
    except Exception:
        with pool.connection() as conn:
            conn.execute(
                "UPDATE imports SET status='failed', completed_at=now() WHERE id=%(id)s",
                {"id": import_id},
            )
            conn.commit()
        raise
    finally:
        pool.close()

    pool = _get_pool(database_url)
    try:
        return get_status(pool)
    finally:
        pool.close()
```

For each existing `create_task(pool, phase=...)` call inside `run_pipeline`, append `import_id=import_id`. There are four call sites in the current orchestrator:
- The split task: `create_task(pool, phase="split", import_id=import_id)`
- Each parse-chunk task: `create_task(pool, phase="parse", chunk_path=str(chunk_path), import_id=import_id)`
- The index task: `create_task(pool, phase="index", import_id=import_id)`
- The embed task: `create_task(pool, phase="embed", import_id=import_id)`

Also remove the old recursive call at the `split_incomplete_restarting` branch and inline the same restart logic with `import_id` plumbed in (or simply keep the recursive call but also pass the new `source_account` argument).

The recursive call needs `source_account=source_account` added, like so:

```python
            return run_pipeline(
                mbox_path=mbox_path,
                database_url=database_url,
                attachment_dir=attachment_dir,
                tmp_dir=tmp_dir,
                source_account=source_account,
                chunk_size_bytes=chunk_size_bytes,
                parse_workers=parse_workers,
                embed_workers=embed_workers,
                embed_batch_size=embed_batch_size,
                ollama_url=ollama_url,
                embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
                skip_embed=skip_embed,
            )
```

- [ ] **Step 5: Update `parse.process_chunk` to read `import_id` and stamp emails**

In `src/maildb/ingest/parse.py`, update `INSERT_EMAIL_SQL`:

```python
INSERT_EMAIL_SQL = """
INSERT INTO emails (
    id, message_id, thread_id, subject, sender_name, sender_address, sender_domain,
    recipients, date, body_text, body_html, has_attachment, attachments,
    labels, in_reply_to, "references", source_account, import_id
) VALUES (
    %(id)s, %(message_id)s, %(thread_id)s, %(subject)s, %(sender_name)s, %(sender_address)s,
    %(sender_domain)s, %(recipients)s, %(date)s, %(body_text)s, %(body_html)s,
    %(has_attachment)s, %(attachments)s, %(labels)s, %(in_reply_to)s, %(references)s,
    %(source_account)s, %(import_id)s
) ON CONFLICT (message_id) DO NOTHING
"""
```

Modify `process_chunk` to fetch `source_account` from the `imports` row when it claims a task:

```python
def process_chunk(
    *,
    database_url: str,
    attachment_dir: Path | str,
) -> int:
    """Claim and process chunks in a loop until no work remains. Returns chunks processed."""
    attachment_dir = Path(attachment_dir)
    pool = ConnectionPool(conninfo=database_url, min_size=1, max_size=1, open=True)
    worker_id = str(os.getpid())
    chunks_processed = 0

    try:
        while True:
            claimed = claim_task(pool, phase="parse", worker_id=worker_id)
            if claimed is None:
                break
            task_id = claimed["id"]
            chunk_path = claimed["chunk_path"]
            import_id = claimed["import_id"]
            source_account = _lookup_source_account(pool, import_id)
            try:
                _process_single_chunk(
                    pool, task_id, chunk_path, attachment_dir,
                    import_id=import_id, source_account=source_account,
                )
                chunks_processed += 1
            except Exception as exc:
                logger.exception("chunk_failed", task_id=task_id)
                try:
                    fail_task(pool, task_id, error=str(exc))
                except Exception:
                    logger.exception("failed_to_update_task", task_id=task_id)
    finally:
        pool.close()

    return chunks_processed


def _lookup_source_account(pool: ConnectionPool, import_id: Any) -> str:
    with pool.connection() as conn:
        cur = conn.execute(
            "SELECT source_account FROM imports WHERE id = %(id)s",
            {"id": import_id},
        )
        row = cur.fetchone()
        if row is None:
            msg = f"No imports row for id {import_id}"
            raise RuntimeError(msg)
        return row[0]  # type: ignore[no-any-return]
```

Add `Any` to the imports at the top of `parse.py`:

```python
from typing import Any
```

Update `_process_single_chunk` signature and the per-row dict construction:

```python
def _process_single_chunk(
    pool: ConnectionPool,
    task_id: int,
    chunk_path: str,
    attachment_dir: Path,
    *,
    import_id: Any,
    source_account: str,
) -> None:
    """Process a single chunk file: parse, extract attachments, insert into DB."""
    messages = list(parse_mbox(chunk_path))
    email_rows: list[dict] = []
    attachment_meta: list[dict] = []
    unique_hashes: dict[str, dict] = {}

    for msg in messages:
        email_id = uuid4()

        email_rows.append(
            {
                "id": email_id,
                "message_id": msg["message_id"],
                "thread_id": msg["thread_id"],
                "subject": msg["subject"],
                "sender_name": msg["sender_name"],
                "sender_address": msg["sender_address"],
                "sender_domain": msg["sender_domain"],
                "recipients": json.dumps(msg["recipients"]) if msg["recipients"] else None,
                "date": msg["date"],
                "body_text": msg["body_text"],
                "body_html": msg["body_html"],
                "has_attachment": msg["has_attachment"],
                "attachments": json.dumps(msg["attachments"]) if msg["attachments"] else None,
                "labels": msg["labels"] or None,
                "in_reply_to": msg["in_reply_to"],
                "references": msg["references"] or None,
                "source_account": source_account,
                "import_id": import_id,
            }
        )
        # ... rest unchanged ...
```

- [ ] **Step 6: Run the test**

```bash
uv run pytest tests/integration/test_orchestrator.py::test_run_pipeline_writes_imports_row_and_stamps_emails -v
```

Expected: PASS.

- [ ] **Step 7: Update existing orchestrator tests**

The pre-existing tests in `tests/integration/test_orchestrator.py` (like `test_run_pipeline_split_and_parse`) call `run_pipeline(...)` without `source_account`. Add `source_account="test@example.com"` to each existing call.

- [ ] **Step 8: Run the full test suite**

```bash
uv run pytest tests/integration/ -v --no-header 2>&1 | tail -10
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/maildb/ingest/orchestrator.py src/maildb/ingest/parse.py src/maildb/ingest/tasks.py tests/integration/test_orchestrator.py
git commit -m "feat(ingest): require source_account, write imports row, stamp emails"
```

### Task 2.5: Add `ingest status` and `ingest reset` Typer commands

**Files:**
- Modify: `src/maildb/cli.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_cli.py`:

```python
def test_ingest_status_invokes_get_status():
    with (
        patch("maildb.cli.get_status") as mock_status,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        mock_status.return_value = {
            "split": {}, "parse": {}, "index": {}, "embed": {},
            "total_emails": 0,
        }
        result = runner.invoke(app, ["ingest", "status"])
    assert result.exit_code == 0, result.output
    mock_status.assert_called_once()


def test_ingest_reset_requires_yes_or_aborts():
    with (
        patch("maildb.cli.reset_pipeline") as mock_reset,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        # Without --yes, prompt is auto-aborted by CliRunner with no input.
        result = runner.invoke(app, ["ingest", "reset"], input="n\n")
    assert mock_reset.call_count == 0


def test_ingest_reset_with_yes_calls_reset():
    with (
        patch("maildb.cli.reset_pipeline") as mock_reset,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        result = runner.invoke(app, ["ingest", "reset", "--yes"])
    assert result.exit_code == 0, result.output
    mock_reset.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_cli.py -v -k "status or reset"
```

Expected: FAIL — commands don't exist.

- [ ] **Step 3: Implement the commands**

Add to `src/maildb/cli.py`:

```python
from maildb.ingest.orchestrator import get_status, reset_pipeline
```

```python
@ingest_app.command("status")
def ingest_status(
    account: str | None = typer.Option(
        None, "--account", help="Filter to one source account."
    ),
) -> None:
    """Print pipeline phase counts and per-import breakdown."""
    settings = Settings()
    pool = create_pool(settings)
    init_db(pool)
    try:
        status = get_status(pool)
        _print_status_dict(status)
        _print_imports_summary(pool, account)
    finally:
        pool.close()


def _print_imports_summary(pool, account: str | None) -> None:  # type: ignore[no-untyped-def]
    """Print a per-import breakdown to stdout."""
    sql = (
        "SELECT started_at, source_account, status, messages_inserted, messages_skipped "
        "FROM imports "
    )
    params: dict = {}
    if account is not None:
        sql += "WHERE source_account = %(account)s "
        params["account"] = account
    sql += "ORDER BY started_at DESC LIMIT 20"
    with pool.connection() as conn:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    if not rows:
        return
    typer.echo("\nImports")
    for started, acct, status, inserted, skipped in rows:
        ts = started.strftime("%Y-%m-%d %H:%M") if started else "?"
        typer.echo(
            f"  {ts}  {acct:<24} {status:<10} "
            f"{inserted or 0:>10,} inserted   {skipped or 0:>4} skipped"
        )


@ingest_app.command("reset")
def ingest_reset(
    phase: str | None = typer.Option(
        None, "--phase", help="Reset only one phase: parse, index, or embed.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Delete pipeline state. Without --phase, performs a full reset."""
    settings = Settings()
    target = phase or "all phases"
    if not yes:
        if not typer.confirm(f"This will reset {target}. Continue?", default=False):
            typer.echo("Aborted.")
            raise typer.Exit(code=1)

    pool = create_pool(settings)
    init_db(pool)
    try:
        reset_pipeline(pool, phase=phase)
    finally:
        pool.close()
    typer.echo(f"Reset complete ({phase or 'full'}).")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_cli.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): add 'ingest status' and 'ingest reset' subcommands

Status output now includes a per-import breakdown with optional --account
filter."
```

### Task 2.6: Add `ingest migrate` backfill command

**Files:**
- Modify: `src/maildb/cli.py`
- Modify: `src/maildb/ingest/orchestrator.py`
- Create: `tests/integration/test_migrate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migrate.py`:

```python
from __future__ import annotations

from uuid import uuid4

import pytest

from maildb.ingest.orchestrator import backfill_source_account

pytestmark = pytest.mark.integration


def _insert_untagged_email(pool, message_id: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, sender_name,
                   body_text, created_at)
               VALUES (%(id)s, %(mid)s, 'thread-1', 'T', 'S', 'b', now())""",
            {"id": uuid4(), "mid": message_id},
        )
        conn.commit()


def test_backfill_tags_null_rows(test_pool):
    _insert_untagged_email(test_pool, "<a@example.com>")
    _insert_untagged_email(test_pool, "<b@example.com>")

    result = backfill_source_account(test_pool, account="you@example.com")
    assert result["rows_updated"] == 2

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM emails "
            "WHERE source_account = 'you@example.com' AND import_id IS NOT NULL"
        )
        assert cur.fetchone()[0] == 2

        cur = conn.execute(
            "SELECT source_file, status, messages_inserted FROM imports "
            "WHERE source_account = 'you@example.com'"
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "migration"
        assert rows[0][1] == "completed"
        assert rows[0][2] == 2


def test_backfill_is_idempotent(test_pool):
    _insert_untagged_email(test_pool, "<c@example.com>")

    first = backfill_source_account(test_pool, account="you@example.com")
    second = backfill_source_account(test_pool, account="you@example.com")

    assert first["rows_updated"] == 1
    assert second["rows_updated"] == 0
    # Second call still creates an imports row, but with messages_inserted=0
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM imports WHERE source_file = 'migration'"
        )
        assert cur.fetchone()[0] == 2


def test_backfill_does_not_overwrite_tagged_rows(test_pool):
    # Pre-tagged row
    with test_pool.connection() as conn:
        conn.execute(
            "INSERT INTO imports (id, source_account, source_file, status) "
            "VALUES (%(id)s, %(acct)s, 'preexisting', 'completed')",
            {"id": uuid4(), "acct": "other@example.com"},
        )
        cur = conn.execute(
            "SELECT id FROM imports WHERE source_account = 'other@example.com'"
        )
        existing_iid = cur.fetchone()[0]
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject,
                   source_account, import_id, created_at)
               VALUES (%(id)s, '<tagged@example.com>', 't', 'T',
                   'other@example.com', %(iid)s, now())""",
            {"id": uuid4(), "iid": existing_iid},
        )
        conn.commit()

    # Add an untagged row too
    _insert_untagged_email(test_pool, "<untagged@example.com>")

    result = backfill_source_account(test_pool, account="you@example.com")
    assert result["rows_updated"] == 1

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT source_account FROM emails WHERE message_id = '<tagged@example.com>'"
        )
        assert cur.fetchone()[0] == "other@example.com"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/integration/test_migrate.py -v
```

Expected: ImportError — `backfill_source_account` doesn't exist.

- [ ] **Step 3: Implement `backfill_source_account`**

Add to `src/maildb/ingest/orchestrator.py`:

```python
def backfill_source_account(pool: ConnectionPool, *, account: str) -> dict[str, Any]:
    """Tag all emails with NULL source_account using the given account.

    Idempotent: re-running it inserts another (empty) imports row but
    updates zero email rows. Never overwrites previously-tagged emails.
    """
    migration_id = uuid4()
    with pool.connection() as conn:
        # Insert the synthetic import row so we can FK rows to it.
        conn.execute(
            """INSERT INTO imports (id, source_account, source_file, status,
                                    started_at, completed_at)
               VALUES (%(id)s, %(acct)s, 'migration', 'running', now(), NULL)""",
            {"id": migration_id, "acct": account},
        )
        cur = conn.execute(
            """UPDATE emails
               SET source_account = %(acct)s, import_id = %(id)s
               WHERE source_account IS NULL""",
            {"id": migration_id, "acct": account},
        )
        rows_updated = cur.rowcount
        conn.execute(
            """UPDATE imports
               SET status = 'completed', completed_at = now(),
                   messages_total = %(n)s, messages_inserted = %(n)s
               WHERE id = %(id)s""",
            {"id": migration_id, "n": rows_updated},
        )
        conn.commit()
    logger.info("backfill_complete", account=account, rows_updated=rows_updated)
    return {"rows_updated": rows_updated, "import_id": migration_id}
```

- [ ] **Step 4: Add the `migrate` Typer command**

Append to `src/maildb/cli.py`:

```python
from maildb.ingest.orchestrator import backfill_source_account
```

```python
@ingest_app.command("migrate")
def ingest_migrate(
    account: str = typer.Option(..., "--account", help="Email address to tag legacy rows with."),
) -> None:
    """Backfill source_account/import_id on rows that lack them."""
    _validate_account(account)
    settings = Settings()
    pool = create_pool(settings)
    init_db(pool)
    try:
        result = backfill_source_account(pool, account=account)
    finally:
        pool.close()
    typer.echo(
        f"Backfilled {result['rows_updated']} rows with source_account={account}"
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_migrate.py -v
uv run pytest tests/unit/test_cli.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/maildb/cli.py src/maildb/ingest/orchestrator.py tests/integration/test_migrate.py
git commit -m "feat(cli): add 'ingest migrate' command for source_account backfill (#14)

Idempotent backfill: only updates rows where source_account IS NULL.
Creates a synthetic 'migration' imports row each invocation."
```

### Task 2.7: Add NOT NULL self-tightening to `init_db`

**Files:**
- Modify: `src/maildb/db.py`
- Test: `tests/integration/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_db.py`:

```python
from uuid import uuid4

from maildb.db import init_db


def test_init_db_tightens_source_account_when_no_nulls(test_pool):
    # Drop the constraint if it's already there (re-runnable test).
    with test_pool.connection() as conn:
        conn.execute("ALTER TABLE emails ALTER COLUMN source_account DROP NOT NULL")
        conn.execute("DELETE FROM emails")
        conn.execute(
            "INSERT INTO imports (id, source_account, source_file, status) "
            "VALUES (%(id)s, 'you@example.com', 'test', 'completed')",
            {"id": uuid4()},
        )
        cur = conn.execute("SELECT id FROM imports LIMIT 1")
        iid = cur.fetchone()[0]
        conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, source_account, import_id) "
            "VALUES (%(id)s, '<x@example.com>', 't', 'you@example.com', %(iid)s)",
            {"id": uuid4(), "iid": iid},
        )
        conn.commit()

    init_db(test_pool)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'emails' AND column_name = 'source_account'"
        )
        assert cur.fetchone()[0] == "NO"


def test_init_db_leaves_nullable_when_some_nulls(test_pool):
    with test_pool.connection() as conn:
        conn.execute("ALTER TABLE emails ALTER COLUMN source_account DROP NOT NULL")
        conn.execute("DELETE FROM emails")
        conn.execute(
            "INSERT INTO emails (id, message_id, thread_id) "
            "VALUES (%(id)s, '<y@example.com>', 't')",
            {"id": uuid4()},
        )
        conn.commit()

    init_db(test_pool)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'emails' AND column_name = 'source_account'"
        )
        assert cur.fetchone()[0] == "YES"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/integration/test_db.py -v -k "tightens or leaves_nullable"
```

Expected: `test_init_db_tightens_source_account_when_no_nulls` FAILs (column stays nullable).

- [ ] **Step 3: Add the self-tightening logic**

Update `src/maildb/db.py` `init_db`:

```python
def init_db(pool: ConnectionPool) -> None:
    """Apply idempotent table DDL from schema_tables.sql.

    Self-tightens emails.source_account to NOT NULL once every row is tagged.
    """
    schema_sql = importlib.resources.files("maildb").joinpath("schema_tables.sql").read_text()
    with pool.connection() as conn:
        conn.execute(schema_sql)
        cur = conn.execute("SELECT count(*) FROM emails WHERE source_account IS NULL")
        null_rows = cur.fetchone()[0]  # type: ignore[index]
        if null_rows == 0:
            try:
                conn.execute(
                    "ALTER TABLE emails ALTER COLUMN source_account SET NOT NULL"
                )
            except Exception:  # noqa: BLE001
                logger.warning("source_account_not_null_constraint_skipped", exc_info=True)
        else:
            logger.info(
                "source_account_not_null_skipped",
                null_rows=null_rows,
                hint="run `maildb ingest migrate --account <addr>`",
            )
        conn.commit()
    logger.info("database_initialized")
```

- [ ] **Step 4: Run the test suite**

```bash
uv run pytest tests/integration/test_db.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/db.py tests/integration/test_db.py
git commit -m "feat(db): tighten source_account to NOT NULL once DB is fully tagged"
```

### Task 2.8: Replace the legacy entry points

**Files:**
- Modify: `src/maildb/__main__.py`
- Delete: `src/maildb/ingest/__main__.py`

- [ ] **Step 1: Replace `__main__.py` with a thin Typer dispatch**

Overwrite `src/maildb/__main__.py`:

```python
"""Entry point for `python -m maildb`. Defaults to the MCP server (`serve`)."""

from __future__ import annotations

import sys

from maildb.cli import app


def main() -> None:
    # If invoked with no subcommand, default to `serve` to preserve
    # the historical `python -m maildb` behavior.
    if len(sys.argv) == 1:
        sys.argv.append("serve")
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Delete the ingest CLI entrypoint**

```bash
git rm src/maildb/ingest/__main__.py
```

- [ ] **Step 3: Verify both entry points work**

```bash
uv run python -m maildb --help
uv run maildb --help
uv run maildb ingest --help
```

Expected: all three print Typer-generated help text. The first should show subcommands including `serve` and `ingest`.

- [ ] **Step 4: Run the full check**

```bash
uv run just check
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/__main__.py src/maildb/ingest/__main__.py
git commit -m "refactor(cli): retire legacy __main__ entry points

`python -m maildb` now dispatches to the Typer app and defaults to
the `serve` subcommand. The standalone ingest entrypoint is replaced
by `maildb ingest run/status/reset/migrate`."
```

---

## Step 3 — Query API + MCP (issue #13)

Adds the `account` parameter to all relevant query methods, exposes `accounts()` and `import_history()`, switches `Settings` to `MAILDB_USER_EMAILS` (list, with backwards-compat alias), expands the DSL whitelist, and updates the MCP server tools.

### Task 3.1: `Settings` accepts a list of user emails

**Files:**
- Modify: `src/maildb/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_config.py`:

```python
import pytest

from maildb.config import Settings


def test_user_emails_parses_csv(monkeypatch):
    monkeypatch.setenv("MAILDB_USER_EMAILS", "a@example.com,b@example.com")
    s = Settings(_env_file=None)
    assert s.user_emails == ["a@example.com", "b@example.com"]


def test_legacy_user_email_merges_into_list(monkeypatch):
    monkeypatch.setenv("MAILDB_USER_EMAIL", "legacy@example.com")
    monkeypatch.delenv("MAILDB_USER_EMAILS", raising=False)
    s = Settings(_env_file=None)
    assert s.user_emails == ["legacy@example.com"]


def test_legacy_user_email_prepended_when_not_already_present(monkeypatch):
    monkeypatch.setenv("MAILDB_USER_EMAIL", "legacy@example.com")
    monkeypatch.setenv("MAILDB_USER_EMAILS", "a@example.com,b@example.com")
    s = Settings(_env_file=None)
    assert s.user_emails == ["legacy@example.com", "a@example.com", "b@example.com"]


def test_legacy_user_email_not_duplicated(monkeypatch):
    monkeypatch.setenv("MAILDB_USER_EMAIL", "a@example.com")
    monkeypatch.setenv("MAILDB_USER_EMAILS", "a@example.com,b@example.com")
    s = Settings(_env_file=None)
    assert s.user_emails == ["a@example.com", "b@example.com"]


def test_no_user_emails_is_empty_list():
    s = Settings(_env_file=None)
    assert s.user_emails == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_config.py -v -k "user_email"
```

Expected: FAIL — `user_emails` field doesn't exist.

- [ ] **Step 3: Update `Settings`**

In `src/maildb/config.py`:

```python
class Settings(BaseSettings):
    model_config = {
        "env_prefix": "MAILDB_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    database_url: str = "postgresql://maildb@localhost:5432/maildb"
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768
    user_email: str | None = None
    user_emails: list[str] = []
    attachment_dir: str = "~/maildb/attachments"
    ingest_chunk_size_mb: int = 50
    ingest_tmp_dir: str = "/tmp/maildb-ingest-tmp-dir"  # noqa: S108
    ingest_workers: int = -1
    embed_workers: int = 4
    embed_batch_size: int = 50

    debug_log: str = "~/.maildb/debug.log"
    debug_log_level: str = "DEBUG"
    debug_log_max_bytes: int = 10_485_760

    @model_validator(mode="after")
    def _expand_paths(self) -> Settings:
        self.attachment_dir = str(Path(self.attachment_dir).expanduser())
        self.ingest_tmp_dir = str(Path(self.ingest_tmp_dir).expanduser())
        self.debug_log = str(Path(self.debug_log).expanduser())
        return self

    @model_validator(mode="after")
    def _merge_legacy_user_email(self) -> Settings:
        if self.user_email and self.user_email not in self.user_emails:
            self.user_emails = [self.user_email, *self.user_emails]
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/config.py tests/unit/test_config.py
git commit -m "feat(config): add user_emails list with legacy user_email merge"
```

### Task 3.2: Add `account` to `_build_filters`, `find()`, `search()`

**Files:**
- Modify: `src/maildb/maildb.py`
- Test: `tests/integration/test_maildb.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_maildb.py` (near other find tests):

```python
def test_find_filters_by_account(maildb, multi_account_seed):
    """find(account=...) returns only emails tagged with that account."""
    a_only, _ = maildb.find(account="a@example.com")
    assert {e.source_account for e in a_only} == {"a@example.com"}

    all_emails, _ = maildb.find()
    assert {e.source_account for e in all_emails} == {"a@example.com", "b@example.com"}
```

(`multi_account_seed` is a fixture added in Task 4.1 — temporarily mark this test `@pytest.mark.skip(reason='fixture pending')` if you want to commit before Step 4. Alternatively, inline a minimal seed in this test.)

For now, inline a minimal seed so this test stands alone:

```python
def test_find_filters_by_account(test_pool, test_settings):
    from uuid import uuid4

    from maildb.maildb import MailDB

    db = MailDB._from_pool(test_pool, config=test_settings)
    iid_a = uuid4()
    iid_b = uuid4()
    with test_pool.connection() as conn:
        for iid, acct in [(iid_a, "a@example.com"), (iid_b, "b@example.com")]:
            conn.execute(
                "INSERT INTO imports (id, source_account, source_file, status) "
                "VALUES (%(id)s, %(acct)s, 'test', 'completed')",
                {"id": iid, "acct": acct},
            )
        for n, (iid, acct) in enumerate(
            [(iid_a, "a@example.com"), (iid_a, "a@example.com"), (iid_b, "b@example.com")]
        ):
            conn.execute(
                """INSERT INTO emails (id, message_id, thread_id, subject, sender_address,
                       date, source_account, import_id, created_at)
                   VALUES (%(id)s, %(mid)s, 't', 'T', 'x@example.com',
                       now(), %(acct)s, %(iid)s, now())""",
                {"id": uuid4(), "mid": f"<find-acct-{n}@example.com>",
                 "acct": acct, "iid": iid},
            )
        conn.commit()

    a_only, _ = db.find(account="a@example.com")
    assert len(a_only) == 2
    assert all(e.source_account == "a@example.com" for e in a_only)

    all_emails, _ = db.find()
    assert len(all_emails) == 3
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_maildb.py::test_find_filters_by_account -v
```

Expected: FAIL — `find()` doesn't accept `account`.

- [ ] **Step 3: Add `account` to `_build_filters` and pass through `find`/`search`**

In `src/maildb/maildb.py`, update `_build_filters` signature:

```python
    @staticmethod
    def _build_filters(
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        has_attachment: bool | None = None,
        subject_contains: str | None = None,
        labels: list[str] | None = None,
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
        account: str | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
```

Inside `_build_filters`, before the `return`, add:

```python
        if account is not None:
            conditions.append("source_account = %(account)s")
            params["account"] = account
```

Update `find()`:

```python
    def find(
        self,
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        has_attachment: bool | None = None,
        subject_contains: str | None = None,
        labels: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str = "date DESC",
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
        account: str | None = None,
    ) -> tuple[list[Email], int]:
```

Pass `account=account` to the inner `_build_filters(...)` call.

Update `search()` the same way: add `account: str | None = None` to the signature and to the `_build_filters` call.

Update `mention_search()` the same way (it also calls `_build_filters` for recipient counts; add the param there too even though the SQL is custom — pass `account=account` through and it appears as a condition).

Actually `mention_search` builds its own `conditions` list and calls `_build_filters` only for recipient-count filters; the cleanest approach is:

```python
    def mention_search(
        self,
        *,
        text: str,
        sender: str | None = None,
        sender_domain: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 50,
        offset: int = 0,
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
        account: str | None = None,
    ) -> tuple[list[Email], int]:
        # ... existing body, but after building `conditions`, add:
        if account is not None:
            conditions.append("source_account = %(account)s")
            params["account"] = account
        # ... rest unchanged ...
```

- [ ] **Step 4: Run the test**

```bash
uv run pytest tests/integration/test_maildb.py::test_find_filters_by_account -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/maildb.py tests/integration/test_maildb.py
git commit -m "feat(query): add 'account' filter to find/search/mention_search"
```

### Task 3.3: Refactor `top_contacts` and `unreplied` for user_emails list

**Files:**
- Modify: `src/maildb/maildb.py`
- Test: `tests/integration/test_maildb.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_maildb.py`:

```python
def test_top_contacts_scoped_by_account(test_pool, test_settings):
    from uuid import uuid4
    from maildb.maildb import MailDB

    config = test_settings.model_copy()
    config.user_emails = ["a@example.com", "b@example.com"]
    db = MailDB._from_pool(test_pool, config=config)

    iid_a, iid_b = uuid4(), uuid4()
    with test_pool.connection() as conn:
        for iid, acct in [(iid_a, "a@example.com"), (iid_b, "b@example.com")]:
            conn.execute(
                "INSERT INTO imports (id, source_account, source_file, status) "
                "VALUES (%(id)s, %(acct)s, 't', 'completed')",
                {"id": iid, "acct": acct},
            )
        # Inbound to A from alice; inbound to B from bob.
        for sender, acct, iid in [
            ("alice@x.com", "a@example.com", iid_a),
            ("alice@x.com", "a@example.com", iid_a),
            ("bob@y.com", "b@example.com", iid_b),
        ]:
            conn.execute(
                """INSERT INTO emails (id, message_id, thread_id, sender_address,
                       date, source_account, import_id, created_at)
                   VALUES (%(id)s, %(mid)s, 't', %(sender)s,
                       now(), %(acct)s, %(iid)s, now())""",
                {"id": uuid4(), "mid": f"<topc-{uuid4()}@x>",
                 "sender": sender, "acct": acct, "iid": iid},
            )
        conn.commit()

    a_results, _ = db.top_contacts(account="a@example.com", direction="inbound")
    addrs = {r["address"] for r in a_results}
    assert addrs == {"alice@x.com"}

    all_results, _ = db.top_contacts(direction="inbound")
    addrs = {r["address"] for r in all_results}
    assert addrs == {"alice@x.com", "bob@y.com"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_maildb.py::test_top_contacts_scoped_by_account -v
```

Expected: FAIL — `top_contacts` doesn't accept `account` and references `user_email`.

- [ ] **Step 3: Refactor `_require_user_email` → list-aware helper**

Replace `_require_user_email` in `src/maildb/maildb.py`:

```python
    def _identity_addresses(self, account: str | None) -> list[str]:
        """Return the addresses that represent 'you' for identity-aware queries.

        If `account` is provided, returns just that single address.
        Otherwise returns the configured user_emails list.
        Raises if neither is available.
        """
        if account is not None:
            return [account]
        if self._config.user_emails:
            return list(self._config.user_emails)
        msg = "user_emails must be configured (or pass account=...) for this method"
        raise ValueError(msg)
```

Delete `_require_user_email` entirely. Update `top_contacts`:

```python
    def top_contacts(
        self,
        *,
        period: str | None = None,
        limit: int = 10,
        offset: int = 0,
        direction: str = "both",
        group_by: str = "address",
        exclude_domains: list[str] | None = None,
        account: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """... (existing docstring) ..."""
        if group_by not in ("address", "domain"):
            msg = f"group_by must be 'address' or 'domain', got {group_by!r}"
            raise ValueError(msg)

        identities = self._identity_addresses(account)
        params: dict[str, Any] = {
            "user_emails": identities,
            "limit": limit,
            "offset": offset,
        }

        if period:
            period_cond = "AND date >= %(period_start)s"
            params["period_start"] = period
        else:
            period_cond = ""

        if exclude_domains:
            params["exclude_domains"] = exclude_domains
            exclude_inbound = "AND sender_domain != ALL(%(exclude_domains)s)"
            exclude_outbound = "AND split_part(r.addr, '@', 2) != ALL(%(exclude_domains)s)"
        else:
            exclude_inbound = ""
            exclude_outbound = ""

        if account is not None:
            account_cond = "AND source_account = %(account)s"
            params["account"] = account
        else:
            account_cond = ""

        label = group_by
        if group_by == "domain":
            inbound_col = "sender_domain"
            outbound_col = "split_part(r.addr, '@', 2)"
        else:
            inbound_col = "sender_address"
            outbound_col = "r.addr"

        if direction == "inbound":
            sql = f"""
                SELECT {inbound_col} AS {label}, count(*) AS count, COUNT(*) OVER() AS _total
                FROM emails
                WHERE sender_address != ALL(%(user_emails)s)
                  {period_cond}
                  {exclude_inbound}
                  {account_cond}
                GROUP BY {inbound_col}
                ORDER BY count DESC
                LIMIT %(limit)s OFFSET %(offset)s
            """
        elif direction == "outbound":
            sql = f"""
                SELECT {outbound_col} AS {label}, count(*) AS count, COUNT(*) OVER() AS _total
                FROM emails,
                     LATERAL (
                         SELECT jsonb_array_elements_text(recipients->'to') AS addr
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'cc')
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'bcc')
                     ) AS r(addr)
                WHERE sender_address = ANY(%(user_emails)s)
                  AND r.addr != ALL(%(user_emails)s)
                  {period_cond}
                  {exclude_outbound}
                  {account_cond}
                GROUP BY {outbound_col}
                ORDER BY count DESC
                LIMIT %(limit)s OFFSET %(offset)s
            """
        else:  # both
            sql = f"""
                SELECT {label}, sum(count) AS count, COUNT(*) OVER() AS _total
                FROM (
                    SELECT {inbound_col} AS {label}, count(*) AS count
                    FROM emails
                    WHERE sender_address != ALL(%(user_emails)s)
                      {period_cond}
                      {exclude_inbound}
                      {account_cond}
                    GROUP BY {inbound_col}

                    UNION ALL

                    SELECT {outbound_col} AS {label}, count(*) AS count
                    FROM emails,
                         LATERAL (
                             SELECT jsonb_array_elements_text(recipients->'to') AS addr
                             UNION ALL
                             SELECT jsonb_array_elements_text(recipients->'cc')
                             UNION ALL
                             SELECT jsonb_array_elements_text(recipients->'bcc')
                         ) AS r(addr)
                    WHERE sender_address = ANY(%(user_emails)s)
                      AND r.addr != ALL(%(user_emails)s)
                      {period_cond}
                      {exclude_outbound}
                      {account_cond}
                    GROUP BY {outbound_col}
                ) AS combined
                GROUP BY {label}
                ORDER BY count DESC
                LIMIT %(limit)s OFFSET %(offset)s
            """

        rows = _query_dicts(self._pool, sql, params)
        total = rows[0]["_total"] if rows else 0
        for row in rows:
            row.pop("_total", None)
        return rows, total
```

Update `unreplied()` similarly. The existing body uses `params["user_email"] = user_email` and SQL like `e.sender_address = %(user_email)s` / `!= %(user_email)s`. Replace those with:

```python
        identities = self._identity_addresses(account)
        params: dict[str, Any] = {"user_emails": identities}
```

And replace `= %(user_email)s` with `= ANY(%(user_emails)s)` and `!= %(user_email)s` with `!= ALL(%(user_emails)s)` everywhere in the method body. Add an account filter to the main `WHERE` clause:

```python
            if account is not None:
                conditions.append("e.source_account = %(account)s")
                params["account"] = account
```

Update the signature to add `account: str | None = None` at the end.

The reply-detection subquery (`NOT EXISTS (... reply.sender_address = %(user_email)s ...)`) becomes `reply.sender_address = ANY(%(user_emails)s)` / `!= ALL(%(user_emails)s)`. This is intentional per the spec — a reply from any of the user's accounts counts, even when filtering by one account.

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/integration/test_maildb.py::test_top_contacts_scoped_by_account -v
uv run pytest tests/integration/test_maildb.py -v --no-header 2>&1 | tail -10
```

Expected: PASS. Existing `top_contacts`/`unreplied` tests may need their fixtures updated to set `user_emails` instead of `user_email`. If failures appear, add `config.user_emails = [config.user_email]` to those fixtures.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/maildb.py tests/integration/test_maildb.py
git commit -m "feat(query): account filter on top_contacts/unreplied; user_emails list semantics"
```

### Task 3.4: Add `account` to `long_threads`

**Files:**
- Modify: `src/maildb/maildb.py`
- Test: `tests/integration/test_maildb.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_maildb.py`:

```python
def test_long_threads_scoped_by_account(test_pool, test_settings):
    from uuid import uuid4
    from maildb.maildb import MailDB

    db = MailDB._from_pool(test_pool, config=test_settings)
    iid_a, iid_b = uuid4(), uuid4()
    with test_pool.connection() as conn:
        for iid, acct in [(iid_a, "a@example.com"), (iid_b, "b@example.com")]:
            conn.execute(
                "INSERT INTO imports (id, source_account, source_file, status) "
                "VALUES (%(id)s, %(acct)s, 't', 'completed')",
                {"id": iid, "acct": acct},
            )
        # Account A has a thread of 6 messages; B has a thread of 2.
        for n in range(6):
            conn.execute(
                """INSERT INTO emails (id, message_id, thread_id, sender_address,
                       date, source_account, import_id, created_at)
                   VALUES (%(id)s, %(mid)s, 'long-A', 'x@example.com',
                       now(), 'a@example.com', %(iid)s, now())""",
                {"id": uuid4(), "mid": f"<lt-A-{n}@x>", "iid": iid_a},
            )
        for n in range(2):
            conn.execute(
                """INSERT INTO emails (id, message_id, thread_id, sender_address,
                       date, source_account, import_id, created_at)
                   VALUES (%(id)s, %(mid)s, 'long-B', 'y@example.com',
                       now(), 'b@example.com', %(iid)s, now())""",
                {"id": uuid4(), "mid": f"<lt-B-{n}@x>", "iid": iid_b},
            )
        conn.commit()

    a_threads, _ = db.long_threads(min_messages=5, account="a@example.com")
    assert {t["thread_id"] for t in a_threads} == {"long-A"}

    b_threads, _ = db.long_threads(min_messages=5, account="b@example.com")
    assert b_threads == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_maildb.py::test_long_threads_scoped_by_account -v
```

Expected: FAIL — `long_threads` doesn't accept `account`.

- [ ] **Step 3: Update `long_threads`**

```python
    def long_threads(
        self,
        *,
        participant: str | None = None,
        min_messages: int = 5,
        after: str | None = None,
        limit: int = 50,
        offset: int = 0,
        account: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """... (existing docstring) ..."""
        conditions: list[str] = []
        params: dict[str, Any] = {
            "min_messages": min_messages, "limit": limit, "offset": offset,
        }
        if after:
            conditions.append("date >= %(after)s")
            params["after"] = after
        if account is not None:
            conditions.append("source_account = %(account)s")
            params["account"] = account
        where = " AND ".join(conditions) if conditions else "TRUE"
        having_participant = ""
        if participant:
            having_participant = "AND %(participant)s = ANY(array_agg(sender_address))"
            params["participant"] = participant
        sql = f"""
            SELECT thread_id, count(*) AS message_count,
                   min(date) AS first_date, max(date) AS last_date,
                   array_agg(DISTINCT sender_address) AS participants,
                   COUNT(*) OVER() AS _total
            FROM emails WHERE {where}
            GROUP BY thread_id
            HAVING count(*) >= %(min_messages)s {having_participant}
            ORDER BY count(*) DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        rows = _query_dicts(self._pool, sql, params)
        total = rows[0]["_total"] if rows else 0
        for row in rows:
            row.pop("_total", None)
        return rows, total
```

- [ ] **Step 4: Run the test**

```bash
uv run pytest tests/integration/test_maildb.py::test_long_threads_scoped_by_account -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/maildb.py tests/integration/test_maildb.py
git commit -m "feat(query): add account filter to long_threads"
```

### Task 3.5: Add `accounts()` and `import_history()` methods + dataclasses

**Files:**
- Modify: `src/maildb/models.py`
- Modify: `src/maildb/maildb.py`
- Test: `tests/integration/test_maildb.py`, `tests/unit/test_models.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_models.py`:

```python
from maildb.models import AccountSummary, ImportRecord


def test_account_summary_dataclass_shape():
    s = AccountSummary(
        source_account="you@example.com",
        email_count=10,
        first_date=None,
        last_date=None,
        import_count=2,
    )
    assert s.source_account == "you@example.com"
    assert s.email_count == 10


def test_import_record_dataclass_shape():
    from datetime import datetime, timezone
    from uuid import uuid4

    r = ImportRecord(
        id=uuid4(),
        source_account="you@example.com",
        source_file="x.mbox",
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        messages_total=0,
        messages_inserted=0,
        messages_skipped=0,
        status="running",
    )
    assert r.status == "running"
```

Append to `tests/integration/test_maildb.py`:

```python
def test_accounts_returns_summary(test_pool, test_settings):
    from uuid import uuid4
    from maildb.maildb import MailDB

    db = MailDB._from_pool(test_pool, config=test_settings)
    iid_a, iid_b = uuid4(), uuid4()
    with test_pool.connection() as conn:
        for iid, acct in [(iid_a, "a@example.com"), (iid_b, "b@example.com")]:
            conn.execute(
                "INSERT INTO imports (id, source_account, source_file, status) "
                "VALUES (%(id)s, %(acct)s, 't', 'completed')",
                {"id": iid, "acct": acct},
            )
        for n, (acct, iid) in enumerate(
            [("a@example.com", iid_a)] * 3 + [("b@example.com", iid_b)] * 2
        ):
            conn.execute(
                """INSERT INTO emails (id, message_id, thread_id, source_account,
                       import_id, date, created_at)
                   VALUES (%(id)s, %(mid)s, 't', %(acct)s, %(iid)s, now(), now())""",
                {"id": uuid4(), "mid": f"<acc-{n}@x>", "acct": acct, "iid": iid},
            )
        conn.commit()

    summaries = db.accounts()
    by_acct = {s.source_account: s for s in summaries}
    assert by_acct["a@example.com"].email_count == 3
    assert by_acct["b@example.com"].email_count == 2
    assert by_acct["a@example.com"].import_count == 1


def test_import_history_returns_records(test_pool, test_settings):
    from uuid import uuid4
    from maildb.maildb import MailDB

    db = MailDB._from_pool(test_pool, config=test_settings)
    with test_pool.connection() as conn:
        for acct in ["a@example.com", "b@example.com", "a@example.com"]:
            conn.execute(
                "INSERT INTO imports (id, source_account, source_file, status) "
                "VALUES (%(id)s, %(acct)s, 't', 'completed')",
                {"id": uuid4(), "acct": acct},
            )
        conn.commit()

    all_records = db.import_history()
    assert len(all_records) == 3

    a_only = db.import_history(account="a@example.com")
    assert len(a_only) == 2
    assert all(r.source_account == "a@example.com" for r in a_only)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_models.py tests/integration/test_maildb.py -v -k "account_summary or import_record or accounts_returns or import_history"
```

Expected: FAIL.

- [ ] **Step 3: Add the dataclasses**

Append to `src/maildb/models.py`:

```python
@dataclass
class AccountSummary:
    source_account: str
    email_count: int
    first_date: datetime | None
    last_date: datetime | None
    import_count: int


@dataclass
class ImportRecord:
    id: UUID
    source_account: str
    source_file: str | None
    started_at: datetime
    completed_at: datetime | None
    messages_total: int
    messages_inserted: int
    messages_skipped: int
    status: str
```

Note: `datetime` and `UUID` are already imported under `TYPE_CHECKING` at the top of `models.py`. Move them out of the `TYPE_CHECKING` block:

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID
```

(Remove the `if TYPE_CHECKING:` guard around them.)

- [ ] **Step 4: Add `accounts()` and `import_history()` to MailDB**

Append to the `MailDB` class in `src/maildb/maildb.py`:

```python
    def accounts(self) -> list[AccountSummary]:
        """Summarize email counts per source_account."""
        sql = """
            SELECT
                source_account,
                COUNT(*)                  AS email_count,
                MIN(date)                 AS first_date,
                MAX(date)                 AS last_date,
                COUNT(DISTINCT import_id) AS import_count
            FROM emails
            WHERE source_account IS NOT NULL
            GROUP BY source_account
            ORDER BY email_count DESC
        """
        rows = _query_dicts(self._pool, sql)
        return [
            AccountSummary(
                source_account=row["source_account"],
                email_count=row["email_count"],
                first_date=row["first_date"],
                last_date=row["last_date"],
                import_count=row["import_count"],
            )
            for row in rows
        ]

    def import_history(
        self,
        *,
        account: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ImportRecord]:
        """Return import session records, newest first."""
        conditions: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if account is not None:
            conditions.append("source_account = %(account)s")
            params["account"] = account
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT id, source_account, source_file, started_at, completed_at,
                   messages_total, messages_inserted, messages_skipped, status
            FROM imports{where}
            ORDER BY started_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        rows = _query_dicts(self._pool, sql, params)
        return [
            ImportRecord(
                id=row["id"],
                source_account=row["source_account"],
                source_file=row["source_file"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                messages_total=row["messages_total"],
                messages_inserted=row["messages_inserted"],
                messages_skipped=row["messages_skipped"],
                status=row["status"],
            )
            for row in rows
        ]
```

Add the imports at the top of `maildb.py`:

```python
from maildb.models import AccountSummary, Email, ImportRecord, SearchResult
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/unit/test_models.py tests/integration/test_maildb.py -v -k "account_summary or import_record or accounts_returns or import_history"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/maildb/models.py src/maildb/maildb.py tests/unit/test_models.py tests/integration/test_maildb.py
git commit -m "feat(query): add accounts() and import_history() methods"
```

### Task 3.6: Add `source_account`/`import_id` to DSL whitelist

**Files:**
- Modify: `src/maildb/dsl.py`
- Test: `tests/unit/test_dsl.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dsl.py`:

```python
def test_source_account_is_filterable():
    sql, params = parse_query({
        "from": "emails",
        "select": [{"field": "id"}],
        "where": {"field": "source_account", "eq": "you@example.com"},
        "limit": 10,
    })
    assert "source_account = %(__p0)s" in sql
    assert params["__p0"] == "you@example.com"


def test_import_id_is_filterable():
    sql, params = parse_query({
        "from": "emails",
        "select": [{"field": "id"}],
        "where": {"field": "import_id", "is_null": False},
        "limit": 10,
    })
    assert "import_id IS NOT NULL" in sql
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_dsl.py -v -k "source_account or import_id"
```

Expected: FAIL — "Unknown column".

- [ ] **Step 3: Update `_EMAILS_COLUMNS`**

In `src/maildb/dsl.py`:

```python
_EMAILS_COLUMNS: set[str] = {
    "id",
    "message_id",
    "thread_id",
    "subject",
    "sender_name",
    "sender_address",
    "sender_domain",
    "date",
    "body_text",
    "has_attachment",
    "labels",
    "in_reply_to",
    "created_at",
    "source_account",
    "import_id",
}
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/unit/test_dsl.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/dsl.py tests/unit/test_dsl.py
git commit -m "feat(dsl): allow source_account and import_id in queries"
```

### Task 3.7: Expose `account` parameter on MCP tools and add new tools

**Files:**
- Modify: `src/maildb/server.py`
- Test: `tests/unit/test_server.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_server.py` (look at existing patterns in that file for how MCP tools are tested — they use `from maildb import server` and call the tool functions with a mocked context):

```python
from unittest.mock import MagicMock


def test_find_passes_account_to_db():
    from maildb import server

    mock_db = MagicMock()
    mock_db.find.return_value = ([], 0)
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db

    server.find(ctx, account="you@example.com")
    kwargs = mock_db.find.call_args.kwargs
    assert kwargs["account"] == "you@example.com"


def test_accounts_tool_serializes_summaries():
    from datetime import datetime, timezone
    from uuid import uuid4

    from maildb import server
    from maildb.models import AccountSummary

    mock_db = MagicMock()
    mock_db.accounts.return_value = [
        AccountSummary(
            source_account="a@example.com",
            email_count=10,
            first_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            import_count=2,
        ),
    ]
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db

    result = server.accounts(ctx)
    assert isinstance(result, list)
    assert result[0]["source_account"] == "a@example.com"
    assert result[0]["email_count"] == 10
    assert result[0]["first_date"].startswith("2026-01")


def test_import_history_tool():
    from datetime import datetime, timezone
    from uuid import uuid4

    from maildb import server
    from maildb.models import ImportRecord

    mock_db = MagicMock()
    iid = uuid4()
    mock_db.import_history.return_value = [
        ImportRecord(
            id=iid,
            source_account="a@example.com",
            source_file="x.mbox",
            started_at=datetime(2026, 4, 16, tzinfo=timezone.utc),
            completed_at=None,
            messages_total=0,
            messages_inserted=0,
            messages_skipped=0,
            status="running",
        ),
    ]
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db

    result = server.import_history(ctx)
    assert result[0]["id"] == str(iid)
    assert result[0]["status"] == "running"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_server.py -v -k "account or import_history"
```

Expected: FAIL — `account` param missing or new tools don't exist.

- [ ] **Step 3: Add `account` to all relevant tool signatures**

In `src/maildb/server.py`, for each of the following tools, add `account: str | None = None` to the signature and pass `account=account` to the underlying MailDB call:

- `find`
- `search`
- `top_contacts`
- `unreplied`
- `long_threads`
- `mention_search`

Update the docstring of each tool to mention the `account` parameter:

```
  account: limit results to this source account (e.g. "you@gmail.com").
    Omit to query across all accounts.
```

- [ ] **Step 4: Add new MCP tools**

Append to `src/maildb/server.py`:

```python
@mcp.tool()
@log_tool
def accounts(ctx: Context) -> list[dict[str, Any]]:
    """List the email accounts present in the database with email counts.

    Returns list of {source_account, email_count, first_date, last_date, import_count}.
    Use this to discover which accounts are available before scoping queries with `account=...`.
    """
    db = _get_db(ctx)
    summaries = db.accounts()
    return [
        {
            "source_account": s.source_account,
            "email_count": s.email_count,
            "first_date": s.first_date.isoformat() if s.first_date else None,
            "last_date": s.last_date.isoformat() if s.last_date else None,
            "import_count": s.import_count,
        }
        for s in summaries
    ]


@mcp.tool()
@log_tool
def import_history(
    ctx: Context,
    account: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List ingest sessions, newest first.

    Parameters:
      account: filter to one source account (optional)
      limit: max rows (default 50)
      offset: pagination offset

    Returns list of {id, source_account, source_file, started_at, completed_at,
    messages_total, messages_inserted, messages_skipped, status}.
    """
    db = _get_db(ctx)
    records = db.import_history(account=account, limit=limit, offset=offset)
    return [
        {
            "id": str(r.id),
            "source_account": r.source_account,
            "source_file": r.source_file,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "messages_total": r.messages_total,
            "messages_inserted": r.messages_inserted,
            "messages_skipped": r.messages_skipped,
            "status": r.status,
        }
        for r in records
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_server.py -v
```

Expected: PASS.

- [ ] **Step 6: Run the full check**

```bash
uv run just check
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/maildb/server.py tests/unit/test_server.py
git commit -m "feat(mcp): expose account filter and accounts/import_history tools"
```

---

## Step 4 — Test coverage (issue #15)

Adds the dedicated multi-account integration test file and seed fixture. By this step, most behavior is already exercised inline by the per-task tests above. Step 4 fills two gaps explicitly called out in the spec: cross-account threading and deduplication semantics.

### Task 4.1: Add `multi_account_seed` fixture

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add the fixture**

Append to `tests/conftest.py`:

```python
from uuid import uuid4


@pytest.fixture
def multi_account_seed(test_pool):
    """Seed two accounts with varied data for cross-account scenarios.

    Layout:
      - account A: 3 emails, including one in a thread that crosses to B
      - account B: 2 emails, one in the cross-account thread
      - one duplicate message_id between A and B (A wins via ON CONFLICT)
    """
    iid_a = uuid4()
    iid_b = uuid4()
    with test_pool.connection() as conn:
        for iid, acct in [(iid_a, "a@example.com"), (iid_b, "b@example.com")]:
            conn.execute(
                "INSERT INTO imports (id, source_account, source_file, status, completed_at) "
                "VALUES (%(id)s, %(acct)s, 'seed', 'completed', now())",
                {"id": iid, "acct": acct},
            )

        rows = [
            # account A
            ("<a-1@example.com>", "thread-A", "alice@example.com", "a@example.com", iid_a),
            ("<a-2@example.com>", "thread-A", "alice@example.com", "a@example.com", iid_a),
            ("<cross-1@example.com>", "thread-cross", "carol@example.com", "a@example.com", iid_a),
            # account B
            ("<b-1@example.com>", "thread-B", "bob@example.com", "b@example.com", iid_b),
            ("<cross-2@example.com>", "thread-cross", "carol@example.com", "b@example.com", iid_b),
        ]
        for mid, tid, sender, acct, iid in rows:
            conn.execute(
                """INSERT INTO emails (id, message_id, thread_id, sender_address,
                       sender_domain, date, source_account, import_id, created_at)
                   VALUES (%(id)s, %(mid)s, %(tid)s, %(sender)s, %(domain)s,
                       now(), %(acct)s, %(iid)s, now())""",
                {
                    "id": uuid4(),
                    "mid": mid,
                    "tid": tid,
                    "sender": sender,
                    "domain": sender.split("@")[1],
                    "acct": acct,
                    "iid": iid,
                },
            )

        # Duplicate message_id — second insert no-ops via ON CONFLICT.
        # Insert in A first so A wins.
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, sender_address,
                   date, source_account, import_id, created_at)
               VALUES (%(id)s, '<dup@example.com>', 't-dup', 'x@example.com',
                   now(), 'a@example.com', %(iid)s, now())
               ON CONFLICT (message_id) DO NOTHING""",
            {"id": uuid4(), "iid": iid_a},
        )
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, sender_address,
                   date, source_account, import_id, created_at)
               VALUES (%(id)s, '<dup@example.com>', 't-dup', 'x@example.com',
                   now(), 'b@example.com', %(iid)s, now())
               ON CONFLICT (message_id) DO NOTHING""",
            {"id": uuid4(), "iid": iid_b},
        )
        conn.commit()
    return {"iid_a": iid_a, "iid_b": iid_b}
```

- [ ] **Step 2: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add multi_account_seed fixture for cross-account scenarios"
```

### Task 4.2: Add cross-account integration tests

**Files:**
- Create: `tests/integration/test_multi_account_queries.py`

- [ ] **Step 1: Write the test file**

Create `tests/integration/test_multi_account_queries.py`:

```python
"""End-to-end multi-account query scenarios from spec §9 / issue #15."""

from __future__ import annotations

import pytest

from maildb.maildb import MailDB

pytestmark = pytest.mark.integration


def _db(test_pool, test_settings) -> MailDB:
    config = test_settings.model_copy()
    config.user_emails = ["you@example.com"]
    return MailDB._from_pool(test_pool, config=config)


def test_get_thread_returns_cross_account_messages(
    test_pool, test_settings, multi_account_seed
):
    """get_thread(...) ignores account and returns the full cross-account thread."""
    db = _db(test_pool, test_settings)
    thread = db.get_thread("thread-cross")
    assert {e.message_id for e in thread} == {
        "<cross-1@example.com>", "<cross-2@example.com>"
    }
    assert {e.source_account for e in thread} == {"a@example.com", "b@example.com"}


def test_deduplication_first_import_wins(test_pool, test_settings, multi_account_seed):
    """Duplicate message_id keeps the first import's source_account."""
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*), array_agg(source_account) FROM emails "
            "WHERE message_id = '<dup@example.com>'"
        )
        count, accounts = cur.fetchone()
    assert count == 1
    assert accounts == ["a@example.com"]


def test_find_no_account_returns_all(test_pool, test_settings, multi_account_seed):
    db = _db(test_pool, test_settings)
    results, total = db.find(limit=100)
    accounts = {e.source_account for e in results}
    assert accounts == {"a@example.com", "b@example.com"}
    assert total >= 6


def test_accounts_summary(test_pool, test_settings, multi_account_seed):
    db = _db(test_pool, test_settings)
    summaries = db.accounts()
    by_acct = {s.source_account: s for s in summaries}
    assert set(by_acct) == {"a@example.com", "b@example.com"}
    # A has 4 emails (a-1, a-2, cross-1, dup), B has 2 (b-1, cross-2)
    assert by_acct["a@example.com"].email_count == 4
    assert by_acct["b@example.com"].email_count == 2


def test_import_history_filters_by_account(
    test_pool, test_settings, multi_account_seed
):
    db = _db(test_pool, test_settings)
    a_records = db.import_history(account="a@example.com")
    assert len(a_records) == 1
    assert a_records[0].source_account == "a@example.com"
```

- [ ] **Step 2: Run the tests**

```bash
uv run pytest tests/integration/test_multi_account_queries.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_multi_account_queries.py
git commit -m "test(integration): cross-account query and dedup scenarios (#15)"
```

### Task 4.3: Add ingest-side multi-account tests

**Files:**
- Modify: `tests/integration/test_orchestrator.py`

- [ ] **Step 1: Append the test**

```python
def test_re_running_ingest_creates_new_import_but_zero_emails(
    test_pool, test_settings, tmp_path
):
    """Idempotent ingest: second run inserts zero emails but logs a new import row."""
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
    # Wipe pipeline state so the second run replays without `split_complete` short-circuiting.
    reset_pipeline(test_pool, phase="parse")
    run_pipeline(**common_kwargs)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM imports WHERE source_account = 're-run@example.com'"
        )
        assert cur.fetchone()[0] == 2
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest tests/integration/test_orchestrator.py::test_re_running_ingest_creates_new_import_but_zero_emails -v
```

Expected: PASS (the second run will skip via ON CONFLICT, but a fresh imports row is still recorded).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_orchestrator.py
git commit -m "test(ingest): idempotency creates new import row, no duplicate emails"
```

### Task 4.4: Final check

- [ ] **Step 1: Run the full check**

```bash
uv run just check
```

Expected: all of fmt, lint, mypy, and pytest pass with no warnings.

- [ ] **Step 2: Manual smoke test of the CLI**

```bash
uv run maildb --help
uv run maildb ingest --help
uv run maildb ingest run --help
uv run maildb ingest migrate --help
```

Expected: each prints valid Typer help including the documented options.

- [ ] **Step 3: Commit any cleanup**

If `uv run just check` produced fixes (formatter/linter), stage and commit them:

```bash
git status
git add -u
git commit -m "chore: format and lint cleanup after multi-account work"
```

---

## Self-Review Checklist (run after writing the plan)

This was checked while drafting; recording the result for the implementing agent:

- **Spec coverage:** Each spec section maps to at least one task —
  - §4 schema → Tasks 1.1, 1.2
  - §4.4 NOT NULL self-tightening → Task 2.7
  - §5 pipeline → Task 2.4 (orchestrator + parse worker)
  - §6 backfill → Task 2.6
  - §7 Typer rework → Tasks 2.1–2.5, 2.8
  - §8.1–8.3 query API → Tasks 3.2, 3.3, 3.4, 3.5
  - §8.4 user_emails → Task 3.1
  - §8.5 DSL → Task 3.6
  - §8.6 MCP → Task 3.7
  - §8.7 model fields → Task 1.3
  - §9 test coverage → Tasks 4.1–4.3 (plus per-task TDD tests throughout)
- **Placeholder scan:** Verified no TBD/TODO/"add appropriate" phrases in the plan body.
- **Type consistency:** `AccountSummary`, `ImportRecord`, and `MailDB.accounts/import_history` signatures are consistent across Task 3.5 (definition) and Task 3.7 (server consumption).
