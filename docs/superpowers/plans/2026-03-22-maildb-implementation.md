# MailDB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build MailDB — a Python library that stores email history in PostgreSQL with pgvector for semantic search, exposing a `MailDB` class as the agent-facing interface.

**Architecture:** Single PostgreSQL table (`emails`) with B-tree, GIN, and HNSW indexes. psycopg3 (sync) for direct SQL — no ORM. Ollama with nomic-embed-text for local embeddings. Layered modules: config → db → models → parsing → embeddings → ingest → maildb.

**Tech Stack:** Python 3.12+, psycopg3 (sync), pgvector, Ollama, pydantic-settings, BeautifulSoup4, structlog, pytest, uv, just

**Spec:** `docs/superpowers/specs/2026-03-22-maildb-design.md`

---

## File Map

| File | Responsibility |
|------|---------------|
| `pyproject.toml` | Project metadata, dependencies, tool config |
| `justfile` | Task runner targets |
| `src/maildb/__init__.py` | Public API: exports `MailDB`, `Email`, `SearchResult` |
| `src/maildb/config.py` | `Settings` class via pydantic-settings |
| `src/maildb/db.py` | Connection pool, `init_db()` |
| `src/maildb/schema.sql` | Idempotent DDL |
| `src/maildb/models.py` | `Email`, `Recipients`, `Attachment`, `SearchResult` dataclasses |
| `src/maildb/parsing.py` | Mbox parsing, header extraction, body cleaning |
| `src/maildb/embeddings.py` | `EmbeddingClient`, `build_embedding_text()` |
| `src/maildb/ingest.py` | `ingest_mbox()`, `backfill_embeddings()` |
| `src/maildb/maildb.py` | `MailDB` class with all query methods |
| `tests/conftest.py` | Test DB setup, pool fixture, transaction rollback |
| `tests/fixtures/sample.mbox` | ~10 crafted test messages |
| `tests/unit/test_parsing.py` | Header extraction, MIME walking, threading logic |
| `tests/unit/test_cleaning.py` | Body cleaning pipeline |
| `tests/unit/test_models.py` | `Email.from_row()`, JSONB deserialization |
| `tests/unit/test_embeddings.py` | `build_embedding_text()`, mocked client |
| `tests/integration/test_db.py` | `init_db()` idempotency, pool lifecycle |
| `tests/integration/test_ingest.py` | Full ingestion, deduplication, backfill |
| `tests/integration/test_maildb.py` | All MailDB methods against seeded DB |

---

## Task 0: System Prerequisites

**Purpose:** Install PostgreSQL 16 with pgvector, just, and configure the database.

- [ ] **Step 1: Install PostgreSQL 16 and pgvector**

```bash
sudo apt-get install -y postgresql-16 postgresql-16-pgvector
```

- [ ] **Step 2: Start PostgreSQL**

```bash
sudo pg_ctlcluster 16 main start
```

Verify: `sudo -u postgres psql -c "SELECT version();"` should show PostgreSQL 16.

- [ ] **Step 3: Create the maildb database and test database**

```bash
sudo -u postgres psql -c "CREATE DATABASE maildb;"
sudo -u postgres psql -c "CREATE DATABASE maildb_test;"
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
sudo -u postgres psql -d maildb -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d maildb_test -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

- [ ] **Step 4: Install just**

```bash
sudo apt-get install -y just || (curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to /usr/local/bin)
```

Verify: `just --version`

---

## Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `justfile`
- Create: `src/maildb/__init__.py`
- Create: `.env`

- [ ] **Step 1: Initialize the project with uv**

```bash
cd /Users/splaice/Code/maildb
uv init --lib --name maildb --python ">=3.12"
```

This creates a basic `pyproject.toml` and `src/maildb/__init__.py`.

- [ ] **Step 2: Replace pyproject.toml with full config**

```toml
[project]
name = "maildb"
version = "0.1.0"
description = "Personal email database with semantic search"
requires-python = ">=3.12"
dependencies = [
    "psycopg[binary]>=3.2",
    "psycopg-pool>=3.2",
    "pgvector>=0.3",
    "ollama>=0.4",
    "pydantic-settings>=2.5",
    "beautifulsoup4>=4.12",
    "structlog>=24.4",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-cov>=6.0",
    "mypy>=1.13",
    "ruff>=0.8",
    "factory-boy>=3.3",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/maildb"]

[tool.ruff]
target-version = "py312"
line-length = 99

[tool.ruff.lint]
select = [
    "E", "W", "F", "I", "N", "UP", "B", "SIM", "RUF",
    "S", "T20", "PTH", "ERA",
]
ignore = ["E501"]

[tool.ruff.lint.isort]
known-first-party = ["maildb"]

[tool.mypy]
python_version = "3.12"
strict = false
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true

[[tool.mypy.overrides]]
module = ["ollama.*", "pgvector.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--strict-markers --tb=short -q"
markers = [
    "integration: tests requiring PostgreSQL",
]

[tool.coverage.run]
source = ["src"]
branch = true

[tool.coverage.report]
fail_under = 80
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "if __name__",
]
```

- [ ] **Step 3: Create justfile**

```justfile
set dotenv-load

default:
    @just --list

fmt:
    uv run ruff format .
    uv run ruff check . --fix

lint:
    uv run ruff check .
    uv run mypy src/

test *ARGS:
    uv run pytest {{ARGS}}

test-unit *ARGS:
    uv run pytest tests/unit/ {{ARGS}}

test-integration *ARGS:
    uv run pytest tests/integration/ -m integration {{ARGS}}

test-cov:
    uv run pytest --cov --cov-report=term-missing --cov-report=html

check: fmt lint test
```

- [ ] **Step 4: Create .env file**

```
MAILDB_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/maildb
MAILDB_OLLAMA_URL=http://localhost:11434
```

- [ ] **Step 5: Create src/maildb/__init__.py stub**

```python
from __future__ import annotations
```

- [ ] **Step 6: Create empty test directories**

```bash
mkdir -p tests/unit tests/integration tests/fixtures
touch tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py
```

- [ ] **Step 7: Install dependencies**

```bash
uv sync
```

Verify: `uv run python -c "import psycopg; print('ok')"` prints `ok`.

- [ ] **Step 8: Verify tooling works**

```bash
uv run ruff check src/
uv run mypy src/
```

Both should pass with no errors.

- [ ] **Step 9: Commit**

```bash
git init
echo ".env" >> .gitignore
git add pyproject.toml uv.lock justfile .gitignore src/ tests/ CLAUDE.md ARCHITECTURE.md docs/
git commit -m "chore: scaffold maildb project with dependencies and tooling"
```

---

## Task 2: Configuration

**Files:**
- Create: `src/maildb/config.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_config.py
from __future__ import annotations

import pytest

from maildb.config import Settings


def test_settings_defaults() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.database_url == "postgresql://localhost:5432/maildb"
    assert settings.ollama_url == "http://localhost:11434"
    assert settings.embedding_model == "nomic-embed-text"
    assert settings.embedding_dimensions == 768
    assert settings.user_email is None


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILDB_DATABASE_URL", "postgresql://custom:5432/mydb")
    monkeypatch.setenv("MAILDB_USER_EMAIL", "me@example.com")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.database_url == "postgresql://custom:5432/mydb"
    assert settings.user_email == "me@example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'maildb.config'`

- [ ] **Step 3: Implement config.py**

```python
# src/maildb/config.py
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "MAILDB_"}

    database_url: str = "postgresql://localhost:5432/maildb"
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768
    user_email: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/config.py tests/unit/test_config.py
git commit -m "feat: add Settings config with pydantic-settings"
```

---

## Task 3: Models

**Files:**
- Create: `src/maildb/models.py`
- Create: `tests/unit/test_models.py`

- [ ] **Step 1: Write the tests**

```python
# tests/unit/test_models.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from maildb.models import Attachment, Email, Recipients, SearchResult


def test_recipients_from_dict() -> None:
    data = {"to": ["a@x.com"], "cc": ["b@x.com"], "bcc": []}
    r = Recipients(to=data["to"], cc=data["cc"], bcc=data["bcc"])
    assert r.to == ["a@x.com"]
    assert r.cc == ["b@x.com"]
    assert r.bcc == []


def test_attachment_fields() -> None:
    a = Attachment(filename="doc.pdf", content_type="application/pdf", size=1024)
    assert a.filename == "doc.pdf"
    assert a.size == 1024


def _make_row() -> dict:
    return {
        "id": uuid4(),
        "message_id": "abc@example.com",
        "thread_id": "abc@example.com",
        "subject": "Test",
        "sender_name": "Alice",
        "sender_address": "alice@example.com",
        "sender_domain": "example.com",
        "recipients": json.dumps({"to": ["bob@example.com"], "cc": [], "bcc": []}),
        "date": datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
        "body_text": "Hello",
        "body_html": None,
        "has_attachment": False,
        "attachments": json.dumps([]),
        "labels": ["INBOX"],
        "in_reply_to": None,
        "references": [],
        "embedding": None,
        "created_at": datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
    }


def test_email_from_row() -> None:
    row = _make_row()
    email = Email.from_row(row)
    assert email.message_id == "abc@example.com"
    assert email.sender_name == "Alice"
    assert email.recipients is not None
    assert email.recipients.to == ["bob@example.com"]
    assert email.attachments == []
    assert email.has_attachment is False


def test_email_from_row_with_attachments() -> None:
    row = _make_row()
    row["has_attachment"] = True
    row["attachments"] = json.dumps([
        {"filename": "file.pdf", "content_type": "application/pdf", "size": 500}
    ])
    email = Email.from_row(row)
    assert len(email.attachments) == 1
    assert email.attachments[0].filename == "file.pdf"


def test_email_from_row_null_recipients() -> None:
    row = _make_row()
    row["recipients"] = None
    email = Email.from_row(row)
    assert email.recipients is None


def test_search_result() -> None:
    row = _make_row()
    email = Email.from_row(row)
    sr = SearchResult(email=email, similarity=0.95)
    assert sr.similarity == 0.95
    assert sr.email.subject == "Test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement models.py**

```python
# src/maildb/models.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass
class Attachment:
    filename: str
    content_type: str
    size: int


@dataclass
class Recipients:
    to: list[str]
    cc: list[str]
    bcc: list[str]


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
    created_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Email:
        # Parse recipients JSONB
        raw_recipients = row["recipients"]
        if raw_recipients is None:
            recipients = None
        else:
            if isinstance(raw_recipients, str):
                raw_recipients = json.loads(raw_recipients)
            recipients = Recipients(
                to=raw_recipients.get("to", []),
                cc=raw_recipients.get("cc", []),
                bcc=raw_recipients.get("bcc", []),
            )

        # Parse attachments JSONB
        raw_attachments = row["attachments"]
        if raw_attachments is None:
            attachments_list: list[Attachment] = []
        else:
            if isinstance(raw_attachments, str):
                raw_attachments = json.loads(raw_attachments)
            attachments_list = [
                Attachment(
                    filename=a["filename"],
                    content_type=a["content_type"],
                    size=a["size"],
                )
                for a in raw_attachments
            ]

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
            embedding=row.get("embedding"),
            created_at=row["created_at"],
        )


@dataclass
class SearchResult:
    email: Email
    similarity: float
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/models.py tests/unit/test_models.py
git commit -m "feat: add Email, Recipients, Attachment, SearchResult dataclasses"
```

---

## Task 4: Schema & Database Layer

**Files:**
- Create: `src/maildb/schema.sql`
- Create: `src/maildb/db.py`
- Create: `tests/conftest.py`
- Create: `tests/integration/test_db.py`

- [ ] **Step 1: Create schema.sql**

This is pure DDL, no test-first needed — it's the foundation everything else tests against.

```sql
-- src/maildb/schema.sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS emails (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      TEXT UNIQUE NOT NULL,
    thread_id       TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_email_sender_address ON emails (sender_address);
CREATE INDEX IF NOT EXISTS idx_email_sender_domain ON emails (sender_domain);
CREATE INDEX IF NOT EXISTS idx_email_date ON emails (date);
CREATE INDEX IF NOT EXISTS idx_email_thread_id ON emails (thread_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_message_id ON emails (message_id);
CREATE INDEX IF NOT EXISTS idx_email_in_reply_to ON emails (in_reply_to);
CREATE INDEX IF NOT EXISTS idx_email_has_attachment ON emails (has_attachment) WHERE has_attachment = TRUE;
CREATE INDEX IF NOT EXISTS idx_email_labels ON emails USING GIN (labels);
CREATE INDEX IF NOT EXISTS idx_email_recipients ON emails USING GIN (recipients);
CREATE INDEX IF NOT EXISTS idx_email_embedding ON emails USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
```

- [ ] **Step 2: Write integration tests for db.py**

```python
# tests/integration/test_db.py
from __future__ import annotations

import pytest

from maildb.db import create_pool, init_db


pytestmark = pytest.mark.integration


def test_init_db_creates_table(test_pool) -> None:  # type: ignore[no-untyped-def]
    """init_db() should create the emails table."""
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'emails')"
        )
        assert cur.fetchone()[0] is True


def test_init_db_is_idempotent(test_pool) -> None:  # type: ignore[no-untyped-def]
    """Calling init_db() twice should not raise."""
    init_db(test_pool)  # second call (first was in fixture)
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'emails')"
        )
        assert cur.fetchone()[0] is True


def test_pool_connection(test_pool) -> None:  # type: ignore[no-untyped-def]
    """Pool should provide working connections."""
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
```

- [ ] **Step 3: Write conftest.py with test_pool fixture**

```python
# tests/conftest.py
from __future__ import annotations

import os

import pytest

from maildb.config import Settings
from maildb.db import create_pool, init_db


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "MAILDB_TEST_DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/maildb_test",
        ),
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture(scope="session")
def test_pool(test_settings: Settings):  # type: ignore[no-untyped-def]
    pool = create_pool(test_settings)
    init_db(pool)
    yield pool
    pool.close()


@pytest.fixture(autouse=True)
def _clean_emails(test_pool, request) -> None:  # type: ignore[no-untyped-def]
    """Delete all rows after each integration test to prevent test pollution."""
    if "integration" not in [m.name for m in request.node.iter_markers()]:
        return
    yield  # type: ignore[misc]
    with test_pool.connection() as conn:
        conn.execute("DELETE FROM emails")
        conn.commit()
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'maildb.db'`

- [ ] **Step 5: Implement db.py**

```python
# src/maildb/db.py
from __future__ import annotations

import importlib.resources

import structlog
from psycopg_pool import ConnectionPool

from maildb.config import Settings

logger = structlog.get_logger()


def create_pool(config: Settings) -> ConnectionPool:
    """Create a psycopg3 connection pool."""
    pool = ConnectionPool(conninfo=config.database_url, min_size=1, max_size=5)
    return pool


def init_db(pool: ConnectionPool) -> None:
    """Apply idempotent DDL from schema.sql."""
    schema_sql = importlib.resources.files("maildb").joinpath("schema.sql").read_text()
    with pool.connection() as conn:
        conn.execute(schema_sql)
        conn.commit()
    logger.info("database_initialized")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_db.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add src/maildb/schema.sql src/maildb/db.py tests/conftest.py tests/integration/test_db.py
git commit -m "feat: add schema.sql and db.py with init_db and connection pool"
```

---

## Task 5: Body Cleaning Functions

**Files:**
- Create: `src/maildb/parsing.py` (cleaning functions only — parsing comes in Task 6)
- Create: `tests/unit/test_cleaning.py`

- [ ] **Step 1: Write the tests**

```python
# tests/unit/test_cleaning.py
from __future__ import annotations

from maildb.parsing import remove_quoted_replies, remove_signature, normalize_whitespace, clean_body


def test_remove_quoted_replies_single_level() -> None:
    text = "Hello\n> quoted line\nWorld"
    assert remove_quoted_replies(text) == "Hello\nWorld"


def test_remove_quoted_replies_nested() -> None:
    text = "Hello\n>> deeply quoted\n> quoted\nWorld"
    assert remove_quoted_replies(text) == "Hello\nWorld"


def test_remove_quoted_replies_outlook() -> None:
    text = "Hello\n-----Original Message-----\nFrom: someone\nOld content"
    assert remove_quoted_replies(text) == "Hello"


def test_remove_signature_standard() -> None:
    text = "Hello World\n-- \nJohn Doe\nCEO"
    assert remove_signature(text) == "Hello World"


def test_remove_signature_no_signature() -> None:
    text = "Hello World\nNo sig here"
    assert remove_signature(text) == "Hello World\nNo sig here"


def test_normalize_whitespace() -> None:
    text = "Hello\n\n\n\nWorld  \n  \nEnd"
    result = normalize_whitespace(text)
    assert "\n\n\n" not in result
    assert result == "Hello\n\nWorld\n\nEnd"


def test_clean_body_full_pipeline() -> None:
    text = "New content\n> old reply\n-- \nSig line\n\n\n"
    result = clean_body(text)
    assert "old reply" not in result
    assert "Sig line" not in result
    assert result == "New content"


def test_clean_body_empty_input() -> None:
    assert clean_body("") == ""
    assert clean_body(None) == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_cleaning.py -v`
Expected: FAIL

- [ ] **Step 3: Implement cleaning functions in parsing.py**

```python
# src/maildb/parsing.py
from __future__ import annotations

import re


def remove_quoted_replies(text: str) -> str:
    """Remove lines starting with > and Outlook-style quoted blocks."""
    lines = text.split("\n")
    result: list[str] = []
    for line in lines:
        if line.startswith(">"):
            continue
        if line.strip() == "-----Original Message-----":
            break
        result.append(line)
    return "\n".join(result)


def remove_signature(text: str) -> str:
    """Remove everything below the standard '-- ' signature delimiter."""
    parts = text.split("\n-- \n")
    return parts[0]


def normalize_whitespace(text: str) -> str:
    """Collapse multiple blank lines and strip trailing whitespace."""
    # Strip trailing whitespace from each line
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    # Collapse 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip leading/trailing whitespace
    return text.strip()


def clean_body(text: str | None) -> str:
    """Full body cleaning pipeline."""
    if not text:
        return ""
    text = remove_quoted_replies(text)
    text = remove_signature(text)
    text = normalize_whitespace(text)
    return text
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/unit/test_cleaning.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/parsing.py tests/unit/test_cleaning.py
git commit -m "feat: add body cleaning pipeline (quoted reply, signature, whitespace)"
```

---

## Task 6: Mbox Parsing

**Files:**
- Modify: `src/maildb/parsing.py` (add parsing functions)
- Create: `tests/fixtures/sample.mbox`
- Create: `tests/unit/test_parsing.py`

- [ ] **Step 1: Create sample.mbox fixture**

Create a file with ~10 crafted test messages covering: plain text, HTML-only, multipart with attachments, threading chains, missing headers, various encodings.

```python
# Use this script to generate the fixture:
# tests/fixtures/generate_mbox.py (helper, not shipped)
import mailbox
import email.utils
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from datetime import datetime, timezone, timedelta

mbox = mailbox.mbox("tests/fixtures/sample.mbox")

# Message 1: Simple plain text, thread root
msg1 = MIMEText("Hey team, let's discuss the Q1 budget.\n\nThanks,\nAlice")
msg1["Message-ID"] = "<msg001@example.com>"
msg1["From"] = "Alice Smith <alice@example.com>"
msg1["To"] = "bob@example.com, carol@example.com"
msg1["Cc"] = "dave@example.com"
msg1["Subject"] = "Q1 Budget Discussion"
msg1["Date"] = email.utils.format_datetime(datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc))
mbox.add(msg1)

# Message 2: Reply to msg1 with quoted text
msg2 = MIMEText("Sounds good, I'll prepare the spreadsheet.\n\n> Hey team, let's discuss the Q1 budget.\n> Thanks,\n> Alice\n-- \nBob Jones\nFinance")
msg2["Message-ID"] = "<msg002@example.com>"
msg2["From"] = "Bob Jones <bob@example.com>"
msg2["To"] = "alice@example.com"
msg2["Subject"] = "Re: Q1 Budget Discussion"
msg2["Date"] = email.utils.format_datetime(datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc))
msg2["In-Reply-To"] = "<msg001@example.com>"
msg2["References"] = "<msg001@example.com>"
mbox.add(msg2)

# Message 3: Second reply in thread
msg3 = MIMEText("Can we schedule a meeting for Thursday?")
msg3["Message-ID"] = "<msg003@example.com>"
msg3["From"] = "Carol White <carol@example.com>"
msg3["To"] = "alice@example.com, bob@example.com"
msg3["Subject"] = "Re: Q1 Budget Discussion"
msg3["Date"] = email.utils.format_datetime(datetime(2025, 1, 16, 9, 0, tzinfo=timezone.utc))
msg3["In-Reply-To"] = "<msg002@example.com>"
msg3["References"] = "<msg001@example.com> <msg002@example.com>"
mbox.add(msg3)

# Message 4: HTML-only message, new thread
msg4 = MIMEText("<html><body><h1>Welcome!</h1><p>Your account is ready.</p></body></html>", "html")
msg4["Message-ID"] = "<msg004@notifications.example.com>"
msg4["From"] = "noreply@notifications.example.com"
msg4["To"] = "alice@example.com"
msg4["Subject"] = "Account Ready"
msg4["Date"] = email.utils.format_datetime(datetime(2025, 2, 1, 8, 0, tzinfo=timezone.utc))
mbox.add(msg4)

# Message 5: Multipart with attachment
msg5 = MIMEMultipart()
msg5["Message-ID"] = "<msg005@example.com>"
msg5["From"] = "Dave Miller <dave@example.com>"
msg5["To"] = "alice@example.com"
msg5["Subject"] = "Q1 Report Attached"
msg5["Date"] = email.utils.format_datetime(datetime(2025, 2, 10, 15, 0, tzinfo=timezone.utc))
msg5.attach(MIMEText("Please find the Q1 report attached."))
attachment = MIMEBase("application", "pdf")
attachment.set_payload(b"fake pdf content")
attachment.add_header("Content-Disposition", "attachment", filename="q1-report.pdf")
msg5.attach(attachment)
mbox.add(msg5)

# Message 6: Message with no subject
msg6 = MIMEText("Quick note - the server is down again.")
msg6["Message-ID"] = "<msg006@example.com>"
msg6["From"] = "ops@example.com"
msg6["To"] = "alice@example.com"
msg6["Date"] = email.utils.format_datetime(datetime(2025, 2, 15, 3, 0, tzinfo=timezone.utc))
mbox.add(msg6)

# Message 7: Multiple recipients, BCC
msg7 = MIMEText("Confidential: restructuring plan enclosed.")
msg7["Message-ID"] = "<msg007@example.com>"
msg7["From"] = "CEO <ceo@bigcorp.com>"
msg7["To"] = "alice@example.com"
msg7["Cc"] = "legal@bigcorp.com"
msg7["Bcc"] = "board@bigcorp.com"
msg7["Subject"] = "Confidential - Restructuring"
msg7["Date"] = email.utils.format_datetime(datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc))
mbox.add(msg7)

# Message 8: Outlook-style quoting
msg8 = MIMEText("I agree with the proposal.\n\n-----Original Message-----\nFrom: someone@example.com\nSent: Monday\nSubject: Proposal\n\nHere is my proposal.")
msg8["Message-ID"] = "<msg008@example.com>"
msg8["From"] = "Frank <frank@example.com>"
msg8["To"] = "alice@example.com"
msg8["Subject"] = "RE: Proposal"
msg8["Date"] = email.utils.format_datetime(datetime(2025, 3, 5, 16, 0, tzinfo=timezone.utc))
msg8["In-Reply-To"] = "<msg-proposal@example.com>"
mbox.add(msg8)

# Message 9: Timezone-naive date (edge case)
msg9 = MIMEText("Testing timezone handling.")
msg9["Message-ID"] = "<msg009@example.com>"
msg9["From"] = "Grace <grace@example.com>"
msg9["To"] = "alice@example.com"
msg9["Subject"] = "Timezone Test"
msg9["Date"] = "Mon, 10 Mar 2025 10:00:00"  # No timezone
mbox.add(msg9)

# Message 10: Multipart alternative (text + HTML)
msg10 = MIMEMultipart("alternative")
msg10["Message-ID"] = "<msg010@example.com>"
msg10["From"] = "Newsletter <news@updates.example.com>"
msg10["To"] = "alice@example.com"
msg10["Subject"] = "Weekly Update"
msg10["Date"] = email.utils.format_datetime(datetime(2025, 3, 15, 7, 0, tzinfo=timezone.utc))
msg10.attach(MIMEText("This week in tech: AI advances continue."))
msg10.attach(MIMEText("<html><body><b>This week in tech:</b> AI advances continue.</body></html>", "html"))
mbox.add(msg10)

mbox.close()
```

Run this script to generate `tests/fixtures/sample.mbox`.

- [ ] **Step 2: Write parsing tests**

```python
# tests/unit/test_parsing.py
from __future__ import annotations

from pathlib import Path

from maildb.parsing import parse_mbox, parse_message


FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_parse_mbox_yields_all_messages() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    assert len(messages) == 10


def test_parse_message_extracts_message_id() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    assert messages[0]["message_id"] == "msg001@example.com"


def test_parse_message_strips_angle_brackets() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    # Should not have < > around message_id
    assert "<" not in messages[0]["message_id"]
    assert ">" not in messages[0]["message_id"]


def test_parse_sender_fields() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[0]
    assert msg["sender_name"] == "Alice Smith"
    assert msg["sender_address"] == "alice@example.com"
    assert msg["sender_domain"] == "example.com"


def test_parse_recipients() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[0]
    assert "bob@example.com" in msg["recipients"]["to"]
    assert "carol@example.com" in msg["recipients"]["to"]
    assert "dave@example.com" in msg["recipients"]["cc"]


def test_parse_date_utc() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[0]
    assert msg["date"].year == 2025
    assert msg["date"].month == 1
    assert msg["date"].tzinfo is not None


def test_threading_root_message() -> None:
    """A message with no References or In-Reply-To uses own message_id as thread_id."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[0]  # msg001 — thread root
    assert msg["thread_id"] == msg["message_id"]


def test_threading_with_references() -> None:
    """A reply with References uses the first reference as thread_id."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[2]  # msg003 — has References: <msg001> <msg002>
    assert msg["thread_id"] == "msg001@example.com"


def test_threading_in_reply_to_only() -> None:
    """A reply with only In-Reply-To uses that as thread_id."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[7]  # msg008 — has In-Reply-To only
    assert msg["thread_id"] == "msg-proposal@example.com"


def test_html_only_body_extraction() -> None:
    """HTML-only message should have body_text from BS4 get_text."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[3]  # msg004 — HTML only
    assert msg["body_html"] is not None
    assert "Welcome" in msg["body_text"]
    assert "<html>" not in msg["body_text"]


def test_attachment_metadata() -> None:
    """Multipart message with attachment should extract metadata."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[4]  # msg005 — has PDF attachment
    assert msg["has_attachment"] is True
    assert len(msg["attachments"]) == 1
    assert msg["attachments"][0]["filename"] == "q1-report.pdf"
    assert msg["attachments"][0]["content_type"] == "application/pdf"


def test_missing_subject() -> None:
    """Message with no Subject header should have subject=None."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[5]  # msg006 — no subject
    assert msg["subject"] is None


def test_multipart_alternative_prefers_plain() -> None:
    """Multipart/alternative should prefer text/plain over HTML."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[9]  # msg010 — multipart/alternative
    assert "This week in tech" in msg["body_text"]
    assert "<html>" not in msg["body_text"]


def test_body_cleaning_applied() -> None:
    """Body text should have quotes and signatures removed."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[1]  # msg002 — has quoted reply and signature
    assert ">" not in msg["body_text"]
    assert "Bob Jones" not in msg["body_text"]
    assert "spreadsheet" in msg["body_text"]


def test_timezone_naive_date_becomes_utc() -> None:
    """A date without timezone info should be assumed UTC."""
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[8]  # msg009 — no timezone in date
    assert msg["date"].tzinfo is not None


def test_in_reply_to_stripped() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[1]  # msg002 — In-Reply-To: <msg001@example.com>
    assert msg["in_reply_to"] == "msg001@example.com"


def test_references_parsed_as_list() -> None:
    messages = list(parse_mbox(FIXTURES / "sample.mbox"))
    msg = messages[2]  # msg003 — References: <msg001> <msg002>
    assert isinstance(msg["references"], list)
    assert len(msg["references"]) == 2
    assert msg["references"][0] == "msg001@example.com"
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/unit/test_parsing.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_mbox'`

- [ ] **Step 4: Add parsing functions to parsing.py**

Add these functions to the existing `src/maildb/parsing.py` (which already has the cleaning functions):

```python
# Add to src/maildb/parsing.py — below the existing cleaning functions

import email.utils
import mailbox
from datetime import UTC, datetime, timezone
from pathlib import Path
from collections.abc import Iterator
from typing import Any

import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger()


def _strip_angles(value: str) -> str:
    """Remove < > brackets from a message-id string."""
    return value.strip().strip("<>")


def _parse_references(header: str | None) -> list[str]:
    """Parse a References header into a list of stripped message-ids."""
    if not header:
        return []
    # References are space-separated message-ids in angle brackets
    return [_strip_angles(ref) for ref in header.split() if ref.strip()]


def _derive_thread_id(message_id: str, references: list[str], in_reply_to: str | None) -> str:
    """Derive thread_id from threading headers."""
    if references:
        return references[0]
    if in_reply_to:
        return in_reply_to
    return message_id


def _extract_body(msg: mailbox.mboxMessage) -> tuple[str | None, str | None]:
    """Extract body_text and body_html from a message.

    Prefers text/plain. Falls back to HTML-to-text via BeautifulSoup.
    """
    text_body: str | None = None
    html_body: str | None = None

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            if content_type == "text/plain" and text_body is None:
                payload = part.get_payload(decode=True)
                if payload:
                    text_body = payload.decode("utf-8", errors="replace")
            elif content_type == "text/html" and html_body is None:
                payload = part.get_payload(decode=True)
                if payload:
                    html_body = payload.decode("utf-8", errors="replace")
    else:
        content_type = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if payload:
            decoded = payload.decode("utf-8", errors="replace")
            if content_type == "text/html":
                html_body = decoded
            else:
                text_body = decoded

    # Fallback: HTML to text via BeautifulSoup
    if text_body is None and html_body is not None:
        soup = BeautifulSoup(html_body, "html.parser")
        text_body = soup.get_text(separator="\n", strip=True)

    return text_body, html_body


def _extract_attachments(msg: mailbox.mboxMessage) -> list[dict[str, Any]]:
    """Extract attachment metadata from MIME parts."""
    attachments: list[dict[str, Any]] = []
    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" not in disposition:
            continue
        filename = part.get_filename() or "unknown"
        content_type = part.get_content_type()
        payload = part.get_payload(decode=True)
        size = len(payload) if payload else 0
        attachments.append({
            "filename": filename,
            "content_type": content_type,
            "size": size,
        })
    return attachments


def parse_message(msg: mailbox.mboxMessage) -> dict[str, Any] | None:
    """Parse a single mbox message into a dict of structured fields.

    Returns None if the message is malformed (missing Message-ID).
    """
    raw_message_id = msg.get("Message-ID")
    if not raw_message_id:
        logger.warning("skipping_message_no_id", subject=msg.get("Subject"))
        return None

    message_id = _strip_angles(raw_message_id)

    # Sender
    sender_name, sender_address = email.utils.parseaddr(msg.get("From", ""))
    sender_domain = sender_address.split("@")[1] if "@" in sender_address else None

    # Recipients
    to_addrs = [addr for _, addr in email.utils.getaddresses(msg.get_all("To", []))]
    cc_addrs = [addr for _, addr in email.utils.getaddresses(msg.get_all("Cc", []))]
    bcc_addrs = [addr for _, addr in email.utils.getaddresses(msg.get_all("Bcc", []))]
    recipients = {"to": to_addrs, "cc": cc_addrs, "bcc": bcc_addrs}

    # Date
    date: datetime | None = None
    raw_date = msg.get("Date")
    if raw_date:
        try:
            date = email.utils.parsedate_to_datetime(raw_date)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            logger.warning("unparseable_date", message_id=message_id, raw_date=raw_date)

    # Threading
    in_reply_to_raw = msg.get("In-Reply-To")
    in_reply_to = _strip_angles(in_reply_to_raw) if in_reply_to_raw else None
    references = _parse_references(msg.get("References"))
    thread_id = _derive_thread_id(message_id, references, in_reply_to)

    # Body
    raw_text, raw_html = _extract_body(msg)
    body_text = clean_body(raw_text) if raw_text else None

    # Attachments
    attachments = _extract_attachments(msg)

    return {
        "message_id": message_id,
        "thread_id": thread_id,
        "subject": msg.get("Subject"),
        "sender_name": sender_name or None,
        "sender_address": sender_address or None,
        "sender_domain": sender_domain,
        "recipients": recipients,
        "date": date,
        "body_text": body_text if body_text else None,
        "body_html": raw_html,
        "has_attachment": len(attachments) > 0,
        "attachments": attachments,
        "labels": [],
        "in_reply_to": in_reply_to,
        "references": references,
    }


def parse_mbox(mbox_path: Path | str) -> Iterator[dict[str, Any]]:
    """Parse all messages from an mbox file.

    Yields one parsed dict per valid message.
    Skips malformed messages (logged via structlog).
    """
    mbox_path = Path(mbox_path)
    mbox_file = mailbox.mbox(str(mbox_path))

    for msg in mbox_file:
        try:
            parsed = parse_message(msg)
            if parsed is not None:
                yield parsed
        except Exception:
            logger.exception("failed_to_parse_message", subject=msg.get("Subject"))

    mbox_file.close()
```

- [ ] **Step 5: Run to verify tests pass**

Run: `uv run pytest tests/unit/test_parsing.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/maildb/parsing.py tests/unit/test_parsing.py tests/fixtures/sample.mbox tests/fixtures/generate_mbox.py
git commit -m "feat: add mbox parsing with header extraction, body cleaning, and threading"
```

---

## Task 7: Embeddings

**Files:**
- Create: `src/maildb/embeddings.py`
- Create: `tests/unit/test_embeddings.py`

- [ ] **Step 1: Write the tests**

```python
# tests/unit/test_embeddings.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from maildb.embeddings import EmbeddingClient, build_embedding_text


def test_build_embedding_text_all_fields() -> None:
    result = build_embedding_text("Q1 Budget", "Alice Smith", "Let's discuss the budget.")
    assert result == "Subject: Q1 Budget\nFrom: Alice Smith\n\nLet's discuss the budget."


def test_build_embedding_text_no_subject() -> None:
    result = build_embedding_text(None, "Alice", "Hello")
    assert result == "Subject: \nFrom: Alice\n\nHello"


def test_build_embedding_text_no_sender() -> None:
    result = build_embedding_text("Test", None, "Body text")
    assert result == "Subject: Test\nFrom: \n\nBody text"


def test_build_embedding_text_no_body() -> None:
    result = build_embedding_text("Test", "Alice", None)
    assert result == "Subject: Test\nFrom: Alice\n\n"


def test_embed_single(mock_ollama: MagicMock) -> None:
    client = EmbeddingClient(
        ollama_url="http://localhost:11434",
        model_name="nomic-embed-text",
        dimensions=768,
    )
    result = client.embed("test text")
    assert len(result) == 768
    mock_ollama.return_value.embed.assert_called_once()


def test_embed_batch(mock_ollama: MagicMock) -> None:
    client = EmbeddingClient(
        ollama_url="http://localhost:11434",
        model_name="nomic-embed-text",
        dimensions=768,
    )
    results = client.embed_batch(["text1", "text2"])
    assert len(results) == 2
    assert len(results[0]) == 768


@pytest.fixture
def mock_ollama() -> MagicMock:  # type: ignore[misc]
    with patch("maildb.embeddings.ollama.Client") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        def _embed_side_effect(model: str, input: str | list[str]) -> dict:
            if isinstance(input, list):
                return {"embeddings": [[0.1] * 768 for _ in input]}
            return {"embeddings": [[0.1] * 768]}

        mock_client.embed.side_effect = _embed_side_effect
        yield mock_cls
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_embeddings.py -v`
Expected: FAIL

- [ ] **Step 3: Implement embeddings.py**

```python
# src/maildb/embeddings.py
from __future__ import annotations

import ollama
import structlog

logger = structlog.get_logger()


def build_embedding_text(
    subject: str | None,
    sender_name: str | None,
    body_text: str | None,
) -> str:
    """Build the text string used for embedding."""
    return f"Subject: {subject or ''}\nFrom: {sender_name or ''}\n\n{body_text or ''}"


class EmbeddingClient:
    """Wraps the Ollama Python client for embedding generation."""

    def __init__(self, ollama_url: str, model_name: str, dimensions: int) -> None:
        self._client = ollama.Client(host=ollama_url)
        self._model = model_name
        self._dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text string."""
        response = self._client.embed(model=self._model, input=text)
        return response["embeddings"][0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        response = self._client.embed(model=self._model, input=texts)
        return response["embeddings"]
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/unit/test_embeddings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/embeddings.py tests/unit/test_embeddings.py
git commit -m "feat: add EmbeddingClient and build_embedding_text"
```

---

## Task 8: Ingestion Pipeline

**Files:**
- Create: `src/maildb/ingest.py`
- Create: `tests/integration/test_ingest.py`

- [ ] **Step 1: Write the integration tests**

```python
# tests/integration/test_ingest.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maildb.ingest import ingest_mbox, backfill_embeddings

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


def test_ingest_mbox_inserts_messages(test_pool) -> None:  # type: ignore[no-untyped-def]
    """Ingesting sample.mbox should insert all valid messages."""
    mock_embed = MagicMock()
    mock_embed.embed_batch.return_value = [[0.1] * 768] * 10

    result = ingest_mbox(test_pool, mock_embed, FIXTURES / "sample.mbox")
    assert result["inserted"] > 0
    assert result["total"] == 10

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        assert cur.fetchone()[0] == result["inserted"]


def test_ingest_mbox_deduplication(test_pool) -> None:  # type: ignore[no-untyped-def]
    """Re-ingesting the same mbox should not create duplicates."""
    mock_embed = MagicMock()
    mock_embed.embed_batch.return_value = [[0.1] * 768] * 10

    result1 = ingest_mbox(test_pool, mock_embed, FIXTURES / "sample.mbox")
    result2 = ingest_mbox(test_pool, mock_embed, FIXTURES / "sample.mbox")

    assert result2["skipped"] == result1["inserted"]
    assert result2["inserted"] == 0

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        assert cur.fetchone()[0] == result1["inserted"]


def test_ingest_mbox_null_embedding_on_failure(test_pool) -> None:  # type: ignore[no-untyped-def]
    """If embedding client fails, rows should still be inserted with NULL embedding."""
    mock_embed = MagicMock()
    mock_embed.embed_batch.side_effect = ConnectionError("Ollama down")

    result = ingest_mbox(test_pool, mock_embed, FIXTURES / "sample.mbox")
    assert result["inserted"] > 0
    assert result["failed_embeddings"] > 0

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails WHERE embedding IS NULL")
        assert cur.fetchone()[0] == result["inserted"]


def test_backfill_embeddings(test_pool) -> None:  # type: ignore[no-untyped-def]
    """backfill_embeddings should update rows with NULL embeddings."""
    # First insert without embeddings
    mock_embed_fail = MagicMock()
    mock_embed_fail.embed_batch.side_effect = ConnectionError("down")
    ingest_mbox(test_pool, mock_embed_fail, FIXTURES / "sample.mbox")

    # Now backfill
    mock_embed_ok = MagicMock()
    mock_embed_ok.embed_batch.return_value = [[0.2] * 768] * 10

    count = backfill_embeddings(test_pool, mock_embed_ok)
    assert count > 0

    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails WHERE embedding IS NULL")
        assert cur.fetchone()[0] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_ingest.py -v`
Expected: FAIL

- [ ] **Step 3: Implement ingest.py**

```python
# src/maildb/ingest.py
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import structlog
from psycopg_pool import ConnectionPool

from maildb.embeddings import EmbeddingClient, build_embedding_text
from maildb.parsing import parse_mbox

logger = structlog.get_logger()

INSERT_SQL = """
INSERT INTO emails (
    message_id, thread_id, subject, sender_name, sender_address, sender_domain,
    recipients, date, body_text, body_html, has_attachment, attachments,
    labels, in_reply_to, "references", embedding
) VALUES (
    %(message_id)s, %(thread_id)s, %(subject)s, %(sender_name)s, %(sender_address)s,
    %(sender_domain)s, %(recipients)s, %(date)s, %(body_text)s, %(body_html)s,
    %(has_attachment)s, %(attachments)s, %(labels)s, %(in_reply_to)s,
    %(references)s, %(embedding)s
) ON CONFLICT (message_id) DO NOTHING
"""


def _prepare_row(msg: dict[str, Any], embedding: list[float] | None) -> dict[str, Any]:
    """Prepare a parsed message dict for database insertion."""
    return {
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
        "labels": msg["labels"],
        "in_reply_to": msg["in_reply_to"],
        "references": msg["references"],
        "embedding": embedding,
    }


def ingest_mbox(
    pool: ConnectionPool,
    embedding_client: EmbeddingClient,
    mbox_path: Path | str,
    batch_size: int = 100,
) -> dict[str, int]:
    """Ingest messages from an mbox file into the database.

    Returns summary: {total, inserted, skipped, failed_embeddings, failed_parsing}.
    """
    message_iter = parse_mbox(mbox_path)
    total = 0
    inserted = 0
    skipped = 0
    failed_embeddings = 0

    while True:
        batch = list(itertools.islice(message_iter, batch_size))
        if not batch:
            break
        total += len(batch)

        # Build embedding texts
        embed_texts = [
            build_embedding_text(m["subject"], m["sender_name"], m["body_text"])
            for m in batch
        ]

        # Generate embeddings
        embeddings: list[list[float] | None]
        try:
            raw_embeddings = embedding_client.embed_batch(embed_texts)
            embeddings = list(raw_embeddings)
        except Exception:
            logger.warning("embedding_batch_failed", batch_size=len(batch))
            embeddings = [None] * len(batch)
            failed_embeddings += len(batch)

        # Insert into database
        with pool.connection() as conn:
            for msg, emb in zip(batch, embeddings):
                row = _prepare_row(msg, emb)
                cur = conn.execute(INSERT_SQL, row)
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            conn.commit()

        if total % 1000 == 0:
            logger.info("ingest_progress", processed=total)

    logger.info(
        "ingest_complete",
        total=total,
        inserted=inserted,
        skipped=skipped,
        failed_embeddings=failed_embeddings,
    )
    return {
        "total": total,
        "inserted": inserted,
        "skipped": skipped,
        "failed_embeddings": failed_embeddings,
    }


def backfill_embeddings(
    pool: ConnectionPool,
    embedding_client: EmbeddingClient,
    batch_size: int = 100,
) -> int:
    """Generate embeddings for rows where embedding IS NULL."""
    updated = 0

    with pool.connection() as conn:
        cur = conn.execute(
            "SELECT id, subject, sender_name, body_text FROM emails WHERE embedding IS NULL"
        )
        rows = cur.fetchall()

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [
            build_embedding_text(row[1], row[2], row[3])
            for row in batch
        ]
        embeddings = embedding_client.embed_batch(texts)

        with pool.connection() as conn:
            for row, emb in zip(batch, embeddings):
                conn.execute(
                    "UPDATE emails SET embedding = %s WHERE id = %s",
                    (emb, row[0]),
                )
            conn.commit()
        updated += len(batch)

    logger.info("backfill_complete", updated=updated)
    return updated
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/integration/test_ingest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/ingest.py tests/integration/test_ingest.py
git commit -m "feat: add ingestion pipeline with batched upsert and embedding backfill"
```

---

## Task 9: MailDB Core Query Methods

**Files:**
- Create: `src/maildb/maildb.py`
- Create: `tests/integration/test_maildb.py` (core methods only)

- [ ] **Step 1: Write the integration tests for core methods**

```python
# tests/integration/test_maildb.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from maildb.maildb import MailDB
from maildb.models import SearchResult

pytestmark = pytest.mark.integration


@pytest.fixture
def seed_emails(test_pool):  # type: ignore[no-untyped-def]
    """Insert a known set of emails for query testing."""
    emails = [
        {
            "message_id": "find-test-1@example.com",
            "thread_id": "find-test-1@example.com",
            "subject": "Budget Discussion",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["bob@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
            "body_text": "Let's discuss the Q1 budget numbers.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.1] * 768,
        },
        {
            "message_id": "find-test-2@example.com",
            "thread_id": "find-test-1@example.com",
            "subject": "Re: Budget Discussion",
            "sender_name": "Bob",
            "sender_address": "bob@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 16, 14, 0, tzinfo=UTC),
            "body_text": "Sounds good, I'll prepare the spreadsheet.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": "find-test-1@example.com",
            "references": ["find-test-1@example.com"],
            "embedding": [0.2] * 768,
        },
        {
            "message_id": "find-test-3@stripe.com",
            "thread_id": "find-test-3@stripe.com",
            "subject": "Invoice #1234",
            "sender_name": "Stripe Billing",
            "sender_address": "billing@stripe.com",
            "sender_domain": "stripe.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 2, 1, 8, 0, tzinfo=UTC),
            "body_text": "Your invoice for January is ready.",
            "body_html": None,
            "has_attachment": True,
            "attachments": json.dumps([{"filename": "invoice.pdf", "content_type": "application/pdf", "size": 2048}]),
            "labels": ["INBOX", "Finance"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.3] * 768,
        },
    ]

    insert_sql = """
    INSERT INTO emails (
        message_id, thread_id, subject, sender_name, sender_address, sender_domain,
        recipients, date, body_text, body_html, has_attachment, attachments,
        labels, in_reply_to, "references", embedding
    ) VALUES (
        %(message_id)s, %(thread_id)s, %(subject)s, %(sender_name)s, %(sender_address)s,
        %(sender_domain)s, %(recipients)s, %(date)s, %(body_text)s, %(body_html)s,
        %(has_attachment)s, %(attachments)s, %(labels)s, %(in_reply_to)s,
        %(references)s, %(embedding)s
    )
    """

    with test_pool.connection() as conn:
        for e in emails:
            conn.execute(insert_sql, e)
        conn.commit()


def test_find_by_sender(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(sender="alice@example.com")
    assert len(results) == 1
    assert results[0].sender_address == "alice@example.com"


def test_find_by_sender_domain(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(sender_domain="stripe.com")
    assert len(results) == 1
    assert results[0].sender_domain == "stripe.com"


def test_find_by_date_range(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(after="2025-01-16", before="2025-02-02")
    assert len(results) == 2  # Bob's reply and Stripe invoice


def test_find_by_attachment(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(has_attachment=True)
    assert len(results) == 1
    assert results[0].has_attachment is True


def test_find_by_subject_contains(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(subject_contains="budget")
    assert len(results) == 2  # Both budget messages


def test_find_by_labels(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(labels=["Finance"])
    assert len(results) == 1
    assert "Finance" in results[0].labels


def test_find_by_recipient(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(recipient="bob@example.com")
    assert len(results) == 1
    assert results[0].message_id == "find-test-1@example.com"


def test_find_with_limit(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(limit=1)
    assert len(results) == 1


def test_find_order_validation(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="Invalid order"):
        db.find(order="DROP TABLE emails")


def test_find_order_date_asc(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    results = db.find(order="date ASC")
    assert results[0].date <= results[-1].date


def test_get_thread(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    thread = db.get_thread("find-test-1@example.com")
    assert len(thread) == 2
    assert thread[0].date <= thread[1].date


def test_get_thread_for(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    thread = db.get_thread_for("find-test-2@example.com")
    assert len(thread) == 2  # Should find the full thread
    assert any(e.message_id == "find-test-1@example.com" for e in thread)


def test_search(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    mock_ec = MagicMock()
    mock_ec.embed.return_value = [0.1] * 768
    db = MailDB._from_pool(test_pool, embedding_client=mock_ec)
    results = db.search("budget discussion")
    assert len(results) > 0
    assert isinstance(results[0], SearchResult)
    assert results[0].similarity > 0


def test_search_with_filters(test_pool, seed_emails) -> None:  # type: ignore[no-untyped-def]
    mock_ec = MagicMock()
    mock_ec.embed.return_value = [0.1] * 768
    db = MailDB._from_pool(test_pool, embedding_client=mock_ec)
    results = db.search("budget", sender_domain="example.com")
    assert all(r.email.sender_domain == "example.com" for r in results)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_maildb.py -v`
Expected: FAIL

- [ ] **Step 3: Implement maildb.py (core methods)**

```python
# src/maildb/maildb.py
from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from maildb.config import Settings
from maildb.db import create_pool, init_db
from maildb.embeddings import EmbeddingClient
from maildb.models import Email, SearchResult

logger = structlog.get_logger()

VALID_ORDERS = {
    "date DESC",
    "date ASC",
    "sender_address ASC",
    "sender_address DESC",
}

SELECT_COLS = """
    id, message_id, thread_id, subject, sender_name, sender_address,
    sender_domain, recipients, date, body_text, body_html, has_attachment,
    attachments, labels, in_reply_to, "references", embedding, created_at
"""


class MailDB:
    """Primary public interface for querying the email database."""

    def __init__(self, config: Settings | None = None) -> None:
        self._config = config or Settings()
        self._pool = create_pool(self._config)
        self._embedding_client = EmbeddingClient(
            ollama_url=self._config.ollama_url,
            model_name=self._config.embedding_model,
            dimensions=self._config.embedding_dimensions,
        )

    @classmethod
    def _from_pool(
        cls,
        pool: ConnectionPool,
        config: Settings | None = None,
        embedding_client: EmbeddingClient | None = None,
    ) -> MailDB:
        """Create a MailDB instance from an existing pool (for testing)."""
        instance = object.__new__(cls)
        instance._config = config or Settings(_env_file=None)  # type: ignore[call-arg]
        instance._pool = pool
        instance._embedding_client = embedding_client or EmbeddingClient(
            ollama_url=instance._config.ollama_url,
            model_name=instance._config.embedding_model,
            dimensions=instance._config.embedding_dimensions,
        )
        return instance

    def init_db(self) -> None:
        """Initialize the database schema."""
        init_db(self._pool)

    def close(self) -> None:
        """Close the connection pool."""
        self._pool.close()

    def __enter__(self) -> MailDB:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

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
        order: str = "date DESC",
    ) -> list[Email]:
        """Structured query with dynamic WHERE clauses."""
        if order not in VALID_ORDERS:
            msg = f"Invalid order '{order}'. Must be one of: {', '.join(sorted(VALID_ORDERS))}"
            raise ValueError(msg)

        conditions: list[str] = []
        params: dict[str, Any] = {}

        if sender is not None:
            conditions.append("sender_address = %(sender)s")
            params["sender"] = sender
        if sender_domain is not None:
            conditions.append("sender_domain = %(sender_domain)s")
            params["sender_domain"] = sender_domain
        if recipient is not None:
            conditions.append(
                "(recipients->'to' @> %(recipient_json)s "
                "OR recipients->'cc' @> %(recipient_json)s "
                "OR recipients->'bcc' @> %(recipient_json)s)"
            )
            params["recipient_json"] = f'["{recipient}"]'
        if after is not None:
            conditions.append("date >= %(after)s")
            params["after"] = after
        if before is not None:
            conditions.append("date < %(before)s")
            params["before"] = before
        if has_attachment is not None:
            conditions.append("has_attachment = %(has_attachment)s")
            params["has_attachment"] = has_attachment
        if subject_contains is not None:
            conditions.append("subject ILIKE %(subject_pattern)s")
            params["subject_pattern"] = f"%{subject_contains}%"
        if labels is not None:
            conditions.append("labels @> %(labels)s")
            params["labels"] = labels

        where = " AND ".join(conditions) if conditions else "TRUE"
        query = f"SELECT {SELECT_COLS} FROM emails WHERE {where} ORDER BY {order} LIMIT %(limit)s"
        params["limit"] = limit

        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute(query, params)
            rows = cur.fetchall()

        return [Email.from_row(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        has_attachment: bool | None = None,
        subject_contains: str | None = None,
        labels: list[str] | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        """Semantic search with optional structured filters."""
        query_embedding = self._embedding_client.embed(query)

        conditions: list[str] = ["embedding IS NOT NULL"]
        params: dict[str, Any] = {"query_embedding": str(query_embedding)}

        if sender is not None:
            conditions.append("sender_address = %(sender)s")
            params["sender"] = sender
        if sender_domain is not None:
            conditions.append("sender_domain = %(sender_domain)s")
            params["sender_domain"] = sender_domain
        if recipient is not None:
            conditions.append(
                "(recipients->'to' @> %(recipient_json)s "
                "OR recipients->'cc' @> %(recipient_json)s "
                "OR recipients->'bcc' @> %(recipient_json)s)"
            )
            params["recipient_json"] = f'["{recipient}"]'
        if after is not None:
            conditions.append("date >= %(after)s")
            params["after"] = after
        if before is not None:
            conditions.append("date < %(before)s")
            params["before"] = before
        if has_attachment is not None:
            conditions.append("has_attachment = %(has_attachment)s")
            params["has_attachment"] = has_attachment
        if subject_contains is not None:
            conditions.append("subject ILIKE %(subject_pattern)s")
            params["subject_pattern"] = f"%{subject_contains}%"
        if labels is not None:
            conditions.append("labels @> %(labels)s")
            params["labels"] = labels

        where = " AND ".join(conditions)
        sql = f"""
            SELECT {SELECT_COLS},
                   1 - (embedding <=> %(query_embedding)s::vector) AS similarity
            FROM emails
            WHERE {where}
            ORDER BY embedding <=> %(query_embedding)s::vector
            LIMIT %(limit)s
        """
        params["limit"] = limit

        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute(sql, params)
            rows = cur.fetchall()

        return [
            SearchResult(
                email=Email.from_row(row),
                similarity=row["similarity"],
            )
            for row in rows
        ]

    def get_thread(self, thread_id: str) -> list[Email]:
        """Retrieve all messages in a thread, ordered by date."""
        sql = f"""
            SELECT {SELECT_COLS}
            FROM emails
            WHERE thread_id = %(thread_id)s
            ORDER BY date ASC
        """
        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute(sql, {"thread_id": thread_id})
            rows = cur.fetchall()
        return [Email.from_row(row) for row in rows]

    def get_thread_for(self, message_id: str) -> list[Email]:
        """Find the thread containing a specific message and return the full thread."""
        sql = """SELECT thread_id FROM emails WHERE message_id = %(message_id)s"""
        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute(sql, {"message_id": message_id})
            row = cur.fetchone()
        if row is None:
            return []
        return self.get_thread(row["thread_id"])
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/integration/test_maildb.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/maildb.py tests/integration/test_maildb.py
git commit -m "feat: add MailDB class with find, search, get_thread, get_thread_for"
```

---

## Task 10: MailDB Advanced Query Methods

**Files:**
- Modify: `src/maildb/maildb.py` (add methods)
- Modify: `tests/integration/test_maildb.py` (add tests)

- [ ] **Step 1: Write the integration tests for advanced methods**

Add these tests to `tests/integration/test_maildb.py`:

```python
# Additional seed data for advanced queries
@pytest.fixture
def seed_advanced(test_pool):  # type: ignore[no-untyped-def]
    """Seed data for advanced query tests. Includes user_email=alice@example.com as context."""
    emails = [
        # Alice sends to Bob
        {
            "message_id": "adv-1@example.com",
            "thread_id": "adv-1@example.com",
            "subject": "Project Alpha",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["bob@corp.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 10, 10, 0, tzinfo=UTC),
            "body_text": "Let's start project alpha.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["Sent"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.9, 0.1] + [0.0] * 766,
        },
        # Bob replies to Alice (inbound)
        {
            "message_id": "adv-2@corp.com",
            "thread_id": "adv-1@example.com",
            "subject": "Re: Project Alpha",
            "sender_name": "Bob",
            "sender_address": "bob@corp.com",
            "sender_domain": "corp.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 11, 10, 0, tzinfo=UTC),
            "body_text": "Great, let's do it.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": "adv-1@example.com",
            "references": ["adv-1@example.com"],
            "embedding": [0.1, 0.9] + [0.0] * 766,
        },
        # Bob sends another message (unreplied by Alice)
        {
            "message_id": "adv-3@corp.com",
            "thread_id": "adv-3@corp.com",
            "subject": "Need help",
            "sender_name": "Bob",
            "sender_address": "bob@corp.com",
            "sender_domain": "corp.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
            "body_text": "Can you help me with this?",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.5, 0.5] + [0.0] * 766,
        },
        # Carol sends to Alice (different domain, inbound)
        {
            "message_id": "adv-4@other.com",
            "thread_id": "adv-4@other.com",
            "subject": "Meeting invite",
            "sender_name": "Carol",
            "sender_address": "carol@other.com",
            "sender_domain": "other.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 20, 10, 0, tzinfo=UTC),
            "body_text": "Let's meet next week.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": None,
            "references": [],
            "embedding": [0.3, 0.7] + [0.0] * 766,
        },
    ]

    insert_sql = """
    INSERT INTO emails (
        message_id, thread_id, subject, sender_name, sender_address, sender_domain,
        recipients, date, body_text, body_html, has_attachment, attachments,
        labels, in_reply_to, "references", embedding
    ) VALUES (
        %(message_id)s, %(thread_id)s, %(subject)s, %(sender_name)s, %(sender_address)s,
        %(sender_domain)s, %(recipients)s, %(date)s, %(body_text)s, %(body_html)s,
        %(has_attachment)s, %(attachments)s, %(labels)s, %(in_reply_to)s,
        %(references)s, %(embedding)s
    )
    """

    with test_pool.connection() as conn:
        for e in emails:
            conn.execute(insert_sql, e)
        conn.commit()


def test_top_contacts_inbound(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(limit=5, direction="inbound")
    # Bob sent 2 messages to Alice, Carol sent 1
    assert len(contacts) >= 2
    assert contacts[0]["address"] == "bob@corp.com"
    assert contacts[0]["count"] == 2


def test_top_contacts_outbound(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(limit=5, direction="outbound")
    assert len(contacts) >= 1
    assert contacts[0]["address"] == "bob@corp.com"


def test_top_contacts_requires_user_email(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="user_email"):
        db.top_contacts()


def test_unreplied(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    unreplied = db.unreplied()
    # adv-3 and adv-4 are unreplied inbound messages
    message_ids = [e.message_id for e in unreplied]
    assert "adv-3@corp.com" in message_ids
    assert "adv-4@other.com" in message_ids
    # adv-2 is replied (Alice sent adv-1, Bob replied with adv-2, but adv-2 is inbound and in thread with Alice's message)


def test_unreplied_requires_user_email(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="user_email"):
        db.unreplied()


def test_long_threads(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    threads = db.long_threads(min_messages=2)
    assert len(threads) >= 1
    assert threads[0]["thread_id"] == "adv-1@example.com"
    assert threads[0]["message_count"] >= 2


def test_topics_with_sender(test_pool, seed_advanced) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    topics = db.topics_with(sender="bob@corp.com", limit=5)
    assert len(topics) >= 1
    assert all(e.sender_address == "bob@corp.com" for e in topics)
```

- [ ] **Step 2: Run to verify new tests fail**

Run: `uv run pytest tests/integration/test_maildb.py -v -k "top_contacts or unreplied or long_threads or topics_with"`
Expected: FAIL — `AttributeError: 'MailDB' object has no attribute 'top_contacts'`

- [ ] **Step 3: Add advanced methods to maildb.py**

Add these methods to the `MailDB` class in `src/maildb/maildb.py`:

```python
    def _require_user_email(self) -> str:
        if not self._config.user_email:
            msg = "user_email must be set in config for this method"
            raise ValueError(msg)
        return self._config.user_email

    def top_contacts(
        self,
        *,
        period: str | None = None,
        limit: int = 10,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """Most frequent correspondents via GROUP BY aggregation."""
        user_email = self._require_user_email()

        conditions: list[str] = []
        params: dict[str, Any] = {"user_email": user_email, "limit": limit}

        if direction == "inbound":
            conditions.append("sender_address != %(user_email)s")
            group_col = "sender_address"
        elif direction == "outbound":
            conditions.append("sender_address = %(user_email)s")
            # For outbound, group by recipient — need a different query
            if period:
                period_cond = "AND date >= %(period_start)s"
                params["period_start"] = period
            else:
                period_cond = ""

            sql = f"""
                SELECT r.addr AS address, count(*) AS count
                FROM emails,
                     LATERAL (
                         SELECT jsonb_array_elements_text(recipients->'to') AS addr
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'cc')
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'bcc')
                     ) AS r(addr)
                WHERE sender_address = %(user_email)s
                  AND r.addr != %(user_email)s
                  {period_cond}
                GROUP BY r.addr
                ORDER BY count DESC
                LIMIT %(limit)s
            """
            with self._pool.connection() as conn:
                conn.row_factory = dict_row
                cur = conn.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        else:  # both
            conditions.append("sender_address != %(user_email)s")
            group_col = "sender_address"

        if period:
            conditions.append("date >= %(period_start)s")
            params["period_start"] = period

        where = " AND ".join(conditions) if conditions else "TRUE"
        sql = f"""
            SELECT {group_col} AS address, count(*) AS count
            FROM emails
            WHERE {where}
            GROUP BY {group_col}
            ORDER BY count DESC
            LIMIT %(limit)s
        """

        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def topics_with(
        self,
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        limit: int = 5,
    ) -> list[Email]:
        """Representative emails spanning different topics with a contact.

        Uses greedy farthest-point selection on embeddings.
        """
        conditions: list[str] = ["embedding IS NOT NULL"]
        params: dict[str, Any] = {}

        if sender:
            conditions.append("sender_address = %(sender)s")
            params["sender"] = sender
        elif sender_domain:
            conditions.append("sender_domain = %(sender_domain)s")
            params["sender_domain"] = sender_domain
        else:
            msg = "Either sender or sender_domain must be provided"
            raise ValueError(msg)

        where = " AND ".join(conditions)
        sql = f"SELECT {SELECT_COLS} FROM emails WHERE {where} ORDER BY date DESC"

        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute(sql, params)
            rows = cur.fetchall()

        if not rows:
            return []

        emails = [Email.from_row(row) for row in rows]

        # Greedy farthest-point selection
        if len(emails) <= limit:
            return emails

        selected: list[Email] = [emails[0]]
        remaining = list(emails[1:])

        while len(selected) < limit and remaining:
            best_idx = -1
            best_dist = -1.0

            for i, candidate in enumerate(remaining):
                if candidate.embedding is None:
                    continue
                # Min distance to any already-selected email
                min_dist = float("inf")
                for sel in selected:
                    if sel.embedding is None:
                        continue
                    dist = self._cosine_distance(candidate.embedding, sel.embedding)
                    min_dist = min(min_dist, dist)
                if min_dist > best_dist:
                    best_dist = min_dist
                    best_idx = i

            if best_idx < 0:
                break
            selected.append(remaining.pop(best_idx))

        return selected

    @staticmethod
    def _cosine_distance(a: list[float], b: list[float]) -> float:
        """Compute cosine distance between two vectors."""
        import math

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 1.0
        return 1.0 - dot / (norm_a * norm_b)

    def unreplied(
        self,
        *,
        after: str | None = None,
        before: str | None = None,
        sender: str | None = None,
        sender_domain: str | None = None,
    ) -> list[Email]:
        """Inbound messages with no outbound reply in the same thread."""
        user_email = self._require_user_email()

        conditions: list[str] = [
            "e.sender_address != %(user_email)s",
        ]
        params: dict[str, Any] = {"user_email": user_email}

        if after:
            conditions.append("e.date >= %(after)s")
            params["after"] = after
        if before:
            conditions.append("e.date < %(before)s")
            params["before"] = before
        if sender:
            conditions.append("e.sender_address = %(sender)s")
            params["sender"] = sender
        if sender_domain:
            conditions.append("e.sender_domain = %(sender_domain)s")
            params["sender_domain"] = sender_domain

        where = " AND ".join(conditions)

        select_cols_aliased = """
            e.id, e.message_id, e.thread_id, e.subject, e.sender_name, e.sender_address,
            e.sender_domain, e.recipients, e.date, e.body_text, e.body_html, e.has_attachment,
            e.attachments, e.labels, e.in_reply_to, e."references", e.embedding, e.created_at
        """

        sql = f"""
            SELECT {select_cols_aliased}
            FROM emails e
            WHERE {where}
              AND NOT EXISTS (
                  SELECT 1 FROM emails reply
                  WHERE reply.thread_id = e.thread_id
                    AND reply.sender_address = %(user_email)s
                    AND reply.date > e.date
              )
            ORDER BY e.date DESC
        """

        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
        return [Email.from_row(row) for row in rows]

    def long_threads(
        self,
        *,
        min_messages: int = 5,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        """Threads exceeding a message count threshold."""
        conditions: list[str] = []
        params: dict[str, Any] = {"min_messages": min_messages}

        if after:
            conditions.append("date >= %(after)s")
            params["after"] = after

        where = " AND ".join(conditions) if conditions else "TRUE"

        sql = f"""
            SELECT thread_id,
                   count(*) AS message_count,
                   min(date) AS first_date,
                   max(date) AS last_date,
                   array_agg(DISTINCT sender_address) AS participants
            FROM emails
            WHERE {where}
            GROUP BY thread_id
            HAVING count(*) >= %(min_messages)s
            ORDER BY count(*) DESC
        """

        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
```

- [ ] **Step 4: Run to verify tests pass**

Run: `uv run pytest tests/integration/test_maildb.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/maildb.py tests/integration/test_maildb.py
git commit -m "feat: add advanced query methods (top_contacts, topics_with, unreplied, long_threads)"
```

---

## Task 11: Public API & Package Finalization

**Files:**
- Modify: `src/maildb/__init__.py`

- [ ] **Step 1: Update __init__.py with public exports**

```python
# src/maildb/__init__.py
from __future__ import annotations

from maildb.maildb import MailDB
from maildb.models import Attachment, Email, Recipients, SearchResult

__all__ = ["MailDB", "Email", "SearchResult", "Recipients", "Attachment"]
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass.

- [ ] **Step 3: Run linting and type checking**

```bash
uv run ruff format .
uv run ruff check .
uv run mypy src/
```

Fix any issues.

- [ ] **Step 4: Commit**

```bash
git add src/maildb/__init__.py
git commit -m "feat: export public API from maildb package"
```

---

## Task 12: Full Check & Cleanup

- [ ] **Step 1: Run the full check**

```bash
uv run just check
```

This runs `fmt`, `lint`, and `test`. All must pass.

- [ ] **Step 2: Fix any issues found**

Address lint errors, type errors, or test failures.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore: pass full check (fmt, lint, test)"
```
