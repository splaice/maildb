# MailDB — Implementation Design Spec

**Personal Email Database for Agent-Powered Retrieval**

Design Spec — March 2026

---

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Interface | Python library (no web framework) | Agent calls `MailDB` class directly via tool use |
| Database driver | psycopg3 (sync) | Direct SQL control, no ORM overhead, fits single-table design |
| Async/sync | Fully sync | Simpler API for consumers; ingestion bottleneck is Ollama, not DB I/O |
| Schema management | Idempotent DDL via `init_db()` | `CREATE IF NOT EXISTS` — no migration tooling needed for a stable single-table schema |
| Structure | Layered modules | One module per responsibility, matching the architecture doc's three layers |

---

## Project Structure

```
maildb/
├── CLAUDE.md
├── ARCHITECTURE.md
├── pyproject.toml
├── uv.lock
├── justfile
├── src/
│   └── maildb/
│       ├── __init__.py          # Public API: exports MailDB, Email
│       ├── config.py            # Settings via pydantic-settings
│       ├── db.py                # Connection pool, init_db()
│       ├── schema.sql           # Idempotent DDL (CREATE IF NOT EXISTS)
│       ├── models.py            # Email dataclass
│       ├── parsing.py           # Mbox parsing, header extraction, body cleaning
│       ├── embeddings.py        # Ollama embedding client
│       ├── maildb.py            # MailDB class — query methods
│       └── ingest.py            # Ingestion pipeline orchestrator
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   └── sample.mbox          # ~10 crafted messages for testing
│   ├── unit/
│   │   ├── test_parsing.py
│   │   ├── test_cleaning.py
│   │   ├── test_models.py
│   │   └── test_embeddings.py
│   └── integration/
│       ├── test_db.py
│       ├── test_ingest.py
│       └── test_maildb.py
└── docs/
```

---

## Configuration

**`config.py`** uses pydantic-settings with `MAILDB_` environment variable prefix.

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `database_url` | `str` | `postgresql://localhost:5432/maildb` | PostgreSQL connection string |
| `ollama_url` | `str` | `http://localhost:11434` | Ollama API endpoint |
| `embedding_model` | `str` | `nomic-embed-text` | Model name for embeddings |
| `embedding_dimensions` | `int` | `768` | Vector size |
| `user_email` | `str \| None` | `None` | User's email address (needed for `unreplied()`, `top_contacts(direction)`) |

Loads from environment variables or `.env` file.

---

## Schema & Database Layer

**`schema.sql`** — idempotent DDL applied by `init_db()`:

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
```

Indexes (all `CREATE INDEX IF NOT EXISTS`):

| Index | Column(s) | Type | Purpose |
|-------|-----------|------|---------|
| `idx_email_sender_address` | `sender_address` | B-tree | Lookup by exact sender |
| `idx_email_sender_domain` | `sender_domain` | B-tree | Lookup by domain |
| `idx_email_date` | `date` | B-tree | Date range filtering and ordering |
| `idx_email_thread_id` | `thread_id` | B-tree | Thread reconstruction |
| `idx_email_message_id` | `message_id` | B-tree (unique) | Deduplication and reply chains |
| `idx_email_in_reply_to` | `in_reply_to` | B-tree | Unreplied message detection |
| `idx_email_has_attachment` | `has_attachment` | B-tree (partial, `WHERE has_attachment = TRUE`) | Attachment filtering |
| `idx_email_labels` | `labels` | GIN | Array containment queries |
| `idx_email_recipients` | `recipients` | GIN | Recipient searches |
| `idx_email_embedding` | `embedding` | HNSW (cosine, `m=16`, `ef_construction=64`) | Approximate nearest neighbor search |

**`db.py`:**

- `create_pool(config) -> ConnectionPool` — creates a psycopg3 connection pool
- `init_db(pool)` — reads `schema.sql` via `importlib.resources` and executes it
- `references` is quoted in DDL since it's a SQL reserved word

---

## Models

**`models.py`** — plain Python dataclasses, no ORM.

```python
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
    def from_row(cls, row) -> "Email":
        # Handles JSONB deserialization for recipients/attachments
        ...
```

---

## Parsing & Body Cleaning

**`parsing.py`** handles raw mbox data to structured dicts.

**Mbox iteration:**
- Takes a file path, opens with `mailbox.mbox()`, yields one parsed dict per message
- Each dict contains all extracted fields (minus `embedding` and `id`)
- Malformed messages (missing Message-ID, unparseable date, corrupted MIME) are logged via structlog and skipped — they do not halt ingestion

**Header extraction:**
- `message_id` — from Message-ID header, angle brackets stripped
- `sender_name`, `sender_address`, `sender_domain` — via `email.utils.parseaddr()`, domain from splitting on `@`
- `recipients` — via `email.utils.getaddresses()` for To/Cc/Bcc headers
- `date` — via `email.utils.parsedate_to_datetime()`, normalized to UTC. If the parsed datetime is timezone-naive (missing timezone in header), assume UTC and attach `timezone.utc`
- `in_reply_to` — from In-Reply-To header, angle brackets stripped
- `references` — from References header, split into list of message-ids
- `attachments` — MIME walk, collect metadata for non-inline parts

**Threading logic:**
1. If `references` is non-empty → `thread_id` = first element
2. Else if `in_reply_to` is set → `thread_id` = that value
3. Else → `thread_id` = own `message_id`

**Body text extraction:**
- Walk MIME parts, prefer `text/plain`
- HTML-only fallback: convert via BeautifulSoup `.get_text()`

**Body cleaning pipeline** (each step is a separate testable function):
1. **Quoted reply removal** — strip lines starting with `>` (any depth). Strip Outlook-style blocks starting with `-----Original Message-----`
2. **Signature removal** — strip everything below `-- ` (dash-dash-space on its own line)
3. **Whitespace normalization** — collapse multiple blank lines, strip trailing whitespace

---

## Embeddings

**`embeddings.py`** — wraps the Ollama Python client.

**`EmbeddingClient` class:**
- Constructor takes `ollama_url`, `model_name`, `dimensions` from config
- `embed(text: str) -> list[float]` — single string embedding
- `embed_batch(texts: list[str]) -> list[list[float]]` — batch embedding via Ollama's native batch support

**Embedding content format:**
```
Subject: {subject}
From: {sender_name}

{body_text}
```

`build_embedding_text(subject, sender_name, body_text) -> str` constructs this string. Used during both ingestion and query time.

**Error handling:**
- Ollama unreachable during ingestion → insert with `embedding = NULL`, backfill later
- Ollama unreachable during `search()` → raise exception (query can't proceed without vector)

---

## Ingestion Pipeline

**`ingest.py`** — orchestrates parsing, embedding, and DB insertion.

**`ingest_mbox(pool, embedding_client, mbox_path, batch_size=100)`:**
1. Iterate parsed messages from `parsing.py`
2. Accumulate into batches of `batch_size`
3. Per batch:
   - Build embedding text via `build_embedding_text()`
   - Call `embed_batch()` for all vectors in one Ollama call
   - Upsert via `executemany()` with `ON CONFLICT (message_id) DO NOTHING`
4. Return summary: `{total, inserted, skipped, failed_embeddings, failed_parsing}`

**Progress:** Log every 1000 messages and at completion via structlog.

**`backfill_embeddings(pool, embedding_client, batch_size=100)`:**
- Query rows where `embedding IS NULL`
- Generate embeddings and update in batches
- For recovering from Ollama downtime or re-embedding with a different model

---

## MailDB Query Class

**`maildb.py`** — the primary public interface.

**Constructor:** `MailDB(config=None)`
- Loads config from environment if not provided
- Creates connection pool and embedding client
- `init_db()` must be called explicitly

**Core methods:**

| Method | Description |
|--------|-------------|
| `find(**filters) -> list[Email]` | Structured queries with dynamic WHERE clauses |
| `search(query, **filters) -> list[SearchResult]` | Semantic search with optional structured filters |
| `get_thread(thread_id) -> list[Email]` | Full conversation by thread_id, ordered by date |
| `get_thread_for(message_id) -> list[Email]` | Find thread containing a specific message |

**`find()` filters:** `sender`, `sender_domain`, `recipient`, `after`, `before`, `has_attachment`, `subject_contains` (ILIKE), `labels` (array containment), `limit` (default 50), `order` (default `date DESC`). All parameterized queries.

- `recipient` performs a JSONB containment query against the `recipients` column (matches to, cc, or bcc)
- `order` is validated against an allowlist of safe values: `date DESC`, `date ASC`, `sender_address ASC`, `sender_address DESC`. Invalid values raise `ValueError`.

**`search()` execution:** Embeds query via embedding client, applies `ORDER BY embedding <=> %s` for cosine distance. Structured filters are always applied as WHERE clauses combined with the vector ORDER BY — the PostgreSQL query planner handles optimization. Returns `list[SearchResult]`.

**`SearchResult`** is a dataclass wrapping the result:
```python
@dataclass
class SearchResult:
    email: Email
    similarity: float  # 1 - cosine distance
```

**Advanced methods:**

| Method | Description |
|--------|-------------|
| `top_contacts(period, limit, direction)` | Most frequent correspondents via GROUP BY aggregation |
| `topics_with(sender, sender_domain, limit)` | Representative emails spanning different topics with a contact |
| `unreplied(after, before, sender, sender_domain)` | Inbound messages with no outbound reply in the same thread |
| `long_threads(min_messages, after)` | Threads exceeding a message count threshold |

**`topics_with()` algorithm:** Fetch all emails matching the contact. Select the first email, then iteratively pick the email whose embedding is most distant (max cosine distance) from all already-selected emails. Repeat until `limit` is reached. This produces a representative sample spanning different conversation topics without requiring a clustering library.

`top_contacts()` and `unreplied()` require `user_email` in config. Raise a clear error if not set.

**Lifecycle:** `close()` closes the pool. Supports `with MailDB() as db:` context manager.

---

## Testing Strategy

**Unit tests** (no database, no Ollama):

- **`test_parsing.py`** — header extraction, MIME walking, multipart messages, malformed headers, missing fields, threading logic with various References/In-Reply-To combinations
- **`test_cleaning.py`** — each cleaning step independently: quoted replies (single/nested, Outlook-style), signature stripping, whitespace normalization. Full pipeline with realistic email bodies
- **`test_models.py`** — `Email.from_row()` with mock row data, JSONB deserialization, null field edge cases
- **`test_embeddings.py`** — `build_embedding_text()` output format, mocked Ollama client for error handling

**Integration tests** (require PostgreSQL + pgvector):

- **`test_db.py`** — `init_db()` idempotency, connection pool lifecycle
- **`test_ingest.py`** — full ingestion with `.mbox` fixture, deduplication on re-import, null embedding backfill (Ollama mocked)
- **`test_maildb.py`** — each MailDB method against a seeded database: structured queries, semantic search with known embeddings, thread retrieval, analytics methods

**Test infrastructure:**

- `conftest.py` — creates test database, runs `init_db()`, provides `pool` fixture
- Each test runs in a transaction that rolls back
- `tests/fixtures/sample.mbox` — ~10 crafted messages covering edge cases (HTML-only, multipart, attachments, threading chains)
- Fake `EmbeddingClient` returning deterministic vectors for most tests — vectors are designed so known queries return known results (e.g., email A gets a vector aligned with a test query direction, email B gets an orthogonal vector, verifying that search returns A before B)

---

## Dependencies

**Core:**
- `psycopg[binary]` — PostgreSQL driver (sync)
- `psycopg_pool` — connection pooling for psycopg3
- `pgvector` — pgvector Python integration for psycopg
- `ollama` — official Ollama Python client
- `pydantic-settings` — configuration management
- `beautifulsoup4` — HTML-to-text fallback
- `structlog` — structured logging

**Dev:**
- `pytest`, `pytest-cov` — testing and coverage
- `mypy`, `ruff` — type checking and linting
- `factory-boy` — test data factories
