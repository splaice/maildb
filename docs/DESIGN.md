# MailDB

**Personal Email Database for Agent-Powered Retrieval**

> High-level design only. For current implementation details — schema columns, indexes, method signatures, DSL operators, pipeline code paths — read the code. This document captures intent, architectural decisions, and things not expressible in code.

---

## 1. What It Is

A local PostgreSQL-backed email database that a user's personal mail gets imported into, then queried by humans and AI agents through a Python library, a JSON DSL, and an MCP server. Structured search (sender, dates, labels, attachments), semantic search (pgvector / nomic-embed-text), and hybrid of both. All execution stays on-device; Ollama runs embeddings locally.

Initial source is local `.mbox` files. Gmail API sync is planned.

## 2. Non-Negotiable Properties

- **Full local execution.** No email content leaves the machine.
- **Multi-account.** One database can hold multiple email accounts; queries scope to one or span all.
- **Sub-second queries** on a corpus up to ~1M messages.
- **Agent-friendly.** Python library and MCP server — both usable by an LLM via tool calls.
- **Single-user.** Multi-user would be a different product.

## 3. Architecture (Four Layers)

| Layer | File(s) | What it does |
|-------|---------|--------------|
| Ingestion | `src/maildb/ingest/` | Mbox → parsed rows → embeddings, 4-phase pipeline with restartability |
| Storage | PostgreSQL + `src/maildb/schema_tables.sql` | Emails, per-account attribution join, import sessions, attachments |
| Query | `src/maildb/maildb.py`, `src/maildb/dsl.py` | Tier 1 fixed methods + Tier 2 JSON DSL |
| MCP server | `src/maildb/server.py` | FastMCP wrapper exposing every query method as a tool |

The CLI (`src/maildb/cli.py`, Typer) ships `serve` (run MCP) and `ingest run|status|reset|migrate`.

## 4. Load-Bearing Design Decisions

**One row per message, not per thread.** Threads are reconstructed at query time using RFC 2822 headers (`References` → `In-Reply-To` → `message_id` fallback). This gives semantic search the precision to match the specific message, not every quoted reply.

**Denormalized `emails` row.** Recipients as JSONB, labels as text array, sender domain extracted at ingest. Avoids joins for the common query shapes.

**`emails.message_id` globally unique + `email_accounts` join for attribution.** The same message ingested under multiple accounts de-duplicates the row (saving storage on bodies, embeddings, attachments) but records attribution once per account. Account-scoped queries use `EXISTS` against the join. See issue #36 for the tradeoff analysis vs. rejected alternatives (composite uniqueness on emails, which would duplicate bodies).

**`emails.source_account` / `import_id` scalars are first-seen only; transitional.** Kept for one release as a compatibility surface; issue #43 tracks their removal.

**Imports are resumable by `(source_account, source_file)` key.** An interrupted-then-retried ingest adopts the existing `status='running'` row instead of minting a new UUID. Prevents the `imports` table from accumulating one-row-per-crash churn. `--force-new-import` is the forensic escape hatch.

**Identity-aware queries auto-derive identities.** `unreplied()` and `top_contacts()` need to know "which addresses are me". We merge `MAILDB_USER_EMAILS` env config with `DISTINCT source_account FROM imports`, so correct-by-default without config.

**Embedding model is swappable.** `nomic-embed-text` (768-dim) chosen for local-first, MTEB quality, Apple Silicon throughput. Swap requires re-embedding (batch) but no schema changes.

**Embedding text is token-aware-truncated.** Binary-search cutoff at 7500 tokens leaves headroom under the 8192-token model context. URL-heavy text is adjusted for higher token density.

**HNSW index on embedding.** Approximate nearest neighbor, cosine distance, built after the embed phase completes.

**Ingest is a 4-phase pipeline backed by `ingest_tasks`.** Split → parse → index → embed. Parse and embed workers claim tasks with `SKIP LOCKED` for parallelism. Indexes are dropped before parse and rebuilt after for bulk-insert performance.

**Dual-sink logging with PII scrubbing.** INFO+ to stderr (MCP-stdio-safe), DEBUG+ to a rotating debug log. All events pass through an email/SSN/CC/phone redactor before either sink.

## 5. Multi-Account Semantics (Summary)

- Ingest `personal@gmail.com` and `work@company.com` into the same database.
- The same email message (you as both sender and recipient) is stored **once** in `emails` but attributed to **both** accounts in `email_accounts`.
- `db.find(account="work@company.com")` surfaces it from either direction. So does the DSL `{"from": "emails_by_account", "where": {"field": "account", "eq": "work@company.com"}}`.
- Threads are account-agnostic — `get_thread()` returns messages from every account that contributed, ordered chronologically.
- `accounts()` returns a per-account summary; `import_history()` returns per-session metadata.

## 6. Performance (Measured, 841,930 messages, 49 GB mbox, M1 Max / 64 GB RAM)

| Phase | Time |
|-------|------|
| Split | ~2 minutes |
| Parse (parallel workers) | ~2 minutes |
| Index rebuild | seconds |
| Embed (4 Ollama workers) | ~12 hours (~20 msg/sec, dominant bottleneck) |

| Query type | Latency |
|-----------|---------|
| Indexed structured (sender/date/thread) | < 10 ms |
| Aggregation (`top_contacts`) | 50–200 ms |
| Semantic (HNSW) | < 100 ms |
| Hybrid | < 150 ms |

Storage: ~8 GB for 841K rows including embeddings and indexes. Multi-account overhead (~80 bytes per `email_accounts` row) is negligible.

## 7. Future Considerations

Listed so the reasoning stays discoverable:

- **Gmail sync.** Incremental sync via Gmail API + OAuth. Same `emails` table, attribution recorded in `email_accounts` like any other import. Will motivate incremental per-message embedding (issue #41) and possibly account auto-detection from Takeout metadata (issue #42).
- **Attachment content indexing.** Extract and embed text from PDFs / documents — a substantial feature, not a gap. Deferred.
- **Multi-user.** Would require `user_id` column + row-level security. Different product; not planned.

## 8. Active Issues

Tracked separately; `gh issue list --state open` for current. As of this writing: #41 (incremental embedding), #42 (account auto-detection), #43 (drop transitional scalar columns once stable).
