# MailDB — Scale Ingest & Attachment Extraction Design Spec

**Redesign of the ingestion pipeline for 45.5GB mbox files with parallel execution, attachment extraction, and deferred indexing/embedding.**

Design Spec — March 2026

---

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Chunking strategy | Pre-split into ~50MB mbox files | Independent atomic units, simple retry, proven approach |
| Parallel execution | `ProcessPoolExecutor` | True parallelism for CPU-bound parsing, stdlib, each worker gets own DB connection |
| Task coordination | PostgreSQL `ingest_tasks` table | Survives crashes, queryable, atomic state transitions, already have the DB |
| Attachment storage | Content-addressed (SHA-256) | Deduplicates forwarded attachments, idempotent writes |
| Embedding parallelism | Multiple workers with `SKIP LOCKED` | Natural DB work queue, no external dependencies, workers are stateless and restartable |
| Index strategy | Deferred to post-ingest | Bulk-build is orders of magnitude faster than incremental maintenance |

---

## Pipeline Overview

Four sequential phases, each orchestrated through the task table:

```
Phase 1: Split   →  Phase 2: Parse & Load  →  Phase 3: Index  →  Phase 4: Embed
(sequential)        (parallel workers)         (sequential)       (parallel workers)
```

Each phase only starts after the previous phase's tasks are all complete. Failed tasks can be retried independently. The entire pipeline is restartable — all state lives in the DB and filesystem.

---

## Task Table Schema

```sql
CREATE TABLE IF NOT EXISTS ingest_tasks (
    id                    SERIAL PRIMARY KEY,
    phase                 TEXT NOT NULL,           -- 'split', 'parse', 'index', 'embed'
    status                TEXT NOT NULL DEFAULT 'pending',
                                                   -- 'pending', 'in_progress', 'completed', 'failed'
    chunk_path            TEXT,                    -- path to chunk file (parse phase)
    worker_id             TEXT,                    -- PID + hostname of claiming worker
    started_at            TIMESTAMPTZ,
    completed_at          TIMESTAMPTZ,
    error_message         TEXT,                    -- last error if failed
    retry_count           INT DEFAULT 0,
    messages_total        INT DEFAULT 0,
    messages_inserted     INT DEFAULT 0,
    messages_skipped      INT DEFAULT 0,
    attachments_extracted INT DEFAULT 0,
    created_at            TIMESTAMPTZ DEFAULT now()
);
```

- Parse tasks have `chunk_path`. Embed phase uses `SKIP LOCKED` on the emails table directly (no pre-assigned batches).
- `worker_id` tracks which process claimed a task (for debugging).
- `retry_count` with configurable max (default 3) before permanently failed.
- Stats columns for progress reporting.

---

## Attachment Storage & Schema

### File Storage

Content-addressed with two-level directory prefix:

```
attachments/
  ab/cd/abcdef1234567890...sha256.pdf
  01/23/0123456789abcdef...sha256.png
```

Files named by SHA-256 hash with original extension preserved. Two-level prefix (`ab/cd/`) from first 4 hex chars avoids filesystem bottleneck. Identical attachments stored once.

### Database Tables

```sql
CREATE TABLE IF NOT EXISTS attachments (
    id              SERIAL PRIMARY KEY,
    sha256          TEXT NOT NULL,
    filename        TEXT NOT NULL,           -- original filename
    content_type    TEXT,
    size            BIGINT NOT NULL,
    storage_path    TEXT NOT NULL,            -- relative path under attachment_dir
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (sha256)
);

CREATE TABLE IF NOT EXISTS email_attachments (
    email_id        UUID NOT NULL REFERENCES emails(id),
    attachment_id   INT NOT NULL REFERENCES attachments(id),
    filename        TEXT NOT NULL,            -- original filename on this specific email
    PRIMARY KEY (email_id, attachment_id)
);
```

- `attachments` — one row per unique file (deduplicated by SHA-256). Stores the filename from the first occurrence. The extension used in `storage_path` comes from this first-seen filename.
- `email_attachments` — many-to-many join table. Same attachment can appear on multiple emails. Carries its own `filename` column to preserve the per-email original name (the same file may be attached as "Q4 Report.pdf" on one email and "report-final.pdf" on another).
- The existing `attachments` JSONB column and `has_attachment` boolean on `emails` remain for quick metadata access without joins.
- Semantic search on attachment content (OCR, PDF text extraction) is a future feature. The schema supports it via an `embedding` column on `attachments` later.

---

## Phase 1: Split

Single-process sequential scan. I/O-bound — no parallelism needed.

**Algorithm:**
1. Open the mbox file in binary mode
2. Scan for `From ` lines at start of lines (mbox message delimiter)
3. Accumulate messages until chunk reaches ~50MB
4. Write chunk to temp directory as standalone mbox file
5. Insert a `phase='parse'` task row for each chunk
6. Log progress every N chunks

**Key details:**
- Reads in large buffers (8MB) for I/O efficiency, splits on message boundaries only
- Chunk size configurable (default 50MB)
- Chunks written to configurable temp directory (default `./ingest_tmp/`)
- Split phase recorded as a single `phase='split'` task row, marked `completed` when all chunks are written and all parse tasks are inserted
- Re-entrant: if interrupted, the temp directory is cleaned and the split restarts from scratch. The orchestrator checks for a completed `phase='split'` task to know whether splitting is done
- Estimated output: ~900 chunks for 45.5GB at 50MB each

---

## Phase 2: Parse & Load

`ProcessPoolExecutor` with N workers (default: CPU count - 1, configurable).

**Per-worker, per-chunk:**
1. Claim task atomically: `UPDATE ingest_tasks SET status='in_progress', worker_id=..., started_at=now() WHERE id = (SELECT id FROM ingest_tasks WHERE phase='parse' AND status='pending' LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *`
2. If no row returned, worker exits (no more work).
3. Open chunk mbox file with `mailbox.mbox()` (safe — chunks are ~50MB)
4. Parse all messages in the chunk. The parsing function must return attachment bytes alongside metadata (see Changes to Existing Code section for the `_extract_attachments()` modification).
5. Pre-generate a UUID (`uuid4()`) client-side for each email row. This is needed to populate the `email_attachments` join table within the same transaction, since `ON CONFLICT DO NOTHING` does not return IDs for skipped rows.
6. For each message with attachments:
   - Hash attachment bytes with SHA-256
   - Write file to `attachments/<ab>/<cd>/<hash>.<ext>` if not already on disk
   - Collect attachment metadata for DB insertion
7. Single transaction:
   - `executemany()` batch INSERT for all email rows with pre-generated UUIDs (`ON CONFLICT (message_id) DO NOTHING`)
   - INSERT into `attachments` table (`ON CONFLICT (sha256) DO NOTHING RETURNING id, sha256`) — for new attachments, get the ID directly; for existing ones, follow up with `SELECT id FROM attachments WHERE sha256 = ANY(%(hashes)s)` to resolve IDs
   - INSERT into `email_attachments` join table using the pre-generated email UUIDs and resolved attachment IDs
   - UPDATE task row with stats and `status='completed'`
8. On failure: rollback transaction, UPDATE task to `status='failed'` with error, increment `retry_count`

**Key details:**
- Task claiming uses `FOR UPDATE SKIP LOCKED` to prevent workers from colliding on the same task.
- `executemany()` replaces the current one-row-at-a-time INSERT.
- Entire chunk is one transaction — all messages land or none (atomic).
- Attachment file writes happen before the transaction. Orphan files on rollback are harmless (content-addressed, idempotent) and reused on retry.
- No indexes except `UNIQUE(message_id)` on emails and `UNIQUE(sha256)` on attachments during this phase.
- Each worker creates its own DB connection inside the worker process (connections are NOT shared across the fork boundary).

---

## Phase 3: Index

Single process, after all parse tasks complete.

**Execution:**
1. Confirm all `phase='parse'` tasks are `status='completed'`
2. Create a single `phase='index'` task
3. Drop existing non-unique indexes (primary keys and unique constraints like `UNIQUE(message_id)` and `UNIQUE(sha256)` are preserved — they are required for `ON CONFLICT` during parse phase)
4. Create all indexes sequentially:
   - **Unique B-tree:** `message_id` on emails (recreate if previously dropped — required for correctness)
   - **B-tree:** `sender_address`, `sender_domain`, `date`, `thread_id`, `in_reply_to`
   - **Partial B-tree:** `has_attachment WHERE has_attachment = TRUE`
   - **GIN:** `labels`, `recipients`
   - **B-tree:** `email_attachments(email_id)`, `email_attachments(attachment_id)`
   - **Composite B-tree:** `(thread_id, sender_address, date)` — for `unreplied()` performance
   - **HNSW:** skipped if all embeddings are NULL (deferred to after embed phase)
5. `ANALYZE emails; ANALYZE attachments; ANALYZE email_attachments;` to update planner statistics
6. Mark task completed

**Key details:**
- Bulk-build via `CREATE INDEX` is much faster than incremental maintenance during inserts.
- HNSW is the most expensive index — deferred to after embed phase.
- All indexes use `IF NOT EXISTS` so the phase is retryable.

---

## Phase 4: Embed

Multiple workers, `SKIP LOCKED` work queue on the emails table.

**Per-worker loop:**
1. Begin transaction
2. `SELECT id, subject, sender_name, body_text FROM emails WHERE embedding IS NULL LIMIT %(batch_size)s FOR UPDATE SKIP LOCKED`
3. If no rows returned, worker exits (queue drained)
4. Build embedding texts, call `embed_batch()` on Ollama
5. `UPDATE emails SET embedding = ... WHERE id = ...` for each row
6. Commit (releases locks)
7. Log progress, repeat from 1

**After all workers exit:**
- Create HNSW index on `embedding` column
- `ANALYZE emails`

**Key details:**
- `SKIP LOCKED` ensures no duplicate Ollama calls across workers.
- Batch size configurable (default 50), tuned to Ollama throughput.
- Worker crash = transaction rollback = rows return to `embedding IS NULL` queue.
- Ollama unreachable: worker retries with exponential backoff (3 attempts), then exits.
- If all embed workers exit without completing any batches and `embedding IS NULL` rows remain, the orchestrator reports "Embed phase failed: Ollama unreachable" and allows retry via `uv run python -m maildb.ingest embed`.
- A single `phase='embed'` task row is created for lifecycle tracking. Its `messages_total` is set to the count of rows needing embeddings, and updated periodically.
- Progress queryable: `SELECT COUNT(*) FROM emails WHERE embedding IS NOT NULL`

---

## Orchestrator & CLI

Single entry point driving the full pipeline. Restartable from any point.

**CLI interface:**
```bash
# Full pipeline
uv run python -m maildb.ingest /path/to/All\ Mail.mbox

# Individual phases
uv run python -m maildb.ingest split /path/to/All\ Mail.mbox
uv run python -m maildb.ingest parse
uv run python -m maildb.ingest index
uv run python -m maildb.ingest embed

# Status
uv run python -m maildb.ingest status
```

**Orchestrator logic:**
1. Check task table for current state
2. If no `phase='split'` task exists, start from split phase
3. If a completed `phase='split'` task exists but parse tasks are incomplete, resume parse phase
4. If all parse tasks are completed, run index phase (if not already done)
5. If index is completed, run embed phase (if not already done)
6. If tasks in any phase are failed with retries remaining, retry them before advancing
7. If tasks are permanently failed (retry_count >= max), report and halt

**Status output:**
```
Phase     Total  Done  Failed  In Progress
split     1      1     0       0
parse     912    908   1       3
index     0      0     0       0
embed     0      0     0       0

Messages: 2,347,891 inserted, 12,403 skipped (duplicates)
Attachments: 89,234 extracted (41,672 unique files, 18.3 GB)
Embeddings: 0 / 2,347,891
```

**Key details:**
- `Ctrl+C` sends SIGINT, workers finish current chunk/batch, then exit cleanly.
- Module structure: `src/maildb/ingest/` package replaces `src/maildb/ingest.py`.
- The existing `backfill_embeddings()` function is subsumed by the embed phase.

---

## Configuration

New fields added to `Settings`:

```python
class Settings(BaseSettings):
    # ... existing fields ...
    attachment_dir: str = "./attachments"
    ingest_chunk_size_mb: int = 50
    ingest_tmp_dir: str = "./ingest_tmp"
    ingest_workers: int = -1              # -1 means cpu_count - 1
    embed_workers: int = 4
    embed_batch_size: int = 50
```

All configurable via `MAILDB_` prefixed environment variables.

---

## Changes to Existing Code

### Schema
- `schema.sql` split into `schema_tables.sql` (table DDL with unique constraints only) and `schema_indexes.sql` (all non-unique indexes). `init_db()` applies tables only. The index phase applies `schema_indexes.sql`.
- No structural changes to `emails` table
- New tables: `ingest_tasks`, `attachments`, `email_attachments`

### Parsing (`parsing.py`)
- `_extract_attachments()` must be modified to return raw attachment bytes alongside metadata. Currently it only collects metadata (filename, content_type, size) and discards the payload. The new version returns `list[dict]` where each dict includes a `data: bytes` field.
- `parse_message()` should extract `X-Gmail-Labels` header into the `labels` field (currently hardcoded to `[]`). Gmail mbox exports include this header and the existing `labels TEXT[]` column and GIN index are ready for it.

### Queries (`maildb.py`)
- `topics_with()` — the greedy farthest-point algorithm is inherently sequential (each step depends on previous selections). Optimization to push distance computation into PostgreSQL is desirable but non-trivial. For now, add a guard: limit the initial query to a reasonable number of recent emails (e.g., 500) to bound memory usage, rather than loading all emails for a contact. Full PostgreSQL migration of this algorithm is a future optimization.
- `unreplied()` — add a configurable `limit` parameter (default 100) to prevent unbounded result sets at scale
- New composite index `(thread_id, sender_address, date)` improves `unreplied()` performance

### Ingest (`ingest.py` → `ingest/` package)
- Replaced entirely by the multi-phase pipeline
- `ingest_mbox()` and `backfill_embeddings()` removed

### MCP Server (`server.py`)
- No changes needed. Attachment metadata already returned by `find` tool.
- Future: `get_attachment_path` tool to retrieve file locations.

---

## Future Features (Not In Scope)

- Semantic search on attachment content (OCR, PDF text extraction, embedding)
- Distributed embedding across multiple machines (architecture supports it via DB work queue)
- Gmail API sync (Phase 6 from original spec)
- Incremental re-ingestion (append new messages without full re-import)
