# Scale Ingest & Attachment Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-pass ingest pipeline with a 4-phase parallel pipeline (split → parse → index → embed) that handles 45.5GB mbox files with content-addressed attachment extraction.

**Architecture:** PostgreSQL `ingest_tasks` table coordinates work. Pre-split mbox into ~50MB chunks. `ProcessPoolExecutor` for parallel parse workers. `SKIP LOCKED` for parallel embed workers. Deferred indexing for bulk-build performance. Content-addressed (SHA-256) attachment storage with deduplication.

**Tech Stack:** Python 3.12+, psycopg3, psycopg_pool, pgvector, Ollama, pydantic-settings, structlog, pytest, uv, just

**Spec:** `docs/superpowers/specs/2026-03-23-scale-ingest-design.md`

---

## File Map

| File | Responsibility |
|------|---------------|
| `src/maildb/schema_tables.sql` | Table DDL with unique constraints only (split from schema.sql) |
| `src/maildb/schema_indexes.sql` | All non-unique indexes (applied by index phase) |
| `src/maildb/config.py` | Add new ingest/attachment config fields |
| `src/maildb/db.py` | Update `init_db()` to use `schema_tables.sql` |
| `src/maildb/parsing.py` | Modify `_extract_attachments()` to return bytes, add Gmail labels extraction |
| `src/maildb/ingest/__init__.py` | Package init, re-export public API |
| `src/maildb/ingest/tasks.py` | Task table operations: claim, complete, fail, query status |
| `src/maildb/ingest/split.py` | Phase 1: binary mbox splitter |
| `src/maildb/ingest/attachments.py` | Content-addressed attachment storage (hash, write, dedupe) |
| `src/maildb/ingest/parse.py` | Phase 2: parse worker (chunk → DB rows + attachment files) |
| `src/maildb/ingest/index.py` | Phase 3: drop/create indexes |
| `src/maildb/ingest/embed.py` | Phase 4: SKIP LOCKED embedding workers |
| `src/maildb/ingest/orchestrator.py` | Pipeline coordination and phase transitions |
| `src/maildb/ingest/__main__.py` | CLI entry point (`python -m maildb.ingest`) |
| `src/maildb/maildb.py` | Add limit to `unreplied()`, guard on `topics_with()` |
| `src/maildb/server.py` | Add `limit` param to `unreplied` tool |
| `tests/unit/test_split.py` | Split phase tests |
| `tests/unit/test_attachments.py` | Attachment storage tests |
| `tests/integration/test_tasks.py` | Task table operation tests (requires DB) |
| `tests/unit/test_parsing.py` | Updated tests for attachment bytes + Gmail labels |
| `tests/integration/test_parse_worker.py` | Parse phase integration tests |
| `tests/integration/test_embed_worker.py` | Embed phase integration tests |
| `tests/integration/test_orchestrator.py` | End-to-end pipeline test |
| `tests/integration/test_ingest.py` | Remove (replaced by new tests) |

---

## Task 0: Schema Split and Config Updates

**Purpose:** Split `schema.sql` into tables and indexes, add new tables and config fields. Foundation for everything else.

**Files:**
- Create: `src/maildb/schema_tables.sql`
- Create: `src/maildb/schema_indexes.sql`
- Modify: `src/maildb/config.py`
- Modify: `src/maildb/db.py`
- Delete: `src/maildb/schema.sql`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Create `schema_tables.sql`**

Copy the existing `schema.sql` content but keep ONLY:
- `CREATE EXTENSION IF NOT EXISTS vector`
- `CREATE TABLE IF NOT EXISTS emails (...)` with the `UNIQUE` constraint on `message_id` as a table constraint
- New `CREATE TABLE IF NOT EXISTS ingest_tasks (...)`
- New `CREATE TABLE IF NOT EXISTS attachments (...)`
- New `CREATE TABLE IF NOT EXISTS email_attachments (...)`

Remove all `CREATE INDEX` statements. Keep the unique index on `message_id` as the only index (needed for `ON CONFLICT`).

```sql
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
    created_at            TIMESTAMPTZ DEFAULT now()
);

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

- [ ] **Step 2: Create `schema_indexes.sql`**

All non-unique indexes from the original `schema.sql` plus new ones from the spec:

```sql
CREATE INDEX IF NOT EXISTS idx_email_sender_address ON emails (sender_address);
CREATE INDEX IF NOT EXISTS idx_email_sender_domain ON emails (sender_domain);
CREATE INDEX IF NOT EXISTS idx_email_date ON emails (date);
CREATE INDEX IF NOT EXISTS idx_email_thread_id ON emails (thread_id);
CREATE INDEX IF NOT EXISTS idx_email_in_reply_to ON emails (in_reply_to);
CREATE INDEX IF NOT EXISTS idx_email_has_attachment ON emails (has_attachment) WHERE has_attachment = TRUE;
CREATE INDEX IF NOT EXISTS idx_email_labels ON emails USING GIN (labels);
CREATE INDEX IF NOT EXISTS idx_email_recipients ON emails USING GIN (recipients);
CREATE INDEX IF NOT EXISTS idx_email_thread_sender_date ON emails (thread_id, sender_address, date);
CREATE INDEX IF NOT EXISTS idx_email_attachments_email_id ON email_attachments (email_id);
CREATE INDEX IF NOT EXISTS idx_email_attachments_attachment_id ON email_attachments (attachment_id);
-- HNSW index created separately after embed phase:
-- CREATE INDEX IF NOT EXISTS idx_email_embedding ON emails USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
```

- [ ] **Step 3: Delete `schema.sql`**

```bash
rm src/maildb/schema.sql
```

- [ ] **Step 4: Update `db.py` to use `schema_tables.sql`**

Change `init_db()` to read `schema_tables.sql` instead of `schema.sql`. Add a new `create_indexes()` function that reads `schema_indexes.sql`.

```python
def init_db(pool: ConnectionPool) -> None:
    """Apply idempotent table DDL from schema_tables.sql."""
    schema_sql = importlib.resources.files("maildb").joinpath("schema_tables.sql").read_text()
    with pool.connection() as conn:
        conn.execute(schema_sql)
        conn.commit()
    logger.info("database_initialized")


def create_indexes(pool: ConnectionPool) -> None:
    """Apply all non-unique indexes from schema_indexes.sql."""
    index_sql = importlib.resources.files("maildb").joinpath("schema_indexes.sql").read_text()
    with pool.connection() as conn:
        conn.execute(index_sql)
        conn.commit()
    logger.info("indexes_created")
```

- [ ] **Step 5: Add new config fields to `config.py`**

```python
class Settings(BaseSettings):
    model_config = {"env_prefix": "MAILDB_", "env_file": ".env", "env_file_encoding": "utf-8"}

    database_url: str = "postgresql://localhost:5432/maildb"
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768
    user_email: str | None = None
    attachment_dir: str = "./attachments"
    ingest_chunk_size_mb: int = 50
    ingest_tmp_dir: str = "./ingest_tmp"
    ingest_workers: int = -1
    embed_workers: int = 4
    embed_batch_size: int = 50
```

- [ ] **Step 6: Update `conftest.py` to clean new tables**

Add cleanup for the new tables in the `_clean_emails` fixture:

```python
@pytest.fixture(autouse=True)
def _clean_emails(test_pool, request) -> Iterator[None]:
    """Delete all rows after each integration test to prevent test pollution."""
    if "integration" not in [m.name for m in request.node.iter_markers()]:
        yield
        return
    yield
    with test_pool.connection() as conn:
        conn.execute("DELETE FROM email_attachments")
        conn.execute("DELETE FROM attachments")
        conn.execute("DELETE FROM ingest_tasks")
        conn.execute("DELETE FROM emails")
        conn.commit()
```

- [ ] **Step 7: Run tests to verify nothing breaks**

Run: `uv run just check`

Expected: All existing tests pass. The schema change is backward-compatible (tables still exist, indexes still get created via `init_db()` in tests — update test conftest to also call `create_indexes()` after `init_db()` so integration tests have indexes).

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "refactor: split schema into tables and indexes, add new config fields"
```

---

## Task 1: Parsing Updates (Attachment Bytes + Gmail Labels)

**Purpose:** Modify the parser to return attachment bytes and extract Gmail labels. Required before the parse phase can work.

**Files:**
- Modify: `src/maildb/parsing.py`
- Modify: `tests/unit/test_parsing.py`

- [ ] **Step 1: Write test for attachment bytes extraction**

Add to `tests/unit/test_parsing.py`:

```python
def test_extract_attachments_includes_bytes(sample_mbox_msg_with_attachment):
    """_extract_attachments should return raw bytes in a 'data' key."""
    attachments = _extract_attachments(sample_mbox_msg_with_attachment)
    assert len(attachments) > 0
    for att in attachments:
        assert "data" in att
        assert isinstance(att["data"], bytes)
        assert len(att["data"]) == att["size"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_parsing.py::test_extract_attachments_includes_bytes -v`

Expected: FAIL — current `_extract_attachments` does not include `data` key.

- [ ] **Step 3: Modify `_extract_attachments()` to return bytes**

In `src/maildb/parsing.py`, update `_extract_attachments()`:

```python
def _extract_attachments(msg: mailbox.mboxMessage) -> list[dict[str, Any]]:
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
        data = payload if payload else b""
        attachments.append(
            {
                "filename": filename,
                "content_type": content_type,
                "size": len(data),
                "data": data,
            }
        )
    return attachments
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_parsing.py::test_extract_attachments_includes_bytes -v`

Expected: PASS

- [ ] **Step 5: Write test for Gmail labels extraction**

```python
def test_parse_message_extracts_gmail_labels(make_mbox_message):
    """parse_message should extract X-Gmail-Labels header."""
    msg = make_mbox_message(headers={"X-Gmail-Labels": "Inbox,Important,Starred"})
    result = parse_message(msg)
    assert result is not None
    assert result["labels"] == ["Inbox", "Important", "Starred"]
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_parsing.py::test_parse_message_extracts_gmail_labels -v`

Expected: FAIL — labels currently hardcoded to `[]`.

- [ ] **Step 7: Add Gmail labels extraction to `parse_message()`**

In `parse_message()`, replace `"labels": []` with:

```python
    gmail_labels_raw = msg.get("X-Gmail-Labels")
    labels = [l.strip() for l in gmail_labels_raw.split(",") if l.strip()] if gmail_labels_raw else []
```

And use `"labels": labels` in the return dict.

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_parsing.py -v`

Expected: All parsing tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/maildb/parsing.py tests/unit/test_parsing.py
git commit -m "feat: extract attachment bytes and Gmail labels in parser"
```

---

## Task 2: Ingest Task Table Operations

**Purpose:** Build the task table CRUD layer used by all phases. Pure DB operations, no pipeline logic.

**Files:**
- Create: `src/maildb/ingest/__init__.py`
- Create: `src/maildb/ingest/tasks.py`
- Create: `tests/integration/test_tasks.py`

- [ ] **Step 1: Delete old `ingest.py` and create package directory**

Python cannot have both `ingest.py` (module) and `ingest/` (package) in the same directory. The old module must be deleted first.

```bash
rm src/maildb/ingest.py
rm tests/integration/test_ingest.py
mkdir -p src/maildb/ingest
touch src/maildb/ingest/__init__.py
```

- [ ] **Step 2: Write tests for task operations**

Create `tests/integration/test_tasks.py`:

```python
import pytest
from maildb.ingest.tasks import create_task, claim_task, complete_task, fail_task, get_phase_status, reset_failed_tasks

pytestmark = pytest.mark.integration


def test_create_task(test_pool):
    task = create_task(test_pool, phase="parse", chunk_path="/tmp/chunk_001.mbox")
    assert task["id"] is not None
    assert task["phase"] == "parse"
    assert task["status"] == "pending"
    assert task["chunk_path"] == "/tmp/chunk_001.mbox"


def test_claim_task(test_pool):
    create_task(test_pool, phase="parse", chunk_path="/tmp/chunk_001.mbox")
    claimed = claim_task(test_pool, phase="parse", worker_id="worker-1")
    assert claimed is not None
    assert claimed["status"] == "in_progress"
    assert claimed["worker_id"] == "worker-1"


def test_claim_task_skip_locked(test_pool):
    """Two claims on same phase should get different tasks."""
    create_task(test_pool, phase="parse", chunk_path="/tmp/chunk_001.mbox")
    create_task(test_pool, phase="parse", chunk_path="/tmp/chunk_002.mbox")
    t1 = claim_task(test_pool, phase="parse", worker_id="w1")
    t2 = claim_task(test_pool, phase="parse", worker_id="w2")
    assert t1["id"] != t2["id"]


def test_claim_task_returns_none_when_empty(test_pool):
    claimed = claim_task(test_pool, phase="parse", worker_id="w1")
    assert claimed is None


def test_complete_task(test_pool):
    task = create_task(test_pool, phase="parse", chunk_path="/tmp/chunk.mbox")
    claim_task(test_pool, phase="parse", worker_id="w1")
    complete_task(test_pool, task["id"], messages_total=100, messages_inserted=95, messages_skipped=5, attachments_extracted=10)
    status = get_phase_status(test_pool, "parse")
    assert status["completed"] == 1


def test_fail_task(test_pool):
    task = create_task(test_pool, phase="parse", chunk_path="/tmp/chunk.mbox")
    claim_task(test_pool, phase="parse", worker_id="w1")
    fail_task(test_pool, task["id"], error="something broke")
    status = get_phase_status(test_pool, "parse")
    assert status["failed"] == 1


def test_reset_failed_tasks(test_pool):
    task = create_task(test_pool, phase="parse", chunk_path="/tmp/chunk.mbox")
    claim_task(test_pool, phase="parse", worker_id="w1")
    fail_task(test_pool, task["id"], error="oops")
    count = reset_failed_tasks(test_pool, phase="parse", max_retries=3)
    assert count == 1
    status = get_phase_status(test_pool, "parse")
    assert status["pending"] == 1
    assert status["failed"] == 0


def test_reset_failed_tasks_skips_permanently_failed(test_pool):
    task = create_task(test_pool, phase="parse", chunk_path="/tmp/chunk.mbox")
    claim_task(test_pool, phase="parse", worker_id="w1")
    # Fail 3 times to exceed max_retries
    for _ in range(3):
        fail_task(test_pool, task["id"], error="oops")
    count = reset_failed_tasks(test_pool, phase="parse", max_retries=3)
    assert count == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_tasks.py -v`

Expected: FAIL — module doesn't exist yet.

- [ ] **Step 4: Implement `tasks.py`**

Create `src/maildb/ingest/tasks.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool


def create_task(
    pool: ConnectionPool,
    *,
    phase: str,
    chunk_path: str | None = None,
) -> dict[str, Any]:
    """Insert a new task row and return it."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """INSERT INTO ingest_tasks (phase, chunk_path)
               VALUES (%(phase)s, %(chunk_path)s)
               RETURNING *""",
            {"phase": phase, "chunk_path": chunk_path},
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row)  # type: ignore[arg-type]


def claim_task(
    pool: ConnectionPool,
    *,
    phase: str,
    worker_id: str,
) -> dict[str, Any] | None:
    """Atomically claim the next pending task for a phase. Returns None if no work."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """UPDATE ingest_tasks
               SET status = 'in_progress', worker_id = %(worker_id)s, started_at = now()
               WHERE id = (
                   SELECT id FROM ingest_tasks
                   WHERE phase = %(phase)s AND status = 'pending'
                   LIMIT 1
                   FOR UPDATE SKIP LOCKED
               )
               RETURNING *""",
            {"phase": phase, "worker_id": worker_id},
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None


def complete_task(
    pool: ConnectionPool,
    task_id: int,
    *,
    messages_total: int = 0,
    messages_inserted: int = 0,
    messages_skipped: int = 0,
    attachments_extracted: int = 0,
) -> None:
    """Mark a task as completed with stats."""
    with pool.connection() as conn:
        conn.execute(
            """UPDATE ingest_tasks
               SET status = 'completed', completed_at = now(),
                   messages_total = %(messages_total)s,
                   messages_inserted = %(messages_inserted)s,
                   messages_skipped = %(messages_skipped)s,
                   attachments_extracted = %(attachments_extracted)s
               WHERE id = %(task_id)s""",
            {
                "task_id": task_id,
                "messages_total": messages_total,
                "messages_inserted": messages_inserted,
                "messages_skipped": messages_skipped,
                "attachments_extracted": attachments_extracted,
            },
        )
        conn.commit()


def fail_task(pool: ConnectionPool, task_id: int, *, error: str) -> None:
    """Mark a task as failed, increment retry count."""
    with pool.connection() as conn:
        conn.execute(
            """UPDATE ingest_tasks
               SET status = 'failed', error_message = %(error)s,
                   retry_count = retry_count + 1
               WHERE id = %(task_id)s""",
            {"task_id": task_id, "error": error},
        )
        conn.commit()


def reset_failed_tasks(pool: ConnectionPool, *, phase: str, max_retries: int = 3) -> int:
    """Reset failed tasks with retries remaining back to pending. Returns count."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE ingest_tasks
               SET status = 'pending', worker_id = NULL, error_message = NULL
               WHERE phase = %(phase)s AND status = 'failed' AND retry_count < %(max_retries)s""",
            {"phase": phase, "max_retries": max_retries},
        )
        conn.commit()
        return cur.rowcount


def get_phase_status(pool: ConnectionPool, phase: str) -> dict[str, int]:
    """Get counts by status for a phase."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT
                   count(*) FILTER (WHERE status = 'pending') AS pending,
                   count(*) FILTER (WHERE status = 'in_progress') AS in_progress,
                   count(*) FILTER (WHERE status = 'completed') AS completed,
                   count(*) FILTER (WHERE status = 'failed') AS failed,
                   count(*) AS total,
                   coalesce(sum(messages_total), 0) AS messages_total,
                   coalesce(sum(messages_inserted), 0) AS messages_inserted,
                   coalesce(sum(messages_skipped), 0) AS messages_skipped,
                   coalesce(sum(attachments_extracted), 0) AS attachments_extracted
               FROM ingest_tasks
               WHERE phase = %(phase)s""",
            {"phase": phase},
        )
        row = cur.fetchone()
        return dict(row)  # type: ignore[arg-type]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_tasks.py -v`

Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/maildb/ingest/ tests/integration/test_tasks.py
git commit -m "feat: add ingest task table CRUD operations"
```

---

## Task 3: Content-Addressed Attachment Storage

**Purpose:** Module for hashing, writing, and deduplicating attachment files on disk. No DB logic — just filesystem operations.

**Files:**
- Create: `src/maildb/ingest/attachments.py`
- Create: `tests/unit/test_attachments.py`

- [ ] **Step 1: Write tests for attachment storage**

Create `tests/unit/test_attachments.py`:

```python
from pathlib import Path
from maildb.ingest.attachments import hash_attachment, store_attachment, storage_path_for


def test_hash_attachment():
    data = b"hello world"
    h = hash_attachment(data)
    assert len(h) == 64  # SHA-256 hex
    assert h == hash_attachment(data)  # deterministic


def test_storage_path_for():
    h = "abcdef1234567890" + "0" * 48
    path = storage_path_for(h, "report.pdf")
    assert path == Path("ab/cd") / f"{h}.pdf"


def test_storage_path_for_no_extension():
    h = "abcdef1234567890" + "0" * 48
    path = storage_path_for(h, "README")
    assert path == Path("ab/cd") / f"{h}"


def test_store_attachment_writes_file(tmp_path):
    data = b"test content"
    h = hash_attachment(data)
    rel_path = store_attachment(data, h, "test.txt", base_dir=tmp_path)
    full_path = tmp_path / rel_path
    assert full_path.exists()
    assert full_path.read_bytes() == data


def test_store_attachment_deduplicates(tmp_path):
    data = b"same content"
    h = hash_attachment(data)
    path1 = store_attachment(data, h, "first.txt", base_dir=tmp_path)
    path2 = store_attachment(data, h, "second.txt", base_dir=tmp_path)
    assert path1 == path2  # same hash = same path
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_attachments.py -v`

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `attachments.py`**

Create `src/maildb/ingest/attachments.py`:

```python
from __future__ import annotations

import hashlib
from pathlib import Path


def hash_attachment(data: bytes) -> str:
    """Return SHA-256 hex digest of attachment bytes."""
    return hashlib.sha256(data).hexdigest()


def storage_path_for(sha256: str, filename: str) -> Path:
    """Compute the relative storage path for a content-addressed file."""
    ext = Path(filename).suffix  # includes the dot, or empty string
    prefix = Path(sha256[:2]) / sha256[2:4]
    return prefix / f"{sha256}{ext}"


def store_attachment(
    data: bytes,
    sha256: str,
    filename: str,
    *,
    base_dir: Path,
) -> Path:
    """Write attachment to disk if not already present. Returns relative path."""
    rel_path = storage_path_for(sha256, filename)
    full_path = base_dir / rel_path
    if not full_path.exists():
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)
    return rel_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_attachments.py -v`

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/ingest/attachments.py tests/unit/test_attachments.py
git commit -m "feat: add content-addressed attachment storage"
```

---

## Task 4: Mbox Splitter (Phase 1)

**Purpose:** Binary mbox file splitter that produces ~50MB chunk files and creates parse task rows.

**Files:**
- Create: `src/maildb/ingest/split.py`
- Create: `tests/unit/test_split.py`

- [ ] **Step 1: Write test for splitting a small mbox**

Create `tests/unit/test_split.py`:

```python
import mailbox
from pathlib import Path

from maildb.ingest.split import split_mbox


def _make_mbox(path: Path, count: int) -> Path:
    """Create a test mbox with `count` messages."""
    mbox = mailbox.mbox(str(path))
    for i in range(count):
        msg = mailbox.mboxMessage()
        msg.set_payload(f"Body of message {i}")
        msg["From"] = f"sender{i}@example.com"
        msg["Subject"] = f"Message {i}"
        msg["Message-ID"] = f"<msg-{i}@example.com>"
        mbox.add(msg)
    mbox.close()
    return path


def test_split_mbox_creates_chunks(tmp_path):
    mbox_path = _make_mbox(tmp_path / "test.mbox", count=20)
    output_dir = tmp_path / "chunks"
    chunks = split_mbox(mbox_path, output_dir=output_dir, chunk_size_bytes=500)
    assert len(chunks) > 1
    # Each chunk should be a valid mbox
    total_messages = 0
    for chunk_path in chunks:
        assert chunk_path.exists()
        mbox = mailbox.mbox(str(chunk_path))
        total_messages += len(mbox)
        mbox.close()
    assert total_messages == 20


def test_split_mbox_single_chunk(tmp_path):
    mbox_path = _make_mbox(tmp_path / "small.mbox", count=3)
    output_dir = tmp_path / "chunks"
    chunks = split_mbox(mbox_path, output_dir=output_dir, chunk_size_bytes=10_000_000)
    assert len(chunks) == 1


def test_split_mbox_cleans_output_dir(tmp_path):
    mbox_path = _make_mbox(tmp_path / "test.mbox", count=5)
    output_dir = tmp_path / "chunks"
    output_dir.mkdir()
    (output_dir / "stale_chunk.mbox").write_text("old data")
    split_mbox(mbox_path, output_dir=output_dir, chunk_size_bytes=500)
    assert not (output_dir / "stale_chunk.mbox").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_split.py -v`

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `split.py`**

Create `src/maildb/ingest/split.py`:

```python
from __future__ import annotations

import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger()

MBOX_FROM_PREFIX = b"From "


def split_mbox(
    mbox_path: Path | str,
    *,
    output_dir: Path | str,
    chunk_size_bytes: int = 50 * 1024 * 1024,
) -> list[Path]:
    """Split an mbox file into chunks of approximately chunk_size_bytes.

    Returns list of chunk file paths.
    """
    mbox_path = Path(mbox_path)
    output_dir = Path(output_dir)

    # Clean and recreate output directory
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    chunks: list[Path] = []
    chunk_idx = 0
    current_chunk: list[bytes] = []
    current_size = 0
    message_buffer: list[bytes] = []
    in_message = False

    with mbox_path.open("rb") as f:
        for line in f:
            if line.startswith(MBOX_FROM_PREFIX) and in_message:
                # End of previous message — flush it to current chunk
                msg_bytes = b"".join(message_buffer)
                current_chunk.append(msg_bytes)
                current_size += len(msg_bytes)
                message_buffer = []

                # Check if chunk is full
                if current_size >= chunk_size_bytes:
                    chunk_path = _write_chunk(output_dir, chunk_idx, current_chunk)
                    chunks.append(chunk_path)
                    chunk_idx += 1
                    current_chunk = []
                    current_size = 0

            message_buffer.append(line)
            in_message = True

    # Flush last message
    if message_buffer:
        current_chunk.append(b"".join(message_buffer))

    # Flush last chunk
    if current_chunk:
        chunk_path = _write_chunk(output_dir, chunk_idx, current_chunk)
        chunks.append(chunk_path)

    logger.info("split_complete", chunks=len(chunks), source=str(mbox_path))
    return chunks


def _write_chunk(output_dir: Path, idx: int, messages: list[bytes]) -> Path:
    chunk_path = output_dir / f"chunk_{idx:06d}.mbox"
    with chunk_path.open("wb") as f:
        for msg in messages:
            f.write(msg)
    return chunk_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_split.py -v`

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/ingest/split.py tests/unit/test_split.py
git commit -m "feat: add binary mbox splitter for Phase 1"
```

---

## Task 5: Parse Worker (Phase 2)

**Purpose:** The core parse-and-load worker that processes a single chunk: parses messages, extracts attachments to disk, and batch-inserts everything into the DB in one atomic transaction.

**Files:**
- Create: `src/maildb/ingest/parse.py`
- Create: `tests/integration/test_parse_worker.py`

- [ ] **Step 1: Write integration test for parse worker**

Create `tests/integration/test_parse_worker.py`:

```python
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from maildb.ingest.parse import process_chunk
from maildb.ingest.tasks import create_task, get_phase_status

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


def test_process_chunk_inserts_emails(test_pool, test_settings, tmp_path):
    task = create_task(test_pool, phase="parse", chunk_path=str(FIXTURES / "sample.mbox"))
    process_chunk(
        database_url=test_settings.database_url,
        task_id=task["id"],
        chunk_path=task["chunk_path"],
        attachment_dir=tmp_path / "attachments",
    )
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        count = cur.fetchone()[0]
    assert count > 0
    status = get_phase_status(test_pool, "parse")
    assert status["completed"] == 1


def test_process_chunk_handles_failure(test_pool, test_settings, tmp_path):
    task = create_task(test_pool, phase="parse", chunk_path="/nonexistent/path.mbox")
    process_chunk(
        database_url=test_settings.database_url,
        task_id=task["id"],
        chunk_path="/nonexistent/path.mbox",
        attachment_dir=tmp_path / "attachments",
    )
    status = get_phase_status(test_pool, "parse")
    assert status["failed"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_parse_worker.py -v`

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `parse.py`**

Create `src/maildb/ingest/parse.py`. This is the worker function that runs inside a `ProcessPoolExecutor` child process. It creates its own DB connection (cannot share across fork).

```python
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import structlog
from psycopg_pool import ConnectionPool

from maildb.ingest.attachments import hash_attachment, store_attachment
from maildb.ingest.tasks import claim_task, complete_task, fail_task
from maildb.parsing import parse_mbox

logger = structlog.get_logger()

INSERT_EMAIL_SQL = """
INSERT INTO emails (
    id, message_id, thread_id, subject, sender_name, sender_address, sender_domain,
    recipients, date, body_text, body_html, has_attachment, attachments,
    labels, in_reply_to, "references"
) VALUES (
    %(id)s, %(message_id)s, %(thread_id)s, %(subject)s, %(sender_name)s, %(sender_address)s,
    %(sender_domain)s, %(recipients)s, %(date)s, %(body_text)s, %(body_html)s,
    %(has_attachment)s, %(attachments)s, %(labels)s, %(in_reply_to)s, %(references)s
) ON CONFLICT (message_id) DO NOTHING
"""

INSERT_ATTACHMENT_SQL = """
INSERT INTO attachments (sha256, filename, content_type, size, storage_path)
VALUES (%(sha256)s, %(filename)s, %(content_type)s, %(size)s, %(storage_path)s)
ON CONFLICT (sha256) DO NOTHING
"""

INSERT_EMAIL_ATTACHMENT_SQL = """
INSERT INTO email_attachments (email_id, attachment_id, filename)
VALUES (%(email_id)s, %(attachment_id)s, %(filename)s)
ON CONFLICT DO NOTHING
"""


def process_chunk(
    *,
    database_url: str,
    attachment_dir: Path | str,
) -> int:
    """Claim and process chunks in a loop until no work remains. Returns chunks processed."""
    attachment_dir = Path(attachment_dir)
    pool = ConnectionPool(conninfo=database_url, min_size=1, max_size=1)
    worker_id = f"{os.getpid()}"
    chunks_processed = 0

    try:
        while True:
            # Claim the next task atomically with SKIP LOCKED
            claimed = claim_task(pool, phase="parse", worker_id=worker_id)
            if claimed is None:
                break  # No more work
            task_id = claimed["id"]
            chunk_path = claimed["chunk_path"]
            try:
                _process_single_chunk(pool, task_id, chunk_path, attachment_dir)
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


def _process_single_chunk(
    pool: ConnectionPool,
    task_id: int,
    chunk_path: str,
    attachment_dir: Path,
) -> None:
    """Process a single chunk file: parse, extract attachments, insert into DB."""
    messages = list(parse_mbox(chunk_path))
        email_rows = []
        attachment_meta = []  # (email_uuid, sha256, filename, content_type, size, storage_path)
        unique_hashes = {}  # sha256 -> (filename, content_type, size, storage_path)

        for msg in messages:
            email_id = uuid4()

            # Prepare email row
            att_metadata = [
                {"filename": a["filename"], "content_type": a["content_type"], "size": a["size"]}
                for a in msg.get("_attachments_with_data", [])
            ]

            email_rows.append({
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
                "attachments": json.dumps(att_metadata) if att_metadata else None,
                "labels": msg["labels"] or None,
                "in_reply_to": msg["in_reply_to"],
                "references": msg["references"] or None,
            })

            # Extract and store attachments
            for att in msg.get("_attachments_with_data", []):
                data = att["data"]
                sha = hash_attachment(data)
                rel_path = store_attachment(data, sha, att["filename"], base_dir=attachment_dir)

                if sha not in unique_hashes:
                    unique_hashes[sha] = {
                        "sha256": sha,
                        "filename": att["filename"],
                        "content_type": att["content_type"],
                        "size": att["size"],
                        "storage_path": str(rel_path),
                    }

                attachment_meta.append({
                    "email_id": email_id,
                    "sha256": sha,
                    "filename": att["filename"],
                })

        # Single atomic transaction
        inserted = 0
        skipped = 0
        with pool.connection() as conn:
            # Insert emails
            for row in email_rows:
                cur = conn.execute(INSERT_EMAIL_SQL, row)
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1

            # Insert unique attachments
            for att_row in unique_hashes.values():
                conn.execute(INSERT_ATTACHMENT_SQL, att_row)

            # Resolve attachment IDs and insert join table
            if attachment_meta:
                all_hashes = list({m["sha256"] for m in attachment_meta})
                cur = conn.execute(
                    "SELECT id, sha256 FROM attachments WHERE sha256 = ANY(%(hashes)s)",
                    {"hashes": all_hashes},
                )
                hash_to_id = {row[1]: row[0] for row in cur.fetchall()}

                for meta in attachment_meta:
                    att_id = hash_to_id.get(meta["sha256"])
                    if att_id:
                        conn.execute(INSERT_EMAIL_ATTACHMENT_SQL, {
                            "email_id": meta["email_id"],
                            "attachment_id": att_id,
                            "filename": meta["filename"],
                        })

            conn.commit()

    complete_task(
        pool, task_id,
        messages_total=len(messages),
        messages_inserted=inserted,
        messages_skipped=skipped,
        attachments_extracted=len(unique_hashes),
    )
    logger.info("chunk_processed", task_id=task_id, inserted=inserted, skipped=skipped)
```

**Note:** This requires `parse_mbox()` / `parse_message()` to include attachment data in the returned dict. The `_extract_attachments()` now returns dicts with a `data` key. The return dict from `parse_message()` must keep the JSONB-safe `"attachments"` list (metadata only, NO `data` bytes) separate from the raw data list.

- [ ] **Step 4: Update `parse_message()` to include `_attachments_with_data`**

In `src/maildb/parsing.py`, at the end of `parse_message()`, build two separate lists from the `_extract_attachments()` output. The `"attachments"` key must NOT contain the `data` bytes (it gets serialized to JSONB):

```python
    attachments_raw = _extract_attachments(msg)
    attachments_metadata = [
        {"filename": a["filename"], "content_type": a["content_type"], "size": a["size"]}
        for a in attachments_raw
    ]

    return {
        # ... existing fields ...
        "has_attachment": len(attachments_raw) > 0,
        "attachments": attachments_metadata,       # metadata only — safe for JSONB
        "_attachments_with_data": attachments_raw,  # includes 'data' bytes — for file extraction
        # ... rest of fields ...
    }
```

Add a test to verify `parse_message()["attachments"]` does NOT contain `data`:

```python
def test_parse_message_attachments_no_data_key(make_mbox_message_with_attachment):
    result = parse_message(make_mbox_message_with_attachment)
    assert result is not None
    for att in result["attachments"]:
        assert "data" not in att
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_parse_worker.py -v`

Expected: All PASS

- [ ] **Step 6: Run all tests**

Run: `uv run just check`

Expected: All tests pass, lint clean.

- [ ] **Step 7: Commit**

```bash
git add src/maildb/ingest/parse.py src/maildb/parsing.py tests/integration/test_parse_worker.py
git commit -m "feat: add parse worker for Phase 2 chunk processing"
```

---

## Task 6: Index Phase (Phase 3)

**Purpose:** Drop non-unique indexes and recreate them from `schema_indexes.sql`. Also handles deferred HNSW creation.

**Files:**
- Create: `src/maildb/ingest/index.py`
- Create: `tests/integration/test_index_phase.py`

- [ ] **Step 1: Write integration test**

Create `tests/integration/test_index_phase.py`:

```python
import pytest
from maildb.ingest.index import run_index_phase

pytestmark = pytest.mark.integration


def test_run_index_phase_creates_indexes(test_pool):
    """Index phase should create all non-unique indexes."""
    run_index_phase(test_pool)
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'emails' AND indexname LIKE 'idx_%'"
        )
        indexes = {row[0] for row in cur.fetchall()}
    assert "idx_email_sender_address" in indexes
    assert "idx_email_date" in indexes
    assert "idx_email_thread_sender_date" in indexes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_index_phase.py -v`

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `index.py`**

Create `src/maildb/ingest/index.py`:

```python
from __future__ import annotations

import importlib.resources
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

# Non-unique indexes to drop before bulk rebuild
DROP_INDEXES = [
    "idx_email_sender_address",
    "idx_email_sender_domain",
    "idx_email_date",
    "idx_email_thread_id",
    "idx_email_in_reply_to",
    "idx_email_has_attachment",
    "idx_email_labels",
    "idx_email_recipients",
    "idx_email_embedding",
    "idx_email_thread_sender_date",
    "idx_email_attachments_email_id",
    "idx_email_attachments_attachment_id",
]


def drop_non_unique_indexes(pool: ConnectionPool) -> None:
    """Drop all non-unique indexes to prepare for bulk rebuild."""
    with pool.connection() as conn:
        for idx_name in DROP_INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {idx_name}")
        conn.commit()
    logger.info("indexes_dropped", count=len(DROP_INDEXES))


def run_index_phase(pool: ConnectionPool, *, include_hnsw: bool = False) -> None:
    """Create all non-unique indexes from schema_indexes.sql."""
    index_sql = importlib.resources.files("maildb").joinpath("schema_indexes.sql").read_text()
    with pool.connection() as conn:
        conn.execute(index_sql)
        if include_hnsw:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_email_embedding "
                "ON emails USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            )
        conn.execute("ANALYZE emails")
        conn.execute("ANALYZE attachments")
        conn.execute("ANALYZE email_attachments")
        conn.commit()
    logger.info("indexes_created", include_hnsw=include_hnsw)


def create_hnsw_index(pool: ConnectionPool) -> None:
    """Create the HNSW index on embeddings. Called after embed phase."""
    with pool.connection() as conn:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_email_embedding "
            "ON emails USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )
        conn.execute("ANALYZE emails")
        conn.commit()
    logger.info("hnsw_index_created")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_index_phase.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/ingest/index.py tests/integration/test_index_phase.py
git commit -m "feat: add index phase for deferred bulk index creation"
```

---

## Task 7: Embed Worker (Phase 4)

**Purpose:** SKIP LOCKED embedding workers that pull batches from the emails table, call Ollama, and update embeddings.

**Files:**
- Create: `src/maildb/ingest/embed.py`
- Create: `tests/integration/test_embed_worker.py`

- [ ] **Step 1: Write integration test**

Create `tests/integration/test_embed_worker.py`:

```python
from unittest.mock import MagicMock

import pytest
from psycopg.rows import dict_row

from maildb.ingest.embed import embed_worker

pytestmark = pytest.mark.integration


def _insert_test_email(pool, message_id="test@example.com"):
    """Insert a minimal email row for testing."""
    from uuid import uuid4
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, sender_name, body_text, created_at)
               VALUES (%(id)s, %(message_id)s, 'thread-1', 'Test', 'Sender', 'Body text', now())""",
            {"id": uuid4(), "message_id": message_id},
        )
        conn.commit()


def test_embed_worker_processes_null_embeddings(test_pool, test_settings):
    _insert_test_email(test_pool, "embed-test-1@example.com")
    _insert_test_email(test_pool, "embed-test-2@example.com")

    mock_client = MagicMock()
    mock_client.embed_batch.return_value = [[0.1] * 768, [0.2] * 768]

    count = embed_worker(
        database_url=test_settings.database_url,
        ollama_url=test_settings.ollama_url,
        embedding_model=test_settings.embedding_model,
        embedding_dimensions=test_settings.embedding_dimensions,
        batch_size=10,
        _embedding_client=mock_client,  # test injection
    )
    assert count == 2

    with test_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT count(*) AS c FROM emails WHERE embedding IS NOT NULL")
        assert cur.fetchone()["c"] == 2


def test_embed_worker_exits_when_no_work(test_pool, test_settings):
    mock_client = MagicMock()
    count = embed_worker(
        database_url=test_settings.database_url,
        ollama_url="http://localhost:11434",
        embedding_model="nomic-embed-text",
        embedding_dimensions=768,
        batch_size=10,
        _embedding_client=mock_client,
    )
    assert count == 0
    mock_client.embed_batch.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_embed_worker.py -v`

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `embed.py`**

Create `src/maildb/ingest/embed.py`:

```python
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from maildb.embeddings import EmbeddingClient, build_embedding_text

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()

SELECT_BATCH_SQL = """
SELECT id, subject, sender_name, body_text
FROM emails
WHERE embedding IS NULL
LIMIT %(batch_size)s
FOR UPDATE SKIP LOCKED
"""


def embed_worker(
    *,
    database_url: str,
    ollama_url: str,
    embedding_model: str,
    embedding_dimensions: int,
    batch_size: int = 50,
    _embedding_client: EmbeddingClient | None = None,
) -> int:
    """Process embedding batches until no work remains. Returns total rows updated."""
    pool = ConnectionPool(conninfo=database_url, min_size=1, max_size=1)
    client = _embedding_client or EmbeddingClient(
        ollama_url=ollama_url,
        model_name=embedding_model,
        dimensions=embedding_dimensions,
    )

    total_updated = 0
    consecutive_failures = 0
    max_failures = 3

    try:
        while True:
            with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SELECT_BATCH_SQL, {"batch_size": batch_size})
                rows = cur.fetchall()

                if not rows:
                    break

                texts = [
                    build_embedding_text(r["subject"], r["sender_name"], r["body_text"])
                    for r in rows
                ]

                try:
                    embeddings = client.embed_batch(texts)
                    consecutive_failures = 0
                except Exception:
                    consecutive_failures += 1
                    logger.warning(
                        "embed_batch_failed",
                        attempt=consecutive_failures,
                        max=max_failures,
                    )
                    conn.rollback()
                    if consecutive_failures >= max_failures:
                        logger.error("embed_worker_giving_up")
                        break
                    time.sleep(2 ** consecutive_failures)
                    continue

                for row, emb in zip(rows, embeddings, strict=True):
                    conn.execute(
                        "UPDATE emails SET embedding = %s WHERE id = %s",
                        (emb, row["id"]),
                    )
                conn.commit()
                total_updated += len(rows)
                logger.info("embed_batch_done", batch_size=len(rows), total=total_updated)

    finally:
        pool.close()

    return total_updated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_embed_worker.py -v`

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/ingest/embed.py tests/integration/test_embed_worker.py
git commit -m "feat: add SKIP LOCKED embed worker for Phase 4"
```

---

## Task 8: Orchestrator and CLI

**Purpose:** Pipeline coordinator that drives all four phases and provides the CLI entry point.

**Files:**
- Create: `src/maildb/ingest/orchestrator.py`
- Create: `src/maildb/ingest/__main__.py`
- Modify: `src/maildb/ingest/__init__.py`
- Create: `tests/integration/test_orchestrator.py`

- [ ] **Step 1: Write integration test for orchestrator**

Create `tests/integration/test_orchestrator.py`:

```python
from pathlib import Path

import pytest

from maildb.ingest.orchestrator import run_pipeline, get_status
from maildb.ingest.tasks import get_phase_status

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


def test_run_pipeline_split_and_parse(test_pool, test_settings, tmp_path):
    """Pipeline should split, parse, and index a small mbox."""
    result = run_pipeline(
        mbox_path=FIXTURES / "sample.mbox",
        database_url=test_settings.database_url,
        attachment_dir=tmp_path / "attachments",
        tmp_dir=tmp_path / "chunks",
        chunk_size_bytes=50 * 1024 * 1024,  # 50MB — won't actually split our small fixture
        parse_workers=2,
        skip_embed=True,  # no Ollama in tests
    )
    assert result["parse"]["completed"] > 0
    with test_pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        assert cur.fetchone()[0] > 0


def test_get_status(test_pool):
    status = get_status(test_pool)
    assert "split" in status
    assert "parse" in status
    assert "index" in status
    assert "embed" in status
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_orchestrator.py -v`

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `orchestrator.py`**

Create `src/maildb/ingest/orchestrator.py`:

```python
from __future__ import annotations

import os
import signal
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import structlog
from psycopg_pool import ConnectionPool

from maildb.ingest.embed import embed_worker
from maildb.ingest.index import create_hnsw_index, drop_non_unique_indexes, run_index_phase
from maildb.ingest.parse import process_chunk
from maildb.ingest.split import split_mbox
from maildb.ingest.tasks import (
    complete_task,
    create_task,
    get_phase_status,
    reset_failed_tasks,
)

logger = structlog.get_logger()


def _get_pool(database_url: str) -> ConnectionPool:
    return ConnectionPool(conninfo=database_url, min_size=1, max_size=5)


def run_pipeline(
    *,
    mbox_path: Path | str,
    database_url: str,
    attachment_dir: Path | str,
    tmp_dir: Path | str,
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
        parse_workers = max(1, os.cpu_count() - 1)  # type: ignore[operator]

    pool = _get_pool(database_url)

    try:
        # Phase 1: Split
        split_status = get_phase_status(pool, "split")
        if split_status["total"] == 0:
            logger.info("phase_start", phase="split")
            split_task = create_task(pool, phase="split")
            chunks = split_mbox(mbox_path, output_dir=tmp_dir, chunk_size_bytes=chunk_size_bytes)
            for chunk_path in chunks:
                create_task(pool, phase="parse", chunk_path=str(chunk_path))
            complete_task(pool, split_task["id"], messages_total=len(chunks))
            logger.info("phase_complete", phase="split", chunks=len(chunks))
        elif split_status["completed"] == 0:
            logger.info("split_incomplete_restarting")
            # Clean and re-split
            with pool.connection() as conn:
                conn.execute("DELETE FROM ingest_tasks WHERE phase IN ('split', 'parse')")
                conn.commit()
            pool.close()
            return run_pipeline(
                mbox_path=mbox_path, database_url=database_url,
                attachment_dir=attachment_dir, tmp_dir=tmp_dir,
                chunk_size_bytes=chunk_size_bytes, parse_workers=parse_workers,
                embed_workers=embed_workers, embed_batch_size=embed_batch_size,
                ollama_url=ollama_url, embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions, skip_embed=skip_embed,
            )

        # Phase 2: Parse
        reset_failed_tasks(pool, phase="parse")
        parse_status = get_phase_status(pool, "parse")
        if parse_status["pending"] > 0 or parse_status["in_progress"] > 0:
            logger.info("phase_start", phase="parse", pending=parse_status["pending"])
            drop_non_unique_indexes(pool)

            # Workers self-assign via SKIP LOCKED — launch N workers that each
            # loop claiming tasks until none remain
            with ProcessPoolExecutor(max_workers=parse_workers) as executor:
                futures = [
                    executor.submit(
                        process_chunk,
                        database_url=database_url,
                        attachment_dir=attachment_dir,
                    )
                    for _ in range(parse_workers)
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        logger.exception("parse_worker_crashed")

            logger.info("phase_complete", phase="parse")

        # Check for permanently failed parse tasks before advancing
        parse_status = get_phase_status(pool, "parse")
        if parse_status["failed"] > 0:
            logger.error("parse_phase_has_permanent_failures", failed=parse_status["failed"])
            raise RuntimeError(
                f"Parse phase has {parse_status['failed']} permanently failed tasks. "
                "Fix errors and retry with: uv run python -m maildb.ingest parse"
            )

        # Phase 3: Index
        index_status = get_phase_status(pool, "index")
        if index_status["completed"] == 0:
            logger.info("phase_start", phase="index")
            index_task = create_task(pool, phase="index")
            run_index_phase(pool, include_hnsw=False)
            complete_task(pool, index_task["id"])
            logger.info("phase_complete", phase="index")

        # Phase 4: Embed
        if not skip_embed:
            embed_status = get_phase_status(pool, "embed")
            if embed_status["completed"] == 0:
                logger.info("phase_start", phase="embed")
                embed_task = create_task(pool, phase="embed")

                with ProcessPoolExecutor(max_workers=embed_workers) as executor:
                    futures = [
                        executor.submit(
                            embed_worker,
                            database_url=database_url,
                            ollama_url=ollama_url,
                            embedding_model=embedding_model,
                            embedding_dimensions=embedding_dimensions,
                            batch_size=embed_batch_size,
                        )
                        for _ in range(embed_workers)
                    ]
                    total_embedded = sum(f.result() for f in futures)

                complete_task(pool, embed_task["id"], messages_total=total_embedded)
                create_hnsw_index(pool)
                logger.info("phase_complete", phase="embed", total=total_embedded)

    finally:
        pool.close()

    pool = _get_pool(database_url)
    try:
        return get_status(pool)
    finally:
        pool.close()


def get_status(pool: ConnectionPool) -> dict[str, Any]:
    """Get status for all phases."""
    result = {}
    for phase in ("split", "parse", "index", "embed"):
        result[phase] = get_phase_status(pool, phase)

    with pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        result["total_emails"] = cur.fetchone()[0]
        cur = conn.execute("SELECT count(*) FROM emails WHERE embedding IS NOT NULL")
        result["total_embedded"] = cur.fetchone()[0]
        cur = conn.execute("SELECT count(*) FROM attachments")
        result["total_attachments_unique"] = cur.fetchone()[0]
        cur = conn.execute("SELECT count(*) FROM email_attachments")
        result["total_attachments"] = cur.fetchone()[0]

    return result
```

- [ ] **Step 4: Create CLI entry point `__main__.py`**

Create `src/maildb/ingest/__main__.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path

from maildb.config import Settings
from maildb.db import create_pool, init_db
from maildb.ingest.orchestrator import get_status, run_pipeline


def main() -> None:
    settings = Settings()
    args = sys.argv[1:]

    if not args:
        sys.stdout.write("Usage: python -m maildb.ingest <mbox_path> | split | parse | index | embed | status\n")
        sys.exit(1)

    command = args[0]

    if command == "status":
        pool = create_pool(settings)
        init_db(pool)
        status = get_status(pool)
        _print_status(status)
        pool.close()
        return

    if command in ("split", "parse", "index", "embed"):
        mbox_path = args[1] if len(args) > 1 and command == "split" else None
        pool = create_pool(settings)
        init_db(pool)
        # Individual phase execution — delegate to orchestrator with phase control
        # For now, run full pipeline (orchestrator skips completed phases)
        if mbox_path:
            run_pipeline(
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
                skip_embed=(command == "split" or command == "parse"),
            )
        pool.close()
        return

    # Default: treat as mbox path for full pipeline
    mbox_path = Path(command)
    if not mbox_path.exists():
        sys.stderr.write(f"Error: {mbox_path} not found\n")
        sys.exit(1)

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
    )
    _print_status(result)


def _print_status(status: dict) -> None:
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
    lines.append(f"Embeddings: {status.get('total_embedded', 0):,} / {status.get('total_emails', 0):,}")
    lines.append(f"Attachments: {status.get('total_attachments', 0):,} ({status.get('total_attachments_unique', 0):,} unique)")
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Update `src/maildb/ingest/__init__.py`**

```python
from maildb.ingest.orchestrator import get_status, run_pipeline

__all__ = ["get_status", "run_pipeline"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_orchestrator.py -v`

Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/maildb/ingest/ tests/integration/test_orchestrator.py
git commit -m "feat: add pipeline orchestrator and CLI entry point"
```

---

## Task 9: Verify Clean Imports and Update Exports

**Purpose:** Verify that all imports of the old `ingest_mbox` and `backfill_embeddings` are removed, and update package exports if needed. (The old `ingest.py` and `test_ingest.py` were already deleted in Task 2.)

**Files:**
- Modify: `src/maildb/__init__.py` (if it exports anything from old ingest)

- [ ] **Step 1: Check for remaining imports of old module**

Search for any references to `maildb.ingest.ingest_mbox` or `maildb.ingest.backfill_embeddings` in the codebase and update or remove them.

Run: `uv run ruff check . && uv run mypy src/`

- [ ] **Step 3: Run full test suite**

Run: `uv run just check`

Expected: All tests pass, lint clean, mypy clean.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: remove old ingest module, replaced by ingest package"
```

---

## Task 10: Query Improvements (topics_with guard, unreplied limit)

**Purpose:** Add scale guards to existing query methods per the spec.

**Files:**
- Modify: `src/maildb/maildb.py`
- Modify: `src/maildb/server.py`
- Modify: `tests/integration/test_maildb.py`

- [ ] **Step 1: Write test for `unreplied()` with limit**

Add to `tests/integration/test_maildb.py`:

```python
def test_unreplied_respects_limit(db_with_data):
    results = db_with_data.unreplied(limit=1)
    assert len(results) <= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_maildb.py::test_unreplied_respects_limit -v`

Expected: FAIL — `unreplied()` doesn't accept `limit` parameter.

- [ ] **Step 3: Add `limit` parameter to `unreplied()`**

In `src/maildb/maildb.py`, add `limit: int = 100` to `unreplied()` signature and `LIMIT %(limit)s` to the SQL:

```python
def unreplied(
    self,
    *,
    after: str | None = None,
    before: str | None = None,
    sender: str | None = None,
    sender_domain: str | None = None,
    limit: int = 100,
) -> list[Email]:
```

Add `params["limit"] = limit` and append `LIMIT %(limit)s` to the SQL string.

- [ ] **Step 4: Add limit guard to `topics_with()`**

In `src/maildb/maildb.py`, add `LIMIT 500` to the initial query in `topics_with()`:

```python
sql = f"SELECT {SELECT_COLS} FROM emails WHERE {where} ORDER BY date DESC LIMIT 500"
```

- [ ] **Step 5: Update `unreplied` tool in `server.py`**

Add `limit: int = 100` parameter to the MCP tool:

```python
@mcp.tool()
def unreplied(
    ctx: Context,
    after: str | None = None,
    before: str | None = None,
    sender: str | None = None,
    sender_domain: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Find inbound emails that have no outbound reply in the same thread."""
    db = _get_db(ctx)
    results = db.unreplied(after=after, before=before, sender=sender, sender_domain=sender_domain, limit=limit)
    return [_serialize_email(e) for e in results]
```

- [ ] **Step 6: Run tests**

Run: `uv run just check`

Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add src/maildb/maildb.py src/maildb/server.py tests/integration/test_maildb.py
git commit -m "feat: add limit to unreplied(), guard topics_with() at 500 rows"
```

---

## Task 11: Full Check and Cleanup

**Purpose:** Final verification that everything works together.

- [ ] **Step 1: Run full check**

Run: `uv run just check`

Expected: fmt clean, lint clean, mypy clean, all tests pass.

- [ ] **Step 2: Verify CLI works**

Run: `uv run python -m maildb.ingest --help` (or with no args to see usage)

Expected: Usage message prints without error.

- [ ] **Step 3: Run `status` command**

Run: `uv run python -m maildb.ingest status`

Expected: Shows empty status table (no ingest has been run).

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git add -A && git commit -m "chore: final cleanup for scale ingest pipeline"
```
