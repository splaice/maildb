# First Import Retrospective

**Date:** 2026-03-28
**Mbox file:** All-mail-Including-Spam-and-Trash-001.mbox (49 GB, Gmail Takeout)
**Hardware:** Apple M1 Max, 64 GB unified memory
**Result:** 841,930 emails ingested, 38,808 attachments extracted (35,570 unique)

---

## Timeline

| Time | Event |
|------|-------|
| 10:32 | Smoke test with sample.mbox (10 messages) succeeds |
| 10:36 | First real import attempt — fails immediately on `Settings` rejecting unknown env var |
| 10:36 | Fix applied (`extra: "ignore"`), restart |
| 10:37–10:39 | Split phase: 49 GB split into 886 chunks (~55 MB each), ~2 min |
| 10:39–10:40 | Parse phase: 871/886 chunks parsed, 15 failures, ~1 min |
| 10:43 | Second parse attempt after NUL byte fix — 12 more pass, 3 still fail |
| 10:44 | Third parse attempt after Header coercion + FK fix — all 886 chunks pass |
| 10:44 | Index phase: B-tree + GIN indexes built, seconds |
| 10:44–10:50 | Embed phase (attempt 1): 1,450/841,930 — workers give up after context length errors |
| 10:50 | Embed phase (attempt 2, 20K char truncation): 404,542/841,930 — still hitting limits |
| 16:41 | Embed phase (attempt 3, 6K char truncation): 330,153/841,930 — workers still giving up |
| 23:02 | Embed phase (final, 6K truncation + zero-vector sentinel + no give-up): 841,930/841,930 |

Total wall clock: ~12.5 hours, dominated by embedding. Parse phase was remarkably fast (~2 minutes for 49 GB).

---

## What Went Well

### Split and parse performance
Splitting 49 GB into 886 chunks took 2 minutes. Parsing all 886 chunks across parallel workers took roughly 1 minute. The `ProcessPoolExecutor` approach and `SKIP LOCKED` task coordination worked exactly as designed. The pipeline processed ~840K messages in under 5 minutes.

### Restartable pipeline
The `ingest_tasks` table and phase-gating logic worked correctly every time. After each fix, the pipeline resumed from exactly where it left off — completed phases were skipped, and only pending or failed work was retried. This was essential given that we restarted the pipeline 6+ times.

### Content-addressed attachment deduplication
38,808 attachment references extracted, deduplicating down to 35,570 unique files on disk via SHA-256 hashing. The dedup ratio (~8.4%) is meaningful savings, and the approach worked without issues.

### Data quality after ingestion
Once all phases completed, the query API worked immediately — structured search, semantic search, thread retrieval, and aggregation queries all returned correct results. The embedding quality was good: a search for "budget" ranked the "Q1 Budget Discussion" email first (0.712 similarity).

---

## What Went Wrong

### 1. Settings rejected unknown env vars

**Symptom:** `pydantic_core.ValidationError: Extra inputs are not permitted` on startup.

**Cause:** The `.env` file created by the bootstrap script included `MAILDB_TEST_DATABASE_URL`, which isn't defined in `Settings`. Pydantic v2 defaults to rejecting extra fields.

**Fix:** Added `"extra": "ignore"` to the `Settings.model_config`.

**Lesson:** Bootstrap and config must stay in sync. Better: the settings class should be tolerant of extra env vars by default.

### 2. NUL bytes in email text fields (15 chunks failed)

**Symptom:** `psycopg.DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes`

**Cause:** Some emails contain raw NUL bytes in their body text or headers — likely from malformed MIME parts, binary content misinterpreted as text, or encoding errors in the original email.

**Fix:** Added `_sanitize_row()` in `parse.py` that strips `\x00` from all string values before INSERT.

**Lesson:** Email is a cesspool. Any text extracted from email messages must be sanitized before going into PostgreSQL. This should have been anticipated.

### 3. `email.header.Header` object instead of str (2 chunks failed)

**Symptom:** `cannot adapt type 'Header' using placeholder '%s'`

**Cause:** Python's `email` library returns `email.header.Header` objects for encoded subject lines (RFC 2047) instead of plain strings. `msg.get("Subject")` does not guarantee a `str` return type.

**Fix:** Explicit coercion: `str(msg.get("Subject")) if msg.get("Subject") is not None else None`

**Lesson:** Never trust the return types of Python's `email` module. Every header value should be coerced to `str` defensively.

### 4. Foreign key violation on email_attachments (1 chunk failed)

**Symptom:** `violates foreign key constraint "email_attachments_email_id_fkey"` — Key (email_id) is not present in table "emails".

**Cause:** When an email INSERT is skipped due to `ON CONFLICT (message_id) DO NOTHING` (duplicate), its `email_id` (a freshly generated UUID) never makes it into the `emails` table. But the code still tried to insert an `email_attachments` row referencing that UUID.

**Fix:** Track which `email_id` values were actually inserted, and only create `email_attachments` rows for those.

**Lesson:** `ON CONFLICT DO NOTHING` silently skips rows. Any downstream references to those rows must account for the skip.

### 5. Embedding context length exceeded — four iterations to solve

This was the most painful issue, requiring four separate attempts to fully resolve.

**Attempt 1 (24K char truncation):** Still failed — 24K chars can produce far more than 8,192 tokens for token-dense content.

**Attempt 2 (20K char truncation):** Still failed for many emails. Empirical testing showed a single email at 10,662 chars already exceeded the context window.

**Attempt 3 (6K char truncation):** Truncation finally low enough, but 41K emails had already been embedded with the old limit and were being re-fetched and failing. Workers gave up after 3 consecutive fallback batches.

**Attempt 4 (6K char truncation + zero-vector sentinel + no give-up logic):** Finally worked. Emails that fail embedding get a zero vector so they are never retried, and workers run until all rows are processed regardless of failure rate.

**Root cause:** nomic-embed-text has an 8,192 token context window, but the char-to-token ratio varies wildly depending on content. English prose averages ~4 chars/token, but URLs, code snippets, base64 fragments, and non-ASCII text can hit ~1.2 chars/token. A safe limit is ~6,000 chars.

**Lesson:** Always benchmark the embedding model's actual token limit against worst-case content, not average content. And never let a single bad row poison an entire batch — mark it and move on.

### 6. Connection pool deadlock in embed fallback

**Symptom:** `psycopg_pool.PoolTimeout: couldn't get a connection after 30.00 sec`

**Cause:** The embed worker created a pool with `max_size=1`. The batch loop held the only connection (via `FOR UPDATE SKIP LOCKED`), then on failure called `_embed_single()` which tried to acquire a connection from the same pool. Deadlock.

**Fix:** Restructured the embed worker to separate fetch and update into independent operations. `_fetch_batch()` acquires a connection, runs the SELECT, does `conn.rollback()` to release the FOR UPDATE locks, and releases the connection. Then `_embed_and_update_batch()` or `_embed_and_update_single()` each acquire their own connections for the UPDATE.

**Lesson:** With `max_size=1` pools, you must never hold a connection while calling code that needs one. The fetch-then-release pattern is correct for this use case.

### 7. Ruff scanning the attachments directory

**Symptom:** `error: Failed to parse attachments/.../*.py` — Ruff tried to lint Python files extracted as email attachments.

**Fix:** Added `extend-exclude = ["attachments", "ingest_tmp"]` to `[tool.ruff]` in `pyproject.toml`.

**Lesson:** Any directory that receives untrusted external content must be excluded from linters and formatters.

---

## Improvements to Make

### Parse phase

1. **Sanitize all header values at parse time, not insert time.** The NUL byte and Header-type fixes are applied at different layers (parse.py vs parsing.py). All text sanitization should happen in `parse_message()` so downstream code can trust its output. Every call to `msg.get()` should pass through a `_safe_header(msg, name) -> str | None` helper that coerces Header objects, strips NUL bytes, and handles encoding errors.

2. **Per-message error handling in chunk processing.** Currently, one bad message in a chunk fails the entire chunk. The loop in `_process_single_chunk` should catch exceptions per-row and skip the bad message (with logging), rather than aborting the whole chunk. The chunk would still be marked complete with a count of skipped messages.

3. **Validate recipients JSON structure.** Some malformed emails may produce unexpected recipient structures. Validate the shape of the recipients dict before inserting.

### Embed phase

4. **Truncation should be token-aware, not char-based.** The 6K char limit is a blunt instrument. Use the model's tokenizer (or a fast approximation like `len(text.encode()) / 4`) to truncate at a token-aware boundary. This would allow longer English-prose emails to use more of the context window while still protecting against dense content.

5. **Log and count skipped embeddings in the final status.** Currently, the status output shows `Embeddings: 841,930 / 841,930` which looks like 100% success, but some of those are zero-vector sentinels. The status should report real embeddings vs sentinels separately, e.g. `Embeddings: 839,200 real + 2,730 skipped / 841,930`.

6. **Make the embed phase incrementally resumable.** Currently, resetting the embed phase requires deleting the task and clearing all embeddings. Since the worker selects `WHERE embedding IS NULL`, it could simply resume from where it left off without any reset — but the orchestrator's phase-gating logic treats the embed task as all-or-nothing. The orchestrator should check for remaining un-embedded rows rather than relying solely on the task status.

### Pipeline

7. **Add a `reset` CLI command.** Manually running `psql` to truncate tables or reset tasks is error-prone. Add `python -m maildb.ingest reset [--phase embed]` to clear state cleanly.

8. **Add progress reporting during long phases.** The embed phase ran for 12+ hours with only periodic `embed_batch_done` log lines. Add a periodic summary (every N batches or every M minutes) showing total progress, rate, and ETA.

9. **Add a `--skip-embed` dry run mode.** For testing parse-phase changes, it would be useful to run split + parse + index without waiting for embedding. The config has `skip_embed` but the CLI doesn't expose it.

### Configuration

10. **Exclude `attachments/` and `ingest_tmp/` in `.gitignore`.** These directories should not be tracked or linted. Ensure they are in `.gitignore` and in Ruff/mypy excludes.

11. **Add `.env.example` to the repo.** Document all recognized env vars with example values so the bootstrap script and manual setup stay aligned.
