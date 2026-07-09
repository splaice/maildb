# Deep Repository Review — 2026-06-10

Multi-agent review (6 dimensions, every finding adversarially verified; several confirmed empirically against a live Postgres). 52 findings confirmed, 0 refuted.

## Confirmed findings

### HIGH

#### [correctness] DSL `in`/`not_in` operators compile to SQL that always fails under psycopg3
*src/maildb/dsl.py:534* — verifier confidence: high

_build_where compiles `in`/`not_in` to `field IN %(pname)s` with a Python tuple as the bound value (`pname = acc.add(tuple(value)); return f"{field} {keyword} %({pname})s"`). psycopg3 uses server-side binding, so this renders as `sender_domain IN $1`, which PostgreSQL rejects. Verified empirically against the project's test DB: parse_query({'where': {'field': 'sender_domain', 'op': 'in', 'value': ['a.com','b.com']}}) produces `SELECT message_id FROM emails WHERE sender_domain IN %(__p0)s ...` which raises `psycopg.errors.SyntaxError: syntax error at or near "$1"`. Every DSL query using `in` or `not_in` fails at runtime. The only test (tests/unit/test_dsl.py:92) asserts the generated SQL string and never executes it; no integration test covers these operators.

**Fix:** Compile to `field = ANY(%(pname)s)` / `field != ALL(%(pname)s)` and bind `list(value)` instead of a tuple (this also handles empty lists correctly — `= ANY('{}')` returns no rows instead of a syntax error). Add an integration test that executes an `in`/`not_in` query against Postgres.

**Verification:** Confirmed empirically: dsl.py:534 binds a tuple to `field IN %(p)s`, and the default psycopg3 server-side binding used by MailDB.query() renders it as `IN $1`, which the test Postgres rejects with `syntax error at or near "$1"`; the only test asserts the SQL string without executing it, and the proposed `= ANY(list)` fix executes cleanly.

#### [correctness] unreplied() silently ignores max_to/max_cc/max_recipients/direct_only filters
*src/maildb/maildb.py:740* — verifier confidence: high

MailDB.unreplied() declares `max_to`, `max_cc`, `max_recipients`, and `direct_only` parameters (lines 740-743), but the body never references them — neither branch calls _build_filters for recipient-count conditions and no jsonb_array_length condition is built (verified: zero uses of these names between lines 746 and 883). The MCP tool wrapper (src/maildb/server.py:451-499) accepts the same parameters, documents them as 'recipient count filters (same as find)', and then drops them — the db.unreplied(...) call at server.py:488 does not pass them. A caller asking for unreplied(direct_only=True) gets unfiltered results (mailing-list and CC-heavy mail included) with no error, i.e. silently wrong data. Compare mention_search() (lines 976-983), which correctly routes the same kwargs through _build_filters.

**Fix:** In unreplied(), call self._build_filters(max_to=max_to, max_cc=max_cc, max_recipients=max_recipients, direct_only=direct_only) and append the returned conditions (with `e.`-qualified column references, e.g. `e.recipients`) to both the inbound and outbound condition lists; plumb the four params through the db.unreplied() call in server.py:488. Add an integration test mirroring test_find_direct_only for unreplied.

**Verification:** Verified in src/maildb/maildb.py: unreplied() declares max_to/max_cc/max_recipients/direct_only but neither the inbound nor outbound branch references them (no _build_filters call, no jsonb_array_length condition), and the MCP wrapper in src/maildb/server.py documents them as 'recipient count filters (same as find)' yet omits them from the db.unreplied() call — so direct_only=True etc. silently return unfiltered results, unlike mention_search() which correctly routes the same kwargs through _build_filters at line 976.

#### [correctness] Embed workers release FOR UPDATE SKIP LOCKED locks before embedding — all workers process the same rows
*src/maildb/ingest/embed.py:34* — verifier confidence: high

_fetch_batch selects rows with `FOR UPDATE SKIP LOCKED` but then immediately calls `conn.rollback()` ("Rollback to release FOR UPDATE locks — we'll update by id later"). Releasing the lock defeats SKIP LOCKED entirely: with the default embed_workers=4, every worker's `SELECT id ... WHERE embedding IS NULL LIMIT 50` (no ORDER BY, same plan) returns largely the same 50 rows, and since no lock is held while embedding, all four workers send the same texts to Ollama and overwrite each other's UPDATEs. Consequences: (1) ~Nx duplicated embedding compute, so multi-worker parallelism yields no throughput gain; (2) `total_updated` summed across workers (orchestrator.py:276, stored as the embed task's messages_total) overcounts because the same email is counted by several workers; (3) a row that one worker marks with the zero-vector skip sentinel can be concurrently re-embedded by another, making _embed_and_update_single's "not picked up again" guarantee racy.

**Fix:** Claim rows atomically instead of select-then-rollback, e.g. `UPDATE emails SET embedding = <claim sentinel> WHERE id IN (SELECT id FROM emails WHERE embedding IS NULL LIMIT %(n)s FOR UPDATE SKIP LOCKED) RETURNING ...`, or keep the transaction (and its locks) open for the duration of the batch and commit the embedding updates on the same connection.

**Verification:** Confirmed: embed.py:30-35 selects with FOR UPDATE SKIP LOCKED then immediately rolls back, releasing locks before the slow embedding step, while orchestrator.py:263-276 spawns 4 identical workers with no other claim/partition mechanism — so workers duplicate work, parallelism is nullified, and summed total_updated (stored as messages_total at orchestrator.py:279) overcounts. Final embeddings data is still correct (duplicate writes are equivalent), but the multi-worker feature is functionally defeated, so high severity stands.

#### [correctness] Parse tasks stuck 'in_progress' after a worker crash are never reclaimed; import finalizes 'completed' with missing emails
*src/maildb/ingest/tasks.py:44* — verifier confidence: high

claim_task only claims `status = 'pending'` rows, and the only reset path is reset_failed_tasks (status='failed'). If a parse worker process crashes (OOM, SIGKILL, power loss) between claim_task and complete_task/fail_task, the task stays 'in_progress' forever. On resume, orchestrator.py:211 starts workers because `parse_status["in_progress"] > 0`, but the workers find no 'pending' work and exit immediately; the failure gate at orchestrator.py:233 checks only `failed > 0`, so the pipeline proceeds to index/embed and finalizes the imports row as 'completed' (orchestrator.py:296-304) even though the crashed chunk's messages were never inserted. Silent data loss with no error surfaced — the same class of latent resumability bug as the recently fixed import_id scoping issue (#86).

**Fix:** At the start of the parse phase (after _resume_or_create_import, before spawning workers), reset this import's stale 'in_progress' tasks back to 'pending' (e.g. `UPDATE ingest_tasks SET status='pending', worker_id=NULL WHERE phase='parse' AND status='in_progress' AND import_id=%(id)s`). Re-parsing a chunk is idempotent thanks to ON CONFLICT (message_id). Additionally, fail the run (like the failed>0 check) if in_progress remains after the worker pool drains.

**Verification:** Confirmed in code: claim_task (tasks.py:44) only claims 'pending', no path resets stale 'in_progress' tasks (reset_failed_tasks handles 'failed' only), parse.py's fail_task fires only on except Exception (misses SIGKILL/OOM and even KeyboardInterrupt), and orchestrator.py:232-239/283-304 gates only on failed>0 before finalizing the import as 'completed' — so a worker killed mid-chunk yields silent missing emails on resume; re-parse is idempotent via ON CONFLICT(message_id), so the proposed reset fix is safe.

#### [correctness] `process_attachments retry` livelocks on the first deterministically failing attachment
*src/maildb/cli.py:668* — verifier confidence: high

process_run's --retry-failed defaults to False explicitly to avoid "the livelock where a failed row is immediately re-claimed by the same worker" (cli.py:445), but process_retry reintroduces exactly that: it passes retry_failed=True with selector `AND status='failed' AND (reason IS NULL OR reason NOT LIKE 'hard-timeout:%')`. _claim_row (process_attachments.py:269-286) claims pending/failed rows with `ORDER BY attachment_id` and no retry counter or per-session memory. A row that fails deterministically and quickly (e.g. reason 'marker: ...', not a timeout) is set back to 'failed' with a non-hard-timeout reason, immediately matches the selector again, and — being the lowest attachment_id in the set — is re-claimed in a tight loop. The worker spins forever on one attachment, never advancing; in supervised mode the supervisor never sees a stuck row (each attempt is fast) and _count_selected never reaches 0, so with the default --max-runtime=0 the command never exits.

**Fix:** Track attempted attachment_ids within the session (skip rows already tried once, e.g. accumulate failed ids in _claim_and_process_loop and exclude them via `AND attachment_id != ALL(%(tried)s)`), or add a retry_count/attempted_at column to attachment_contents and exclude rows attempted in the current run.

**Verification:** Confirmed in code: process_retry (cli.py:655-670) passes retry_failed=True with a selector that re-matches any non-hard-timeout failure; _claim_row (process_attachments.py:254-292) claims by lowest attachment_id with no attempt tracking and the schema has no retry counter, so a fast deterministic failure is re-claimed in an infinite loop; the supervisor only detects rows stuck in 'extracting' and _count_selected never reaches 0, so with default --max-runtime=0 the command never terminates or advances.

#### [correctness] RFC 2047 encoded-word headers are never decoded — non-ASCII subjects/sender names stored as '=?UTF-8?B?...?=' literals
*src/maildb/parsing.py:66* — verifier confidence: high

_safe_header just does `str(value)` on the raw header. Real mbox files (Gmail Takeout uses the default compat32 policy via mailbox.mbox) deliver non-ASCII Subject/From headers as RFC 2047 encoded words, so every non-ASCII subject is stored in the DB as e.g. '=?UTF-8?B?44GT44KT44Gr44Gh44Gv?=' and sender_name from `email.utils.parseaddr(_safe_header(msg, "From"))` (parsing.py:159) is similarly garbage. These fields feed build_embedding_text (embeddings.py:33), so semantic search and embeddings are poisoned for every email with a non-ASCII subject or sender — extremely common in real mail. The existing test (tests/unit/test_parsing.py:201-209) only asserts `"llo" in result`, which passes whether or not decoding happens, so it doesn't pin the correct behavior.

**Fix:** Decode encoded words in _safe_header: `str(email.header.make_header(email.header.decode_header(result)))` wrapped in try/except (falling back to the raw string), before the NUL strip. Add a test asserting a '=?UTF-8?B?...?=' subject round-trips to its decoded form.

**Verification:** Reproduced directly: parse_mbox on an mbox with RFC 2047 headers returns subject/sender_name as raw '=?UTF-8?B?...?=' strings because _safe_header (parsing.py:66) only does str(value) and no decode_header/make_header exists anywhere in the codebase; these undecoded fields feed build_embedding_text (embeddings.py:33), poisoning search/embeddings for all non-ASCII mail.

#### [performance] COUNT(*) OVER() in vector searches defeats HNSW ANN or returns bogus totals
*src/maildb/maildb.py:326* — verifier confidence: high

search() runs `SELECT ..., 1 - (embedding <=> %(query_embedding)s::vector) AS similarity, COUNT(*) OVER() AS _total ... ORDER BY embedding <=> ... LIMIT %(limit)s` (lines 323-331), and search_attachments() does the same at line 1098. A window aggregate over the whole result set must consume every input row before LIMIT applies. Two outcomes, both bad: (a) if the planner picks the HNSW index scan (created in ingest/index.py with default ef_search=40, non-iterative), the scan yields at most ~ef_search candidates, so `_total` is silently capped at ~40 and is meaningless as a pagination total; (b) if the planner picks a seq scan to satisfy the window agg, every semantic search computes 768-dim cosine distance for all 100k-1M rows (seconds per query). Either correctness of `total` or ANN performance is sacrificed on every search/search_attachments/search_all call.

**Fix:** Drop COUNT(*) OVER() from the vector-ordered queries (an exact total is not meaningful for ANN anyway). Return `total = offset + len(rows)` or a separate cheap filtered count. Also consider setting `SET LOCAL hnsw.ef_search` >= limit+offset (search_all over-fetches 2*(limit+offset), which can exceed the default 40) so deep pagination and filtered searches don't silently truncate.

**Verification:** Confirmed empirically on the live 1.28M-row database: with COUNT(*) OVER() the planner picks a Seq Scan + WindowAgg (15.2s per search, HNSW index bypassed); removing the window aggregate restores the HNSW index scan (0.22s, ~70x faster). The exact claimed SQL exists in search() and search_attachments(), and no hnsw.ef_search is set anywhere despite search_all over-fetching 2*(limit+offset).

#### [performance] mention_search and subject_contains use leading-wildcard ILIKE with no trigram index
*src/maildb/maildb.py:953* — verifier confidence: high

mention_search() builds `body_text ILIKE %(pattern)s ESCAPE '\\' OR subject ILIKE %(pattern)s` with pattern `%text%` (maildb.py:951-954), and _build_filters' subject_contains does the same for subject (line 180). There is no pg_trgm index anywhere in schema_indexes.sql, and leading-wildcard ILIKE can't use a btree anyway. Every mention_search MCP call therefore seq-scans and detoasts the body_text of every email. At 100k emails this is already seconds; at 1M with large bodies it is tens of seconds per call, and the tool is advertised as the 'no Ollama needed' search path so it will be used often.

**Fix:** Add `CREATE EXTENSION IF NOT EXISTS pg_trgm;` and GIN trigram indexes: `CREATE INDEX ... ON emails USING gin (body_text gin_trgm_ops)` and `... (subject gin_trgm_ops)` (or move to a tsvector FTS column if word-level matching is acceptable). Keep ILIKE in the query — pg_trgm GIN accelerates ILIKE '%...%' directly.

**Verification:** Confirmed in code (maildb.py:180, 953-954) and empirically: the live DB holds ~1.28M emails and a leading-wildcard ILIKE on body_text exceeds a 5s statement timeout; mention_search has no timeout and no pg_trgm/FTS index exists in schema_indexes.sql, so the advertised no-Ollama search path seq-scans the whole table on every call.

#### [performance] Embed workers release SKIP LOCKED locks via rollback before embedding — 4 workers duplicate each other's work
*src/maildb/ingest/embed.py:34* — verifier confidence: high

_fetch_batch executes `SELECT ... WHERE embedding IS NULL LIMIT %(batch_size)s FOR UPDATE SKIP LOCKED` and then immediately `conn.rollback()` ('Rollback to release FOR UPDATE locks — we'll update by id later', embed.py:33-34). The lock window is only the fetch (~ms), while the Ollama embed call takes seconds per batch of 50. The orchestrator runs embed_workers=4 processes (orchestrator.py:263-275); with no ORDER BY, all workers scan the heap in the same order, so after worker A rolls back, workers B/C/D fetch the very same still-NULL rows and re-embed them. Effective throughput collapses toward a single worker (up to 4x redundant Ollama compute) on a phase that already takes hours at 100k-1M emails.

**Fix:** Keep the claiming transaction open across the embed call (fetch FOR UPDATE SKIP LOCKED, embed, UPDATE, commit in one transaction — the single-connection pool per worker already supports this), or add an explicit claim marker (e.g. a claimed_at column or sentinel) like the attachment pipeline's claimed_by pattern.

**Verification:** embed.py _fetch_batch (lines 28-35) really does FOR UPDATE SKIP LOCKED then immediate conn.rollback(), releasing locks before the multi-second Ollama embed call, and orchestrator.py:263-275 runs 4 identical embed_worker processes with no claim marker, partitioning, or ORDER BY — so workers fetching during another's embed window get the same still-NULL rows and duplicate the work. Results stay correct (idempotent UPDATE by id), but parallelism is defeated on the longest ingestion phase, so the high/performance rating stands.

#### [correctness] unreplied tool accepts and documents recipient-count filters but silently ignores them
*src/maildb/server.py:488* — verifier confidence: high

The MCP tool `unreplied` declares `max_to`, `max_cc`, `max_recipients`, `direct_only` (server.py:459-462) and documents them as "recipient count filters (same as find)", but the call to `db.unreplied(...)` (server.py:488-498) never passes them. Worse, `MailDB.unreplied` itself accepts the same four parameters in its signature (maildb.py:740-743) and never references them anywhere in its body — the SQL it builds (maildb.py:781-877) contains no jsonb_array_length conditions. An LLM caller using `unreplied(direct_only=True)` gets unfiltered results with no error, which is exactly the kind of silent wrong-answer an agent can't detect.

**Fix:** In MailDB.unreplied, build the recipient-count conditions via the existing `_build_filters(max_to=..., max_cc=..., max_recipients=..., direct_only=...)` helper (as mention_search already does at maildb.py:976-983, with `e.`-qualified columns) and append them to `conditions`; then pass the four parameters through in server.py's db.unreplied call. Add a test asserting direct_only changes the result set.

**Verification:** Verified directly: server.py:459-462 declares and documents max_to/max_cc/max_recipients/direct_only but the db.unreplied call (server.py:488-498) drops them, and MailDB.unreplied (maildb.py:729-883) accepts them in its signature yet never uses them in either SQL branch, so callers get silently unfiltered results. The proposed fix mirrors the existing _build_filters usage in mention_search.

### MEDIUM

#### [security] PII scrubber skips nested dict/list values; SQL params (email addresses, search terms) are logged unredacted to debug.log
*src/maildb/pii.py:95* — verifier confidence: high

scrub_pii() only redacts top-level string values and sensitive-named keys: `value = event_dict[key]; if not isinstance(value, str): continue` (pii.py:94-96). It never recurses into dict or list values. Meanwhile MailDB logs the raw SQL parameter dict on every query: `logger.debug("sql_execute", sql=sql, params=params)` in _query_dicts (maildb.py:55) and _query_one_dict (maildb.py:71). Because `params` is a dict (not a str), scrub_pii skips it entirely, so its values pass through unredacted. These values include exactly the PII the module exists to protect: correspondent email addresses (`sender`, `sender_domain`, `recipient_json`, `account`, `address`) and free-text search content (`subject_pattern`, `pattern` ILIKE bodies). I confirmed this at runtime: scrubbing an event with `params={'sender':'alice@acme.com','subject_pattern':'%divorce settlement%'}` leaves both values intact, while the same email address as a top-level string is correctly replaced with [REDACTED-EMAIL]. The file sink logs at DEBUG by default (config.py debug_log_level='DEBUG', file_handler.setLevel(DEBUG) in cli.py:87-89) to ~/.maildb/debug.log, and serve()/all CLI commands call _configure_logging(), so this leak is on by default. The module docstring ('PII scrubbing structlog processor') and its registration in the logging pipeline give a false assurance that logs are PII-safe.

**Fix:** Make scrub_pii recurse into dict and list values (apply the same key-name redaction and _scrub_value/_truncate to nested string leaves), OR stop logging the raw `params` dict (e.g. log only param key names, or pass it through a redacting serializer). Recursing in the processor is the robust fix since it protects every call site, not just these two.

**Verification:** Confirmed in code and reproduced at runtime: scrub_pii (pii.py:95) skips non-string values without recursing, while maildb.py:55/71 log the raw SQL params dict at DEBUG; the file sink defaults to DEBUG (config.py:36, cli.py:87-91), so email addresses and search terms reach ~/.maildb/debug.log unredacted despite cli.py's explicit claim that PII scrubbing covers both sinks. Medium severity is appropriate since the leak is to a local file on the user's own machine but defeats the module's stated purpose.

#### [correctness] DSL default ORDER BY date DESC breaks aggregate-only queries without group_by
*src/maildb/dsl.py:347* — verifier confidence: high

_resolve_order_by appends a default ` ORDER BY date DESC` whenever the spec has no `order_by` and no `group_by`. For a whole-table aggregate spec like {'select': [{'count': '*', 'as': 'total'}]} this generates `SELECT count(*) AS total FROM emails ORDER BY date DESC LIMIT 50`, which fails. Verified empirically: psycopg raises GroupingError: column "emails.date" must appear in the GROUP BY clause or be used in an aggregate function. 'How many emails do I have?' — the most basic Tier-2 query — errors unless the user also supplies a redundant group_by or order_by. All existing tests pair aggregates with group_by, so this path is untested.

**Fix:** Pass the select items (or a has_aggregates flag computed in _build_select) into _resolve_order_by and suppress the default ORDER BY when the select contains any aggregate, mirroring the existing has_group_by suppression.

**Verification:** Confirmed in dsl.py:340-347 — _resolve_order_by only suppresses the default ORDER BY date DESC for group_by, not for aggregate-only selects; parse_query({'select': [{'count': '*', 'as': 'total'}]}) provably emits "SELECT count(*) AS total FROM emails ORDER BY date DESC LIMIT 50", which PostgreSQL rejects, and all existing tests pair aggregates with group_by so the path is untested. Medium severity is correct: a basic whole-table count fails, but it errors loudly and has trivial workarounds.

#### [correctness] search_all() reports a fabricated total capped at the over-fetch size
*src/maildb/maildb.py:1233* — verifier confidence: high

search_all over-fetches each source at `over_fetch = 2 * (limit + offset)`, discards the true totals returned by search() and search_attachments() (both calls bind them to `_`), and returns `total = len(unified)` — the merged over-fetched list length. With the default limit=20, total can never exceed 80 regardless of how many rows actually match, so the {total, offset, limit} pagination contract exposed via the MCP search_all tool is wrong whenever matches exceed the over-fetch (clients will stop paginating early or compute wrong page counts). Also, `max(2 * (limit + offset), limit + offset)` on line 1181 is a no-op since 2*(x) >= x for non-negative x.

**Fix:** Capture the real totals (`email_hits, email_total = ...`; `attachment_hits, att_total = ...`) and return `total = email_total + att_total` (documenting it as an upper bound across sources), or otherwise mark the returned total as truncated. Drop the no-op max() while there.

**Verification:** Confirmed in maildb.py:1182-1234 — both inner searches return true COUNT(*) OVER() totals that search_all discards into `_`, then returns total=len(unified) which is capped at 4*(limit+offset) (80 by default); server.py:812-843 exposes this fabricated total in the documented {total, offset, limit} MCP pagination contract, and the max() on line 1181 is indeed a no-op. Medium severity stands.

#### [correctness] claim_task is not scoped to import_id and split_mbox rmtree's the shared tmp dir — a new import can silently consume/destroy an abandoned import's pending chunks
*src/maildb/ingest/split.py:28* — verifier confidence: high

Two interacting latent bugs in the multi-account flow: (1) claim_task (tasks.py:38-53) claims any pending 'parse' task regardless of import_id, so workers spawned for import B will drain leftover pending tasks from an interrupted import A (whose imports row stays 'running' but is keyed to a different source_file, so it isn't resumed). The DB tagging is correct (parse.py looks up source_account per task), but attachments are written under import B's attachment_dir argument, and B's run blocks on/absorbs A's work invisibly. (2) Worse, split_mbox unconditionally `shutil.rmtree(output_dir)` (split.py:27-29) before writing chunks — if both imports use the same tmp_dir (the normal CLI configuration), B's split deletes A's remaining chunk files while A's parse tasks still reference them, guaranteeing those tasks process empty data (see the parse_mbox/mailbox.mbox finding) and A's remaining messages are silently lost.

**Fix:** Scope claim_task to the orchestrator's import_id (pass import_id into process_chunk and add `AND import_id = %(import_id)s` to the claim query), and write chunks into a per-import subdirectory (e.g. tmp_dir/<import_id>/) instead of rmtree'ing the shared tmp_dir.

**Verification:** Both halves verified: claim_task (tasks.py:40-47) has no import_id filter while the orchestrator only scoped status checks (commit 9635e90), and split_mbox rmtrees the shared ingest_tmp_dir (split.py:27-29) that all imports use via config.py:27. Loss is silent because mailbox.mbox auto-creates missing chunk files, and deterministic chunk names additionally allow A's stale tasks to ingest B's data under A's source_account.

#### [correctness] parse_mbox silently treats a missing chunk file as an empty mailbox and the task completes with 0 messages
*src/maildb/parsing.py:246* — verifier confidence: high

parse_mbox calls `mailbox.mbox(str(mbox_path))` with the default create=True. If a chunk file referenced by a pending task no longer exists (machine reboot clearing tmp_dir, the split.py rmtree above, manual cleanup), mailbox.mbox creates an empty file instead of raising, parse yields zero messages, and _process_single_chunk happily calls complete_task with messages_total=0 (parse.py:264-271). The resume path therefore converts missing input data into a 'completed' task and a 'completed' import with no error — silent data loss masquerading as success.

**Fix:** Pass create=False (`mailbox.mbox(str(mbox_path), create=False)`) or check `mbox_path.exists()` first and raise, so the task is marked failed and surfaced by the orchestrator's failed-tasks gate instead of silently completing.

**Verification:** Confirmed empirically: mailbox.mbox with default create=True turns a missing chunk file into an empty mailbox (parsing.py:246), _process_single_chunk has no existence check and calls complete_task with messages_total=0 (parse.py:159, 264-271), and the precondition is realistic — chunks default to /tmp/maildb-ingest-tmp-dir (cleared on reboot/periodic macOS cleanup) and split_mbox rmtree's the shared tmp_dir, while the pipeline is explicitly restartable. Medium severity stands: silent data loss signal-wise, but recoverable since the source mbox remains.

#### [correctness] _extract_body decodes all bodies as UTF-8, ignoring the MIME part charset
*src/maildb/parsing.py:104* — verifier confidence: high

Every payload decode is `payload.decode("utf-8", errors="replace")` (parsing.py:104, 108, 113) with no use of `part.get_content_charset()` anywhere in the codebase (verified by grep). Emails declared as ISO-8859-1, windows-1252, Shift-JIS, GB2312, etc. — a large fraction of older mail — are stored as mojibake/replacement characters in body_text and body_html, which then corrupts embeddings and search. Data is permanently degraded at ingest time since re-parse requires a full reset.

**Fix:** Use the declared charset with a UTF-8 fallback: `charset = part.get_content_charset() or "utf-8"` then `payload.decode(charset, errors="replace")` inside a try/except LookupError that falls back to utf-8 for bogus charset names.

**Verification:** Confirmed: parsing.py:104/108/113 are the only payload decode sites and all hardcode UTF-8 with errors="replace"; get_content_charset is unused anywhere in src/ or tests/, so non-UTF-8 declared bodies (ISO-8859-1, windows-1252, Shift-JIS — common in older mbox archives this tool ingests) are stored as mojibake at ingest, corrupting persisted body_text/body_html and downstream embeddings/search. Medium severity stands since the majority UTF-8/ASCII mail is unaffected and nothing crashes.

#### [correctness] _split_oversized emits an empty-string chunk and an over-limit chunk when a single word exceeds max_tokens
*src/maildb/ingest/chunking.py:99* — verifier confidence: high

In the word-level fallback, when the first word appended already exceeds max_tokens: `current=[w]` → count > max → `current.pop()` → `out.append(" ".join(current))` appends an empty string "" → `current=[w]`. At loop end `if current: out.append(" ".join(current))` emits the giant word as a chunk whose token_count exceeds max_tokens. The same over-limit emission happens mid-loop whenever a giant word follows other words (the giant word becomes `current` and is emitted whole on the next iteration). Single 'words' longer than 1024 tokens are realistic in Marker output (data URIs, base64 blobs, long URLs, tables collapsed to one line). Consequences: empty-text rows in attachment_chunks get sent to `client.embed("")` which can error and fail the whole attachment via EmbedFailedError, and oversized chunks can exceed the embedding model's context, violating the max_tokens contract.

**Fix:** In the giant-word branch, skip appending when `current` is empty after pop, and hard-split the oversized word itself using truncate_to_tokens/token-level slicing (maildb.tokenizer already provides the machinery) instead of emitting it whole. Filter out empty pieces before returning.

**Verification:** Empirically reproduced: a realistic whitespace-free base64-like word (13998 tokens; punctuation defeats WordPiece's [UNK] collapse) makes _split_oversized emit an empty-string piece plus the whole over-limit word, and the empty chunk survives chunk_markdown's merge into attachment_chunks where process_attachments embeds it unfiltered. Medium stands — Ollama truncates oversized input by default and the empty-embed error is version-dependent, so the likely impact is contract violation and degraded/failed embedding of one attachment, not data corruption.

#### [correctness] Supervisor stuck-row kill leaves the interrupted worker's in-flight row as 'extracting', causing cascading false hard-timeouts after an abnormal worker death
*src/maildb/ingest/process_attachments.py:829* — verifier confidence: high

If a worker dies without the supervisor's involvement (OOM SIGKILL — a documented failure mode in this project's drain retrospectives), its in-flight row stays 'extracting' with claimed_by=supervisor_id. The supervisor respawns a worker; once that orphaned row's extracted_at passes extract_timeout_s, _find_stuck_extracting returns it and the supervisor kills the *new, innocently progressing* worker (line 829: `worker.kill()`), marks the orphan failed, and — unlike the max_runtime path which calls _revert_in_flight_to_pending — leaves the newly interrupted row as 'extracting'. That row then becomes the next 'stuck' row, so every extract_timeout_s the supervisor kills a healthy worker and falsely marks one row `hard-timeout: killed after Ns` even though it never hung. Default retry deliberately excludes hard-timeout rows, so these falsely-failed rows are effectively dropped from future retries.

**Fix:** After killing a stuck worker and marking the genuinely stuck rows failed, revert the supervisor's remaining 'extracting' rows to 'pending' (reuse _revert_in_flight_to_pending excluding the stuck ids). Alternatively, detect dead-worker orphans when `worker.is_alive()` turns false (killed=False path) and revert claimed_by rows to pending before respawning.

**Verification:** Confirmed in process_attachments.py: the killed=False respawn path never reverts a dead worker's orphaned 'extracting' row, new workers only claim pending/failed so the orphan ages until _find_stuck_extracting flags it, and the stuck-kill branch (line 829) kills the healthy replacement worker while leaving its in-flight row 'extracting' — repeating the cycle every extract_timeout_s, with each falsely hard-timeout-failed row excluded from default retry (cli.py:661). The max_runtime branch's _revert_in_flight_to_pending (line 813) shows the missing cleanup exists but is not applied here.

#### [performance] Recipient filters use recipients->'to' @> which cannot use the GIN index on recipients
*src/maildb/maildb.py:164* — verifier confidence: high

All recipient predicates are written as `recipients->'to' @> %(recipient_json)s OR recipients->'cc' @> ... OR recipients->'bcc' @> ...` (maildb.py:164-169 in _build_filters, 903-908 in correspondence(), 843-846 in unreplied(), 1027-1031 in search_attachments). The only relevant index is `idx_email_recipients ON emails USING GIN (recipients)` (schema_indexes.sql:8), which indexes the whole jsonb column; an expression like `recipients->'to'` is a derived value and cannot use that index. So find(recipient=...), correspondence() (a hot MCP tool with default limit 500), and unreplied(outbound, recipient=...) all do full sequential scans of the emails table. At 1M emails with jsonb detoasting this is multi-second per call.

**Fix:** Rewrite the predicate to the indexable form: `(recipients @> jsonb_build_object('to', %(addr_arr)s) OR recipients @> jsonb_build_object('cc', ...) OR recipients @> jsonb_build_object('bcc', ...))` — i.e. `recipients @> '{"to": ["a@b.com"]}'::jsonb` — which the existing GIN jsonb_ops index supports. Alternatively add three expression GIN indexes on (recipients->'to'), (recipients->'cc'), (recipients->'bcc').

**Verification:** Confirmed empirically against the live maildb database (1,279,362 emails): the current `recipients->'to' @> ...` form at maildb.py:165-167, 843-845, 905-907, 1028-1030 plans as a Parallel Seq Scan (271 ms measured) while the proposed `recipients @> '{"to": [...]}'` form uses idx_email_recipients via Bitmap Index Scan (3.5 ms, ~80x faster) — the GIN jsonb_ops index genuinely cannot serve the derived-expression predicate. Severity corrected from high to medium because the real-world impact is ~270 ms warm (not multi-second) on this single-user local tool, though the fix is trivial and clearly worthwhile.

#### [performance] Every list query fetches and parses the 768-dim embedding and body_html, then discards them
*src/maildb/maildb.py:41* — verifier confidence: high

SELECT_COLS (maildb.py:41-46) includes `embedding` and `body_html` and is used by find, search, get_thread, correspondence, mention_search, unreplied, get_emails. A 768-dim vector is ~8-15KB in pgvector text form; Email.from_row calls _parse_embedding (models.py:25-34) which splits the string and runs float() 768 times per row. The MCP layer then immediately drops both fields: `d.pop("embedding", None); d.pop("body_html", None)` (server.py:120-121). correspondence() defaults to limit=500, so a single MCP call transfers ~5MB of vector text and executes ~384,000 Python float() conversions plus the full HTML bodies, all to throw them away. Only topics_with()/cluster() actually use embeddings client-side.

**Fix:** Define a second column list without embedding/body_html for all list/read paths (and make Email.embedding/body_html optional fields defaulting to None); keep the full SELECT_COLS only for topics_with()/cluster() which need embeddings for farthest-point selection.

**Verification:** Confirmed in code: SELECT_COLS (maildb.py:41-46) and unreplied's select_cols_aliased fetch embedding+body_html on every list path, Email.from_row parses the 768-dim vector via _parse_embedding (models.py:25-34, 108), and server.py:120-121 immediately drops both fields; only topics_with/cluster use embeddings. Real waste, but for a local single-user tool against local Postgres the impact is modest latency/CPU per call, so medium rather than high severity.

#### [performance] Repeated 'WHERE embedding IS NULL' batch fetch has no supporting index — O(n^2) page scans over the embed run
*src/maildb/ingest/embed.py:15* — verifier confidence: high

SELECT_BATCH_SQL (embed.py:15-21) is re-executed for every batch: `SELECT id, subject, sender_name, body_text FROM emails WHERE embedding IS NULL LIMIT 50`. There is no partial index for embedding IS NULL (the HNSW index is created only after the phase and doesn't index NULLs), so each fetch is a seq scan that must skip over the growing prefix of already-embedded rows before finding 50 NULLs. Over a 1M-email run that's ~20k fetches scanning on average ~500k rows each — billions of row visits of pure overhead layered on top of the duplicate-work issue.

**Fix:** Create a partial index before the embed phase: `CREATE INDEX ... ON emails (id) WHERE embedding IS NULL` (drop it afterwards), or use a keyset cursor (WHERE id > last_seen ORDER BY id) per worker so each fetch is O(batch).

**Verification:** Confirmed: embed.py:15-21 re-runs 'WHERE embedding IS NULL LIMIT n FOR UPDATE SKIP LOCKED' per batch across 4 workers, no partial/btree index on embedding exists anywhere (HNSW is created only post-phase and wouldn't serve IS NULL), and the project's documented real corpus is 841,930 emails — so the quadratic prefix-skip seq-scan pattern is real at realistic scale. Severity stays medium because the phase is bottlenecked by Ollama embedding throughput, making this significant but non-dominant overhead.

#### [performance] Orchestrator drops and rebuilds all indexes (including HNSW) even for small incremental imports
*src/maildb/ingest/orchestrator.py:213* — verifier confidence: high

Whenever the parse phase has any pending tasks, run_pipeline calls drop_non_unique_indexes(pool) (orchestrator.py:213), whose DROP list includes idx_email_embedding — the HNSW vector index (index.py:13-26). After the embed phase, create_hnsw_index rebuilds it with ef_construction=64. The repo explicitly supports multi-account incremental imports (email_accounts, resume logic), so importing a second small mbox (e.g. 10k messages) into an existing 1M-email archive drops every search index and triggers a full HNSW rebuild over 1M 768-dim vectors — hours of CPU — plus leaves the MCP server effectively unusable (seq scans, no vector index) for the duration. Drop-before-bulk-load only pays off for the initial large import.

**Fix:** Make index dropping conditional on import size relative to the existing table (e.g. only drop when emails is empty or chunk count exceeds a threshold), or at minimum never drop idx_email_embedding — incremental inserts into an existing HNSW index are far cheaper than a full rebuild.

**Verification:** Confirmed: orchestrator.py:213 unconditionally calls drop_non_unique_indexes (whose list includes the HNSW idx_email_embedding) whenever parse has pending tasks — true for every new import — and the project demonstrably supports incremental multi-account imports into an existing 841K-email archive (DESIGN.md, commit 9635e90), so a small second import drops all search indexes and forces a full HNSW rebuild. Downgraded to medium because the impact is a temporary, self-healing degradation (likely tens of minutes, not hours, for ~841K vectors) on an infrequently-run single-user local tool.

#### [performance] Pagination COUNT(*) OVER() forces full materialization on every list endpoint; worst in unreplied()
*src/maildb/maildb.py:808* — verifier confidence: high

find(), mention_search(), correspondence(), unreplied() and long_threads() all append `COUNT(*) OVER() AS _total` so the LIMIT can never terminate the plan early — the executor must produce every matching row to feed the window aggregate. This is worst in unreplied() (maildb.py:808-820): the outer filter `sender_address != ALL(%(user_emails)s)` matches nearly the whole table, and each row runs the NOT EXISTS probe against idx_email_thread_sender_date — so a default unreplied() call at 1M emails performs ~1M index probes even though only 100 rows are returned, where a plain ORDER BY date DESC LIMIT 100 plan could stop after a few hundred probes.

**Fix:** Make the exact total opt-in (e.g. include_total=False default, or run a separate count query only when the client asks for it), or return `total = offset + len(rows)` with a has_more flag for the expensive endpoints (unreplied especially).

**Verification:** Confirmed: COUNT(*) OVER() AS _total appears in all cited methods (maildb.py:271, 326, 809, 871, 924, 985, 1098), and the window aggregate genuinely prevents LIMIT from terminating the plan early; unreplied() inbound (lines 808-820) filters nearly the whole table and runs a NOT EXISTS probe per row, with idx_email_date and idx_email_thread_sender_date both existing in schema_indexes.sql so the claimed early-termination alternative plan is real. Severity stays medium: the exact total is a deliberate pagination feature and the cost is seconds-level latency on a local single-user tool, not a correctness issue.

#### [correctness] process_attachments run --limit/--sample subqueries ignore status and other selectors, breaking selection
*src/maildb/cli.py:507* — verifier confidence: high

The `--limit` selector appends `AND attachment_id IN (SELECT attachment_id FROM attachment_contents ORDER BY attachment_id LIMIT %(limit)s)` (cli.py:507-513) — the subquery has no `status IN ('pending'...)` filter and ignores the other selector parts (`--only`, `--min-size`, etc.). On a partially drained table, `--limit 100` selects the 100 lowest attachment_ids overall (mostly already extracted), then intersects with pending rows, often processing 0 rows while thousands remain pending. `--sample` (cli.py:500-506) has the same flaw: it samples uniformly from all attachment_contents rows, not from the eligible set, so the effective sample size is unpredictable.

**Fix:** Make the subquery mirror the outer eligibility predicate: `SELECT attachment_id FROM attachment_contents WHERE status IN (...) <other selector parts> ORDER BY attachment_id LIMIT %(limit)s` — or better, move limit/sample handling into process_attachments.run's claim query so the LIMIT applies to actually-claimable rows.

**Verification:** cli.py:500-514 builds --limit/--sample subqueries over all attachment_contents rows without status or other selector filters, and process_attachments.py _claim_row intersects them with status IN ('pending'); since extracted rows persist and claiming proceeds in attachment_id order, --limit N on a partially drained table selects mostly already-extracted ids and can claim 0 rows while many remain pending, and --sample draws from the ineligible population.

#### [design] Invalid `fields` values are silently swallowed; get_emails falls back to full bodies
*src/maildb/server.py:721* — verifier confidence: high

Every tool computes `valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None`. If the caller passes only invalid names (e.g. fields=["body"] instead of "body_text"), `valid` becomes an empty frozenset: list tools then return rows of empty dicts ({} per email, since `_serialize_email` filters to the empty set), while `get_emails` does `fields=valid or SERIALIZABLE_EMAIL_FIELDS` (server.py:721) — the empty set is falsy, so it returns ALL fields including full body_text, the opposite of what a field projection request intends. Same typo yields two different silent behaviors depending on the tool.

**Fix:** Validate fields once in a shared helper: raise ValueError listing unknown names and the allowed set (FastMCP surfaces this to the model, which can self-correct). E.g. `unknown = set(fields) - SERIALIZABLE_EMAIL_FIELDS; if unknown: raise ValueError(f"Unknown fields {sorted(unknown)}; valid: {sorted(SERIALIZABLE_EMAIL_FIELDS)}")`.

**Verification:** Confirmed in src/maildb/server.py: line 717 builds an empty (non-None) frozenset for invalid-only field names, line 721's `valid or SERIALIZABLE_EMAIL_FIELDS` falls back to all fields including full body_text in get_emails, while list tools (e.g. lines 260, 348) pass the empty set straight through and return empty dicts — two silent divergent behaviors with no validation anywhere.

#### [design] get_attachment_markdown returns unbounded document text with no truncation control
*src/maildb/server.py:848* — verifier confidence: high

`get_attachment_markdown` returns the entire extracted markdown for an attachment (server.py:846-857 -> maildb.py:1137-1160 `SELECT c.markdown ...`). Extracted PDFs/XLSX can be hundreds of KB to MB of markdown, which blows the LLM context. The codebase clearly cares about this — there's a RESPONSE_SIZE_WARNING_BYTES=50_000 log warning (server.py:23) and `get_emails` has `body_max_chars` — but this tool, the most likely to return huge payloads, has no equivalent and the warning is log-only.

**Fix:** Add `max_chars: int | None` (with a sane default like 20000) and `offset: int = 0` parameters, returning `{text, total_chars, offset, truncated}` so the model can page through large documents, mirroring the body_max_chars/body_truncated convention used in get_emails.

**Verification:** Confirmed: server.py:846-857 returns the full markdown string from maildb.py:1137-1160 with no truncation/paging, no extraction-side size cap exists, and the codebase's own conventions (body_max_chars/body_truncated in get_emails, 50KB log-only warning) show the gap is real for the tool most likely to return huge payloads. Intentional ("full" in docstring) but a legitimate design gap; medium severity is appropriate.

#### [quality] search_attachments re-implements _build_filters (~55 duplicated lines) that will drift
*src/maildb/maildb.py:1018* — verifier confidence: high

`search_attachments` (maildb.py:1014-1070) hand-duplicates nearly every condition from `_build_filters` (maildb.py:128-213) with `e.`-prefixed columns — sender, sender_domain, recipient JSONB containment, after/before, labels, the direct_only/max_to/max_cc mutual-exclusion check, and the three jsonb_array_length filters, including the duplicated error message "Cannot combine direct_only with max_to or max_cc" (lines 147 and 1043). The inline comment admits the duplication. Any new filter (or bug fix like the missing ESCAPE handling) now has to be made in two places; the `unreplied` bug above shows this family of filters already drifts.

**Fix:** Add a `column_prefix: str = ""` (or `alias`) parameter to `_build_filters` and emit `f"{prefix}sender_address = ..."` etc.; have search_attachments call it with prefix="e." and delete the duplicated block. The account-EXISTS clause can take the alias too (it already uses its own subquery alias).

**Verification:** Verified: search_attachments (maildb.py:1018-1072) duplicates 9 filter conditions from _build_filters nearly verbatim with e. prefixes, including the identical ValueError message at lines 147 and 1044. The inline comment's rationale only argues against post-hoc string rewriting, not against the proposed prefix-parameter fix, which handles the alias-collision concern cleanly.

#### [quality] Multiple MCP tool docstrings misstate return shape and field defaults
*src/maildb/server.py:339* — verifier confidence: high

Tool docstrings are the API contract the LLM plans against, and several are wrong: (1) `get_thread`, `get_thread_for`, `topics_with`, and `cluster` say `fields: ... (default: all)` (server.py:339, 363, 434, 615) but `_serialize_email(e, None)` filters to DEFAULT_LIST_FIELDS which excludes body_text (server.py:133-134) — a model expecting thread bodies gets body_length instead and must guess why. (2) `top_contacts`, `topics_with`, `cluster`, and `long_threads` say "Returns list of {...}" (server.py:398, 436, 617, 650) but actually return the `{total, offset, limit, results}` wrapper via `_wrap_response`. 

**Fix:** Correct the docstrings: state that the default field set excludes body_text (pass fields=["body_text", ...] to include it) and that all wrapped tools return {total, offset, limit, results: [...]}, matching the wording already used by find/search/unreplied.

**Verification:** Confirmed in src/maildb/server.py: DEFAULT_LIST_FIELDS (line 102) excludes body_text yet get_thread/get_thread_for/topics_with/cluster docstrings claim "default: all"; and top_contacts/topics_with/cluster/long_threads docstrings say "Returns list of {...}" but actually return the {total, offset, limit, results} dict via _wrap_response. Docstrings are the MCP API contract the LLM plans against, so medium/quality severity is correct.

#### [correctness] search_all returns a fabricated `total` capped by over-fetch, not the real match count
*src/maildb/maildb.py:1233* — verifier confidence: high

`search_all` discards the true totals from both sub-searches (`email_hits, _ = self.search(...)`, `attachment_hits, _ = ...`) and sets `total = len(unified)` (maildb.py:1232-1234), which is bounded by 2*over_fetch = 4*(limit+offset). With limit=20 the tool reports at most total=80 regardless of actual matches, and the value grows as the caller pages (offset increases over_fetch) — so the pagination metadata the wrapper exposes ({total, offset, limit}) actively misleads an agent deciding whether to keep paging. `topics_with` (maildb.py:640) and `cluster` (maildb.py:712) have a related quirk: total is the candidate-pool size capped at 500, not the result count.

**Fix:** Return `email_total + attachment_total` from the sub-searches as `total` (documenting that merged ordering is only computed over the over-fetched window), or rename/document the field as `candidates_scanned`. For topics_with/cluster, document that total is the candidate pool size (capped at 500).

**Verification:** Verified in code: search_all (maildb.py:1182-1233) discards the true totals (which search genuinely computes via COUNT(*) OVER() at line 326) and returns total=len(unified), capped at 2*over_fetch=4*(limit+offset); server.py:814 documents this as pagination metadata {total, offset, limit} with no mention of the cap, and the value grows with offset. topics_with:640 and cluster:712 likewise return the 500-capped candidate-pool size as total.

#### [quality] README and DESIGN.md omit the entire attachment-extraction pipeline and `jobs` command
*docs/DESIGN.md:32* — verifier confidence: high

DESIGN.md line 32 states: "The CLI (`src/maildb/cli.py`, Typer) ships `serve` (run MCP) and `ingest run|status|reset|migrate`." The actual CLI also registers `jobs` (cli.py:143) and a full `process_attachments` sub-app with `run|status|retry|reembed` (cli.py:368, 437, 561, 613, 712). README.md similarly documents only ingest + MCP server and never mentions attachment extraction/search at all, despite it being a flagship shipped feature (~1,900 lines across src/maildb/ingest/process_attachments.py, extraction.py, chunking.py, plus the `search_attachments`/`search_all`/`get_attachment_markdown` MCP tools and 5 recent PRs #80-#86). Other DESIGN.md claims I spot-checked are accurate (identity derivation via `SELECT DISTINCT source_account FROM imports` at maildb.py:378; 4-phase pipeline with index drop/rebuild in orchestrator.py; multi-account dedup semantics match conftest/schema).

**Fix:** Update DESIGN.md line 32 to list `jobs` and `process_attachments`, and add a short README section covering `maildb process_attachments run/status/retry/reembed` and the attachment semantic-search MCP tools. DESIGN.md's architecture table (section 3) should also name extraction (Marker/MarkItDown) as part of the ingestion layer since it is a load-bearing pipeline.

**Verification:** Verified directly: DESIGN.md:32 enumerates only serve + ingest while cli.py registers jobs (line 143) and process_attachments run|status|retry|reembed (lines 368-712), and neither README.md nor DESIGN.md mentions process_attachments, extraction (Marker/MarkItDown), jobs, or the search_attachments/search_all/get_attachment_markdown MCP tools (server.py:749/795/848) despite ~1,300 lines of shipped pipeline code. Minor mitigation: DESIGN.md:29 does mention "attachment semantic search" in the Query layer, but the explicit CLI list is still wrong and the extraction leg is undocumented.

### LOW

#### [correctness] init_db: caught ALTER failure silently rolls back the entire init transaction
*src/maildb/db.py:59* — verifier confidence: high

init_db runs all DDL, the attachments.reference_count backfill, and the email_accounts backfill on one connection in a single transaction, then attempts `ALTER TABLE emails ALTER COLUMN source_account SET NOT NULL` inside try/except (lines 59-62). If the ALTER fails (the except exists precisely because that's anticipated — e.g. a concurrent insert of a NULL row between the count check and the ALTER, or a lock error), the Python exception is swallowed but the PostgreSQL transaction is already aborted; the subsequent `conn.commit()` (line 69) is silently converted to ROLLBACK by the server and raises nothing. Verified empirically: after a caught failing ALTER, commit() succeeds without exception and all prior work in the transaction is gone. So the warning log 'source_account_not_null_constraint_skipped' is wrong — schema DDL, the reference_count backfill, and the email_accounts backfill are all discarded, and 'database_initialized' is still logged.

**Fix:** Wrap the ALTER in a savepoint (`with conn.transaction():` nested block in psycopg3 creates a savepoint and rolls back only the ALTER on failure), or run the ALTER on a separate connection/after committing the main work.

**Verification:** Verified empirically with the project's psycopg3: a caught failing statement leaves the transaction INERROR, commit() at db.py:69 silently rolls back all DDL and backfills, yet 'database_initialized' is logged. Downgraded to low because every discarded operation is idempotent and init_db re-runs on each CLI/server start, and the ALTER-failure precondition (concurrent NULL insert; lock errors block rather than fail) is rare for this local single-user tool.

#### [correctness] OFFSET pagination over non-unique ORDER BY date can skip/duplicate rows across pages
*src/maildb/maildb.py:271* — verifier confidence: high

find() (line 271), correspondence() (line 924), mention_search() (line 985), and unreplied() (lines 818, 875) paginate with LIMIT/OFFSET ordered solely by `date` (VALID_ORDERS contains only single-column orders; sender_address variants have the same problem). Email Date headers have one-second resolution, so ties are common in real mailboxes (bulk sends, list traffic, same-thread replies); PostgreSQL's order among equal keys is unstable between executions, so consecutive pages can repeat some rows and silently omit others. Additionally `date` is nullable (schema_tables.sql:27) and `date DESC` defaults to NULLS FIRST, so undated emails surface at the top of the default ordering.

**Fix:** Append a unique tiebreaker to every ORDER BY used with OFFSET pagination, e.g. `ORDER BY date DESC, id` (and `ORDER BY {order}, id` for the validated orders), optionally with `NULLS LAST` for the date orders.

**Verification:** All cited queries verifiably paginate with LIMIT/OFFSET over non-unique nullable `date` with no tiebreaker (maildb.py:271, 924, 985, 818, 875; VALID_ORDERS lines 34-39), so the finding stands; severity downgraded to low because the database is static and single-user after ingest, making unstable tie ordering between consecutive page fetches unlikely in practice (mainly a risk while paginating during an active import).

#### [correctness] DSL empty and/or lists compile to invalid SQL '()'
*src/maildb/dsl.py:492* — verifier confidence: high

_build_where for combinators does `f"({' AND '.join(parts)})"` (lines 493-498). A spec like {'where': {'and': []}} — easy for an LLM caller of the MCP `query` tool to emit when it has no filters — produces `WHERE ()`, a PostgreSQL syntax error, instead of either matching everything or failing with a clear validation message.

**Fix:** In _build_where, raise ValueError('and/or requires at least one condition') for empty lists, or compile empty 'and' to TRUE and empty 'or' to FALSE.

**Verification:** Confirmed empirically: parse_query({'where': {'and': []}}) generates 'WHERE ()' (verified by running the code), and the MCP query tool in server.py passes the spec dict to parse_query with no validation layer. Impact is a confusing PostgreSQL syntax error rather than data corruption, so low severity is correct.

#### [correctness] COUNT(*) OVER() total collapses to 0 when offset is past the last row
*src/maildb/maildb.py:276* — verifier confidence: high

All paginated methods compute the total from the first returned row (`total = rows[0]["_total"] if rows else 0` — find:276, search:336, top_contacts:536, unreplied:880, correspondence:927, mention_search:987, long_threads:1307). When `offset >= matching rows`, the page is empty and the method reports total=0 even though matches exist. A client paginating until `offset >= total` that overshoots by one page (or re-requests after data shrank) is told the dataset is empty, which contradicts the totals it saw on earlier pages.

**Fix:** When rows is empty and offset > 0, run a cheap `SELECT count(*)` with the same WHERE clause (or document that total is only meaningful when results are non-empty); a shared helper would cover all seven call sites.

**Verification:** All seven call sites in src/maildb/maildb.py use `total = rows[0]["_total"] if rows else 0` with COUNT(*) OVER(), and no upstream clamping or count fallback exists in maildb.py or server.py, so an offset past the last matching row genuinely reports total=0 despite matches existing. Impact is minor (clients typically stop on an empty page), so low severity stands.

#### [quality] process_attachments.run() returns DB-wide cumulative status counts as the run's result
*src/maildb/ingest/process_attachments.py:548* — verifier confidence: high

At the end of run(), counts come from `SELECT status, count(*) FROM attachment_contents GROUP BY status` with no time or claimed_by scoping, so the 'Done. extracted=X failed=Y skipped=Z' CLI output (cli.py:546-548) and the counts persisted into run.json by _end_run_log report lifetime totals for the whole table, not what this run processed. A run that extracts 3 documents reports tens of thousands 'extracted', making per-run telemetry in the new run-log system (Tier 2/3 of #78) misleading.

**Fix:** Snapshot the status counts before processing and report the delta, or count rows with `extracted_at >= run_start` (extracted_at is set on every terminal transition in _set_status), or scope by claimed_by for supervised runs.

**Verification:** Confirmed: run() returns unscoped table-wide status counts (process_attachments.py:548-553), which cli.py prints as per-run results ("Done. extracted=...") and persists into run.json via finalize_run, so per-run telemetry reports lifetime totals; the per-run counts dict at line 499 is never incremented, and the only test asserts >= 1 on a fresh DB, masking the issue. Severity low is appropriate (misleading output, no functional harm).

#### [performance] Parse phase inserts emails one row at a time with savepoint round-trips
*src/maildb/ingest/parse.py:221* — verifier confidence: high

_process_single_chunk loops `for row in email_rows:` issuing per-row `SAVEPOINT row_insert`, INSERT ... RETURNING, an email_accounts INSERT, and `RELEASE SAVEPOINT` (parse.py:221-245) — roughly 4 server round-trips per email. The bad-row isolation rationale is sound, but paying it on every row means a 1M-email ingest performs ~4M round-trips in the parse phase. _link_attachments (parse.py:134-146) similarly executes up to 3 statements per email-attachment link.

**Fix:** Insert optimistically in batches (executemany or COPY into a temp/staging table with `INSERT ... SELECT ... ON CONFLICT`), and only fall back to the existing per-row savepoint path for a batch that raises. This keeps one-bad-row isolation while making the common all-good case 1-2 round-trips per batch.

**Verification:** The per-row savepoint/INSERT/account-INSERT/RELEASE pattern exists exactly as claimed (parse.py:221-254, plus _link_attachments at 133-146), and batched-with-fallback would be faster. But against a local-socket PostgreSQL with parallel parse workers, an occasional-bulk-ingest workload, and a corpus far below 1M emails, the round-trip overhead is minutes at worst and not the parse phase bottleneck — a valid optimization, low severity here.

#### [performance] topics_with/cluster do farthest-point selection in pure Python over 500 string-parsed 768-dim vectors
*src/maildb/maildb.py:645* — verifier confidence: high

topics_with() and cluster() fetch up to 500 rows including embeddings (parsed from pgvector text into Python lists — 384,000 float() calls), then _farthest_point_select (maildb.py:645-670) computes _cosine_distance (maildb.py:719-727) as a pure-Python generator expression `sum(x * y for x, y in zip(a, b, strict=True))`. For limit=5 that is roughly 5 x 500 x 768 ≈ 2M Python-level multiply-adds plus repeated norm computations (norms are recomputed inside every distance call instead of cached). Each MCP cluster/topics_with call burns 1-3 seconds of CPU in the server before serialization.

**Fix:** Either compute distances in SQL (greedy selection with `embedding <=> ...` per iteration, ~limit queries using the HNSW/exact operators), or pull vectors as numpy arrays once (np.fromstring on the pgvector text), pre-normalize, and do the farthest-point loop with matrix ops. Also stop fetching body_html for these 500-row pulls (see SELECT_COLS finding).

**Verification:** Code matches the claim exactly (pure-Python FPS over 500 string-parsed 768-dim vectors, norms recomputed per distance call, body_html fetched, uncapped limit+offset in server.py), but a direct benchmark of the actual algorithm shows ~0.33s at the default limit=5 — not the claimed 1-3s — which for a local single-user MCP tool is a minor inefficiency, only reaching seconds with large limit/offset paging.

#### [performance] init_db on every MCP server startup runs full-table backfill scans
*src/maildb/db.py:33* — verifier confidence: high

server.py app_lifespan calls db.init_db() on every server start (server.py:171-173), and init_db (db.py:21-70) unconditionally runs: a reference_count recount UPDATE joining a GROUP BY over all of email_attachments, an `INSERT INTO email_accounts ... SELECT id, source_account, import_id FROM emails ... ON CONFLICT DO NOTHING` that scans all 1M email rows and probes the email_accounts PK for each, and a `SELECT count(*) FROM emails WHERE source_account IS NULL` seq scan. These are one-time migrations being paid on every MCP server launch — tens of seconds of startup at 1M emails.

**Fix:** Guard the backfills with a cheap existence check (e.g. `SELECT 1 FROM emails WHERE source_account IS NULL LIMIT 1`, and skip the email_accounts mirror when `(SELECT count(*) FROM email_accounts) > 0` matches expectations), or move them behind an explicit `maildb ingest migrate` step / schema-version flag instead of running on every startup.

**Verification:** init_db does run unconditionally on every MCP startup (server.py:172) and the email_accounts mirror INSERT (db.py:49-54) really rescans all 1.28M emails each launch — measured 2.88 s on the live DB. But the other two claimed costs are refuted: the reference_count recount takes 0.03 s (only 56k email_attachments rows) and the NULL count uses idx_email_source_account (0.00 s), so the real impact is ~3 s per startup, not tens of seconds — low severity for a once-per-session local server.

#### [performance] log_tool re-serializes every tool response to JSON solely to log its size
*src/maildb/server.py:51* — verifier confidence: high

The @log_tool decorator runs `response_bytes = len(json.dumps(result, default=str).encode())` (server.py:51) on every tool call, even when the resulting log line is debug-level and dropped. For large responses (correspondence with limit=500, get_emails with full bodies, get_attachment_markdown returning multi-MB markdown) this doubles serialization work — the MCP framework will JSON-encode the same payload again immediately after.

**Fix:** Only compute the size when the relevant log level is enabled, or use a cheap proxy (row count, or len() of the few known-large string fields) instead of a full json.dumps of the response.

**Verification:** server.py:51 does unconditionally json.dumps every tool response in log_tool (wrapping all 18 tools) with no log-level guard, duplicating FastMCP's own serialization — the claim is accurate, though the size also feeds the 50KB warning check (not solely the debug line), and for a local tool the cost is milliseconds, so 'low' is the right severity.

#### [performance] Parse worker materializes an entire 50MB chunk, including all decoded attachment bytes, in memory
*src/maildb/ingest/parse.py:159* — verifier confidence: high

_process_single_chunk does `messages = list(parse_mbox(chunk_path))` (parse.py:159) where each parsed dict carries `_attachments_with_data` with the full decoded attachment bytes (parsing.py:239). Attachments are written to disk inside the loop, but the `messages` list keeps every byte buffer alive until the function returns, so peak RSS per worker is the whole decoded chunk (>50MB, larger after base64 decode), multiplied by parse_workers (defaults to cpu_count-1, orchestrator.py:142).

**Fix:** Iterate parse_mbox lazily: build email_rows/attachment_meta per message, store the attachment to disk, then `del msg["_attachments_with_data"]` (or pop the data field) before appending, so attachment payloads are released as the loop advances.

**Verification:** Confirmed: parse.py:159 eagerly lists the parse_mbox generator, each dict retains full decoded attachment bytes in _attachments_with_data (parsing.py:137-146, 239), and nothing releases them after disk write, so peak RSS per worker is the whole decoded chunk times cpu_count-1 workers (orchestrator.py:142). It is a genuine but minor inefficiency on a local tool with 50MB chunk caps, so low/performance is the correct severity.

#### [quality] CLI embeds raw SQL that duplicates MailDB.import_history
*src/maildb/cli.py:295* — verifier confidence: high

`_print_imports_summary` (cli.py:293-315) runs its own `SELECT started_at, source_account, status, messages_inserted, messages_skipped FROM imports ... ORDER BY started_at DESC LIMIT 20` while the library already exposes exactly this via `MailDB.import_history` (maildb.py:571-606), which the MCP `import_history` tool uses. Two query paths over the same table will drift (the CLI version already lacks the columns and account semantics of the library one, and hardcodes LIMIT 20). `process_status` (cli.py:561-610) similarly embeds three analytic queries in the CLI handler.

**Fix:** Have `_print_imports_summary` call `MailDB.import_history(account=..., limit=20)` (or a thin module-level function in the library that both CLI and server share), and move the process_status aggregate queries into src/maildb/ingest/process_attachments.py as a `status_summary(pool)` function the CLI just formats.

**Verification:** The duplication exists as described (cli.py:293-316 vs maildb.py:571-606), but the CLI intentionally operates at the pool level and never instantiates MailDB (whose constructor builds its own pool and EmbeddingClient), so the proposed fix is more invasive than claimed; the "lacks account semantics" claim is inaccurate (CLI filters by source_account identically) and process_status duplicates no existing library function. A valid DRY nit, not a medium issue.

#### [quality] Dead config: Settings.extract_timeout_s is never read
*src/maildb/config.py:32* — verifier confidence: high

`Settings.extract_timeout_s: int = 300` (config.py:31-32) is never referenced anywhere: the CLI hardcodes `extract_timeout: int = typer.Option(300, ...)` in both `process_run` (cli.py:461) and `process_retry` (cli.py:616) and passes that through, and process_attachments.py only uses its own function parameters (default 0). Setting `MAILDB_EXTRACT_TIMEOUT_S=600` in the environment silently does nothing, which violates the expectation a settings field creates.

**Fix:** Either wire it in — `extract_timeout: int | None = typer.Option(None, ...)` with fallback to `Settings().extract_timeout_s` — or delete the field from Settings.

**Verification:** A repo-wide grep confirms Settings.extract_timeout_s (config.py:32) is never read anywhere — cli.py hardcodes 300 in typer.Option for both process_run and process_retry and passes that CLI value through, so MAILDB_EXTRACT_TIMEOUT_S silently does nothing. Severity low/quality is accurate since defaults coincidentally match and behavior is otherwise correct.

#### [performance] _farthest_point_select does O(n*k*d) pure-Python vector math over 768-dim embeddings
*src/maildb/maildb.py:646* — verifier confidence: high

`topics_with`/`cluster` fetch up to 500 rows including the embedding column, parse each 768-dim vector from a pgvector string via float() splitting (`_parse_embedding`, models.py:25-34: ~384k float parses), then `_farthest_point_select` (maildb.py:645-670) computes cosine distance with pure-Python generator sums (maildb.py:719-727) — for limit=5 that's roughly 500*5*5*768 ≈ 10M multiply-adds per call, plus re-computing norms of the same vectors on every comparison. These are interactive MCP tools; multi-second latency per call is avoidable.

**Fix:** Register pgvector's psycopg adapter (or use numpy): load embeddings into one (n,768) ndarray, pre-normalize once, and do farthest-point selection with matrix ops (`dists = 1 - cand @ sel.T`). Cuts the call to milliseconds with ~15 lines.

**Verification:** The code is exactly as described (pure-Python O(n*k*d) cosine with recomputed norms over 500 parsed 768-dim vectors, maildb.py:645-727 and models.py:25-34), but a direct benchmark of the algorithm shows ~0.33s per call at default limit=5, not multi-second — a real but minor latency cost for a local interactive tool, so severity is low rather than medium.

#### [design] correspondence and topics_with lack the `account` parameter every other email tool has
*src/maildb/server.py:506* — verifier confidence: high

The server establishes a uniform convention — find, search, mention_search, unreplied, top_contacts, long_threads, search_attachments, search_all, even get_attachment_markdown all take `account` ("limit results to this source account") — but `correspondence` (server.py:504-536, library at maildb.py:885-930) and `topics_with` (server.py:415-446) do not, and neither do get_thread/get_thread_for. In a multi-account database an agent that learned the account convention from `accounts()` cannot scope these tools, and results silently mix accounts.

**Fix:** Add `account: str | None = None` to correspondence and topics_with (library + server), reusing the email_accounts EXISTS clause already in `_build_filters` (maildb.py:206-211).

**Verification:** Verified in code: correspondence (server.py:506-536, maildb.py:885-930) and topics_with (server.py:417-446, maildb.py:608-643) lack the account parameter that all nine other email tools expose with identical docs, and neither library method applies the email_accounts EXISTS clause available in _build_filters (maildb.py:206-211); the accounts() tool explicitly tells agents to scope queries with account, so the inconsistency is real. Low/design severity is appropriate since unscoped results are still correct, just unfilterable.

#### [correctness] _effective_user_emails cache never invalidates in the long-running MCP server
*src/maildb/maildb.py:386* — verifier confidence: high

`_effective_user_emails` caches the merged config + `SELECT DISTINCT source_account FROM imports` list in `self._effective_user_emails_cache` (maildb.py:375-387) for the lifetime of the MailDB instance. The MCP server creates one MailDB at startup and keeps it for the whole session (server.py:168-176), so an mbox ingested for a new account while the server is running is invisible to identity-aware tools (`unreplied`, `top_contacts` treat the new account's address as "not you") until the server restarts — a confusing, hard-to-diagnose wrong answer.

**Fix:** Drop the cache (the DISTINCT query on the small imports table is cheap) or add a short TTL (e.g. re-query if older than 60s).

**Verification:** Confirmed: the cache at maildb.py:386 is never invalidated, server.py:168-176 holds one MailDB for the whole MCP session, and ingestion runs in a separate CLI process, so a newly ingested account's address is invisible to identity-aware tools until server restart. Scenario is narrow (new account address not in configured user_emails, ingested mid-session after cache population), so low severity is correct.

#### [quality] Coverage gate (fail_under=80) is configured but never enforced; no CI exists
*pyproject.toml:137* — verifier confidence: high

pyproject.toml sets `[tool.coverage.report] fail_under = 80`, but the `just check` recipe (justfile line 26: `check: fmt lint test`) runs plain `uv run pytest` without `--cov`. Coverage is only exercised by the optional `just test-cov` target. There is also no CI at all (no .github/, .gitlab-ci.yml, or equivalent), so the only quality gate is the developer remembering to run `just check` locally (per CLAUDE.md). The 80% threshold is effectively dead configuration.

**Fix:** Either add `--cov --cov-fail-under=80` to the `test` recipe used by `check` (or make `check` depend on `test-cov`), or add a minimal GitHub Actions workflow that runs `just lint` + unit tests (with a Postgres service container for integration tests). If neither is wanted, delete the fail_under setting so the config reflects reality.

**Verification:** Verified: pyproject.toml:137 sets fail_under=80 but `just check` runs plain `uv run pytest` (no --cov, no coverage addopts), coverage only runs via the optional `just test-cov`, and there is no .github/, pre-commit, or other CI/hook enforcement. The config-vs-reality mismatch is real, but for a single-developer local tool with intentionally no CI it is minor housekeeping, so severity should be low rather than medium.

#### [quality] ~15.5 MB of ad-hoc operational logs accumulating in the repo root
*drain-A.log* — verifier confidence: high

The repo root contains drain-A.log (12.7 MB), reembed.log (2.3 MB), retry-drain.log (512 KB), process_attachments.log, drain.log, drain-B.log, and retry-surya-drain.log. They are covered by the `*.log` pattern in .gitignore (line 24), so nothing is committed, but they are stale one-off drain artifacts cluttering the working tree — and the project now has a purpose-built persistent run-log facility (src/maildb/ingest/run_logs.py, shipped in PR #81) that makes root-level redirect logs obsolete.

**Fix:** Delete the root-level *.log files (or archive the ones with forensic value next to the retrospectives) and route future ad-hoc drain runs through the run_logs directory introduced in #81 instead of shell redirects into the repo root.

**Verification:** All seven claimed log files exist in the repo root (drain-A.log 12.7MB, reembed.log 2.3MB, etc., ~15.5MB total), are gitignored by the *.log pattern at .gitignore:25, and src/maildb/ingest/run_logs.py exists (shipped in commit b13c6e5, PR #81), so they are stale redundant artifacts. Low severity is appropriate since nothing is committed and the impact is only working-tree clutter.

#### [quality] Unreferenced one-off spike scripts left in scripts/
*scripts/spike_markitdown.py:1* — verifier confidence: high

scripts/spike_markitdown.py (a read-only spike evaluating MarkItDown on skipped attachments) and scripts/test_marker_with_patch.py are referenced nowhere — not in the justfile, README, or docs/ (verified by grep). The MarkItDown spike already concluded and shipped as the Tier-4 extraction leg (PR #84), so the spike script is a completed experiment. By contrast, scripts/surya_mps_patch.py and scripts/smoke_marker.py are wired into justfile targets (`patch-surya`, `smoke-marker`) and earn their keep. test_marker_with_patch.py additionally has a pytest-collectable `test_*.py` name (only saved from collection by `testpaths = ["tests"]`).

**Fix:** Delete scripts/spike_markitdown.py and scripts/test_marker_with_patch.py (their conclusions live in the retrospectives/PRs), or if they must stay, rename test_marker_with_patch.py to avoid the pytest naming pattern and add a one-line note in each pointing to the runbook that supersedes them.

**Verification:** Both scripts exist as completed one-off experiments (the MarkItDown spike's work shipped in PR #84/commit e70efa3) and grep confirms zero references in justfile/README/docs, unlike the justfile-wired surya_mps_patch.py and smoke_marker.py; test_marker_with_patch.py's pytest-collectable name is only saved by testpaths=["tests"] in pyproject.toml. Low severity is appropriate for unreferenced spike scripts in a personal tool.

#### [quality] .env.example is missing settings that config.py supports, contradicting README's 'full list' claim
*.env.example:23* — verifier confidence: high

src/maildb/config.py defines `extract_timeout_s` (line 32), `debug_log` (line 35), `debug_log_level` (line 36), and `debug_log_max_bytes` (line 37), but .env.example contains no MAILDB_EXTRACT_TIMEOUT_S or MAILDB_DEBUG_LOG* entries (verified by grep). README.md says "See `.env.example` for the full list", which is now inaccurate — notably extract_timeout_s is an operationally important knob for the attachment drain.

**Fix:** Add commented entries for MAILDB_EXTRACT_TIMEOUT_S, MAILDB_DEBUG_LOG, MAILDB_DEBUG_LOG_LEVEL, and MAILDB_DEBUG_LOG_MAX_BYTES (with their defaults) to .env.example.

**Verification:** config.py (lines 32-37) defines extract_timeout_s, debug_log, debug_log_level, and debug_log_max_bytes, none of which appear in .env.example, while README.md line 83 claims .env.example shows "the full list" of MAILDB_ settings. The finding stands exactly as described; low severity is correct for a docs gap.

#### [correctness] Integration DB cleanup runs only after each test, so a previously aborted run leaves dirty state
*tests/conftest.py:55* — verifier confidence: high

`_clean_emails` (conftest.py:55-70) deletes all rows in teardown only (`yield` first, DELETEs after). If a prior pytest run was killed hard (SIGKILL, machine crash, OOM during a Marker extraction in test_process_attachments_e2e), the next run's first integration tests start against leftover rows — e.g. test_dsl.py aggregation counts and test_maildb.py `total` assertions would fail mysteriously. The delete order and cascade handling are otherwise correct (attachment_contents/attachment_chunks cascade from the attachments delete per schema_tables.sql:93,111).

**Fix:** Run the same DELETE block before the test as well as after (cheap on an empty DB), or switch to a single `TRUNCATE email_attachments, attachments, ingest_tasks, email_accounts, emails, imports CASCADE` executed in setup. This makes the suite self-healing after an aborted run.

**Verification:** Confirmed: conftest.py:55-70 cleans only after yield with no setup-side cleanup anywhere, seeds commit mid-run (so a SIGKILL/OOM — plausible given test_process_attachments_e2e runs real Marker — leaves committed rows), and unscoped assertions like test_maildb.py:177 `assert total == 3` would then fail. Severity low is right since the dirty state self-heals after the first failed test's teardown.

#### [quality] Integration tests error rather than skip when the test database is unreachable
*tests/conftest.py:28* — verifier confidence: high

The session-scoped `test_pool` fixture (conftest.py:28-34) calls `create_pool` + `init_db` with no reachability check, and `just test`/`just check` run the full testpaths including tests/integration/. On a machine without the `maildb_test` database, all ~180 integration tests produce fixture ERRORs (one connection-failure traceback each) instead of clean skips, drowning out the 358 useful unit-test results and making `just check` (the mandated pre-commit gate) unusable away from the primary dev box.

**Fix:** In `test_pool` (or a pytest_collection_modifyitems hook), attempt one connection and call `pytest.skip("PostgreSQL test database unavailable", allow_module_level=True)` / mark all `integration`-marked items as skipped when it fails, so unit tests still gate commits cleanly.

**Verification:** Verified: test_pool (tests/conftest.py:28-34) has no reachability check or skip path, no pytest_collection_modifyitems/pytest.skip exists anywhere in tests/, justfile's check target runs full pytest over testpaths=["tests"], and exactly 181 integration tests depend on the fixture — so an unreachable maildb_test DB produces 181 fixture ERRORs and a failing `just check` instead of clean skips. Low/quality severity is correct for this single-dev personal project.

#### [quality] Untracked retrospective doc sitting in the working tree
*docs/retrospectives/attachment-drain-retrospective.md* — verifier confidence: high

git status shows `?? docs/retrospectives/attachment-drain-retrospective.md` — the attachment-drain retrospective was written but never committed, while its siblings (attachment-residuals-2026-05-03.md, first-import-retrospective.md) are tracked. Untracked docs are easy to lose and invisible to anyone reading the repo history of the drain work it documents (PRs #80-#86).

**Fix:** Commit the retrospective alongside the other docs/retrospectives/ files (or delete it if it was superseded).

**Verification:** Verified directly: git status shows the file as untracked (??), git check-ignore confirms it is not gitignored, and git ls-files confirms its two sibling retrospectives in docs/retrospectives/ are tracked. Low severity is correct for an uncommitted doc.

---

## Technical design assessment

# MailDB Technical Design Assessment

Based on full reads of `docs/DESIGN.md`, `src/maildb/maildb.py`, `src/maildb/dsl.py`, `src/maildb/server.py`, `src/maildb/ingest/orchestrator.py`, `src/maildb/ingest/{tasks,parse,embed,index}.py`, `src/maildb/db.py`, `src/maildb/embeddings.py`, `src/maildb/jobs.py`, and both schema files.

---

## 1. Load-bearing decisions, and which are worth changing

### Decision A: One denormalized `emails` row, globally-unique `message_id`, `email_accounts` join for attribution — **keep, this is right**

The schema (`schema_tables.sql`) stores each message once, dedupes on `message_id`, and records per-account attribution in `email_accounts` with `ON CONFLICT DO NOTHING` (`parse.py:32-36`). The `ON CONFLICT (message_id) DO UPDATE SET thread_id = emails.thread_id RETURNING id` trick (`parse.py:28-29`) to detect insert-vs-conflict while always getting the id back is clever and correct. The `EXISTS` pattern for account scoping (`maildb.py:206-211`) is the right query shape. The tradeoff analysis (bodies/embeddings stored once) is sound for a personal archive where the same message appears in multiple Takeout exports. Don't touch this.

### Decision B: The JSON DSL (Tier 2) — **questionable; it's becoming a SQL reimplementation**

`dsl.py` is 546 lines and already shows the strain of re-deriving SQL semantics:

- `_expand_having_aliases` (`dsl.py:324-337`) recursively rewrites alias references into underlying expressions because PostgreSQL requires it — that's the parser learning PostgreSQL's name-resolution rules one bug at a time.
- `_resolve_group_by` (`dsl.py:283-305`) carries a parallel `alias_exprs` map for the same reason.
- Virtual sources (`sent_to`, `email_labels`, `emails_by_account`) are hand-maintained CTE templates plus hand-maintained column whitelists that must be kept in sync (`dsl.py:35-60`, `134-165`).

Every new ask from an agent ("window functions", "join to attachments", "EXTRACT(dow)") means another whitelist, another alias-tracking edge case. Meanwhile this is a **single-user, local, read-mostly** database — the threat model is "the LLM writes a destructive or runaway query", which Postgres itself solves: a read-only role (`GRANT SELECT` on a whitelist of tables/views, no write perms) plus the `statement_timeout`/row-cap you already enforce in `MailDB.query()` (`maildb.py:1242-1248`).

**What to change to:** add a `sql` tool backed by a dedicated read-only Postgres role and the same 5s timeout + 1000-row cap, exposing the three virtual sources as actual SQL views. Freeze the DSL (keep it for compat, stop extending it). Frontier models write better SQL than any bespoke DSL, and the views give you the same column-shaping the CTE templates do today.

**Migration cost:** low. ~30 lines for the role/views, one new MCP tool; DSL stays untouched. The main cost is deciding which columns the read-only role can see (e.g. hide `body_html`, `embedding`).

### Decision C: Thread identity stamped at ingest from `References[0]` — **questionable, and DESIGN.md misdescribes it**

DESIGN.md §4 says "Threads are reconstructed at query time." They aren't — `thread_id` is derived once at parse time (`parsing.py:83-88`):

```python
def _derive_thread_id(message_id, references, in_reply_to):
    if references:
        return references[0]
    if in_reply_to:
        return in_reply_to
    return message_id
```

This fragments threads whenever a client sends `In-Reply-To` but no `References` (common for some mobile/enterprise clients): message C replying to B gets `thread_id = B`, while B itself has `thread_id = A` — the thread silently splits. Since the value is baked into the row, `get_thread()`/`unreplied()`/`long_threads()` all inherit the fragmentation, and `unreplied()` will report false positives ("never replied") for exactly these threads.

**What to change to:** a periodic union-find pass (transitive closure over `message_id` ∪ `references` ∪ `in_reply_to`) that rewrites `thread_id` to the connected-component root — runnable as a phase after parse and idempotent. Also fix the DESIGN.md sentence either way.

**Migration cost:** moderate — one maintenance function plus a re-run over existing rows (a single `UPDATE ... FROM` after computing components; minutes at 841K rows). No schema change.

### Decision D: Embedding stored inline as a column on `emails`, zero-vector as "skip" sentinel — **worth changing in two ways**

1. **`SELECT_COLS` includes `embedding`** (`maildb.py:41-46`), so every Tier 1 method drags a 768-float vector per row to the client, and the MCP layer immediately discards it (`server.py:120 d.pop("embedding")`). `get_thread()` on a 100-message thread moves ~600KB of vector text for nothing; `topics_with`/`cluster` pull 500 full rows. Cheap fix: drop `embedding` from `SELECT_COLS` and select it explicitly only in `topics_with`/`cluster` which actually use it.
2. **The zero-vector sentinel** (`embed.py:25-26`, `63-77`) is an in-band magic value that forces `vector_norm(embedding) > 0` guards into every semantic query (`maildb.py:319`, `1104`) and triples the status-counting queries in `get_status` (`orchestrator.py:372-381`). A nullable `embedding_status` column (or just `embedded_at` + `embed_skipped` boolean) is one ALTER and removes a permanent gotcha — anyone who writes a new vector query and forgets the norm guard gets garbage neighbors silently.

Keeping the embedding **on the row** (vs. a side table) is fine at one vector per message; the swappable-model decision and token-aware truncation are good.

### Decision E: 4-phase orchestrator with drop-indexes-before-parse — **right for the first bulk load, wrong as a steady-state design**

The phase pipeline with `ingest_tasks` + `SKIP LOCKED` claims is a good shape, and `import_id` scoping (`tasks.py:128-160`, recently fixed in #86) is correct. Two real problems:

1. **Index drop is unconditional for any pending parse work** (`orchestrator.py:211-213` → `index.py:29-35`), and `DROP_INDEXES` includes `idx_email_embedding` (the HNSW index, `index.py:22`). Ingesting a second small mbox into the existing 841K-row database drops every query index — including the 12-hours-of-embedding-derived HNSW index — and rebuilds them all, while a concurrently-running MCP server degrades to sequential scans. This actively fights the planned Gmail incremental sync (DESIGN §7). Fix: gate the drop on import size (e.g. only when `emails` is empty or chunk count exceeds a threshold).
2. **The embed "queue" doesn't actually hold its claims.** `_fetch_batch` (`embed.py:28-35`) takes `FOR UPDATE SKIP LOCKED` and then immediately `conn.rollback()`s "to release locks". Rows stay `embedding IS NULL` for the seconds-long Ollama round-trip, and `SELECT ... WHERE embedding IS NULL LIMIT n` has no ordering or claim marker — so concurrent workers can and will fetch the *same* batch and re-embed it. It's not a correctness bug (last write wins with the same value) but it silently wastes a fraction of the dominant 12-hour phase, and it contradicts DESIGN §4's claim that "parse and embed workers claim tasks with SKIP LOCKED." Fix: hold the transaction open for the batch, or add a `claimed_by/claimed_at` marker like `attachment_contents` already has.

Related smell: there are now **two parallel work-queue idioms** — `ingest_tasks` (status + worker_id + retry_count) and `attachment_contents` (status + `claimed_by` + watchdog reclaim of stale `extracting` rows, `process_attachments.py:93-107`). The attachment one is the more battle-hardened design (it survived the orphan-supervisor incident); converging on it would simplify the mental model, but this is a "next time you touch it" change, not urgent.

---

## 2. Architectural debt — where it hurts first as the archive grows

Ordered by when it bites:

1. **`COUNT(*) OVER() AS _total` on every list query** (`maildb.py:271`, `326`, `809`, `924`, `985`). The window count forces full evaluation of the WHERE set before LIMIT. For `find()` with loose filters at ~1M rows, every page-1 request pays a full count. Worse, in `search()` (`maildb.py:323-331`) the window aggregate sits on top of the HNSW-ordered scan — a window function over all qualifying rows is in tension with HNSW's early-termination, so the "total" is either misleading (bounded by ef_search candidates) or expensive; either way it's not the exact total the pagination envelope implies. Worth EXPLAINing on the real corpus; the cheap fix is making `total` optional or estimated.
2. **`mention_search()` is an unindexed `ILIKE '%...%'` over `body_text` and `subject`** (`maildb.py:951-954`) — there's no trigram or tsvector index in `schema_indexes.sql`, so it's a sequential scan over the whole corpus today, ~49GB of body text. A `pg_trgm` GIN index (or proper FTS column) is the single highest-leverage index addition available.
3. **`init_db()` runs heavyweight reconciliation on every MCP server start.** `app_lifespan` calls `db.init_db()` (`server.py:171-173`), which re-executes the full DDL, the `attachments.reference_count` backfill UPDATE-from-aggregate, the `email_accounts` mirror INSERT over all emails, and a NULL-count probe with a conditional `ALTER TABLE ... SET NOT NULL` (`db.py:27-69`). At 841K emails this is real startup latency and write churn on every server launch, and it grows linearly. Schema management generally is "idempotent DDL palimpsest" — `ALTER TABLE ADD COLUMN IF NOT EXISTS` lines accreting inside `schema_tables.sql`. With Gmail sync coming (more migrations guaranteed), adopt a trivial numbered-migrations table now while there are only ~4 migrations to renumber; cost is an afternoon.
4. **`unreplied()` is an unbounded anti-join.** The inbound variant (`maildb.py:808-820`) evaluates `NOT EXISTS` per row over *all* messages not from you (no default `after`), plus the window count. At 1M rows this is the first Tier 1 method to cross from "fast" to "seconds". A default time window or a materialized last-reply-per-thread helper would fix it.
5. **`search_all()` merges email and attachment hits by raw cosine similarity** (`maildb.py:1232`). Whole-message embeddings and small-chunk embeddings have different similarity distributions — chunks systematically score higher, so attachments will dominate merged results as the chunk corpus grows. Reciprocal rank fusion is a ~10-line replacement and rank-stable.
6. **`topics_with`/`cluster` do farthest-point selection in pure Python** (`maildb.py:645-670`) — O(n·k·768) float math per call plus shipping 500 full rows. Acceptable today; cap is the only thing saving it. If clustering becomes a real feature, push the distance math into SQL (pgvector operators) or numpy.

## 3. Genuinely well-designed — preserve

- **The content-addressed attachment subsystem.** sha256-keyed dedupe, `reference_count`, the `attachment_contents` status machine (`pending/extracting/extracted/failed/skipped` with `claimed_by` and watchdog reclaim), and chunked embeddings carrying `heading_path`/`page_number` provenance (`schema_tables.sql`, `process_attachments.py`). This is the most mature part of the codebase and visibly hardened by real incidents.
- **Savepoint-per-row inserts in parse** (`parse.py:220-254`) — one malformed message can't kill a 50MB chunk, and the inserted/skipped/errored accounting flows back into task stats. Exactly right for messy mbox data.
- **The MCP serialization discipline** (`server.py:101-135`): bodies excluded by default in favor of `body_length`, explicit field selection, `body_max_chars` truncation flags, pagination envelopes, and the 50KB response-size warning in `log_tool`. This is token-budget-aware tool design that most MCP servers lack.
- **Parameterization discipline.** Every dynamic value in both Tier 1 and the DSL goes through bound params; identifiers only ever come from whitelists with regex-validated aliases (`dsl.py:121-127`). Whatever happens to the DSL's future, keep this posture.
- **Operational observability born from incidents**: `jobs.py`'s orphan-worker detection with process-group kill (`jobs.py:183-239`), per-content-type yield tables, throughput/ETA windows. Unglamorous and exactly what a 12-hour pipeline needs.
- **Resumable imports keyed on `(source_account, source_file)`** (`orchestrator.py:34-72`) — adopting the running row instead of minting UUIDs per crash is a small decision that keeps `imports` meaningful as history.

## Summary verdict

The storage model (A) and the attachment subsystem are sound and should anchor everything else. The three changes I'd actually schedule: (1) gate the index-drop on bulk-vs-incremental and fix the embed claim race before Gmail sync lands, since sync makes both problems chronic; (2) thread-id union-find, because it silently corrupts `unreplied()` answers today; (3) stop extending the DSL and ship a read-only-role SQL tool. The cheap wins (drop `embedding` from `SELECT_COLS`, pg_trgm index for `mention_search`, replace the zero-vector sentinel) are each under a day and pay forever.

---

## Product assessment

# MailDB Product Assessment

## 1. Who it's for and the job-to-be-done

This is a single-owner product (DESIGN.md §2 is explicit: "Single-user. Multi-user would be a different product") for someone with a large email archive — the design doc benchmarks against the owner's own 841K-message, 49 GB Gmail Takeout. The core job: **"let an LLM answer questions about my email history"** — retrieval ("find that contract PDF"), relationship analysis (`top_contacts`, `correspondence`, `unreplied`), and topic exploration (`search`, `cluster`, `topics_with`). The secondary job is owner-as-operator via CLI: import, monitor, repair.

The surface serves the job well overall. The headers-first/bodies-on-demand contract (`DEFAULT_LIST_FIELDS` excludes `body_text`, server.py:102; `get_emails` is the body-fetch tool) is a genuinely good design for context-limited LLM consumers, and the shipped `using-maildb` skill (skills/using-maildb/SKILL.md) plus the pagination wrapper (`{total, offset, limit, results}`) show real attention to the actual consumer. The biggest gap is that the product is read-only and snapshot-based: with no Gmail sync (DESIGN.md §7, "planned"), the archive goes stale the day after import, which undercuts the most natural recurring uses ("what did I not reply to *this week*").

## 2. The MCP tool surface (18 tools)

**Tools an LLM would actually reach for:** `search`, `find`, `get_emails`, `get_thread_for`, `correspondence`, `mention_search`, `search_all`, `accounts`. That's a strong core eight. `query` (DSL) is the power escape hatch for aggregations and is well-documented inline (server.py:672–689).

**Overlap and confusion:**

- **Three semantic search entry points** — `search`, `search_attachments`, `search_all` — with near-identical 15-parameter signatures. `search_all` returns tagged `source="email"|"attachment"` payloads (server.py:835–842) and could plausibly subsume the other two with a `scope` parameter. Worse, the two newest tools have the thinnest docstrings: `search_attachments` documents zero parameters (server.py:767–769) and `search_all` likewise, while `find`/`search` document every parameter. An LLM choosing by description will under-use exactly the tools that unlock the attachment corpus.
- **`topics_with` vs `cluster`**: `topics_with(sender=...)` is strictly a special case of `cluster(where={"field": "sender_address", "eq": ...})` — both are farthest-point diversity selection. Two tools for one concept; `topics_with` also lacks the `account` parameter every sibling has (server.py:417–423).
- **`get_thread` vs `get_thread_for`**: `get_thread_for(message_id)` subsumes `get_thread(thread_id)` for an LLM, since list results already carry `thread_id` *and* `message_id`. One tool would do.
- **Inconsistent return shapes**: `get_thread`, `get_thread_for`, `query`, `accounts`, `import_history` return bare lists; everything else returns the pagination wrapper. The skill has to carve out exceptions (SKILL.md:83), which is a tax on every consumer.
- **A real docstring bug**: `get_thread`, `get_thread_for`, `topics_with`, and `cluster` all claim `fields: ... (default: all)` (e.g. server.py:339), but `_serialize_email(e, None)` falls through to `DEFAULT_LIST_FIELDS`, which excludes `body_text` (server.py:130–134). An LLM that calls `get_thread` expecting to read the conversation gets headers only and must guess at the `fields` override. Related: `frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS` (server.py:260) means a typo like `fields=["body"]` silently yields an empty frozenset → every result serialized as `{}`, with no error.
- **Skill drift**: SKILL.md:112 claims recipient-count filters are "Available on find, search, mention_search, unreplied, **correspondence**" — `correspondence` has none of them (server.py:506–514, maildb.py:885–894). SKILL.md:223 still says "Set via `MAILDB_USER_EMAIL`" when the canonical var is now `MAILDB_USER_EMAILS`. The skill also never mentions `search_attachments`, `search_all`, `get_attachment_markdown`, `accounts`, or `import_history` — a third of the tool surface is invisible to its primary teaching document.

**What's missing:**

- **Person disambiguation.** People have multiple addresses (old work, new work, gmail) and the LLM has no tool to resolve "Bob" → addresses. `top_contacts` groups by exact address; `correspondence` takes exactly one address; `find(sender=...)` is "exact email address". A `contacts(name_or_fragment)` tool returning `{name variants, addresses, counts, date ranges}` would be the single highest-leverage addition — nearly every human query starts from a name, not an address.
- **Activity overview / date-shape affordances.** There's no cheap "what does my mail look like over time" call (counts by month/year, per account). The `query` DSL can do it, but a canned `overview()` would be the natural first call in any session, and the DSL's `date_trunc` path is buried in one example.
- **Summarization-friendly thread output.** `get_thread` returns every message; long threads with quoted-reply pyramids will blow context. A digest mode (deduplicated/quote-stripped bodies, or first-N-chars per message like `body_max_chars`, which only `get_emails` supports) is missing.

## 3. Onboarding and operability

**First-time setup is genuinely easy** — README's prerequisites/install/DB-bootstrap path is short, accurate, and `init_db()` handles schema creation implicitly (cli.py:224). `.env.example` matches the Settings class. Two README inaccuracies: (a) `claude skill add /path/...` (README:100) is not a real Claude Code command — skills install via `~/.claude/skills` or plugins, so the documented install path fails verbatim; (b) the server is documented as `uv run python -m maildb` (README:76) while the CLI ships a first-class `maildb serve` — pick one.

**The bigger onboarding hole: attachments.** The README never mentions `process_attachments` at all. A new user runs `ingest run`, connects the MCP server, and three tools (`search_attachments`, `search_all`'s attachment leg, `get_attachment_markdown`) silently return nothing — attachment extraction/embedding is a separate, undocumented `maildb process_attachments run` step with its own substantial flag surface (cli.py:437–474). DESIGN.md §3 even still describes the CLI as shipping only "`serve` and `ingest run|status|reset|migrate`," omitting `jobs` and the whole `process_attachments` subtree.

**Day-2 operations are surprisingly mature but undiscoverable.** `maildb jobs --watch` (throughput, ETA, orphan-killing — cli.py:144), per-run log dirs under `~/.maildb/logs/` (cli.py:106–119), `process_attachments status` with failure-reason rollups, `retry --timeouts-only/--hard-timeouts-only`, and `reembed` for zero-vector repair are exactly the right operator tools — and none appear in the README. What's actually missing for day-2: (a) **email re-embedding** — DESIGN.md calls the model "swappable... requires re-embedding (batch)" but the only path is the blunt `ingest reset --phase embed` + re-run; (b) **incremental updates** — no way to ingest "the new mail since last Takeout" except re-importing a full mbox and relying on dedup (issue #41 acknowledges this); (c) **schema upgrade story** — `init_db` appears to be create-if-missing with no migration framework, fine for one user today, a trap the first time a column changes.

## 4. Top 5 product improvements, in priority order

1. **Fix the MCP contract bugs and skill drift** (wrong `(default: all)` fields docstrings, silent empty-`fields` typo behavior, skill's stale `MAILDB_USER_EMAIL` and incorrect `correspondence` filter claims, missing attachment tools in the skill). The LLM is the primary user and the docstrings/skill *are* the UI; today they actively misdirect it. Cheapest fix, highest leverage.

2. **Add a person/contact resolution tool** (`contacts(query)` → name variants, addresses, message counts, active date range). Nearly every real query starts from a name; today the LLM must improvise with `mention_search` or `top_contacts` paging to find the right address before any relationship tool works.

3. **Document and streamline the attachment pipeline** — put `process_attachments run` in the README's import flow (or fold a default extraction pass into `ingest run`), and give `search_attachments`/`search_all` real parameter docs. Three of eighteen tools are dead-on-arrival for anyone following the current README.

4. **Incremental sync (Gmail API or at minimum cheap mbox-delta re-import + incremental embedding, issues #41/#42).** A stale snapshot caps the product at "archive archaeology"; the recurring jobs (`unreplied` triage, "this week" questions) need fresh data to matter.

5. **Consolidate the tool surface toward ~12 tools with one response shape** — merge `get_thread`/`get_thread_for`, fold `topics_with` into `cluster`, fold `search_attachments` into `search_all(scope=...)`, and wrap the flat-list returners in the standard pagination envelope. Fewer, more regular tools measurably improve LLM tool selection, and the current 18 include three pairs that differ only by entry key.

Key files: `/Users/splaice/Code/maildb/src/maildb/server.py`, `/Users/splaice/Code/maildb/src/maildb/cli.py`, `/Users/splaice/Code/maildb/README.md`, `/Users/splaice/Code/maildb/docs/DESIGN.md`, `/Users/splaice/Code/maildb/skills/using-maildb/SKILL.md`.