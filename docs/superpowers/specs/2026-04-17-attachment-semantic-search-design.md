# Attachment Semantic Search — Design

**Date:** 2026-04-17
**Status:** draft, pending implementation
**Target version:** maildb next release after PR #44

---

## 1. Goal

Make the textual contents of email attachments searchable alongside email bodies, via structured, semantic, and hybrid queries. An agent should be able to ask *"find the email with the Acme contract that mentions the termination clause"* and get back the specific chunk of the PDF where that clause lives, tied back to the email(s) that contained the attachment.

## 2. Non-Goals (v1)

- Audio transcription (voicemails, podcasts) — deferred.
- Video transcription — deferred.
- ZIP archive contents — deferred (would need recursive unpacking).
- Automatic extraction triggered inline during `maildb ingest run` — out of scope. Extraction is a backfill-first, standalone operation.
- Parallel model variants / embedding-model switching — locked to `nomic-embed-text` for parity with existing email embeddings.

## 3. Scope

Extraction covers every content type that Marker can reliably convert to markdown. Based on the production corpus (35,570 unique attachments):

| Content type | Count | Marker support |
|--------------|------:|:--------------:|
| application/pdf | 7,048 | ✓ |
| image/png + jpeg + gif + tiff | 2,955 | ✓ (via Surya OCR) |
| Word (.docx + .doc) | 1,330 | ✓ |
| Excel (.xlsx + .xls) | 488 | ✓ |
| text/plain + text/html | 415 | ✓ |
| PowerPoint (.pptx) | 30 | ✓ |
| **Processed total** | **~12,300** | |
| audio/mpeg (voicemail) | 12,444 | ✗ skip |
| application/ics (calendar) | 8,187 | ✗ skip |
| application/zip | 241 | ✗ skip |
| video/* | ~50 | ✗ skip |
| other (octet-stream, etc.) | ~1,850 | ✗ skip |

The scope is defined by "what Marker can reliably convert to markdown" — not by a hard-coded type list. The pipeline dispatches to Marker for supported types and marks the rest `status='skipped'` with a reason.

## 4. Technology Additions

- **Marker** (`marker-pdf` Python package). Runs locally. GPU-accelerated on Apple Silicon via PyTorch MPS backend. Produces markdown with section headings, tables rendered as pipe syntax, image placeholders, and page-number annotations for PDFs.
- **`tokenizers`** (HuggingFace Rust-backed Python lib). Loads `nomic-ai/nomic-embed-text-v1` tokenizer for exact token counts. Replaces the `estimate_tokens` byte-length heuristic in `src/maildb/embeddings.py`.

No new embedding model — `nomic-embed-text` (768-dim) via Ollama, same as the existing email-body embedding path.

## 5. Schema Changes

### 5.1 Existing tables

```sql
-- New column on attachments: denormalized reference count.
ALTER TABLE attachments
    ADD COLUMN reference_count INT NOT NULL DEFAULT 0;

-- One-time backfill at migration time:
-- UPDATE attachments a
--    SET reference_count = (SELECT count(*) FROM email_attachments WHERE attachment_id = a.id);
```

`reference_count` is maintained by app logic: `parse.py` increments when it inserts a new `email_attachments` row. No triggers. No ON DELETE handling — we don't delete attachments today. If that changes, introduce `maildb recompute-reference-counts` for drift recovery.

### 5.2 New tables

```sql
CREATE TABLE attachment_contents (
    attachment_id     INT PRIMARY KEY REFERENCES attachments(id) ON DELETE CASCADE,
    status            TEXT NOT NULL
                      CHECK (status IN ('pending','extracting','extracted','failed','skipped')),
    markdown          TEXT,                        -- non-null iff status='extracted'
    markdown_bytes    INT,                         -- octet_length(markdown) on success
    reason            TEXT,                        -- required when status='failed' or 'skipped'
    extracted_at      TIMESTAMPTZ,
    extraction_ms     INT,                         -- wall-clock extraction time (benchmark signal)
    extractor_version TEXT                         -- e.g. "marker==1.2.3"
);

CREATE TABLE attachment_chunks (
    id             BIGSERIAL PRIMARY KEY,
    attachment_id  INT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    chunk_index    INT NOT NULL,                    -- 0-based ordinal within document
    heading_path   TEXT,                            -- e.g. "Overview > Payment Terms"
    page_number    INT,                             -- NULL for non-paginated sources
    token_count    INT NOT NULL,                    -- from the precise tokenizer
    text           TEXT NOT NULL,
    embedding      VECTOR(768),
    UNIQUE (attachment_id, chunk_index)
);
```

### 5.3 Indexes

```sql
CREATE INDEX idx_attachment_contents_status
    ON attachment_contents (status) WHERE status IN ('pending','failed');
CREATE INDEX idx_attachment_chunks_attachment_id
    ON attachment_chunks (attachment_id);
CREATE INDEX idx_attachment_chunks_embedding
    ON attachment_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

HNSW is built after the first extract run completes, same pattern as `idx_email_embedding`.

### 5.4 On-disk mirror

Each extracted markdown is also written to `{attachment_dir}/{sha256[:2]}/{sha256[2:4]}/{sha256}.md`, adjacent to the original attachment file. The DB column is the canonical source for searches; the disk copy is a convenience (ad-hoc grep, external tooling, belt-and-suspenders).

## 6. Extraction Pipeline

### 6.1 Queueing

When a `maildb process_attachments run` starts (or any time an `email_attachments` row is inserted in the ingest path), the system ensures every attachment has a corresponding `attachment_contents` row:

```sql
INSERT INTO attachment_contents (attachment_id, status)
SELECT a.id, 'pending'
FROM attachments a
LEFT JOIN attachment_contents c ON c.attachment_id = a.id
WHERE c.attachment_id IS NULL;
```

Idempotent. Safe to run before every execution as a sync step.

### 6.2 Worker loop

Workers claim rows with `FOR UPDATE SKIP LOCKED`:

```sql
WITH claimed AS (
    SELECT attachment_id FROM attachment_contents
    WHERE status IN ('pending','failed')
      AND (:retry_failed OR status = 'pending')
      -- selector filters spliced in here
    ORDER BY attachment_id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
UPDATE attachment_contents
   SET status = 'extracting', extracted_at = now()
 WHERE attachment_id IN (SELECT attachment_id FROM claimed)
RETURNING attachment_id;
```

Per claimed attachment:

1. Load the file from `{attachment_dir}/{sha256_path}`.
2. Check content-type against Marker's supported set. If unsupported: mark `status='skipped'` with `reason='content_type <X> not supported by Marker'`.
3. Invoke Marker. On exception: mark `status='failed'` with `reason=str(exception)[:2000]`.
4. On success:
   - Write markdown to `attachment_contents.markdown`, set `markdown_bytes`, `extracted_at`, `extraction_ms`, `extractor_version`.
   - Write markdown to disk at `{sha256}.md` (alongside the original).
   - Chunk the markdown (see §7).
   - For each chunk: insert `attachment_chunks` row (no embedding yet, embedding column NULL).
   - Embed chunks in batches via Ollama, update `embedding` column per chunk.
   - Set `status='extracted'`.

Worker count is configurable (`--workers=N`). Default: `1`. M1 Max can likely sustain several — tune empirically with benchmark runs.

### 6.3 Content-type routing

Marker's Python API handles PDF, DOCX, PPTX, XLSX, HTML, images natively. For `.doc` and `.xls` (legacy binary formats), we pre-convert via LibreOffice in headless mode (`soffice --headless --convert-to docx/xlsx`) in a temp dir, then feed to Marker. `soffice` is not a new runtime dependency worth bundling — it's a soft requirement. If `soffice` is missing, legacy `.doc` and `.xls` are marked `status='skipped'` with `reason='libreoffice not installed'`.

Plain text and HTML go straight through (no Marker needed): read the bytes, decode, and pipe into the chunker. Saves Marker's overhead on trivial input.

### 6.4 Failure retry semantics

`status='failed'` rows are retried by default on the next `maildb process_attachments run`. Pass `--no-retry-failed` to skip them. `reason` is overwritten on each retry.

`status='skipped'` rows are never retried automatically (a `.zip` is still a `.zip`). A future `process_attachments reclassify` command could move rows from `skipped` back to `pending` if new extractors are added, but that's out of scope for v1.

## 7. Chunking Strategy

Marker produces markdown with first-class heading structure. The chunker is structure-aware:

### 7.1 Algorithm

1. Parse the markdown into a tree of heading-scoped sections (H1 → H2 → H3 → body).
2. Walk leaves (deepest sections first). For each section:
   - If `token_count(section_text) ≤ 1024`: emit as a chunk.
   - If `> 1024`: split at paragraph boundaries (blank lines). Each paragraph becomes a chunk. If a single paragraph is still `> 1024`, split at sentence boundaries (`. ?!` followed by whitespace).
   - If `< 128` AND the next sibling is also small: merge with next sibling until hitting 128 or the section boundary.
3. Each chunk carries its heading path (e.g., `"Overview > Payment Terms > Late Fees"`) built by concatenating ancestor headings.
4. Page numbers from Marker's annotations (PDFs only) are preserved per chunk.

Soft floor (128 tokens) avoids a proliferation of tiny chunks from heavily-segmented docs. Hard cap (1024 tokens) leaves 8× margin under `nomic-embed-text`'s 7500-token context limit and keeps per-chunk embeddings topically focused.

### 7.2 Deterministic re-chunking

Chunking is deterministic given the markdown input and the chunker version. We version the chunker alongside the extractor (`extractor_version = "marker==X.Y.Z;chunker=vN"`) so re-chunking can be triggered without re-running Marker when the chunking rules change.

## 8. Embedding

- Model: `nomic-embed-text` (768-dim), via Ollama, same endpoint as email-body embedding.
- Batching: `--embed-batch-size` chunks per request (default 50, matching the existing email embed worker).
- Token counts per chunk come from the precise tokenizer — the chunker guarantees `token_count ≤ 1024`, so no further truncation is needed at embed time.
- Failure mode: if a chunk fails to embed after a retry, mark it with a zero-vector sentinel (same pattern as email embeddings). The parent attachment stays `status='extracted'` — the extraction succeeded, only a sub-step failed.

## 9. Precise Tokenizer

A new module `src/maildb/tokenizer.py`:

```python
from tokenizers import Tokenizer

_TOKENIZER: Tokenizer | None = None

def get_tokenizer() -> Tokenizer:
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = Tokenizer.from_pretrained("nomic-ai/nomic-embed-text-v1")
    return _TOKENIZER

def count_tokens(text: str) -> int:
    return len(get_tokenizer().encode(text).ids)

def truncate_to_tokens(text: str, max_tokens: int) -> str:
    enc = get_tokenizer().encode(text)
    if len(enc.ids) <= max_tokens:
        return text
    # Decode the first max_tokens tokens back to string.
    return get_tokenizer().decode(enc.ids[:max_tokens])
```

In this feature, `count_tokens` is the primary API (chunker calls it heavily). `truncate_to_tokens` replaces the binary-search heuristic in `build_embedding_text` — follow-up change (see §15).

## 10. Search API

### 10.1 Python (`MailDB` class)

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
    content_type: str | None = None,   # e.g. "application/pdf"
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[AttachmentSearchResult], int]:
    ...

def search_all(
    self,
    query: str,
    *,
    # same filter parameters as above
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[UnifiedSearchResult], int]:
    ...

def get_attachment_markdown(self, attachment_id: int) -> str | None:
    """Return the full extracted markdown, or None if not yet extracted / failed."""
    ...
```

`AttachmentSearchResult` dataclass:

```python
@dataclass
class AttachmentSearchResult:
    attachment_id: int
    filename: str
    content_type: str
    sha256: str
    chunk: AttachmentChunk       # index, heading_path, page_number, text, token_count
    emails: list[str]             # message_ids of emails that carried this attachment
    similarity: float
```

`UnifiedSearchResult`:

```python
@dataclass
class UnifiedSearchResult:
    source: Literal["email", "attachment"]
    similarity: float
    email: Email | None                         # populated when source="email"
    attachment_result: AttachmentSearchResult | None  # populated when source="attachment"
```

Under the hood, `search_all` runs two separate queries (email-embedding HNSW + attachment-chunk-embedding HNSW) with a shared over-fetch (2× `limit` from each), merges by similarity, trims to `limit`. Cheap and keeps the two indexes honest about their own ranking.

### 10.2 Filter push-down

`search_attachments` SQL skeleton:

```sql
WITH candidate_chunks AS (
    SELECT ac.*,
           1 - (ac.embedding <=> %(q)s::vector) AS similarity,
           a.filename, a.content_type, a.sha256
    FROM attachment_chunks ac
    JOIN attachments a ON a.id = ac.attachment_id
    WHERE ac.embedding IS NOT NULL
      AND vector_norm(ac.embedding) > 0
      AND (%(content_type)s IS NULL OR a.content_type = %(content_type)s)
      -- email-level filters applied via EXISTS on email_attachments → emails:
      AND EXISTS (
          SELECT 1 FROM email_attachments ea
          JOIN emails e ON e.id = ea.email_id
          WHERE ea.attachment_id = a.id
            -- all the email-level filter conditions injected here:
            -- AND e.sender_address = %(sender)s
            -- AND e.date >= %(after)s
            -- AND EXISTS (SELECT 1 FROM email_accounts ea2 WHERE ea2.email_id = e.id AND ea2.source_account = %(account)s)
            -- etc.
      )
    ORDER BY ac.embedding <=> %(q)s::vector
    LIMIT %(limit)s OFFSET %(offset)s
)
SELECT cc.*,
       (SELECT array_agg(e2.message_id)
          FROM email_attachments ea2
          JOIN emails e2 ON e2.id = ea2.email_id
         WHERE ea2.attachment_id = cc.attachment_id) AS email_message_ids
FROM candidate_chunks cc;
```

Filter conditions are composed the same way `_build_filters` builds them today; the only difference is the surrounding `EXISTS (SELECT 1 FROM email_attachments ...)` wrapper.

### 10.3 MCP tools

New tools: `search_attachments`, `search_all`, `get_attachment_markdown`. Serialization follows the existing conventions:

- `fields` parameter for selective return.
- `offset` pagination.
- Response size warnings via `@log_tool` decorator.
- UUIDs and datetimes serialized to strings.
- Chunk text truncated to a configurable length via a `chunk_max_chars` parameter (default 2000, analogous to `body_max_chars`).

## 11. CLI

```
maildb process_attachments run [OPTIONS]
    --workers N                    # parallel workers (default 1)
    --limit N                      # process only first N pending
    --sample N                     # random sample of N from pending set
    --only TYPE                    # filter by bucket: pdf | docx | xlsx | image | text | html
    --min-size BYTES
    --max-size BYTES
    --ids ID1,ID2,...              # specific attachment_ids
    --retry-failed/--no-retry-failed   # default true
    --marker-batch-size N
    --embed-batch-size N
    --dry-run                      # show selection count, don't extract

maildb process_attachments status [--content-type TYPE]
    # pending / extracting / extracted / failed / skipped counts
    # top-10 failure reasons with counts
    # per-content-type throughput (avg extraction_ms, avg chunks per doc)

maildb process_attachments retry [--reason-contains SUBSTR]
    # convenience: run against status='failed' rows, optionally filtered by reason
```

Selector flags compose with `AND`. `--sample N` caps `--limit N`.

Every run logs structured events: `attachment_extract_start`, `attachment_extract_complete` (with `extraction_ms`, `chunk_count`, `markdown_bytes`), `attachment_extract_failed` (with `reason`), `attachment_skip`. These feed into the `status` aggregate.

## 12. Failure, Retry, Idempotency

- **Marker fails mid-extraction:** row stays `status='extracting'` if the worker crashes without updating. A sentinel watchdog query (`status='extracting' AND extracted_at < now() - interval '1 hour'`) in the next `run` invocation resets these to `pending`. Simple, no transactions needed beyond the claim.
- **Worker is killed mid-chunk-embed:** partial `attachment_chunks` rows exist, but `status` is still `extracting` on `attachment_contents`. The watchdog resets to `pending`. Re-run deletes existing `attachment_chunks` for that `attachment_id` before re-chunking. Idempotent by construction.
- **`process_attachments run` killed:** all claimed rows are either `extracted`, `failed`, `skipped`, or `extracting` (watchdog reclaims). No corruption possible because each attachment's extract → chunk → embed → status-update sequence is independent.
- **Re-running the same command:** by default picks up `pending` + `failed`. Produces identical results for unchanged input.
- **Ingest of a new email that references an already-extracted attachment:** the `attachment_contents` row already exists with `status='extracted'` — `parse.py` checks before inserting. Nothing to do. The `email_attachments` link is made; `reference_count` increments.

## 13. Performance & Benchmarking

### 13.1 Expected rough throughput

(Estimated; benchmarks will refine):

- Marker on M1 Max Metal: ~1–5 pages/sec per worker for text-heavy PDFs; slower for OCR-heavy pages.
- ~12,300 attachments → 1-2 days single-worker, possibly hours with tuned parallelism.

### 13.2 Benchmarking flow

```bash
# Establish a small deterministic baseline.
maildb process_attachments run --sample 50 --workers 1

# Compare worker counts.
maildb process_attachments run --sample 50 --workers 4 --ids <same set>

# Try Marker batch sizes.
maildb process_attachments run --sample 50 --marker-batch-size 8

# Type-specific tuning.
maildb process_attachments run --only pdf --sample 25 --workers 2
```

After each run, `maildb process_attachments status` reports avg `extraction_ms` per content type so each variable's effect is quantified.

### 13.3 Storage impact

- Per-attachment markdown: ~50 KB avg × 12K = ~600 MB DB growth.
- Per-chunk row: ~1 KB text + 768-dim embedding (~3 KB) = ~4 KB. Assume ~10 chunks per doc avg × 12K = ~480 MB for `attachment_chunks`.
- On-disk markdown mirror: ~600 MB in `attachment_dir`.
- HNSW index on attachment chunks: ~50–100 MB for 120K chunks.

Total: under 2 GB new storage. Well within available headroom.

## 14. Testing Strategy

### 14.1 Unit tests

- **Chunker:** given a known markdown input, produces deterministic chunks with expected heading paths, page numbers, and token counts at boundaries. Covers: flat docs (no headings), deeply nested docs, oversized sections requiring paragraph-split fallback, undersized sections requiring merge.
- **Tokenizer:** exact token counts for known inputs (compare against HuggingFace directly-loaded tokenizer).
- **Content-type dispatcher:** supported types route to Marker; unsupported types return `skipped` with reasoned message.
- **Serialization:** `AttachmentSearchResult` and `UnifiedSearchResult` → JSON match expected shape.

### 14.2 Integration tests

- **End-to-end on a tiny PDF fixture:** `process_attachments run --ids <id>` produces `attachment_contents` + `attachment_chunks` rows; disk mirror exists; `search_attachments("some keyword")` returns the chunk.
- **Failure path:** corrupt PDF fixture → `status='failed'` with non-null `reason`. Retry succeeds if the fixture is replaced.
- **Idempotency:** running `process_attachments run` twice with no input change yields identical `attachment_chunks` rows (chunk_index assignments stable).
- **Filter push-down:** `search_attachments(query, account=X)` only returns chunks whose attachments belong to emails in account X. Reuses the `multi_account_seed` fixture.
- **`search_all` merge ordering:** synthetic fixtures with known similarities; assert the merged ranking.
- **Watchdog:** insert a row with `status='extracting'` and stale `extracted_at`; next `run` resets it to `pending`.

### 14.3 Benchmark fixtures

Commit a small "benchmark corpus" under `tests/fixtures/attachments/` — a handful of representative PDFs (short, long, OCR-heavy), a DOCX, an XLSX, a PNG. Used by integration tests and by the `--ids` selector for reproducible benchmark runs locally.

## 15. Follow-ups (out of this spec)

- **Migrate email body embedding to the precise tokenizer.** `src/maildb/embeddings.py::build_embedding_text` currently uses `estimate_tokens` + binary search. Swap for `count_tokens` + `truncate_to_tokens`. File a dedicated issue. Will motivate a one-time email re-embed pass.
- **Extractor/chunker version tracking & re-run command.** `extractor_version` is stored per row; a `process_attachments rechunk --if-below-version vN` command could re-chunk (without re-extracting) whenever the chunker changes.
- **DSL virtual source for attachment chunks** — `from: "attachment_chunks"` analogous to `sent_to` / `email_labels`, so ad-hoc queries can aggregate over chunks. File as follow-up after the main feature lands.
- **`recompute-reference-counts` utility** — drift-recovery for when attachment deletion is introduced.

## 16. Migration & Rollout

1. Schema migration (ALTER + two CREATE TABLEs) applied via the existing `init_db` path. Idempotent `ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`.
2. One-time backfill of `attachments.reference_count` executed during `init_db` if the column is newly added.
3. New `maildb process_attachments` subcommand available.
4. Operator runs `maildb process_attachments run` against the ~12K pending rows — benchmarks first with `--sample 50` to tune `--workers` and batch sizes, then commits to a full run.
5. After full run, `maildb process_attachments status` should show 0 `pending`, 0 `extracting`, some mix of `extracted` / `failed` / `skipped`. HNSW index is created on the `embedding` column.
6. Verify: `search_attachments("a known phrase from a known doc")` returns the expected chunk.
7. Tag a runbook at `docs/runbooks/attachment-extraction-migration.md` analogous to `multi-account-migration.md`.

Rollback: the new tables are additive. Dropping them and the `reference_count` column returns the DB to the prior state. No email data is touched.
