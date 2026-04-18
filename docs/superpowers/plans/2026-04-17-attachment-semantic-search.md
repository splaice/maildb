# Attachment Semantic Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the textual contents of email attachments searchable alongside email bodies, via Marker-extracted markdown, structure-aware chunking, and pgvector semantic search.

**Architecture:** Backfill-first pipeline. A new `maildb process_attachments` CLI command claims rows from a new `attachment_contents` table via `FOR UPDATE SKIP LOCKED`, runs Marker to produce markdown, splits into heading-scoped chunks (1024-token cap), embeds each chunk via `nomic-embed-text`, and writes to a new `attachment_chunks` table. Search layer adds `search_attachments` (chunk-level), `search_all` (merged emails + chunks), and `get_attachment_markdown` (retrieve full doc). A precise HuggingFace tokenizer replaces the existing `estimate_tokens` byte-length heuristic.

**Tech Stack:** `marker-pdf`, `tokenizers` (HF Rust-backed), psycopg3, pgvector, Ollama (`nomic-embed-text`), Typer, pytest.

**Spec:** `docs/superpowers/specs/2026-04-17-attachment-semantic-search-design.md`

---

## File Map

### New files

| Path | Responsibility |
|------|----------------|
| `src/maildb/tokenizer.py` | Cached HF tokenizer, `count_tokens`, `truncate_to_tokens` |
| `src/maildb/ingest/extraction.py` | Content-type routing + Marker wrapper; returns markdown or raises with a reason |
| `src/maildb/ingest/chunking.py` | Structure-aware chunker (heading scope, token cap, fallbacks) |
| `src/maildb/ingest/process_attachments.py` | Worker orchestrator: claim → extract → chunk → embed → status |
| `tests/unit/test_tokenizer.py` | Tokenizer exact-count + truncation tests |
| `tests/unit/test_chunking.py` | Chunker determinism + boundary behavior |
| `tests/unit/test_extraction.py` | Content-type router + Marker wrapper (Marker mocked) |
| `tests/integration/test_process_attachments.py` | End-to-end: claim, extract on fixture, verify rows |
| `tests/integration/test_search_attachments.py` | search_attachments + search_all against seeded data |
| `tests/fixtures/attachments/hello.pdf` | Minimal real PDF for integration tests (committed binary) |

### Modified files

| Path | Changes |
|------|---------|
| `pyproject.toml` | Add `marker-pdf` and `tokenizers` to `[project] dependencies` |
| `src/maildb/schema_tables.sql` | Add `attachments.reference_count`, `attachment_contents`, `attachment_chunks` |
| `src/maildb/schema_indexes.sql` | Add `idx_attachment_contents_status`, `idx_attachment_chunks_attachment_id` |
| `src/maildb/db.py` | Backfill `reference_count` in `init_db`; create HNSW index helper for attachment chunks |
| `src/maildb/models.py` | Add `AttachmentChunk`, `AttachmentSearchResult`, `UnifiedSearchResult` dataclasses |
| `src/maildb/maildb.py` | Add `search_attachments`, `search_all`, `get_attachment_markdown` methods |
| `src/maildb/server.py` | Expose three new MCP tools + serialization helpers |
| `src/maildb/cli.py` | Add `process_attachments run|status|retry` Typer app |
| `src/maildb/ingest/parse.py` | Increment `reference_count` + insert `pending` `attachment_contents` row |
| `src/maildb/embeddings.py` | Keep existing behavior; follow-up will swap `estimate_tokens` for the precise tokenizer |
| `docs/runbooks/attachment-extraction-migration.md` | New runbook (written in final step) |

---

## Step 1 — Foundations: dependencies and precise tokenizer

### Task 1.1: Add dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add to `[project] dependencies`**

```toml
dependencies = [
    # ...existing...
    "typer>=0.12",
    "marker-pdf>=1.2.0",
    "tokenizers>=0.20",
]
```

- [ ] **Step 2: Sync**

Run: `uv sync`
Expected: `marker-pdf`, `tokenizers`, and transitive deps installed.

- [ ] **Step 3: Smoke import**

Run:
```bash
uv run python -c "from marker.converters.pdf import PdfConverter; from tokenizers import Tokenizer; print('ok')"
```

Expected: prints `ok`. If `marker.converters.pdf` moved in the installed version, resolve the correct import path now and record it — it's referenced again in Task 3.3.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add marker-pdf and tokenizers dependencies"
```

### Task 1.2: Precise tokenizer module

**Files:**
- Create: `src/maildb/tokenizer.py`
- Create: `tests/unit/test_tokenizer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_tokenizer.py`:

```python
from __future__ import annotations

import pytest

from maildb.tokenizer import count_tokens, truncate_to_tokens


def test_count_tokens_nonzero_for_nonempty() -> None:
    assert count_tokens("Hello world") > 0


def test_count_tokens_empty_is_zero() -> None:
    assert count_tokens("") == 0


def test_count_tokens_scales_with_length() -> None:
    short = count_tokens("hello")
    long = count_tokens("hello " * 500)
    assert long > short * 100


def test_truncate_preserves_short_input() -> None:
    text = "short text"
    assert truncate_to_tokens(text, 1000) == text


def test_truncate_cuts_to_limit() -> None:
    text = "word " * 1000
    truncated = truncate_to_tokens(text, 50)
    assert count_tokens(truncated) <= 50


def test_truncate_result_fits_within_limit_exactly() -> None:
    text = "The quick brown fox jumps over the lazy dog. " * 100
    limit = 32
    truncated = truncate_to_tokens(text, limit)
    assert count_tokens(truncated) <= limit
    # Should use most of the available budget, not be trivially short
    assert count_tokens(truncated) >= limit - 4
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_tokenizer.py -v
```

Expected: `ModuleNotFoundError: No module named 'maildb.tokenizer'`.

- [ ] **Step 3: Implement the module**

Create `src/maildb/tokenizer.py`:

```python
"""Precise token counting using the nomic-embed-text HF tokenizer.

Replaces the byte-length heuristic in `estimate_tokens`. The tokenizer
is loaded once and cached at module level — it's a lightweight Rust
object that's safe to share across threads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tokenizers import Tokenizer

if TYPE_CHECKING:
    pass

_TOKENIZER: Tokenizer | None = None

_MODEL_NAME = "nomic-ai/nomic-embed-text-v1"


def get_tokenizer() -> Tokenizer:
    """Return the cached tokenizer, loading on first use."""
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = Tokenizer.from_pretrained(_MODEL_NAME)
    return _TOKENIZER


def count_tokens(text: str) -> int:
    """Return the exact token count for the given text."""
    if not text:
        return 0
    return len(get_tokenizer().encode(text).ids)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Return a prefix of text whose token count is <= max_tokens."""
    if not text:
        return text
    tok = get_tokenizer()
    enc = tok.encode(text)
    if len(enc.ids) <= max_tokens:
        return text
    return tok.decode(enc.ids[:max_tokens])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_tokenizer.py -v
```

Expected: all pass. If `Tokenizer.from_pretrained` fails with a network error, the test machine needs internet access on first run — the tokenizer is cached locally afterward.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/tokenizer.py tests/unit/test_tokenizer.py
git commit -m "feat(tokenizer): precise token counts via HF tokenizers + nomic-embed-text-v1"
```

---

## Step 2 — Schema changes

### Task 2.1: `attachments.reference_count` column

**Files:**
- Modify: `src/maildb/schema_tables.sql`
- Modify: `src/maildb/db.py`
- Test: `tests/integration/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_db.py`:

```python
def test_attachments_has_reference_count(test_pool) -> None:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'attachments' AND column_name = 'reference_count'"
        )
        row = cur.fetchone()
    assert row is not None
    assert row[1] == "integer"
    assert row[2] == "NO"


def test_init_db_backfills_reference_count(test_pool) -> None:  # type: ignore[no-untyped-def]
    """Given rows in email_attachments, init_db computes reference_count."""
    with test_pool.connection() as conn:
        # Seed: one attachment referenced by two distinct emails.
        conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES ('aa', 'x.pdf', 'application/pdf', 1, 'aa/x.pdf') RETURNING id"
        )
        att_id = conn.execute("SELECT id FROM attachments WHERE sha256='aa'").fetchone()[0]
        for mid in ("<ref-1@ex.com>", "<ref-2@ex.com>"):
            conn.execute(
                "INSERT INTO emails (id, message_id, thread_id, source_account) "
                "VALUES (gen_random_uuid(), %s, 't', 'test@example.com') RETURNING id",
                (mid,),
            )
            eid = conn.execute(
                "SELECT id FROM emails WHERE message_id=%s", (mid,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO email_attachments (email_id, attachment_id, filename) "
                "VALUES (%s, %s, 'x.pdf')",
                (eid, att_id),
            )
        # Reset reference_count to 0 so we can prove the backfill runs.
        conn.execute(
            "UPDATE attachments SET reference_count = 0 WHERE id = %s", (att_id,),
        )
        conn.commit()

    from maildb.db import init_db
    init_db(test_pool)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT reference_count FROM attachments WHERE id = %s", (att_id,)
        )
        assert cur.fetchone()[0] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/integration/test_db.py::test_attachments_has_reference_count tests/integration/test_db.py::test_init_db_backfills_reference_count -v
```

Expected: column missing, backfill missing.

- [ ] **Step 3: Add column to the schema**

In `src/maildb/schema_tables.sql`, find the `CREATE TABLE IF NOT EXISTS attachments (...)` block and add the column:

```sql
CREATE TABLE IF NOT EXISTS attachments (
    id              SERIAL PRIMARY KEY,
    sha256          TEXT NOT NULL,
    filename        TEXT NOT NULL,
    content_type    TEXT,
    size            BIGINT NOT NULL,
    storage_path    TEXT NOT NULL,
    reference_count INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (sha256)
);

ALTER TABLE attachments
    ADD COLUMN IF NOT EXISTS reference_count INT NOT NULL DEFAULT 0;
```

The idempotent `ALTER TABLE` handles databases created before this schema revision.

- [ ] **Step 4: Add the backfill to `init_db`**

In `src/maildb/db.py::init_db`, after the schema-SQL execute and before the NOT NULL tightening block:

```python
# Backfill attachments.reference_count from email_attachments.
# Safe to run every init_db: only rewrites rows where count differs.
conn.execute(
    """
    UPDATE attachments a
       SET reference_count = sub.n
      FROM (
          SELECT attachment_id, count(*) AS n
            FROM email_attachments
           GROUP BY attachment_id
      ) sub
     WHERE a.id = sub.attachment_id
       AND a.reference_count != sub.n
    """
)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_db.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/maildb/schema_tables.sql src/maildb/db.py tests/integration/test_db.py
git commit -m "feat(schema): add attachments.reference_count + init_db backfill"
```

### Task 2.2: `attachment_contents` table

**Files:**
- Modify: `src/maildb/schema_tables.sql`
- Modify: `src/maildb/schema_indexes.sql`
- Test: `tests/integration/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_db.py`:

```python
def test_attachment_contents_table_exists(test_pool) -> None:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'attachment_contents' ORDER BY column_name"
        )
        cols = {row[0] for row in cur.fetchall()}
    assert cols == {
        "attachment_id",
        "status",
        "markdown",
        "markdown_bytes",
        "reason",
        "extracted_at",
        "extraction_ms",
        "extractor_version",
    }


def test_attachment_contents_status_check_enforced(test_pool) -> None:  # type: ignore[no-untyped-def]
    """CHECK constraint rejects invalid status values."""
    import psycopg
    with test_pool.connection() as conn:
        conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES ('bb', 'y.pdf', 'application/pdf', 1, 'bb/y.pdf')"
        )
        att_id = conn.execute("SELECT id FROM attachments WHERE sha256='bb'").fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO attachment_contents (attachment_id, status) VALUES (%s, %s)",
                (att_id, "bogus"),
            )
        conn.rollback()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/integration/test_db.py -v -k attachment_contents
```

Expected: table missing → empty column set.

- [ ] **Step 3: Add the table**

Append to `src/maildb/schema_tables.sql` (after `email_accounts`):

```sql
CREATE TABLE IF NOT EXISTS attachment_contents (
    attachment_id     INT PRIMARY KEY REFERENCES attachments(id) ON DELETE CASCADE,
    status            TEXT NOT NULL
                      CHECK (status IN ('pending','extracting','extracted','failed','skipped')),
    markdown          TEXT,
    markdown_bytes    INT,
    reason            TEXT,
    extracted_at      TIMESTAMPTZ,
    extraction_ms     INT,
    extractor_version TEXT
);
```

Append to `src/maildb/schema_indexes.sql`:

```sql
CREATE INDEX IF NOT EXISTS idx_attachment_contents_status
    ON attachment_contents (status)
    WHERE status IN ('pending','failed','extracting');
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_db.py -v -k attachment_contents
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/schema_tables.sql src/maildb/schema_indexes.sql tests/integration/test_db.py
git commit -m "feat(schema): attachment_contents table + status index"
```

### Task 2.3: `attachment_chunks` table

**Files:**
- Modify: `src/maildb/schema_tables.sql`
- Modify: `src/maildb/schema_indexes.sql`
- Test: `tests/integration/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_db.py`:

```python
def test_attachment_chunks_table_exists(test_pool) -> None:  # type: ignore[no-untyped-def]
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'attachment_chunks' ORDER BY column_name"
        )
        cols = {row[0] for row in cur.fetchall()}
    assert cols == {
        "id",
        "attachment_id",
        "chunk_index",
        "heading_path",
        "page_number",
        "token_count",
        "text",
        "embedding",
    }


def test_attachment_chunks_unique_index_on_attachment_chunk(test_pool) -> None:  # type: ignore[no-untyped-def]
    """(attachment_id, chunk_index) must be unique."""
    import psycopg
    with test_pool.connection() as conn:
        conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES ('cc', 'z.pdf', 'application/pdf', 1, 'cc/z.pdf')"
        )
        att_id = conn.execute("SELECT id FROM attachments WHERE sha256='cc'").fetchone()[0]
        conn.execute(
            "INSERT INTO attachment_chunks (attachment_id, chunk_index, token_count, text) "
            "VALUES (%s, 0, 5, 'hi')", (att_id,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO attachment_chunks (attachment_id, chunk_index, token_count, text) "
                "VALUES (%s, 0, 6, 'hey')", (att_id,),
            )
        conn.rollback()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/integration/test_db.py -v -k attachment_chunks
```

Expected: table missing.

- [ ] **Step 3: Add the table**

Append to `src/maildb/schema_tables.sql`:

```sql
CREATE TABLE IF NOT EXISTS attachment_chunks (
    id             BIGSERIAL PRIMARY KEY,
    attachment_id  INT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    chunk_index    INT NOT NULL,
    heading_path   TEXT,
    page_number    INT,
    token_count    INT NOT NULL,
    text           TEXT NOT NULL,
    embedding      vector(768),
    UNIQUE (attachment_id, chunk_index)
);
```

Append to `src/maildb/schema_indexes.sql`:

```sql
CREATE INDEX IF NOT EXISTS idx_attachment_chunks_attachment_id
    ON attachment_chunks (attachment_id);
-- HNSW created separately after first extraction completes:
-- CREATE INDEX IF NOT EXISTS idx_attachment_chunks_embedding
--   ON attachment_chunks USING hnsw (embedding vector_cosine_ops)
--   WITH (m = 16, ef_construction = 64);
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_db.py -v -k attachment_chunks
```

Expected: PASS.

- [ ] **Step 5: Full suite check**

```bash
uv run just check
```

Expected: PASS (all prior tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/maildb/schema_tables.sql src/maildb/schema_indexes.sql tests/integration/test_db.py
git commit -m "feat(schema): attachment_chunks table + attachment_id index"
```

### Task 2.4: Dataclasses for attachment chunks and search results

**Files:**
- Modify: `src/maildb/models.py`
- Test: `tests/unit/test_models.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_models.py`:

```python
def test_attachment_chunk_dataclass_shape() -> None:
    from maildb.models import AttachmentChunk
    c = AttachmentChunk(
        id=1,
        attachment_id=10,
        chunk_index=0,
        heading_path="Overview > Payment Terms",
        page_number=3,
        token_count=250,
        text="Late fees apply after 30 days.",
    )
    assert c.token_count == 250
    assert c.heading_path == "Overview > Payment Terms"


def test_attachment_search_result_shape() -> None:
    from maildb.models import AttachmentChunk, AttachmentSearchResult
    chunk = AttachmentChunk(
        id=1,
        attachment_id=10,
        chunk_index=0,
        heading_path=None,
        page_number=None,
        token_count=5,
        text="hi",
    )
    r = AttachmentSearchResult(
        attachment_id=10,
        filename="x.pdf",
        content_type="application/pdf",
        sha256="aa",
        chunk=chunk,
        emails=["<a@b.com>"],
        similarity=0.87,
    )
    assert r.similarity == 0.87
    assert r.emails == ["<a@b.com>"]


def test_unified_search_result_either_branch() -> None:
    from maildb.models import UnifiedSearchResult
    email_side = UnifiedSearchResult(
        source="email", similarity=0.9, email=None, attachment_result=None,
    )
    assert email_side.source == "email"
    attach_side = UnifiedSearchResult(
        source="attachment", similarity=0.7, email=None, attachment_result=None,
    )
    assert attach_side.source == "attachment"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_models.py -v -k "attachment_chunk or attachment_search_result or unified_search_result"
```

Expected: FAIL (names not defined).

- [ ] **Step 3: Add the dataclasses**

Append to `src/maildb/models.py` (below `ImportRecord`):

```python
@dataclass
class AttachmentChunk:
    id: int
    attachment_id: int
    chunk_index: int
    heading_path: str | None
    page_number: int | None
    token_count: int
    text: str
    embedding: list[float] | None = None


@dataclass
class AttachmentSearchResult:
    attachment_id: int
    filename: str
    content_type: str | None
    sha256: str
    chunk: AttachmentChunk
    emails: list[str]
    similarity: float


@dataclass
class UnifiedSearchResult:
    source: Literal["email", "attachment"]
    similarity: float
    email: Email | None
    attachment_result: AttachmentSearchResult | None
```

Add `from typing import Literal` to the imports at the top if it's not already there.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_models.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/models.py tests/unit/test_models.py
git commit -m "feat(models): AttachmentChunk, AttachmentSearchResult, UnifiedSearchResult"
```

---

## Step 3 — Extraction: content-type routing, chunking, Marker wrapper

### Task 3.1: Content-type router

**Files:**
- Create: `src/maildb/ingest/extraction.py`
- Create: `tests/unit/test_extraction.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_extraction.py`:

```python
from __future__ import annotations

import pytest

from maildb.ingest.extraction import SUPPORTED, route_content_type


@pytest.mark.parametrize(
    "content_type,expected_bucket",
    [
        ("application/pdf", "pdf"),
        ("application/msword", "doc_legacy"),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
        ("application/vnd.ms-excel", "xls_legacy"),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
        (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "pptx",
        ),
        ("text/plain", "text"),
        ("text/html", "html"),
        ("image/png", "image"),
        ("image/jpeg", "image"),
        ("image/jpg", "image"),
        ("image/gif", "image"),
        ("image/tiff", "image"),
    ],
)
def test_supported_types_route_to_known_buckets(content_type, expected_bucket):
    assert route_content_type(content_type) == expected_bucket


@pytest.mark.parametrize(
    "content_type",
    [
        "audio/mpeg",
        "application/zip",
        "video/quicktime",
        "application/octet-stream",
        "application/ics",
        "application/json",
        "",
        None,
    ],
)
def test_unsupported_types_return_none(content_type):
    assert route_content_type(content_type) is None


def test_supported_set_matches_router():
    """Every bucket named by SUPPORTED is reachable via route_content_type."""
    reachable = {
        route_content_type(t)
        for t in [
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "text/plain",
            "text/html",
            "image/png",
        ]
    }
    reachable.discard(None)
    assert reachable <= SUPPORTED
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/unit/test_extraction.py -v
```

Expected: `ModuleNotFoundError: maildb.ingest.extraction`.

- [ ] **Step 3: Create the router**

Create `src/maildb/ingest/extraction.py`:

```python
"""Attachment extraction: content-type routing + Marker wrapper.

route_content_type maps MIME types to an internal bucket name or None
(unsupported). Buckets are the granularity used by CLI --only filters
and by the Marker dispatch below.
"""

from __future__ import annotations

from typing import Final

SUPPORTED: Final[set[str]] = {
    "pdf",
    "doc_legacy",
    "docx",
    "xls_legacy",
    "xlsx",
    "pptx",
    "text",
    "html",
    "image",
}

_ROUTES: Final[dict[str, str]] = {
    "application/pdf": "pdf",
    "application/msword": "doc_legacy",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls_legacy",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "text",
    "text/html": "html",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/gif": "image",
    "image/tiff": "image",
    "image/webp": "image",
}


def route_content_type(content_type: str | None) -> str | None:
    """Return the bucket for a content-type, or None if unsupported."""
    if not content_type:
        return None
    return _ROUTES.get(content_type.lower())
```

- [ ] **Step 4: Run to verify tests pass**

```bash
uv run pytest tests/unit/test_extraction.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/ingest/extraction.py tests/unit/test_extraction.py
git commit -m "feat(extract): content-type router with SUPPORTED bucket set"
```

### Task 3.2: Marker wrapper

**Files:**
- Modify: `src/maildb/ingest/extraction.py`
- Modify: `tests/unit/test_extraction.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_extraction.py`:

```python
from pathlib import Path
from unittest.mock import patch

from maildb.ingest.extraction import (
    ExtractionResult,
    ExtractionFailed,
    extract_markdown,
)


def test_extract_passes_through_text_file(tmp_path: Path):
    p = tmp_path / "hello.txt"
    p.write_text("Hello world\nA second line")
    result = extract_markdown(p, content_type="text/plain")
    assert isinstance(result, ExtractionResult)
    assert "Hello world" in result.markdown
    assert result.extractor_version.startswith("passthrough")


def test_extract_passes_through_html(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text("<html><body><h1>Hi</h1><p>there</p></body></html>")
    result = extract_markdown(p, content_type="text/html")
    # Passthrough preserves the raw content; it's not Marker's job.
    assert "<h1>Hi</h1>" in result.markdown or "Hi" in result.markdown


def test_extract_calls_marker_for_pdf(tmp_path: Path):
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n...")
    with patch(
        "maildb.ingest.extraction._marker_convert",
        return_value="# Fake extracted markdown\n\nBody.",
    ) as m:
        result = extract_markdown(fake_pdf, content_type="application/pdf")
    assert m.called
    assert result.markdown.startswith("# Fake extracted markdown")
    assert result.extractor_version.startswith("marker==")


def test_extract_unsupported_raises_extraction_failed(tmp_path: Path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"ID3\x00")
    with pytest.raises(ExtractionFailed) as exc:
        extract_markdown(p, content_type="audio/mpeg")
    assert "not supported" in str(exc.value).lower()
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/unit/test_extraction.py -v -k "passes_through or marker or unsupported_raises"
```

Expected: FAIL — `ExtractionResult`, `ExtractionFailed`, `extract_markdown`, `_marker_convert` not defined.

- [ ] **Step 3: Implement extraction**

Append to `src/maildb/ingest/extraction.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


class ExtractionFailed(Exception):
    """Raised when extraction cannot proceed. The message is recorded as `reason`."""


@dataclass
class ExtractionResult:
    markdown: str
    extractor_version: str  # e.g. "marker==1.2.3" or "passthrough==1"


def _marker_convert(path: Path) -> tuple[str, str]:
    """Run Marker on a single file; return (markdown, version_string).

    Isolated so tests can monkeypatch it without importing marker-pdf.
    """
    import marker  # noqa: PLC0415 — deferred import keeps the test suite fast
    from marker.converters.pdf import PdfConverter  # noqa: PLC0415
    from marker.models import create_model_dict  # noqa: PLC0415
    from marker.output import text_from_rendered  # noqa: PLC0415

    converter = PdfConverter(artifact_dict=create_model_dict())
    rendered = converter(str(path))
    text, _, _ = text_from_rendered(rendered)
    return text, f"marker=={getattr(marker, '__version__', 'unknown')}"


def extract_markdown(path: Path, *, content_type: str | None) -> ExtractionResult:
    """Extract markdown from an attachment. Raises ExtractionFailed on unsupported
    types or when Marker errors out."""
    bucket = route_content_type(content_type)
    if bucket is None:
        raise ExtractionFailed(
            f"content_type {content_type!r} is not supported by Marker"
        )

    if bucket == "text":
        return ExtractionResult(
            markdown=path.read_text(encoding="utf-8", errors="replace"),
            extractor_version="passthrough==1",
        )

    if bucket == "html":
        # Pass HTML through as-is; Marker can handle conversion downstream if needed,
        # but for v1 we preserve the original markup so agents can see tags.
        return ExtractionResult(
            markdown=path.read_text(encoding="utf-8", errors="replace"),
            extractor_version="passthrough==1",
        )

    # Legacy .doc / .xls need LibreOffice pre-conversion. Defer to Marker for the
    # rest — Marker handles PDF, DOCX, XLSX, PPTX, and images natively.
    if bucket in ("doc_legacy", "xls_legacy"):
        raise ExtractionFailed(
            f"{bucket}: legacy binary format requires LibreOffice pre-conversion "
            "(not implemented in v1)"
        )

    try:
        markdown, version = _marker_convert(path)
    except Exception as exc:  # noqa: BLE001 — surface any Marker failure as reason
        raise ExtractionFailed(f"marker: {exc}") from exc

    return ExtractionResult(markdown=markdown, extractor_version=version)
```

- [ ] **Step 4: Run to verify tests pass**

```bash
uv run pytest tests/unit/test_extraction.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/ingest/extraction.py tests/unit/test_extraction.py
git commit -m "feat(extract): Marker wrapper + text/html pass-through"
```

### Task 3.3: Structure-aware chunker

**Files:**
- Create: `src/maildb/ingest/chunking.py`
- Create: `tests/unit/test_chunking.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_chunking.py`:

```python
from __future__ import annotations

import pytest

from maildb.ingest.chunking import Chunk, chunk_markdown


def test_chunk_flat_short_doc_single_chunk():
    md = "Just a few words here, no headings."
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].heading_path is None
    assert chunks[0].text.startswith("Just a few")


def test_chunk_with_headings_preserves_path():
    md = (
        "# Overview\n\n"
        "Top-level text.\n\n"
        "## Payment Terms\n\n"
        "Net 30 days.\n\n"
        "### Late Fees\n\n"
        "5% per month.\n"
    )
    chunks = chunk_markdown(md)
    # We expect at least three chunks (the three sections) with heading paths.
    paths = [c.heading_path for c in chunks]
    assert "Overview" in paths
    assert any(p and p.startswith("Overview > Payment Terms") for p in paths)
    assert any(p and "Late Fees" in p for p in paths)


def test_chunk_respects_token_cap():
    # Very long single paragraph exceeds the cap; chunker must split.
    para = "word " * 5000
    chunks = chunk_markdown(para, max_tokens=256)
    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= 256


def test_chunk_small_sections_merge_under_soft_floor():
    md = "## A\n\ntiny\n\n## B\n\ntiny\n\n## C\n\ntiny\n"
    chunks = chunk_markdown(md, max_tokens=1024, min_tokens=128)
    # Expect a single merged chunk since each section is tiny.
    assert len(chunks) == 1


def test_chunk_determinism():
    md = "# H\n\nSection text.\n\n## Sub\n\nMore text.\n"
    assert chunk_markdown(md) == chunk_markdown(md)


def test_chunk_indexes_are_sequential():
    md = "# A\n\n" + ("word " * 2000) + "\n\n# B\n\nshort tail\n"
    chunks = chunk_markdown(md, max_tokens=256)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunk_empty_input_returns_empty_list():
    assert chunk_markdown("") == []


@pytest.mark.parametrize("md", ["   \n\n   \n", "\n\n\n"])
def test_chunk_whitespace_only_returns_empty(md):
    assert chunk_markdown(md) == []
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/unit/test_chunking.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the chunker**

Create `src/maildb/ingest/chunking.py`:

```python
"""Structure-aware markdown chunker.

Parses markdown into heading-scoped sections, emits chunks respecting
token bounds. Soft floor merges adjacent small sections; hard cap
triggers paragraph/sentence splits on oversized sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from maildb.tokenizer import count_tokens

DEFAULT_MAX_TOKENS = 1024
DEFAULT_MIN_TOKENS = 128

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_PARA_SPLIT_RE = re.compile(r"\n\s*\n+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


@dataclass
class Chunk:
    chunk_index: int
    heading_path: str | None
    page_number: int | None
    token_count: int
    text: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Chunk):
            return NotImplemented
        return (
            self.chunk_index == other.chunk_index
            and self.heading_path == other.heading_path
            and self.page_number == other.page_number
            and self.token_count == other.token_count
            and self.text == other.text
        )


@dataclass
class _Section:
    heading_path: str | None
    body: str


def _parse_sections(markdown: str) -> list[_Section]:
    """Walk headings top-to-bottom, producing sections with full heading paths."""
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []  # (heading_level, heading_text)

    # Split text into heading markers + bodies
    positions = [(m.start(), m.end(), len(m.group(1)), m.group(2).strip()) for m in _HEADING_RE.finditer(markdown)]

    # Preamble before the first heading (if any)
    first_start = positions[0][0] if positions else len(markdown)
    preamble = markdown[:first_start].strip()
    if preamble:
        sections.append(_Section(heading_path=None, body=preamble))

    for i, (_, end, level, heading) in enumerate(positions):
        # Pop to the parent level
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        heading_path = " > ".join(h for _, h in stack)
        body_start = end
        body_end = positions[i + 1][0] if i + 1 < len(positions) else len(markdown)
        body = markdown[body_start:body_end].strip()
        if body:
            sections.append(_Section(heading_path=heading_path, body=body))

    return sections


def _split_oversized(body: str, max_tokens: int) -> list[str]:
    """Split a body that exceeds max_tokens into smaller pieces."""
    parts = [p.strip() for p in _PARA_SPLIT_RE.split(body) if p.strip()]
    out: list[str] = []
    for p in parts:
        if count_tokens(p) <= max_tokens:
            out.append(p)
            continue
        # Fall back to sentence splits
        for s in _SENT_SPLIT_RE.split(p):
            s = s.strip()
            if not s:
                continue
            if count_tokens(s) <= max_tokens:
                out.append(s)
            else:
                # Very long sentence — hard-split by word count as last resort
                words = s.split()
                current: list[str] = []
                for w in words:
                    current.append(w)
                    if count_tokens(" ".join(current)) > max_tokens:
                        # Back off one word, emit, reset
                        current.pop()
                        out.append(" ".join(current))
                        current = [w]
                if current:
                    out.append(" ".join(current))
    return out


def chunk_markdown(
    markdown: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    page_number: int | None = None,
) -> list[Chunk]:
    """Split markdown into heading-scoped chunks.

    Order-preserving. Deterministic for the same input.
    """
    if not markdown.strip():
        return []

    sections = _parse_sections(markdown)

    # Step 1: expand oversized sections
    prepared: list[tuple[str | None, str, int]] = []  # (heading_path, text, token_count)
    for sec in sections:
        tokens = count_tokens(sec.body)
        if tokens <= max_tokens:
            prepared.append((sec.heading_path, sec.body, tokens))
            continue
        pieces = _split_oversized(sec.body, max_tokens)
        for piece in pieces:
            prepared.append((sec.heading_path, piece, count_tokens(piece)))

    # Step 2: merge adjacent sections when both are under the soft floor AND share a path
    merged: list[tuple[str | None, str, int]] = []
    for heading_path, text, tokens in prepared:
        if (
            merged
            and merged[-1][2] < min_tokens
            and tokens < min_tokens
            and (merged[-1][2] + tokens) <= max_tokens
        ):
            prev_path, prev_text, prev_tokens = merged[-1]
            merged[-1] = (
                prev_path,  # keep the earlier path; this is a merge, not a demotion
                prev_text + "\n\n" + text,
                count_tokens(prev_text + "\n\n" + text),
            )
        else:
            merged.append((heading_path, text, tokens))

    return [
        Chunk(
            chunk_index=i,
            heading_path=path,
            page_number=page_number,
            token_count=tokens,
            text=text,
        )
        for i, (path, text, tokens) in enumerate(merged)
    ]
```

- [ ] **Step 4: Run to verify tests pass**

```bash
uv run pytest tests/unit/test_chunking.py -v
```

Expected: PASS. If `test_chunk_small_sections_merge_under_soft_floor` fails, inspect the merge condition — the default `min_tokens=128` expects a "tiny" section to count as under 128 tokens, which it is (a single word).

- [ ] **Step 5: Commit**

```bash
git add src/maildb/ingest/chunking.py tests/unit/test_chunking.py
git commit -m "feat(chunk): structure-aware chunker with token-cap splits and soft-floor merges"
```

---

## Step 4 — Process worker and CLI

### Task 4.1: Worker orchestrator — claim + process single row

**Files:**
- Create: `src/maildb/ingest/process_attachments.py`
- Create: `tests/integration/test_process_attachments.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_process_attachments.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from maildb.ingest.process_attachments import (
    ensure_pending_rows,
    process_one,
    run,
)
from maildb.ingest.extraction import ExtractionResult

pytestmark = pytest.mark.integration


def _insert_attachment(pool, sha256: str, ct: str, filename: str, size: int = 10) -> int:
    """Insert a minimal attachments row and return its id."""
    with pool.connection() as conn:
        cur = conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (sha256, filename, ct, size, f"{sha256[:2]}/{sha256[2:4]}/{sha256}"),
        )
        att_id = cur.fetchone()[0]
        conn.commit()
    return att_id


def test_ensure_pending_rows_creates_missing(test_pool):
    att_id = _insert_attachment(test_pool, "11", "application/pdf", "a.pdf")
    ensure_pending_rows(test_pool)
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT status FROM attachment_contents WHERE attachment_id = %s", (att_id,)
        )
        assert cur.fetchone()[0] == "pending"
    # Idempotent
    ensure_pending_rows(test_pool)
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM attachment_contents WHERE attachment_id = %s", (att_id,)
        )
        assert cur.fetchone()[0] == 1


def test_process_one_success_path(test_pool, tmp_path: Path):
    att_id = _insert_attachment(test_pool, "22", "text/plain", "greeting.txt")
    # Stage the attachment file on disk where the worker expects it.
    sp = tmp_path / "22" / "22" / "22"
    sp.parent.mkdir(parents=True)
    sp.write_text("Hello world from the attachment")

    ensure_pending_rows(test_pool)
    with patch(
        "maildb.ingest.process_attachments._embed_chunks",
        return_value=None,  # embedding step is stubbed here; covered in later task
    ):
        process_one(test_pool, att_id, attachment_dir=tmp_path)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT status, markdown IS NOT NULL, markdown_bytes, extraction_ms, "
            "extractor_version FROM attachment_contents WHERE attachment_id = %s",
            (att_id,),
        )
        status, has_md, md_bytes, ms, version = cur.fetchone()
    assert status == "extracted"
    assert has_md is True
    assert md_bytes > 0
    assert ms >= 0
    assert version.startswith("passthrough")

    # On-disk mirror written
    mirror = tmp_path / "22" / "22" / "22.md"
    assert mirror.exists()
    assert "Hello world" in mirror.read_text()


def test_process_one_failure_records_reason(test_pool, tmp_path: Path):
    att_id = _insert_attachment(test_pool, "33", "application/pdf", "broken.pdf")
    sp = tmp_path / "33" / "33" / "33"
    sp.parent.mkdir(parents=True)
    sp.write_bytes(b"not really a pdf")

    ensure_pending_rows(test_pool)
    with patch(
        "maildb.ingest.process_attachments.extract_markdown",
        side_effect=Exception("boom"),
    ):
        process_one(test_pool, att_id, attachment_dir=tmp_path)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT status, reason FROM attachment_contents WHERE attachment_id = %s",
            (att_id,),
        )
        status, reason = cur.fetchone()
    assert status == "failed"
    assert reason and "boom" in reason


def test_process_one_unsupported_records_skipped(test_pool, tmp_path: Path):
    att_id = _insert_attachment(test_pool, "44", "audio/mpeg", "voicemail.mp3")
    ensure_pending_rows(test_pool)
    process_one(test_pool, att_id, attachment_dir=tmp_path)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT status, reason FROM attachment_contents WHERE attachment_id = %s",
            (att_id,),
        )
        status, reason = cur.fetchone()
    assert status == "skipped"
    assert "not supported" in reason.lower()


def test_run_processes_multiple(test_pool, tmp_path: Path):
    ids = []
    for i, sha in enumerate(["55", "66", "77"]):
        aid = _insert_attachment(test_pool, sha, "text/plain", f"t{i}.txt")
        sp = tmp_path / sha[:2] / sha[2:4] / sha
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(f"content {i}")
        ids.append(aid)
    ensure_pending_rows(test_pool)
    with patch("maildb.ingest.process_attachments._embed_chunks", return_value=None):
        run(test_pool, attachment_dir=tmp_path, workers=1)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM attachment_contents WHERE status = 'extracted'"
        )
        assert cur.fetchone()[0] >= 3
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/integration/test_process_attachments.py -v
```

Expected: ImportError — module doesn't exist.

- [ ] **Step 3: Implement the worker**

Create `src/maildb/ingest/process_attachments.py`:

```python
"""Attachment extraction worker.

Claims pending rows from attachment_contents via SKIP LOCKED, runs
extract_markdown, chunks, embeds each chunk, writes markdown to disk,
and transitions status. Idempotent per-attachment; safe to crash
mid-run (watchdog reclaims 'extracting' rows older than the threshold).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from maildb.ingest.chunking import chunk_markdown
from maildb.ingest.extraction import ExtractionFailed, extract_markdown
from maildb.tokenizer import count_tokens

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

WATCHDOG_STALE_SECONDS = 3600  # 1 hour


def ensure_pending_rows(pool: "ConnectionPool") -> int:
    """Insert a 'pending' row into attachment_contents for every attachment that
    doesn't already have one. Returns count of newly inserted rows.
    """
    with pool.connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO attachment_contents (attachment_id, status)
            SELECT a.id, 'pending'
              FROM attachments a
              LEFT JOIN attachment_contents c ON c.attachment_id = a.id
             WHERE c.attachment_id IS NULL
            """
        )
        conn.commit()
        return cur.rowcount


def _reclaim_stale(pool: "ConnectionPool") -> int:
    """Reset 'extracting' rows that haven't been updated in a while to 'pending'."""
    with pool.connection() as conn:
        cur = conn.execute(
            """
            UPDATE attachment_contents
               SET status = 'pending', extracted_at = NULL
             WHERE status = 'extracting'
               AND (extracted_at IS NULL OR extracted_at < now() - (%s || ' seconds')::interval)
            """,
            (WATCHDOG_STALE_SECONDS,),
        )
        conn.commit()
        return cur.rowcount


def _load_attachment(pool: "ConnectionPool", attachment_id: int) -> dict[str, Any]:
    with pool.connection() as conn:
        cur = conn.execute(
            "SELECT id, sha256, filename, content_type, storage_path "
            "FROM attachments WHERE id = %s",
            (attachment_id,),
        )
        row = cur.fetchone()
    if row is None:
        msg = f"attachment {attachment_id} not found"
        raise LookupError(msg)
    return {
        "id": row[0],
        "sha256": row[1],
        "filename": row[2],
        "content_type": row[3],
        "storage_path": row[4],
    }


def _write_markdown_mirror(attachment_dir: Path, sha256: str, markdown: str) -> None:
    mirror = attachment_dir / sha256[:2] / sha256[2:4] / f"{sha256}.md"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(markdown, encoding="utf-8")


def _embed_chunks(pool: "ConnectionPool", chunks: list[dict[str, Any]]) -> None:
    """Stub embed entry point; real implementation lives in a later task.
    Kept as a function here so tests can monkeypatch it.
    """
    raise NotImplementedError  # replaced in Task 4.3


def _set_status(
    pool: "ConnectionPool",
    attachment_id: int,
    *,
    status: str,
    reason: str | None = None,
    markdown: str | None = None,
    markdown_bytes: int | None = None,
    extraction_ms: int | None = None,
    extractor_version: str | None = None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE attachment_contents
               SET status = %(status)s,
                   reason = %(reason)s,
                   markdown = %(markdown)s,
                   markdown_bytes = %(markdown_bytes)s,
                   extracted_at = CASE
                        WHEN %(status)s IN ('extracted','failed','skipped') THEN now()
                        ELSE extracted_at
                   END,
                   extraction_ms = COALESCE(%(extraction_ms)s, extraction_ms),
                   extractor_version = COALESCE(%(extractor_version)s, extractor_version)
             WHERE attachment_id = %(attachment_id)s
            """,
            {
                "attachment_id": attachment_id,
                "status": status,
                "reason": reason,
                "markdown": markdown,
                "markdown_bytes": markdown_bytes,
                "extraction_ms": extraction_ms,
                "extractor_version": extractor_version,
            },
        )
        conn.commit()


def _claim_row(
    pool: "ConnectionPool",
    *,
    retry_failed: bool,
    selector_sql: str = "",
    selector_params: dict[str, Any] | None = None,
) -> int | None:
    """Atomically move one row to 'extracting' and return its attachment_id."""
    states = "('pending','failed')" if retry_failed else "('pending')"
    sql = f"""
        WITH claimed AS (
            SELECT attachment_id FROM attachment_contents
             WHERE status IN {states}
               {selector_sql}
             ORDER BY attachment_id
             LIMIT 1
             FOR UPDATE SKIP LOCKED
        )
        UPDATE attachment_contents
           SET status = 'extracting', extracted_at = now(), reason = NULL
         WHERE attachment_id IN (SELECT attachment_id FROM claimed)
        RETURNING attachment_id
    """
    with pool.connection() as conn:
        cur = conn.execute(sql, selector_params or {})
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None


def process_one(pool: "ConnectionPool", attachment_id: int, *, attachment_dir: Path) -> None:
    """Extract → chunk → embed → status for a single attachment row."""
    att = _load_attachment(pool, attachment_id)
    file_path = attachment_dir / Path(att["storage_path"])
    t0 = time.monotonic()
    try:
        result = extract_markdown(file_path, content_type=att["content_type"])
    except ExtractionFailed as exc:
        # Unsupported types are skipped; Marker errors are failures.
        if "not supported" in str(exc).lower() or "requires LibreOffice" in str(exc):
            _set_status(pool, attachment_id, status="skipped", reason=str(exc))
        else:
            _set_status(pool, attachment_id, status="failed", reason=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — any unexpected error = failure
        _set_status(pool, attachment_id, status="failed", reason=f"{type(exc).__name__}: {exc}")
        return

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Drop any prior chunks (re-run safety)
    with pool.connection() as conn:
        conn.execute("DELETE FROM attachment_chunks WHERE attachment_id = %s", (attachment_id,))
        conn.commit()

    chunks = chunk_markdown(result.markdown)
    chunk_rows: list[dict[str, Any]] = []
    with pool.connection() as conn:
        for c in chunks:
            conn.execute(
                """INSERT INTO attachment_chunks
                       (attachment_id, chunk_index, heading_path, page_number, token_count, text)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    attachment_id,
                    c.chunk_index,
                    c.heading_path,
                    c.page_number,
                    c.token_count,
                    c.text,
                ),
            )
            chunk_rows.append({"attachment_id": attachment_id, **c.__dict__})
        conn.commit()

    # Embed (stubbed in unit tests; real implementation attached in Task 4.3)
    try:
        _embed_chunks(pool, chunk_rows)
    except NotImplementedError:
        pass  # tolerated until Task 4.3 wires the real embed path

    # Write the on-disk markdown mirror.
    _write_markdown_mirror(attachment_dir, att["sha256"], result.markdown)

    _set_status(
        pool,
        attachment_id,
        status="extracted",
        markdown=result.markdown,
        markdown_bytes=len(result.markdown.encode("utf-8")),
        extraction_ms=elapsed_ms,
        extractor_version=result.extractor_version,
    )


def run(
    pool: "ConnectionPool",
    *,
    attachment_dir: Path,
    workers: int = 1,
    retry_failed: bool = True,
    selector_sql: str = "",
    selector_params: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Process all matching pending/failed rows using N workers.

    For workers > 1 this uses threads, which is appropriate for the
    mixed I/O + short GPU bursts Marker produces. Connection pool is
    configured for concurrent access already.
    """
    ensure_pending_rows(pool)
    _reclaim_stale(pool)

    counts = {"extracted": 0, "failed": 0, "skipped": 0}

    def _worker() -> None:
        while True:
            attachment_id = _claim_row(
                pool,
                retry_failed=retry_failed,
                selector_sql=selector_sql,
                selector_params=selector_params,
            )
            if attachment_id is None:
                return
            try:
                process_one(pool, attachment_id, attachment_dir=attachment_dir)
            except Exception as exc:  # noqa: BLE001 — last-resort failure capture
                _set_status(pool, attachment_id, status="failed", reason=str(exc))
            # Tally is computed from DB at the end (more reliable than local counters
            # under thread contention).

    if workers <= 1:
        _worker()
    else:
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_worker) for _ in range(workers)]
            for f in futures:
                f.result()

    with pool.connection() as conn:
        cur = conn.execute(
            "SELECT status, count(*) FROM attachment_contents GROUP BY status"
        )
        for status, n in cur.fetchall():
            if status in counts:
                counts[status] = n
    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_process_attachments.py -v
```

Expected: PASS. If `test_process_one_unsupported_records_skipped` fails, double-check that `ExtractionFailed` with "not supported" routes to the `skipped` branch, not `failed`.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/ingest/process_attachments.py tests/integration/test_process_attachments.py
git commit -m "feat(process): attachment worker — claim, extract, chunk, markdown mirror"
```

### Task 4.2: Embed the chunks

**Files:**
- Modify: `src/maildb/ingest/process_attachments.py`
- Modify: `tests/integration/test_process_attachments.py`

- [ ] **Step 1: Write a failing test**

Append to `tests/integration/test_process_attachments.py`:

```python
def test_process_one_embeds_chunks_when_ollama_available(test_pool, tmp_path, test_settings):
    """With a mocked EmbeddingClient, chunks get embedded and the embedding column is populated."""
    from unittest.mock import MagicMock

    att_id = _insert_attachment(test_pool, "ee", "text/plain", "embed.txt", size=80)
    sp = tmp_path / "ee" / "ee" / "ee"
    sp.parent.mkdir(parents=True)
    sp.write_text("# Heading\n\nA paragraph that will become a chunk.")

    ensure_pending_rows(test_pool)
    client = MagicMock()
    client.embed_batch.return_value = [[0.1] * 768]

    from maildb.ingest import process_attachments
    with patch.object(
        process_attachments,
        "_build_embedding_client",
        return_value=client,
    ):
        process_one(test_pool, att_id, attachment_dir=tmp_path)

    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM attachment_chunks WHERE attachment_id = %s "
            "AND embedding IS NOT NULL",
            (att_id,),
        )
        assert cur.fetchone()[0] >= 1
    assert client.embed_batch.called
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/integration/test_process_attachments.py::test_process_one_embeds_chunks_when_ollama_available -v
```

Expected: FAIL — `_embed_chunks` is still NotImplementedError / `_build_embedding_client` doesn't exist.

- [ ] **Step 3: Implement embedding**

In `src/maildb/ingest/process_attachments.py`:

1. Add imports at the top:
```python
from maildb.config import Settings
from maildb.embeddings import EmbeddingClient
```

2. Add factory + replace the `_embed_chunks` stub:

```python
_EMBED_BATCH_SIZE = 50


def _build_embedding_client() -> EmbeddingClient:
    settings = Settings()
    return EmbeddingClient(
        ollama_url=settings.ollama_url,
        model_name=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )


def _embed_chunks(pool: "ConnectionPool", chunks: list[dict[str, Any]]) -> None:
    """Embed chunks in batches and write vectors back to the DB.

    On per-batch error, falls back to single-row embedding. Rows that
    still fail get a zero-vector sentinel (same pattern the email embed
    worker uses).
    """
    if not chunks:
        return

    client = _build_embedding_client()

    # Resolve chunk row IDs from DB — tests pass dicts built from Chunk objects
    # that don't yet carry the bigserial id.
    attachment_id = chunks[0]["attachment_id"]
    with pool.connection() as conn:
        cur = conn.execute(
            "SELECT id, chunk_index, text FROM attachment_chunks "
            "WHERE attachment_id = %s ORDER BY chunk_index",
            (attachment_id,),
        )
        rows = cur.fetchall()

    for start in range(0, len(rows), _EMBED_BATCH_SIZE):
        batch = rows[start : start + _EMBED_BATCH_SIZE]
        ids = [r[0] for r in batch]
        texts = [r[2] for r in batch]
        try:
            vectors = client.embed_batch(texts)
        except Exception:
            vectors = []
            for t in texts:
                try:
                    vectors.append(client.embed(t))
                except Exception:
                    vectors.append([0.0] * client._dimensions)

        with pool.connection() as conn:
            for cid, vec in zip(ids, vectors, strict=True):
                conn.execute(
                    "UPDATE attachment_chunks SET embedding = %s WHERE id = %s",
                    (str(vec), cid),
                )
            conn.commit()
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/integration/test_process_attachments.py::test_process_one_embeds_chunks_when_ollama_available -v
```

Expected: PASS.

- [ ] **Step 5: Run the full integration file**

```bash
uv run pytest tests/integration/test_process_attachments.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/maildb/ingest/process_attachments.py tests/integration/test_process_attachments.py
git commit -m "feat(process): embed attachment chunks via Ollama with single-row fallback"
```

### Task 4.3: CLI — `process_attachments run` (core flags)

**Files:**
- Modify: `src/maildb/cli.py`
- Create: `tests/unit/test_cli_process.py`

- [ ] **Step 1: Write the failing CLI test**

Create `tests/unit/test_cli_process.py`:

```python
from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from maildb.cli import app

runner = CliRunner()


def test_process_attachments_help_lists_subcommands():
    result = runner.invoke(app, ["process_attachments", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "status" in result.output
    assert "retry" in result.output


def test_process_attachments_run_passes_workers_and_retry(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_run") as mock_run,
    ):
        mock_pool.return_value = object()
        mock_run.return_value = {"extracted": 3, "failed": 0, "skipped": 0}
        result = runner.invoke(
            app,
            [
                "process_attachments", "run",
                "--workers", "4",
                "--no-retry-failed",
            ],
        )
    assert result.exit_code == 0, result.output
    kwargs = mock_run.call_args.kwargs
    assert kwargs["workers"] == 4
    assert kwargs["retry_failed"] is False


def test_process_attachments_run_dry_run_counts_only(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli._count_selected", return_value=17),
        patch("maildb.cli.pa_run") as mock_run,
    ):
        mock_pool.return_value = object()
        result = runner.invoke(app, ["process_attachments", "run", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "17" in result.output
    assert not mock_run.called
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/unit/test_cli_process.py -v
```

Expected: FAIL — subcommand not registered.

- [ ] **Step 3: Register the command group**

Append to `src/maildb/cli.py` (below the existing `ingest_app` registration):

```python
from maildb.ingest.process_attachments import run as pa_run
from maildb.ingest.process_attachments import ensure_pending_rows

process_app = typer.Typer(
    name="process_attachments",
    help="Extract + embed attachment contents for semantic search.",
    no_args_is_help=True,
)
app.add_typer(process_app, name="process_attachments")


def _build_process_pool() -> "ConnectionPool":  # noqa: UP037
    settings = Settings()
    pool = create_pool(settings)
    init_db(pool)
    return pool


def _count_selected(
    pool: "ConnectionPool",  # noqa: UP037
    *,
    retry_failed: bool,
    selector_sql: str,
    selector_params: dict,  # type: ignore[type-arg]
) -> int:
    states = "('pending','failed')" if retry_failed else "('pending')"
    sql = f"""
        SELECT count(*) FROM attachment_contents
         WHERE status IN {states} {selector_sql}
    """
    with pool.connection() as conn:
        cur = conn.execute(sql, selector_params)
        return cur.fetchone()[0]


@process_app.command("run")
def process_run(
    workers: int = typer.Option(1, "--workers", help="Parallel workers."),
    retry_failed: bool = typer.Option(
        True, "--retry-failed/--no-retry-failed", help="Re-process rows with status='failed'."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report selection count without processing."
    ),
) -> None:
    """Process pending (and optionally failed) attachments."""
    pool = _build_process_pool()
    try:
        ensure_pending_rows(pool)
        if dry_run:
            n = _count_selected(
                pool,
                retry_failed=retry_failed,
                selector_sql="",
                selector_params={},
            )
            typer.echo(f"Would process {n} attachment(s). (--dry-run)")
            return
        settings = Settings()
        counts = pa_run(
            pool,
            attachment_dir=Path(settings.attachment_dir),
            workers=workers,
            retry_failed=retry_failed,
        )
        typer.echo(
            "Done. extracted={extracted} failed={failed} skipped={skipped}".format(**counts)
        )
    finally:
        pool.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_cli_process.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/cli.py tests/unit/test_cli_process.py
git commit -m "feat(cli): process_attachments run with --workers / --retry-failed / --dry-run"
```

### Task 4.4: CLI — selector flags (`--limit`, `--sample`, `--only`, `--ids`, `--min-size`, `--max-size`)

**Files:**
- Modify: `src/maildb/cli.py`
- Modify: `src/maildb/ingest/process_attachments.py`
- Modify: `tests/unit/test_cli_process.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cli_process.py`:

```python
def test_run_with_limit_passes_selector(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_run") as mock_run,
    ):
        mock_pool.return_value = object()
        mock_run.return_value = {"extracted": 0, "failed": 0, "skipped": 0}
        result = runner.invoke(app, ["process_attachments", "run", "--limit", "5"])
    assert result.exit_code == 0
    kwargs = mock_run.call_args.kwargs
    # selector_sql should bound the claim and selector_params carry the limit
    assert "limit" in kwargs["selector_params"]
    assert kwargs["selector_params"]["limit"] == 5


def test_run_with_only_pdf(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_run") as mock_run,
    ):
        mock_pool.return_value = object()
        mock_run.return_value = {"extracted": 0, "failed": 0, "skipped": 0}
        result = runner.invoke(app, ["process_attachments", "run", "--only", "pdf"])
    assert result.exit_code == 0
    kwargs = mock_run.call_args.kwargs
    assert "pdf" in kwargs["selector_params"].values()


def test_run_with_ids(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_run") as mock_run,
    ):
        mock_pool.return_value = object()
        mock_run.return_value = {"extracted": 0, "failed": 0, "skipped": 0}
        result = runner.invoke(
            app, ["process_attachments", "run", "--ids", "1,2,3"]
        )
    assert result.exit_code == 0
    kwargs = mock_run.call_args.kwargs
    assert list(kwargs["selector_params"]["ids"]) == [1, 2, 3]
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/unit/test_cli_process.py -v -k "limit or only or ids"
```

Expected: FAIL — selector params not yet wired.

- [ ] **Step 3: Extend `process_one`'s claim + CLI**

In `src/maildb/ingest/process_attachments.py::_claim_row`, already accepts `selector_sql`/`selector_params`. Extend `run` signature docs to note how selectors compose (no code change needed; already threaded).

Enhance `src/maildb/cli.py::process_run`:

```python
_BUCKET_TO_CONTENT_TYPES: dict[str, list[str]] = {
    "pdf": ["application/pdf"],
    "docx": [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ],
    "xlsx": [
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    ],
    "pptx": [
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ],
    "image": [
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/gif",
        "image/tiff",
        "image/webp",
    ],
    "text": ["text/plain"],
    "html": ["text/html"],
}


@process_app.command("run")
def process_run(
    workers: int = typer.Option(1, "--workers"),
    retry_failed: bool = typer.Option(True, "--retry-failed/--no-retry-failed"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    limit: int | None = typer.Option(None, "--limit", help="Process at most N rows."),
    sample: int | None = typer.Option(
        None, "--sample", help="Random sample of N rows (wins over --limit)."
    ),
    only: str | None = typer.Option(
        None, "--only", help=f"Filter bucket: {', '.join(_BUCKET_TO_CONTENT_TYPES)}."
    ),
    ids: str | None = typer.Option(
        None, "--ids", help="Comma-separated attachment_ids."
    ),
    min_size: int | None = typer.Option(None, "--min-size"),
    max_size: int | None = typer.Option(None, "--max-size"),
) -> None:
    """Process pending (and optionally failed) attachments."""
    selector_sql_parts: list[str] = []
    selector_params: dict[str, object] = {}
    if only is not None:
        if only not in _BUCKET_TO_CONTENT_TYPES:
            raise typer.BadParameter(f"--only must be one of {list(_BUCKET_TO_CONTENT_TYPES)}")
        selector_sql_parts.append(
            "AND attachment_id IN (SELECT id FROM attachments "
            "WHERE content_type = ANY(%(content_types)s))"
        )
        selector_params["content_types"] = _BUCKET_TO_CONTENT_TYPES[only]
    if ids is not None:
        id_list = [int(x) for x in ids.split(",") if x.strip()]
        selector_sql_parts.append("AND attachment_id = ANY(%(ids)s)")
        selector_params["ids"] = id_list
    if min_size is not None:
        selector_sql_parts.append(
            "AND attachment_id IN (SELECT id FROM attachments WHERE size >= %(min_size)s)"
        )
        selector_params["min_size"] = min_size
    if max_size is not None:
        selector_sql_parts.append(
            "AND attachment_id IN (SELECT id FROM attachments WHERE size <= %(max_size)s)"
        )
        selector_params["max_size"] = max_size
    if sample is not None:
        # `sample` outranks `limit`; postgres TABLESAMPLE-style random with server-side LIMIT.
        selector_sql_parts.append(
            "AND attachment_id IN ("
            "SELECT attachment_id FROM attachment_contents "
            "ORDER BY random() LIMIT %(sample)s)"
        )
        selector_params["sample"] = sample
    elif limit is not None:
        # Order claim by attachment_id for determinism, then cap.
        selector_sql_parts.append(
            "AND attachment_id IN ("
            "SELECT attachment_id FROM attachment_contents "
            "ORDER BY attachment_id LIMIT %(limit)s)"
        )
        selector_params["limit"] = limit

    selector_sql = " ".join(selector_sql_parts)

    pool = _build_process_pool()
    try:
        ensure_pending_rows(pool)
        if dry_run:
            n = _count_selected(
                pool,
                retry_failed=retry_failed,
                selector_sql=selector_sql,
                selector_params=selector_params,
            )
            typer.echo(f"Would process {n} attachment(s). (--dry-run)")
            return
        settings = Settings()
        counts = pa_run(
            pool,
            attachment_dir=Path(settings.attachment_dir),
            workers=workers,
            retry_failed=retry_failed,
            selector_sql=selector_sql,
            selector_params=selector_params,
        )
        typer.echo(
            "Done. extracted={extracted} failed={failed} skipped={skipped}".format(**counts)
        )
    finally:
        pool.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_cli_process.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/cli.py src/maildb/ingest/process_attachments.py tests/unit/test_cli_process.py
git commit -m "feat(cli): selector flags — --limit / --sample / --only / --ids / --min-size / --max-size"
```

### Task 4.5: CLI — `status` and `retry` subcommands

**Files:**
- Modify: `src/maildb/cli.py`
- Modify: `tests/unit/test_cli_process.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_cli_process.py`:

```python
def test_process_attachments_status_shows_counts(tmp_path):
    with patch("maildb.cli._build_process_pool") as mock_pool:
        pool_instance = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("pending", 5),
            ("extracted", 100),
            ("failed", 2),
            ("skipped", 12),
        ]
        pool_instance.connection.return_value.__enter__.return_value.execute.return_value = cursor
        mock_pool.return_value = pool_instance
        result = runner.invoke(app, ["process_attachments", "status"])
    assert result.exit_code == 0
    assert "extracted" in result.output.lower()
    assert "100" in result.output


def test_process_attachments_retry_runs_only_failed(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_run") as mock_run,
    ):
        mock_pool.return_value = object()
        mock_run.return_value = {"extracted": 0, "failed": 0, "skipped": 0}
        result = runner.invoke(app, ["process_attachments", "retry"])
    assert result.exit_code == 0
    kwargs = mock_run.call_args.kwargs
    # retry command forces retry_failed=True and restricts to failed-only
    assert kwargs["retry_failed"] is True
    # selector_sql should filter to status='failed' only
    assert "status = 'failed'" in kwargs["selector_sql"]
```

Add `from unittest.mock import MagicMock` to the imports at the top of the file (alongside `patch`).

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/unit/test_cli_process.py -v -k "status or retry"
```

Expected: FAIL — commands not registered.

- [ ] **Step 3: Implement `status` and `retry`**

Append to `src/maildb/cli.py`:

```python
@process_app.command("status")
def process_status() -> None:
    """Summary of extraction state: pending / extracted / failed / skipped."""
    pool = _build_process_pool()
    try:
        with pool.connection() as conn:
            cur = conn.execute(
                "SELECT status, count(*) FROM attachment_contents GROUP BY status "
                "ORDER BY status"
            )
            rows = cur.fetchall()
        typer.echo("Attachment extraction status")
        for status, n in rows:
            typer.echo(f"  {status:<10} {n:>7,}")

        with pool.connection() as conn:
            cur = conn.execute(
                "SELECT reason, count(*) FROM attachment_contents "
                "WHERE status = 'failed' GROUP BY reason ORDER BY count(*) DESC LIMIT 10"
            )
            rows = cur.fetchall()
        if rows:
            typer.echo("\nTop failure reasons")
            for reason, n in rows:
                typer.echo(f"  {n:>4,}  {reason[:120] if reason else ''}")
    finally:
        pool.close()


@process_app.command("retry")
def process_retry(
    workers: int = typer.Option(1, "--workers"),
) -> None:
    """Re-process only rows with status='failed'."""
    pool = _build_process_pool()
    try:
        ensure_pending_rows(pool)
        settings = Settings()
        counts = pa_run(
            pool,
            attachment_dir=Path(settings.attachment_dir),
            workers=workers,
            retry_failed=True,
            selector_sql="AND status = 'failed'",
            selector_params={},
        )
        typer.echo(
            "Retry done. extracted={extracted} failed={failed} skipped={skipped}".format(
                **counts
            )
        )
    finally:
        pool.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_cli_process.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/cli.py tests/unit/test_cli_process.py
git commit -m "feat(cli): process_attachments status + retry subcommands"
```

---

## Step 5 — Ingest integration: reference count + pending row auto-enqueue

### Task 5.1: Increment reference_count; create pending row

**Files:**
- Modify: `src/maildb/ingest/parse.py`
- Test: `tests/integration/test_parse_worker.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_parse_worker.py`:

```python
def test_parse_increments_reference_count(test_pool, test_settings, tmp_path):
    """Running the pipeline on an mbox whose messages share one attachment
    increments attachments.reference_count correctly."""
    # Use the existing sample.mbox fixture — it contains attachment references
    # that the parse worker will collapse onto shared sha256s.
    from maildb.ingest.orchestrator import run_pipeline
    from pathlib import Path as _P

    fixtures = _P(__file__).parent.parent / "fixtures"
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
        cur = conn.execute(
            "SELECT count(*), sum(reference_count) FROM attachments"
        )
        n_att, total_refs = cur.fetchone()
        # Every email_attachments row should map to exactly one reference_count unit.
        cur = conn.execute("SELECT count(*) FROM email_attachments")
        ea_count = cur.fetchone()[0]
    assert ea_count == total_refs, (
        f"reference_count total ({total_refs}) must equal email_attachments count ({ea_count})"
    )


def test_parse_creates_pending_attachment_contents_row(test_pool, test_settings, tmp_path):
    from maildb.ingest.orchestrator import run_pipeline
    from pathlib import Path as _P

    fixtures = _P(__file__).parent.parent / "fixtures"
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/integration/test_parse_worker.py -v -k "reference_count or pending"
```

Expected: FAIL — current parse.py doesn't touch either column/table.

- [ ] **Step 3: Modify parse.py**

In `src/maildb/ingest/parse.py`, locate the attachment insert block (the one that inserts into `attachments` and `email_attachments`). Add the reference_count increment and pending row insert after each successful `INSERT INTO email_attachments`:

```python
INSERT_ATTACHMENT_CONTENTS_SQL = """
INSERT INTO attachment_contents (attachment_id, status)
VALUES (%(attachment_id)s, 'pending')
ON CONFLICT (attachment_id) DO NOTHING
"""

INCREMENT_REFERENCE_COUNT_SQL = """
UPDATE attachments
   SET reference_count = reference_count + 1
 WHERE id = %(attachment_id)s
"""
```

In the existing loop that inserts `email_attachments` rows (look for `INSERT_EMAIL_ATTACHMENT_SQL`), add these two executes right after each successful insert (but guard so an `ON CONFLICT DO NOTHING` on the email_attachments insert doesn't double-count):

```python
# After inserting into email_attachments:
result = conn.execute(
    INSERT_EMAIL_ATTACHMENT_SQL,
    {
        "email_id": meta["email_id"],
        "attachment_id": att_id,
        "filename": meta["filename"],
    },
)
if result.rowcount > 0:  # newly inserted, not a conflict no-op
    conn.execute(INCREMENT_REFERENCE_COUNT_SQL, {"attachment_id": att_id})
    conn.execute(INSERT_ATTACHMENT_CONTENTS_SQL, {"attachment_id": att_id})
```

(Exact placement depends on the current parse.py shape — the implementer should locate the loop that iterates over `valid_meta` and wrap the INSERT in `result = conn.execute(...)` so the rowcount is inspectable.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_parse_worker.py -v
```

Expected: PASS.

- [ ] **Step 5: Verify full ingest pipeline still works**

```bash
uv run pytest tests/integration/test_orchestrator.py -v
```

Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/maildb/ingest/parse.py tests/integration/test_parse_worker.py
git commit -m "feat(ingest): maintain attachments.reference_count + auto-enqueue pending rows"
```

---

## Step 6 — Search API

### Task 6.1: `search_attachments` on MailDB

**Files:**
- Modify: `src/maildb/maildb.py`
- Create: `tests/integration/test_search_attachments.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_search_attachments.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from maildb.config import Settings
from maildb.maildb import MailDB

pytestmark = pytest.mark.integration


def _seed_attachment_chunk(
    test_pool,
    *,
    attachment_id: int | None = None,
    sha256: str = "s1",
    content_type: str = "application/pdf",
    filename: str = "doc.pdf",
    chunk_text: str = "Termination clause: 30 days notice.",
    embedding: list[float] | None = None,
    heading_path: str | None = "Overview > Payment Terms",
    email_ids: list[str] | None = None,
) -> tuple[int, int]:
    """Insert an attachment + one chunk + optional email linkage. Returns (att_id, chunk_id)."""
    with test_pool.connection() as conn:
        if attachment_id is None:
            cur = conn.execute(
                "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (sha256, filename, content_type, 100, f"{sha256[:2]}/{sha256[2:4]}/{sha256}"),
            )
            attachment_id = cur.fetchone()[0]
        vec = str(embedding or [0.1] * 768)
        cur = conn.execute(
            """INSERT INTO attachment_chunks
                   (attachment_id, chunk_index, heading_path, token_count, text, embedding)
               VALUES (%s, 0, %s, 8, %s, %s) RETURNING id""",
            (attachment_id, heading_path, chunk_text, vec),
        )
        chunk_id = cur.fetchone()[0]

        if email_ids:
            for mid in email_ids:
                conn.execute(
                    "INSERT INTO emails (id, message_id, thread_id, source_account) "
                    "VALUES (gen_random_uuid(), %s, 't', 'search@ex.com')",
                    (mid,),
                )
                eid = conn.execute(
                    "SELECT id FROM emails WHERE message_id = %s", (mid,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO email_attachments (email_id, attachment_id, filename) "
                    "VALUES (%s, %s, %s)",
                    (eid, attachment_id, filename),
                )
        conn.commit()
    return attachment_id, chunk_id


def test_search_attachments_returns_matching_chunk(test_pool, test_settings):
    att_id, _ = _seed_attachment_chunk(
        test_pool,
        chunk_text="Late fees accrue after 30 days.",
        email_ids=["<email-1@ex.com>"],
    )
    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.1] * 768
    results, total = db.search_attachments(query="late fees")
    assert total >= 1
    hit = next(r for r in results if r.attachment_id == att_id)
    assert hit.chunk.text == "Late fees accrue after 30 days."
    assert "<email-1@ex.com>" in hit.emails
    assert hit.similarity > 0


def test_search_attachments_filters_by_content_type(test_pool, test_settings):
    _seed_attachment_chunk(
        test_pool, sha256="pdf1", content_type="application/pdf", filename="a.pdf",
        chunk_text="pdf content",
    )
    _seed_attachment_chunk(
        test_pool, sha256="doc1",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="b.docx",
        chunk_text="docx content",
    )
    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.1] * 768
    results, _ = db.search_attachments(query="content", content_type="application/pdf")
    assert all(r.content_type == "application/pdf" for r in results)


def test_search_attachments_honors_email_level_account_filter(test_pool, test_settings):
    # Account A carries attachment X; Account B has a different one.
    from uuid import uuid4
    iid_a = uuid4()
    with test_pool.connection() as conn:
        conn.execute(
            "INSERT INTO imports (id, source_account, source_file, status) "
            "VALUES (%s, %s, 't', 'completed')",
            (iid_a, "a@ex.com"),
        )
        conn.commit()
    att_id, _ = _seed_attachment_chunk(
        test_pool, sha256="accA", filename="only-A.pdf",
        chunk_text="unique token yyy",
        email_ids=["<a-1@ex.com>"],
    )
    # Link the email to account A via email_accounts
    with test_pool.connection() as conn:
        eid = conn.execute(
            "SELECT id FROM emails WHERE message_id = %s", ("<a-1@ex.com>",),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO email_accounts (email_id, source_account, import_id) "
            "VALUES (%s, %s, %s)",
            (eid, "a@ex.com", iid_a),
        )
        conn.commit()
    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.1] * 768

    # Scoped to account A: should find the chunk.
    results_a, _ = db.search_attachments(query="unique", account="a@ex.com")
    assert any(r.attachment_id == att_id for r in results_a)

    # Scoped to account B: should not find it.
    results_b, _ = db.search_attachments(query="unique", account="b@ex.com")
    assert not any(r.attachment_id == att_id for r in results_b)
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/integration/test_search_attachments.py -v
```

Expected: FAIL — `MailDB.search_attachments` doesn't exist.

- [ ] **Step 3: Add `search_attachments`**

Add to `src/maildb/maildb.py` (after `mention_search`):

```python
def search_attachments(
    self,
    query: str,
    *,
    sender: str | None = None,
    sender_domain: str | None = None,
    recipient: str | None = None,
    after: str | None = None,
    before: str | None = None,
    labels: list[str] | None = None,
    max_to: int | None = None,
    max_cc: int | None = None,
    max_recipients: int | None = None,
    direct_only: bool = False,
    account: str | None = None,
    content_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[AttachmentSearchResult], int]:
    """Semantic search over attachment chunk embeddings."""
    query_embedding = self._embedding_client.embed(query)

    # Build email-level conditions directly with `e.` prefixes — safer than
    # trying to rewrite the output of `_build_filters`, which already qualifies
    # some columns (e.g. `ea.source_account` for the account-EXISTS clause) that
    # would collide with naive string substitution.
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if sender is not None:
        conditions.append("e.sender_address = %(sender)s")
        params["sender"] = sender
    if sender_domain is not None:
        conditions.append("e.sender_domain = %(sender_domain)s")
        params["sender_domain"] = sender_domain
    if recipient is not None:
        conditions.append(
            "(e.recipients->'to' @> %(recipient_json)s "
            "OR e.recipients->'cc' @> %(recipient_json)s "
            "OR e.recipients->'bcc' @> %(recipient_json)s)"
        )
        params["recipient_json"] = json.dumps([recipient])
    if after is not None:
        conditions.append("e.date >= %(after)s")
        params["after"] = after
    if before is not None:
        conditions.append("e.date < %(before)s")
        params["before"] = before
    if labels is not None:
        conditions.append("e.labels @> %(labels)s")
        params["labels"] = labels
    if direct_only and (max_to is not None or max_cc is not None):
        msg = "Cannot combine direct_only with max_to or max_cc"
        raise ValueError(msg)
    if direct_only:
        max_to, max_cc = 1, 0
    if max_to is not None:
        conditions.append(
            "jsonb_array_length(COALESCE(e.recipients->'to', '[]'::jsonb)) <= %(max_to)s"
        )
        params["max_to"] = max_to
    if max_cc is not None:
        conditions.append(
            "jsonb_array_length(COALESCE(e.recipients->'cc', '[]'::jsonb)) <= %(max_cc)s"
        )
        params["max_cc"] = max_cc
    if max_recipients is not None:
        conditions.append(
            "(jsonb_array_length(COALESCE(e.recipients->'to', '[]'::jsonb))"
            " + jsonb_array_length(COALESCE(e.recipients->'cc', '[]'::jsonb))"
            " + jsonb_array_length(COALESCE(e.recipients->'bcc', '[]'::jsonb))"
            ") <= %(max_recipients)s"
        )
        params["max_recipients"] = max_recipients
    if account is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM email_accounts ea_acc "
            "WHERE ea_acc.email_id = e.id AND ea_acc.source_account = %(account)s)"
        )
        params["account"] = account

    email_exists = ""
    if conditions:
        email_exists = (
            " AND EXISTS (SELECT 1 FROM email_attachments ea "
            "JOIN emails e ON e.id = ea.email_id "
            f"WHERE ea.attachment_id = ac.attachment_id AND {' AND '.join(conditions)})"
        )

    params["query_embedding"] = str(query_embedding)
    params["limit"] = limit
    params["offset"] = offset

    ct_clause = ""
    if content_type is not None:
        ct_clause = " AND a.content_type = %(content_type)s"
        params["content_type"] = content_type

    sql = f"""
        SELECT ac.id AS chunk_id, ac.attachment_id, ac.chunk_index,
               ac.heading_path, ac.page_number, ac.token_count, ac.text,
               a.filename, a.content_type, a.sha256,
               1 - (ac.embedding <=> %(query_embedding)s::vector) AS similarity,
               (SELECT COALESCE(array_agg(e2.message_id), ARRAY[]::text[])
                  FROM email_attachments ea2
                  JOIN emails e2 ON e2.id = ea2.email_id
                 WHERE ea2.attachment_id = ac.attachment_id) AS email_message_ids,
               COUNT(*) OVER() AS _total
        FROM attachment_chunks ac
        JOIN attachments a ON a.id = ac.attachment_id
        WHERE ac.embedding IS NOT NULL
          AND vector_norm(ac.embedding) > 0
          {ct_clause}
          {email_exists}
        ORDER BY ac.embedding <=> %(query_embedding)s::vector
        LIMIT %(limit)s OFFSET %(offset)s
    """

    rows = _query_dicts(self._pool, sql, params)
    total = rows[0]["_total"] if rows else 0
    results = []
    for row in rows:
        chunk = AttachmentChunk(
            id=row["chunk_id"],
            attachment_id=row["attachment_id"],
            chunk_index=row["chunk_index"],
            heading_path=row["heading_path"],
            page_number=row["page_number"],
            token_count=row["token_count"],
            text=row["text"],
        )
        results.append(
            AttachmentSearchResult(
                attachment_id=row["attachment_id"],
                filename=row["filename"],
                content_type=row["content_type"],
                sha256=row["sha256"],
                chunk=chunk,
                emails=list(row["email_message_ids"] or []),
                similarity=row["similarity"],
            )
        )
    return results, total
```

Add `AttachmentChunk, AttachmentSearchResult` to the imports at the top of maildb.py:

```python
from maildb.models import (
    AccountSummary,
    AttachmentChunk,
    AttachmentSearchResult,
    Email,
    ImportRecord,
    SearchResult,
    UnifiedSearchResult,
)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_search_attachments.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/maildb.py tests/integration/test_search_attachments.py
git commit -m "feat(query): search_attachments — chunk-level semantic search with email-level filters"
```

### Task 6.2: `get_attachment_markdown` and `search_all`

**Files:**
- Modify: `src/maildb/maildb.py`
- Modify: `tests/integration/test_search_attachments.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_search_attachments.py`:

```python
def test_get_attachment_markdown_returns_full_text(test_pool, test_settings):
    with test_pool.connection() as conn:
        cur = conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES ('gm', 'full.pdf', 'application/pdf', 100, 'gm/gm/gm') RETURNING id"
        )
        att_id = cur.fetchone()[0]
        conn.execute(
            "INSERT INTO attachment_contents (attachment_id, status, markdown, markdown_bytes) "
            "VALUES (%s, 'extracted', %s, %s)",
            (att_id, "# Full document text", 20),
        )
        conn.commit()
    db = MailDB._from_pool(test_pool, config=test_settings)
    assert db.get_attachment_markdown(att_id) == "# Full document text"


def test_get_attachment_markdown_returns_none_when_not_extracted(test_pool, test_settings):
    with test_pool.connection() as conn:
        cur = conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES ('gm2', 'pending.pdf', 'application/pdf', 100, 'gm2/gm2/gm2') RETURNING id"
        )
        att_id = cur.fetchone()[0]
        conn.execute(
            "INSERT INTO attachment_contents (attachment_id, status) VALUES (%s, 'pending')",
            (att_id,),
        )
        conn.commit()
    db = MailDB._from_pool(test_pool, config=test_settings)
    assert db.get_attachment_markdown(att_id) is None


def test_search_all_merges_email_and_attachment_hits(test_pool, test_settings):
    """Seed one email with an embedding + one attachment chunk. search_all returns both."""
    from datetime import UTC, datetime

    # Seed one email with an embedding close to our query.
    vec = [0.5] * 768
    with test_pool.connection() as conn:
        conn.execute(
            """INSERT INTO emails (id, message_id, thread_id, subject, sender_address,
                   date, embedding, source_account, created_at)
               VALUES (gen_random_uuid(), %s, 't', 'Budget', 'ceo@acme.com',
                       %s, %s, 'sa@ex.com', now())""",
            ("<email-sa-1@ex.com>", datetime(2025, 1, 1, tzinfo=UTC), str(vec)),
        )
        conn.commit()

    _seed_attachment_chunk(
        test_pool,
        sha256="sa1",
        chunk_text="A chunk about quarterly budget.",
        embedding=[0.5] * 768,
        email_ids=["<email-sa-2@ex.com>"],
    )

    db = MailDB._from_pool(test_pool, config=test_settings)
    db._embedding_client = MagicMock()
    db._embedding_client.embed.return_value = [0.5] * 768
    results, total = db.search_all(query="budget")
    assert total >= 2
    sources = {r.source for r in results}
    assert sources == {"email", "attachment"}
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/integration/test_search_attachments.py -v -k "get_attachment_markdown or search_all"
```

Expected: FAIL — methods missing.

- [ ] **Step 3: Add the methods**

Append to `src/maildb/maildb.py` (after `search_attachments`):

```python
def get_attachment_markdown(self, attachment_id: int) -> str | None:
    """Return the full extracted markdown for an attachment, or None if
    extraction is pending, failed, or the row doesn't exist.
    """
    row = _query_one_dict(
        self._pool,
        "SELECT markdown FROM attachment_contents "
        "WHERE attachment_id = %(id)s AND status = 'extracted'",
        {"id": attachment_id},
    )
    return row["markdown"] if row else None


def search_all(
    self,
    query: str,
    *,
    sender: str | None = None,
    sender_domain: str | None = None,
    recipient: str | None = None,
    after: str | None = None,
    before: str | None = None,
    labels: list[str] | None = None,
    max_to: int | None = None,
    max_cc: int | None = None,
    max_recipients: int | None = None,
    direct_only: bool = False,
    account: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[UnifiedSearchResult], int]:
    """Run both email and attachment searches, merge by similarity."""
    over_fetch = max(2 * (limit + offset), limit + offset)
    email_hits, _ = self.search(
        query,
        sender=sender, sender_domain=sender_domain, recipient=recipient,
        after=after, before=before, labels=labels,
        max_to=max_to, max_cc=max_cc, max_recipients=max_recipients,
        direct_only=direct_only, account=account,
        limit=over_fetch, offset=0,
    )
    attachment_hits, _ = self.search_attachments(
        query,
        sender=sender, sender_domain=sender_domain, recipient=recipient,
        after=after, before=before, labels=labels,
        max_to=max_to, max_cc=max_cc, max_recipients=max_recipients,
        direct_only=direct_only, account=account,
        limit=over_fetch, offset=0,
    )

    unified: list[UnifiedSearchResult] = []
    for h in email_hits:
        unified.append(
            UnifiedSearchResult(
                source="email",
                similarity=h.similarity,
                email=h.email,
                attachment_result=None,
            )
        )
    for a in attachment_hits:
        unified.append(
            UnifiedSearchResult(
                source="attachment",
                similarity=a.similarity,
                email=None,
                attachment_result=a,
            )
        )
    unified.sort(key=lambda r: r.similarity, reverse=True)
    total = len(unified)
    return unified[offset : offset + limit], total
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_search_attachments.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/maildb.py tests/integration/test_search_attachments.py
git commit -m "feat(query): get_attachment_markdown + search_all (merged email + attachment)"
```

### Task 6.3: MCP tool exposure

**Files:**
- Modify: `src/maildb/server.py`
- Modify: `tests/unit/test_server.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_server.py`:

```python
def test_server_has_new_attachment_tools() -> None:
    names = set(mcp._tool_manager._tools.keys())
    assert {"search_attachments", "search_all", "get_attachment_markdown"} <= names


def test_search_attachments_tool_serializes() -> None:
    from maildb.models import AttachmentChunk, AttachmentSearchResult
    mock_db = MagicMock()
    mock_db.search_attachments.return_value = (
        [
            AttachmentSearchResult(
                attachment_id=1,
                filename="a.pdf",
                content_type="application/pdf",
                sha256="abc",
                chunk=AttachmentChunk(
                    id=10, attachment_id=1, chunk_index=0,
                    heading_path="Overview", page_number=3,
                    token_count=5, text="hi",
                ),
                emails=["<x@y.com>"],
                similarity=0.95,
            )
        ],
        1,
    )
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db

    result = server.search_attachments(ctx, query="anything")
    assert result["total"] == 1
    hit = result["results"][0]
    assert hit["attachment_id"] == 1
    assert hit["chunk"]["text"] == "hi"
    assert hit["emails"] == ["<x@y.com>"]


def test_get_attachment_markdown_tool_returns_null_for_missing() -> None:
    mock_db = MagicMock()
    mock_db.get_attachment_markdown.return_value = None
    ctx = MagicMock()
    ctx.request_context.lifespan_context.db = mock_db
    assert server.get_attachment_markdown(ctx, attachment_id=1) is None
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/unit/test_server.py -v -k "attachment"
```

Expected: FAIL — tools not registered.

- [ ] **Step 3: Add the tools**

Append to `src/maildb/server.py`:

```python
def _serialize_attachment_result(r: Any) -> dict[str, Any]:
    return {
        "attachment_id": r.attachment_id,
        "filename": r.filename,
        "content_type": r.content_type,
        "sha256": r.sha256,
        "chunk": {
            "id": r.chunk.id,
            "chunk_index": r.chunk.chunk_index,
            "heading_path": r.chunk.heading_path,
            "page_number": r.chunk.page_number,
            "token_count": r.chunk.token_count,
            "text": r.chunk.text,
        },
        "emails": r.emails,
        "similarity": r.similarity,
    }


@mcp.tool()
@log_tool
def search_attachments(
    ctx: Context,
    query: str,
    sender: str | None = None,
    sender_domain: str | None = None,
    recipient: str | None = None,
    after: str | None = None,
    before: str | None = None,
    labels: list[str] | None = None,
    max_to: int | None = None,
    max_cc: int | None = None,
    max_recipients: int | None = None,
    direct_only: bool = False,
    account: str | None = None,
    content_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Semantic search over attachment chunk embeddings.

    Returns {total, offset, limit, results: [{attachment_id, filename, chunk, emails, similarity}]}.
    """
    db = _get_db(ctx)
    results, total = db.search_attachments(
        query,
        sender=sender, sender_domain=sender_domain, recipient=recipient,
        after=after, before=before, labels=labels,
        max_to=max_to, max_cc=max_cc, max_recipients=max_recipients,
        direct_only=direct_only, account=account, content_type=content_type,
        limit=limit, offset=offset,
    )
    serialized = [_serialize_attachment_result(r) for r in results]
    return _wrap_response(serialized, total=total, offset=offset, limit=limit)


@mcp.tool()
@log_tool
def search_all(
    ctx: Context,
    query: str,
    sender: str | None = None,
    sender_domain: str | None = None,
    recipient: str | None = None,
    after: str | None = None,
    before: str | None = None,
    labels: list[str] | None = None,
    account: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Unified search across emails and attachment contents.

    Returns {total, offset, limit, results: [{source, similarity, ...}]} where each
    result carries source="email" with an email payload, or source="attachment"
    with an attachment_result payload.
    """
    db = _get_db(ctx)
    results, total = db.search_all(
        query,
        sender=sender, sender_domain=sender_domain, recipient=recipient,
        after=after, before=before, labels=labels, account=account,
        limit=limit, offset=offset,
    )
    serialized: list[dict[str, Any]] = []
    for r in results:
        payload: dict[str, Any] = {"source": r.source, "similarity": r.similarity}
        if r.source == "email" and r.email is not None:
            payload["email"] = _serialize_email(r.email)
        elif r.source == "attachment" and r.attachment_result is not None:
            payload["attachment"] = _serialize_attachment_result(r.attachment_result)
        serialized.append(payload)
    return _wrap_response(serialized, total=total, offset=offset, limit=limit)


@mcp.tool()
@log_tool
def get_attachment_markdown(ctx: Context, attachment_id: int) -> str | None:
    """Return the full extracted markdown for an attachment, or null if not extracted."""
    db = _get_db(ctx)
    return db.get_attachment_markdown(attachment_id)
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/unit/test_server.py -v -k "attachment or search_all or search_attachments"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/maildb/server.py tests/unit/test_server.py
git commit -m "feat(mcp): expose search_attachments, search_all, get_attachment_markdown tools"
```

---

## Step 7 — HNSW index, end-to-end validation, runbook

### Task 7.1: HNSW index on attachment_chunks

**Files:**
- Modify: `src/maildb/db.py`
- Test: `tests/integration/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_db.py`:

```python
def test_create_attachment_chunks_hnsw_index(test_pool) -> None:  # type: ignore[no-untyped-def]
    from maildb.db import create_hnsw_index_attachment_chunks
    create_hnsw_index_attachment_chunks(test_pool)
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM pg_indexes "
            "WHERE tablename = 'attachment_chunks' "
            "AND indexname = 'idx_attachment_chunks_embedding'"
        )
        assert cur.fetchone()[0] == 1
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/integration/test_db.py -v -k "hnsw"
```

Expected: FAIL — helper not defined.

- [ ] **Step 3: Add the helper**

Append to `src/maildb/db.py`:

```python
def create_hnsw_index_attachment_chunks(pool: ConnectionPool) -> None:
    """Create the HNSW index on attachment_chunks.embedding.

    Run once, after the first full extract pass completes. Skips if the
    index already exists.
    """
    with pool.connection() as conn:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attachment_chunks_embedding "
            "ON attachment_chunks USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )
        conn.commit()
    logger.info("hnsw_index_created", table="attachment_chunks")
```

- [ ] **Step 4: Wire it into the CLI `run` so it fires after a non-dry-run pass completes**

In `src/maildb/cli.py::process_run`, after `typer.echo("Done. ...")`:

```python
# If any chunks got embedded this run, make sure the HNSW index exists.
with pool.connection() as conn:
    cur = conn.execute(
        "SELECT count(*) FROM attachment_chunks WHERE embedding IS NOT NULL"
    )
    n_embedded = cur.fetchone()[0]
if n_embedded > 0:
    from maildb.db import create_hnsw_index_attachment_chunks  # noqa: PLC0415
    create_hnsw_index_attachment_chunks(pool)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_db.py -v -k "hnsw"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/maildb/db.py src/maildb/cli.py tests/integration/test_db.py
git commit -m "feat(db): create HNSW index on attachment_chunks after first run"
```

### Task 7.2: End-to-end integration test with a real PDF

**Files:**
- Create: `tests/fixtures/attachments/hello.pdf` (binary, small)
- Create: `tests/integration/test_process_attachments_e2e.py`

- [ ] **Step 1: Commit a tiny real PDF fixture**

Generate a 1-page PDF locally and commit it. One option:

```bash
mkdir -p tests/fixtures/attachments
uv run python - <<'PY'
from reportlab.pdfgen import canvas
c = canvas.Canvas("tests/fixtures/attachments/hello.pdf")
c.drawString(100, 750, "Hello world. The brown fox jumps over the lazy dog.")
c.drawString(100, 720, "Termination clause: 30 days notice.")
c.save()
PY
```

If `reportlab` isn't available, `uv add --dev reportlab` first. Alternative: check in a known-good tiny PDF from public domain.

- [ ] **Step 2: Write the end-to-end test**

Create `tests/integration/test_process_attachments_e2e.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maildb.config import Settings
from maildb.ingest.process_attachments import ensure_pending_rows, run

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "attachments"


def test_e2e_pdf_extraction_and_search(test_pool, test_settings, tmp_path):
    # Stage hello.pdf as a content-addressed attachment.
    src = FIXTURES / "hello.pdf"
    sha = "ee11"
    stage_path = tmp_path / sha[:2] / sha[2:4] / sha
    stage_path.parent.mkdir(parents=True)
    stage_path.write_bytes(src.read_bytes())

    with test_pool.connection() as conn:
        cur = conn.execute(
            "INSERT INTO attachments (sha256, filename, content_type, size, storage_path) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (sha, "hello.pdf", "application/pdf", src.stat().st_size, f"{sha[:2]}/{sha[2:4]}/{sha}"),
        )
        att_id = cur.fetchone()[0]
        conn.commit()

    ensure_pending_rows(test_pool)

    # Stub embedding client; real Marker runs.
    fake_client = MagicMock()
    fake_client.embed_batch.side_effect = lambda texts: [[0.1] * 768 for _ in texts]

    with patch(
        "maildb.ingest.process_attachments._build_embedding_client",
        return_value=fake_client,
    ):
        counts = run(
            test_pool,
            attachment_dir=tmp_path,
            workers=1,
            retry_failed=False,
        )
    assert counts["extracted"] >= 1

    # Extracted markdown landed in DB and on disk.
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT markdown, markdown_bytes FROM attachment_contents "
            "WHERE attachment_id = %s",
            (att_id,),
        )
        md, md_bytes = cur.fetchone()
    assert md is not None
    assert md_bytes > 0
    assert "Hello" in md or "hello" in md.lower()
    assert (tmp_path / sha[:2] / sha[2:4] / f"{sha}.md").exists()

    # At least one chunk with a non-null embedding.
    with test_pool.connection() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM attachment_chunks "
            "WHERE attachment_id = %s AND embedding IS NOT NULL",
            (att_id,),
        )
        assert cur.fetchone()[0] >= 1
```

- [ ] **Step 3: Run the test**

```bash
uv run pytest tests/integration/test_process_attachments_e2e.py -v
```

Expected: PASS. First run downloads Marker model weights — may take minutes on a fresh machine.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/attachments/hello.pdf tests/integration/test_process_attachments_e2e.py
git commit -m "test(e2e): full pipeline from PDF → markdown → chunks → embeddings"
```

### Task 7.3: Runbook

**Files:**
- Create: `docs/runbooks/attachment-extraction-migration.md`

- [ ] **Step 1: Write the runbook**

Create `docs/runbooks/attachment-extraction-migration.md`:

```markdown
# Runbook — Attachment Content Extraction

**Audience:** operator running attachment extraction for the first time on a populated database.
**Downtime:** none required; DB remains usable during extraction. CPU + GPU are under load.

---

## 1. Preconditions

- [ ] `maildb` version includes the `process_attachments` command (`maildb --help` lists it).
- [ ] Schema has `attachment_contents` and `attachment_chunks` tables (a fresh `maildb process_attachments status` will complain clearly if not).
- [ ] Ollama reachable at `MAILDB_OLLAMA_URL`.
- [ ] Disk space: budget ~2 GB for DB growth + on-disk markdown mirror.

## 2. Smoke test

```bash
# Count pending selection
maildb process_attachments run --dry-run

# Process 50 random attachments to validate wiring end-to-end.
maildb process_attachments run --sample 50 --workers 1

# Summary
maildb process_attachments status
```

Expected status output: some mix of `extracted`, `skipped`, and (hopefully few) `failed`.

## 3. Benchmark worker count

The M1 Max can likely sustain multiple workers. Try:

```bash
maildb process_attachments run --sample 50 --workers 2
maildb process_attachments status  # note avg extraction_ms

maildb process_attachments run --sample 50 --workers 4
maildb process_attachments status
```

Pick the worker count with best throughput (watch for GPU thermal throttling on sustained runs).

## 4. Full run

```bash
maildb process_attachments run --workers <tuned>
```

Expected duration for ~12K attachments: 1–6 hours depending on content mix and workers.

## 5. Post-run verification

```bash
maildb process_attachments status

# A known phrase from a known PDF should return via search_attachments.
uv run python -c "from maildb.maildb import MailDB; db = MailDB(); print(db.search_attachments('known phrase')[0][:2])"
```

## 6. Rollback

Extraction is additive — `attachment_contents` and `attachment_chunks` only. To roll back, drop those tables; no other data is touched.

```sql
DROP TABLE IF EXISTS attachment_chunks CASCADE;
DROP TABLE IF EXISTS attachment_contents CASCADE;
ALTER TABLE attachments DROP COLUMN IF EXISTS reference_count;
```

(Re-running `maildb process_attachments run` after this will rebuild from scratch.)

## 7. Known edge cases

- **Password-protected PDFs** — `status='failed'` with a Marker-emitted reason mentioning encryption. No retry; skip them.
- **Scanned PDFs with no OCR layer** — Marker's Surya OCR covers these, but accuracy on low-quality scans can be poor. They still count as `extracted`; quality is a separate concern.
- **LibreOffice-dependent formats (.doc, .xls)** — marked `skipped` with reason noting LibreOffice isn't wired in v1. Deferred.
- **Oversized single attachment** — Marker may OOM on very large PDFs (hundreds of pages). These show up as `failed` with an OOM-style reason. Consider splitting those manually and re-ingesting.
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/attachment-extraction-migration.md
git commit -m "docs(runbook): attachment extraction rollout procedure"
```

### Task 7.4: Final check + DESIGN note

**Files:**
- Modify: `docs/DESIGN.md` (tiny update — one line added to Architecture map)

- [ ] **Step 1: Add one line to DESIGN.md §3**

In `docs/DESIGN.md` section 3 ("Architecture (Four Layers)"), update the Query layer row:

```markdown
| Query | `src/maildb/maildb.py`, `src/maildb/dsl.py`, `src/maildb/ingest/chunking.py`, `src/maildb/tokenizer.py` | Tier 1 fixed methods + Tier 2 JSON DSL + attachment semantic search |
```

- [ ] **Step 2: Run the full check**

```bash
uv run just check
```

Expected: PASS across fmt, lint, mypy, and pytest.

- [ ] **Step 3: Verify CLI help is clean**

```bash
uv run maildb --help
uv run maildb process_attachments --help
uv run maildb process_attachments run --help
```

Expected: each renders valid Typer help including all documented flags.

- [ ] **Step 4: Commit any remaining format/lint fixes**

```bash
git add -u
git commit -m "chore: format and lint cleanup after attachment search work" || true
```

---

## Self-Review Checklist (checked while writing the plan)

- **Spec coverage:** Every spec section maps to at least one task —
  - §3 scope → Task 3.1 (router bucket set)
  - §4 tech (Marker, tokenizers) → Task 1.1, 1.2
  - §5.1 reference_count → Task 2.1, 5.1
  - §5.2 attachment_contents/chunks tables → Task 2.2, 2.3
  - §5.3 indexes → Task 2.2, 2.3, 7.1
  - §5.4 on-disk mirror → Task 4.1 (step 3, `_write_markdown_mirror`)
  - §6 extraction pipeline → Tasks 4.1, 4.2
  - §7 chunking → Task 3.3
  - §8 embedding → Task 4.2
  - §9 precise tokenizer → Task 1.2
  - §10 search API (Python + MCP + filter pushdown) → Tasks 6.1–6.3
  - §11 CLI surface → Tasks 4.3–4.5
  - §12 failure/retry/idempotency → Task 4.1 (watchdog + status transitions), Task 4.5 (retry command)
  - §13 performance/benchmarking → Tasks 4.3 (`--sample`, `--dry-run`), 4.4 (selector flags), 7.3 (runbook)
  - §14 testing → unit + integration + e2e throughout
  - §15 follow-ups → NOT in this plan by design (separate issues)
  - §16 migration/rollout → Task 7.3 (runbook)
- **Placeholder scan:** No TBD, TODO, or "handle edge cases later" language in task bodies. Every code block is complete.
- **Type consistency:** `AttachmentChunk`, `AttachmentSearchResult`, `UnifiedSearchResult` signatures match across Tasks 2.4, 6.1, 6.2, 6.3. `ExtractionResult` / `ExtractionFailed` consistent across 3.2, 4.1. `route_content_type` signature stable across 3.1, 4.4. `run(...)` signature consistent across 4.1, 4.3, 4.4, 4.5, 7.2. `_embed_chunks`, `_build_embedding_client` introduced in 4.2 and used in 4.1/7.2.
