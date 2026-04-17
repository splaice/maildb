# MailDB

**Personal Email Database for Agent-Powered Retrieval**

Design Document — Version 3.1 — April 2026

PostgreSQL · pgvector · Ollama · Python · Typer

---

## 1. Executive Summary

MailDB is a personal email database that stores the full contents of a user's email history in PostgreSQL and exposes it to AI agents through a Python library and an MCP server. It combines traditional structured search (by sender, date, labels, attachments) with semantic vector search (by topic, intent, or fuzzy description) using the pgvector extension.

The system supports **multiple email accounts** within a single database. Message bodies, embeddings, and attachments are de-duplicated globally by `message_id`, while per-account attribution is recorded in a separate `email_accounts` join table. The same message ingested under two accounts appears in the `emails` table once (storage efficient) but surfaces under either account when queries scope to `account=...`. Import sessions are tracked in an `imports` table for debugging, resumption, and per-account statistics.

The system is designed to run entirely on local hardware with no external API dependencies. Embeddings are generated locally using an open-source model served by Ollama, ensuring complete privacy of email content.

The initial implementation targets local mailbox (.mbox) import, with Gmail API sync planned as a future phase.

---

## 2. Goals and Requirements

### 2.1 Functional Goals

The system must support five categories of queries, reflecting the natural ways a user or agent would search for emails:

- **Structured lookups:** Find emails by concrete attributes — sender address, domain, date ranges, attachment presence, subject keywords, and folder labels.
- **Semantic search:** Find emails related to a topic, concept, or vague description using natural language. Examples include "complaints about the deployment process" or "discussions about switching CI providers."
- **People and relationships:** Identify frequent correspondents, discover what topics are discussed with a specific contact, and find all participants in a conversation.
- **Summarization and synthesis:** Retrieve relevant emails so an agent can summarize threads, extract action items, or identify commitments. The database provides retrieval; the agent handles reasoning.
- **Temporal and pattern analysis:** Detect unreplied messages, long threads, and time-sensitive communications.

### 2.2 Non-Functional Requirements

- **Full local execution:** No email content leaves the local machine. Embedding inference runs on-device via Ollama.
- **Sub-second query latency:** Both structured and semantic queries should return results in under one second for a corpus of up to 1,000,000 messages across multiple accounts.
- **Agent-friendly Python API:** A clean Python library that an LLM agent can call directly via tool use. Also exposed as an MCP server for integration with AI assistants.
- **Multi-account support:** Multiple email accounts can be imported into the same database. Queries can be scoped to a single account or searched across all accounts. Identity-aware queries (`unreplied`, `top_contacts`) auto-derive the list of user identities from the `imports` table, with env-var overrides for aliases and forwarding addresses.
- **Import traceability:** Every import session is assigned a unique ID. Each message records every import that saw it, enabling debugging, selective re-imports, and per-account aggregation.
- **Incremental extensibility:** The architecture must accommodate Gmail API sync in a future phase without schema changes.

---

## 3. System Architecture

### 3.1 Technology Stack

- **Database:** PostgreSQL 16+ with the pgvector extension for vector similarity search.
- **Embedding model:** `nomic-embed-text` (768 dimensions), served locally via Ollama. Embedding text is truncated via a token-aware heuristic (`estimate_tokens`, URL-adjusted) with binary-search cutoff, targeting 7,500 tokens to leave headroom under the model's 8,192-token context.
- **Python library:** Custom `MailDB` class using psycopg (v3) with `psycopg_pool` for connection pooling, and the `ollama` Python package for embedding generation.
- **MCP server:** FastMCP server exposing all MailDB methods as tools for AI assistant integration. Includes field selection, offset pagination, response size management, and body-length truncation.
- **DSL engine:** JSON-to-SQL translator (`dsl.py`) supporting arbitrary queries with strict column/operator whitelists, parameterized values, and a 5-second statement timeout.
- **Email parsing:** Python's standard library `mailbox` and `email` modules for mbox ingestion, with BeautifulSoup4 for HTML-to-text conversion.
- **CLI:** Typer-based `maildb` console script (`cli.py`) with `serve` and `ingest run|status|reset|migrate` subcommands.
- **Configuration:** `pydantic-settings` for environment-backed `Settings` (prefix `MAILDB_`). Supports `.env` files.
- **Observability:** Dual-sink structured logging via structlog — INFO+ to stderr (safe for MCP stdio transport) and DEBUG+ to a rotating debug log file. A PII scrubbing processor redacts email addresses, SSNs, credit card numbers, and phone numbers from all log output before it reaches either sink.
- **Target hardware:** Tested on Apple M1 Max with 64GB unified memory. Ollama runs embedding inference on the Metal GPU.

### 3.2 Architecture Overview

The system consists of four layers:

- **Ingestion layer:** Reads raw email sources (mbox files initially, Gmail API in Phase 2), parses each message into structured fields, cleans the body text, generates an embedding vector, and upserts the row into PostgreSQL. Deduplication is enforced by the unique constraint on `message_id`; per-account attribution is recorded in the `email_accounts` join table so the same message can be surfaced under every account that ingested it.
- **Storage layer:** A PostgreSQL database containing `emails` (one row per unique message), `email_accounts` (join table: message × account), `imports` (one row per ingest session), `ingest_tasks` (pipeline phase state), `attachments` (content-addressed), and `email_attachments` (many-to-many). B-tree indexes support structured queries, GIN indexes cover JSONB and array columns, and an HNSW index on the vector column supports approximate nearest-neighbor search.
- **Query layer:** The `MailDB` Python class, which translates high-level method calls (`find`, `search`, `get_thread`, `top_contacts`, etc.) into SQL queries. Account-scoped filters use `EXISTS` against `email_accounts`. Semantic search queries are first embedded using the same Ollama model, then compared against stored vectors using cosine distance. A DSL engine supports arbitrary structured queries beyond the fixed method signatures.
- **MCP server layer:** A FastMCP server wraps the `MailDB` class, serializing results to JSON with automatic stripping of `body_html` and `embedding` fields. All email-returning tools support a `fields` parameter for selective field return, an `offset` parameter for pagination, and `get_emails` additionally supports `body_max_chars` for body truncation. A `log_tool` decorator instruments every tool call with entry parameters, exit stats, and response size warnings (>50KB).

### 3.3 Data Flow

The ingestion pipeline processes each email message through the following steps:

1. Parse the message using Python's `mailbox` module to extract raw headers and body parts.
2. Extract structured metadata: sender name, address, and domain; recipient lists (to/cc/bcc); date; subject; message-id; in-reply-to; references; attachment metadata.
3. Derive the `thread_id` from threading headers (see Section 4.3).
4. Clean the body text: convert HTML to plain text, strip quoted reply blocks and email signatures, and produce a focused `body_text` representing only the new content of this message.
5. Generate the embedding by concatenating subject, sender name, and cleaned body text, then passing this string to `nomic-embed-text` via Ollama. The resulting 768-dimensional vector is stored in the `embedding` column.
6. Upsert the row into PostgreSQL using `INSERT ... ON CONFLICT (message_id) DO UPDATE SET thread_id = emails.thread_id RETURNING id`. The no-op update guarantees `RETURNING id` fires on both insert and conflict, so the worker always knows the authoritative `emails.id`.
7. Record per-account attribution in `email_accounts` via `INSERT ... ON CONFLICT (email_id, source_account) DO NOTHING`. A message already in the table gets a new `email_accounts` row for the current ingest's account, making it visible to `account=...` queries under both accounts.

---

## 4. Database Schema

### 4.1 Table: `emails`

One row represents one unique email message, keyed by `message_id`. This intentional denormalization (recipients/attachments as JSONB, labels as a text array) avoids joins for common query patterns and keeps the agent-facing API simple.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `message_id` | TEXT UNIQUE | RFC 2822 Message-ID header |
| `thread_id` | TEXT | Derived thread root identifier |
| `source_account` | TEXT | Email address of the first account to ingest this message (retained for backwards compatibility; authoritative attribution lives in `email_accounts`) |
| `import_id` | UUID | References `imports.id` — the first import that saw this message |
| `subject` | TEXT | Email subject line |
| `sender_name` | TEXT | Display name of sender |
| `sender_address` | TEXT | Full email address of sender |
| `sender_domain` | TEXT | Domain portion, extracted and indexed |
| `recipients` | JSONB | `{to: [...], cc: [...], bcc: [...]}` |
| `date` | TIMESTAMPTZ | Date sent from email headers |
| `body_text` | TEXT | Cleaned plain text (no quotes/sigs) |
| `body_html` | TEXT | Raw HTML body preserved for fidelity |
| `has_attachment` | BOOLEAN | Quick filter flag |
| `attachments` | JSONB | `[{filename, content_type, size}, ...]` |
| `labels` | TEXT[] | Mailbox/folder labels |
| `in_reply_to` | TEXT | Parent message_id (direct reply) |
| `references` | TEXT[] | Full ancestry chain of message_ids |
| `embedding` | VECTOR(768) | pgvector semantic embedding |
| `created_at` | TIMESTAMPTZ | Row insertion timestamp |

### 4.2 Design Decisions

- **One row per message (not per thread):** Individual messages are the unit of storage and embedding. Threads are reconstructed at query time using `thread_id`. This gives semantic search the precision to identify which specific message in a thread is relevant.
- **`emails.source_account` / `import_id` as "first seen":** These scalar columns record the first account/import that ingested each message. They are preserved for backward compatibility, debugging, and the `init_db` self-tightening invariant (once every row has a non-NULL `source_account`, the column is promoted to `NOT NULL` automatically). **Authoritative per-account attribution lives in `email_accounts`** — that's what account-scoped queries use.
- **`email_accounts` many-to-many (§4.6):** When the same `message_id` is ingested under multiple accounts (e.g., the user exports both `personal@` and `work@` mailboxes and the same message appears as sender in one and recipient in the other), the `emails` row is de-duplicated by `ON CONFLICT (message_id) DO NOTHING`, but `email_accounts` gets one row per account. This preserves storage efficiency (bodies, embeddings, attachments aren't duplicated) while giving `find(account="work@")` the ability to still return that message.
- **`sender_domain` as a denormalized column:** Queries like "all emails from anyone at stripe.com" are extremely common. Extracting the domain at ingestion time and indexing it avoids per-query string operations.
- **`recipients` as JSONB:** Keeps the schema flat (no join table) while supporting containment queries. The GIN index on this column enables efficient lookups like "emails where alice@example.com is in the to or cc list."
- **`body_text` vs `body_html`:** `body_text` is the cleaned version used for embedding and display. `body_html` is the raw original preserved for fidelity. Only `body_text` is embedded; the raw HTML is never fed to the embedding model.
- **`labels` as a text array:** Mailbox folder names (e.g., INBOX, Sent, Drafts) and Gmail labels are stored as a PostgreSQL array, queryable via GIN index and the `@>` containment operator.

### 4.3 Threading Strategy

Email threading is reconstructed using RFC 2822 headers. The `thread_id` column is derived at ingestion time using the following rules:

1. If the message has a `References` header, the first message-id in that list is the thread root. Use that value as `thread_id`.
2. If the message has only an `In-Reply-To` header (no References), use that value as `thread_id`.
3. If the message has neither header, it is a thread root. Use its own `message_id` as `thread_id`.

This approach mirrors the threading logic used by most email clients. It ensures that `get_thread(thread_id)` returns all messages in a conversation, ordered chronologically. `get_thread` is intentionally account-agnostic — a cross-account thread (where account A has one message and account B has the reply) returns both messages. The `in_reply_to` and `references` columns are additionally preserved for building full reply trees and detecting unreplied messages.

### 4.4 Table: `imports`

The `imports` table records metadata for each ingest pipeline invocation. Every row in `email_accounts` references an import via `import_id`.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key, also used as `import_id` on email and email_accounts rows |
| `source_account` | TEXT NOT NULL | Email address of the account being imported |
| `source_file` | TEXT | Path to the mbox file (or other source identifier) |
| `started_at` | TIMESTAMPTZ | When the import began |
| `completed_at` | TIMESTAMPTZ | When the import finished (NULL if still running) |
| `messages_total` | INT | Total messages processed |
| `messages_inserted` | INT | New messages inserted |
| `messages_skipped` | INT | Duplicates skipped |
| `status` | TEXT | `running`, `completed`, `failed` (CHECK-constrained) |

**Resume semantics.** `run_pipeline` looks up the most recent `status='running'` row for the same `(source_account, source_file)` and reuses its `import_id` instead of creating a new one. This collapses the imports-table churn that would otherwise accumulate from interrupted-then-retried ingests. A `--force-new-import` flag on `maildb ingest run` is the forensic escape hatch for truly fresh runs.

### 4.5 Table: `email_accounts`

The `email_accounts` table records which accounts have ingested each message. It is the authoritative source of account attribution; `emails.source_account` / `emails.import_id` are kept as "first seen" scalars for backward compatibility.

| Column | Type | Description |
|--------|------|-------------|
| `email_id` | UUID NOT NULL | References `emails.id` (CASCADE DELETE) |
| `source_account` | TEXT NOT NULL | Email address of the account that ingested this message |
| `import_id` | UUID NOT NULL | References `imports.id` — the specific import session |
| `first_seen_at` | TIMESTAMPTZ NOT NULL | When this account first encountered the message (DEFAULT now()) |

Primary key is `(email_id, source_account)`. Multiple imports of the same message under the same account are idempotent: the first row wins via `ON CONFLICT DO NOTHING`, preserving the original `first_seen_at`.

### 4.6 Indexes

The following indexes support the query patterns required by the Python API. (`message_id UNIQUE` is enforced as a constraint, which PostgreSQL implements with an auto-created unique index.)

| Index Name | Column(s) | Type | Purpose |
|------------|-----------|------|---------|
| `idx_email_sender_address` | `sender_address` | B-tree | Lookup by exact sender |
| `idx_email_sender_domain` | `sender_domain` | B-tree | Lookup by domain (@stripe.com) |
| `idx_email_date` | `date` | B-tree | Date range filtering and ordering |
| `idx_email_thread_id` | `thread_id` | B-tree | Thread reconstruction |
| `idx_email_in_reply_to` | `in_reply_to` | B-tree | Unreplied message detection |
| `idx_email_has_attachment` | `has_attachment` | B-tree (partial, WHERE = TRUE) | Attachment filtering |
| `idx_email_labels` | `labels` | GIN | Array containment queries |
| `idx_email_recipients` | `recipients` | GIN | Recipient searches |
| `idx_email_thread_sender_date` | `thread_id, sender_address, date` | B-tree | Accelerates `unreplied` subqueries |
| `idx_email_source_account` | `source_account` | B-tree | Legacy "first seen" account lookups |
| `idx_email_import_id` | `import_id` | B-tree | Legacy "first seen" import lookups |
| `idx_email_accounts_source_account` | `email_accounts.source_account` | B-tree | Account-scoped query filter |
| `idx_email_accounts_import_id` | `email_accounts.import_id` | B-tree | Import session lookups |
| `idx_email_attachments_email_id` | `email_attachments.email_id` | B-tree | Attachment JOINs |
| `idx_email_attachments_attachment_id` | `email_attachments.attachment_id` | B-tree | Attachment JOINs |
| `idx_imports_source_account` | `imports.source_account` | B-tree | Per-account import history |
| `idx_imports_started_at` | `imports.started_at DESC` | B-tree | Recent-imports ordering |
| `idx_email_embedding` | `embedding` | HNSW | Approximate nearest neighbor search |

The HNSW index on the embedding column uses cosine distance (`vector_cosine_ops`). It is created outside `schema_indexes.sql` — specifically, after the embed phase of the pipeline completes, when every row has a vector. Default parameters (`m=16`, `ef_construction=64`) can be tuned if the corpus grows significantly.

### 4.7 Self-Tightening NOT NULL Invariant

The schema declares `emails.source_account TEXT` (nullable) so that a database created before the multi-account feature can be migrated in place. On every call to `init_db`:

1. Apply the schema DDL (idempotent `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`).
2. Backfill `email_accounts` from `(emails.source_account, emails.import_id)` where present, `ON CONFLICT DO NOTHING`.
3. Count rows with `source_account IS NULL`. If zero, run `ALTER TABLE emails ALTER COLUMN source_account SET NOT NULL` — promoting the column once the data is clean. If non-zero, log a hint to run `maildb ingest migrate --account <addr>`.

---

## 5. Embedding Strategy

### 5.1 Model Selection

The chosen model is **nomic-embed-text**, producing 768-dimensional vectors. This model was selected for several reasons:

- **Local execution:** Runs efficiently on Apple Silicon via Ollama with Metal GPU acceleration. No data leaves the device.
- **Quality:** Scores competitively on MTEB retrieval benchmarks, particularly for short-to-medium text passages typical of email messages.
- **Speed:** Approximately 20 messages per second per Ollama worker on an M1 Max with 4 workers in parallel, yielding ~12 hours for an 840K-message corpus (see §8).
- **Dimensionality:** 768 dimensions is a good balance between retrieval quality and storage/index efficiency.

The embedding model is treated as a swappable component. Changing models requires re-embedding all rows (a one-time batch operation) and updating the vector column dimension, but no schema or API changes.

### 5.2 Embedding Content

For each message, the following string is constructed and passed to the embedding model:

```
Subject: {subject}
From: {sender_name}

{body_text}
```

Including the subject and sender name in the embedded text provides semantic signal for queries that reference both topic and person (e.g., "budget email from Sarah"). The `body_text` has already been cleaned of quoted reply blocks and signatures at ingestion time, so the embedding reflects only the new content contributed by this specific message.

### 5.3 Query-Time Embedding

When the user or agent calls `search()`, the query string is embedded using the same `nomic-embed-text` model via Ollama. The resulting vector is compared against the stored embeddings using cosine distance, leveraging the HNSW index. The top-N results are returned, optionally filtered by structured predicates (sender, date range, account, etc.) applied in the WHERE clause alongside the vector ordering.

---

## 6. Python API Design

### 6.1 Overview

The agent-facing interface is a Python class called `MailDB`. It wraps all database and embedding operations behind a clean method-based API. Every method returns `Email` objects (or lists of them) with all columns accessible as attributes, including the full body text.

The primary external interface is the MCP server, which wraps `MailDB` and adds serialization, field selection, pagination, and observability (see Section 6.6).

### 6.2 Method Reference

The `MailDB` class provides three tiers of query methods:

**Tier 1 — Fixed-signature methods** for the most common query patterns. All methods that return results support `offset` for pagination. All result-returning methods accept `account: str | None = None` unless otherwise noted; when provided, results are scoped to emails attributed to that account via `email_accounts`.

| Method | Description | Key Parameters |
|--------|-------------|----------------|
| `find()` | Structured attribute-based lookup | `sender`, `sender_domain`, `recipient`, `after`, `before`, `has_attachment`, `subject_contains`, `labels`, `max_to`, `max_cc`, `max_recipients`, `direct_only`, `account`, `limit`, `offset`, `order` |
| `search()` | Semantic search with optional structured filters | `query` (text), plus all `find()` filters. Requires Ollama |
| `mention_search()` | Case-insensitive keyword search in body and subject (ILIKE) | `text`, `sender`, `sender_domain`, `after`, `before`, recipient count filters, `account`, `limit`, `offset` |
| `get_thread()` | Retrieve full conversation by thread_id (account-agnostic) | `thread_id` |
| `get_thread_for()` | Find thread containing a specific message (account-agnostic) | `message_id` |
| `get_emails()` | Fetch full Email objects by message_id list, preserving input order | `message_ids` |
| `correspondence()` | All emails exchanged with a person (sent by them or to them) | `address`, `after`, `before`, `limit`, `offset`, `order` |
| `top_contacts()` | Most frequent correspondents | `period`, `limit`, `offset`, `direction` (inbound/outbound/both), `group_by` (address/domain), `exclude_domains`, `account` |
| `topics_with()` | Diverse topic representatives for a contact (farthest-point selection on embeddings) | `sender` or `sender_domain`, `limit`, `offset` |
| `cluster()` | Diverse topic extraction from arbitrary email subsets | `where` (DSL filter) or `message_ids`, `limit`, `offset` |
| `unreplied()` | Messages with no reply in the same thread | `direction` (inbound/outbound), `recipient`, `after`, `before`, `sender`, `sender_domain`, recipient count filters, `account`, `limit`, `offset` |
| `long_threads()` | Threads exceeding a message count | `min_messages`, `after`, `participant`, `account`, `limit`, `offset` |
| `accounts()` | Per-account summary of ingested messages | returns `list[AccountSummary]` |
| `import_history()` | Import session log, newest first | `account`, `limit`, `offset` → `list[ImportRecord]` |

**Tier 2 — DSL query engine** for arbitrary structured queries beyond the fixed method signatures (see Section 6.5).

| Method | Description | Parameters |
|--------|-------------|------------|
| `query()` | Execute a structured query using the JSON DSL | `spec` (dict) |

### 6.3 Identity-Aware Queries

`unreplied()` and `top_contacts()` need to know "which addresses represent *you*" to distinguish inbound from outbound messages and to exclude self-correspondence from top-contact tallies. The resolution order:

1. If the caller passes `account="X"`, identity is `[X]` — narrows to just that one address.
2. Otherwise `MailDB._effective_user_emails()` returns:
   - Every address configured via `MAILDB_USER_EMAILS` (comma-separated) or the legacy `MAILDB_USER_EMAIL`, merged first (in order);
   - Plus every `DISTINCT source_account` from the `imports` table, deduplicated and appended.
3. If both sources are empty, identity-aware methods raise `ValueError`.

This means a user who ingests `alice@personal.com` and `alice@work.com` gets correct `unreplied` / `top_contacts` results with zero configuration. The env var is an override surface — useful for aliases, forwarding addresses, or to pre-declare identities before ingestion. Results from a given `MailDB` instance are cached.

### 6.4 Usage Examples

Structured lookup — all emails from a domain in a date range, scoped to one account:

```python
results = db.find(sender_domain="stripe.com", account="work@example.com",
                  after="2025-01-01", before="2025-03-01")
```

Semantic search across all accounts:

```python
results = db.search("complaints about the deployment process")
```

Hybrid query — semantic + account + date:

```python
results = db.search("budget concerns", account="personal@example.com",
                    sender_domain="acme.com", after="2024-07-01")
```

Thread expansion — cross-account threads return messages from every account:

```python
hits = db.search("the office move")
thread = db.get_thread(hits[0].thread_id)  # may include messages from multiple accounts
```

Per-account inventory:

```python
for s in db.accounts():
    print(s.source_account, s.email_count, s.import_count)
```

Recent import sessions:

```python
for r in db.import_history(limit=10):
    print(r.started_at, r.source_account, r.status, r.messages_inserted)
```

DSL query — recipient domain distribution:

```python
results = db.query({
    "from": "sent_to",
    "select": [
        {"field": "recipient_domain"},
        {"count": "*", "as": "n"},
    ],
    "group_by": ["recipient_domain"],
    "order_by": [{"field": "n", "dir": "desc"}],
    "limit": 10,
})
```

### 6.5 DSL Query Engine

The `query()` method exposes a JSON DSL for arbitrary structured queries that go beyond the fixed method signatures. This enables agents to answer ad-hoc analytical questions ("how many emails did I receive per month from stripe.com?") without requiring new Python methods for every query shape.

**Specification format:**

```json
{
  "from": "emails | sent_to | email_labels",
  "select": [{"field": "col"}, {"count": "*", "as": "n"}, {"date_trunc": "month", "field": "date", "as": "period"}],
  "where": {"field": "col", "eq": "value"} or {"and|or|not": [...]},
  "group_by": ["col1", "col2"],
  "having": {"field": "n", "gte": 10},
  "order_by": [{"field": "col", "dir": "asc|desc"}],
  "limit": 50,
  "offset": 0
}
```

**Sources.** `from = "emails"` queries the table directly. `"sent_to"` expands recipients into one row per recipient address via a LATERAL join (adding `recipient_address`, `recipient_domain`, `recipient_type`). `"email_labels"` unnests the labels array (adding `label`). `"emails_by_account"` joins `emails` to `email_accounts`, producing one row per `(email, account)` pair and adding `account` (the source account address), `account_import_id` (the per-account import row), and `first_seen_at`. Use `emails_by_account` whenever true multi-account attribution matters — e.g., finding messages that exist in account B whether or not B was the first importer.

**Safety.** All column names, operators, aggregate functions, and date-trunc precisions are validated against strict whitelists. All values are parameterized. A 5-second `statement_timeout` and 1,000-row hard cap are enforced server-side.

**Operators.** `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `ilike`, `not_ilike`, `in`, `not_in`, `contains`, `is_null`. Boolean combinators: `and`, `or`, `not`.

**Aggregates.** `count`, `count_distinct`, `min`, `max`, `sum`, `array_agg_distinct`.

**Column whitelist on `emails`** includes `source_account` and `import_id`, reflecting the scalar "first seen" attribution stored directly on the emails row. For true multi-account attribution (the same message appearing under every account that ingested it), use the `emails_by_account` source and filter on `account`.

### 6.6 MCP Server Layer

The MCP server (`server.py`) is the primary external interface, wrapping all MailDB methods as tools accessible to AI assistants via the Model Context Protocol.

**Tool inventory.** `find`, `search`, `mention_search`, `get_thread`, `get_thread_for`, `get_emails`, `correspondence`, `top_contacts`, `topics_with`, `cluster`, `unreplied`, `long_threads`, `query`, `accounts`, `import_history`.

**Serialization.** All `Email` objects are converted to JSON-serializable dicts. The `embedding` vector and `body_html` fields are always stripped from responses to prevent context overflow. UUIDs and datetimes are serialized to strings. `AccountSummary` and `ImportRecord` are converted to plain dicts with ISO-formatted timestamps.

**Field selection.** All email-returning tools accept an optional `fields` parameter — a list of field names to include in the response. This lets agents request only the columns they need, reducing response size. Valid fields: `id`, `message_id`, `thread_id`, `subject`, `sender_name`, `sender_address`, `sender_domain`, `recipients`, `date`, `body_text`, `body_length`, `body_truncated`, `has_attachment`, `attachments`, `labels`, `in_reply_to`, `references`, `created_at`. By default, list tools return everything *except* `body_text` (replacing it with `body_length`); `get_emails` returns everything *including* `body_text`.

**Body truncation.** `get_emails` accepts `body_max_chars` — when set and `body_text` exceeds that length, the body is truncated with `...` and a `body_truncated: true` marker is added.

**Offset pagination.** All list-returning tools accept an `offset` parameter (default 0) for cursor-free pagination. Combined with `limit`, this enables agents to page through large result sets.

**Observability.** The `@log_tool` decorator instruments every tool call, logging entry parameters and exit stats (row count, response bytes, elapsed time). Responses exceeding 50KB trigger a warning-level log entry.

---

## 7. Ingestion Pipeline

### 7.1 Mbox Import

The initial data source is one or more local `.mbox` files. The ingest pipeline is a 4-phase process coordinated by a PostgreSQL `ingest_tasks` table, making it restartable and parallelizable.

**Phase 1: Split.** The mbox file is split into chunks (default 50 MB) for parallel processing.

**Phase 2: Parse.** Multiple worker processes claim chunks via `SKIP LOCKED` and parse each message:

- Parses all relevant headers (From, To, Cc, Bcc, Date, Subject, Message-ID, In-Reply-To, References). All header values are sanitized: `Header` objects are coerced to `str`, NUL bytes are stripped. If the `Date` header is missing or unparseable, the parser falls back to extracting the timestamp from the first `Received` header.
- Extracts the best plain-text body: prefers `text/plain` parts; falls back to HTML-to-text conversion for HTML-only messages.
- Cleans the body by removing quoted reply blocks (lines beginning with `>`) and common email signature delimiters (`-- `).
- Extracts attachments and stores them to disk using content-addressed (SHA-256) deduplication.
- Derives `thread_id` per the threading rules in Section 4.3.
- Upserts the email row with `INSERT ... ON CONFLICT (message_id) DO UPDATE SET thread_id = emails.thread_id RETURNING id`. The no-op update ensures `RETURNING id` fires whether the row was inserted or already existed, giving the worker an authoritative `email_id`.
- Upserts into `email_accounts` with `(email_id, current source_account, current import_id)` via `ON CONFLICT (email_id, source_account) DO NOTHING`. This is what makes account-scoped queries correct across re-ingests and cross-account duplicates.
- Stamps `emails.source_account` and `emails.import_id` on initial insert only (they become read-only "first seen" attribution for the lifetime of the row).

**Phase 3: Index.** Non-unique indexes are rebuilt after the parse phase (they're dropped before it to speed up bulk inserts).

**Phase 4: Embed.** Multiple worker processes generate embeddings via Ollama. Batches of 50 rows are selected with `SKIP LOCKED` for parallel processing. If a batch fails (e.g., context length exceeded), the worker falls back to single-row embedding and marks permanently failing rows with a zero-vector sentinel so they are not retried. After the embed phase completes, the HNSW index is created on the `embedding` column.

**Import session lifecycle (resume-by-key).** On every invocation of `run_pipeline`:

1. Check for `ingest_tasks` in a stale `split` state (started but never completed). If found, clear them and recurse — this restart path never creates an imports row, so it can't orphan one.
2. Look up the most recent `imports` row with the same `(source_account, source_file)` and `status='running'`. If found, reuse its `import_id` (logging `resuming_import`).
3. Otherwise, insert a fresh `imports` row with a new UUID and `status='running'`.
4. On success, update to `status='completed'` with final `messages_total`, `messages_inserted`, and `messages_skipped` counts.
5. On exception, update to `status='failed'` with `completed_at` set, then re-raise.

The `--force-new-import` CLI flag on `maildb ingest run` bypasses step 2 and always creates a fresh row — useful for forensic scenarios where a new attribution boundary is intentional.

### 7.2 CLI

```bash
maildb serve                                        # run the MCP server (stdio)
maildb ingest run --account you@work.com mbox       # run/resume the full pipeline
maildb ingest run --account you@work.com mbox --skip-embed
maildb ingest run --account you@work.com mbox --force-new-import
maildb ingest status                                # phase counts + import history
maildb ingest status --account you@work.com         # filter history to one account
maildb ingest reset                                 # full wipe (interactive confirm)
maildb ingest reset --phase parse --yes             # cascade reset from parse onward
maildb ingest migrate --account legacy@gmail.com    # tag pre-multi-account rows
```

`python -m maildb` defaults to `serve` when no subcommand is given, preserving the pre-Typer entry point.

### 7.3 Body Text Cleaning

Cleaning the body text is critical for embedding quality. The following transformations are applied in order:

- **HTML to text:** If only an HTML body part is available, it is converted to plain text using BeautifulSoup4, preserving paragraph structure but stripping tags.
- **Quoted reply removal:** Lines beginning with `>` (any depth of nesting) are removed. Outlook-style quoted blocks (delineated by `-----Original Message-----`) are also stripped.
- **Signature removal:** Content below the standard signature delimiter (`-- ` on its own line) is removed.
- **Whitespace normalization:** Excessive blank lines and trailing whitespace are collapsed.

The result is a focused `body_text` that represents only the new content this message contributed to the conversation. This prevents redundant information from inflating embeddings and ensures semantic search matches the actual source message.

### 7.4 Backfill Migration

For databases ingested before the multi-account feature, `maildb ingest migrate --account <addr>`:

1. Inserts a synthetic `imports` row with `source_file='migration'`.
2. Updates every `emails` row with `source_account IS NULL`, setting both `source_account` and `import_id` to the migration row.
3. Mirrors each updated row into `email_accounts`.
4. Reports the number of rows updated.

The command is idempotent — re-running produces a new empty migration row but updates zero additional emails. Next time `init_db` runs after all rows are tagged, the self-tightening NOT NULL promotion will fire automatically.

### 7.5 Gmail Sync (Phase 2 — Future)

A future phase will add incremental sync from Gmail via the Gmail API. This will involve OAuth 2.0 authentication, label-to-`labels` column mapping, and a sync cursor to efficiently pull only new or modified messages. The database schema is designed to accommodate this without changes — Gmail messages will populate the same `emails` table with the same columns, and a Gmail sync session will record its attribution in `email_accounts` just like an mbox import. The sync mechanism will be defined in a separate design document when that phase begins.

---

## 8. Performance Estimates

Based on a production import (49 GB mbox, 841,930 messages) on Apple M1 Max with 64 GB RAM:

- **Split phase:** ~2 minutes for 49 GB, producing 886 chunks.
- **Parse phase:** ~2 minutes for 841,930 messages across parallel workers. CPU-bound on parsing, I/O-bound on PostgreSQL writes. The `ON CONFLICT DO UPDATE ... RETURNING id` pattern plus the `email_accounts` upsert adds one extra insert per row; negligible in practice.
- **Index phase:** Seconds. B-tree and GIN indexes on 841K rows.
- **Embedding generation:** ~12 hours at ~20 messages/second with 4 Ollama workers and batch fallback for oversized messages. This is the dominant bottleneck.
- **Structured query latency:** < 10ms for indexed lookups (sender, date, thread_id). Aggregation queries like `top_contacts` may take 50–200ms depending on the time window. Adding an `account` filter uses the B-tree index on `email_accounts.source_account` and adds negligible overhead.
- **Semantic query latency:** < 100ms for HNSW approximate nearest neighbor search. Hybrid queries add the structured filter overhead, typically < 150ms total.
- **Storage:** At 841,930 rows the database (including embeddings, text, metadata, and indexes) consumes approximately 8 GB. The attachment directory (content-addressed, deduplicated) adds additional storage depending on email content.
- **Multi-account overhead:** `emails.source_account` / `import_id` add ~50 bytes/row. Each `email_accounts` row is ~80 bytes; one row per `(message, account)` — so a user with 2 accounts and 100% overlap pays ~16 MB for an 800K-message corpus.

---

## 9. Implementation Plan

The project is structured in phases, each independently testable. Phases 1–6 are complete. Phase 7 (Gmail sync) is deferred.

| # | Phase | Deliverables | Status |
|---|-------|-------------|--------|
| 1 | Schema & Database Setup | PostgreSQL database, pgvector extension, table creation, index definitions | Complete |
| 2 | Mbox Ingestion | 4-phase parallel pipeline (split → parse → index → embed) with content-addressed attachment deduplication | Complete |
| 3 | Embedding Generation | Ollama integration, nomic-embed-text inference, batch embedding with fallback | Complete |
| 4 | Python Library (Core) | `MailDB` class with `find()`, `search()`, `get_thread()`, hybrid queries | Complete |
| 5 | Python Library (Advanced) + MCP Server | See §9.1 | Complete |
| 6 | Multi-Account Support | See §9.2 | Complete |
| 7 | Gmail Sync (Future) | Gmail API integration, incremental sync, OAuth, label mapping | Deferred |

### 9.1 Phase 5: Full Scope

Phase 5 was delivered across three sprints, expanding well beyond the initial `top_contacts` / `unreplied` / `long_threads` scope:

**Sprint 1:** Core advanced methods — `top_contacts()` with direction/group_by/exclude_domains, `topics_with()` with farthest-point embedding selection, `unreplied()` with bidirectional support (inbound/outbound) and recipient filtering, `long_threads()` with participant filtering. FastMCP server with tool registration.

**Sprint 2:** Observability — dual-sink structured logging (stderr + debug file) with PII scrubbing, `@log_tool` decorator, SQL debug logging.

**Sprint 3:** API surface expansion — `correspondence()`, `mention_search()`, `cluster()`, `query()` DSL engine, `sent_to` and `email_labels` virtual sources. MCP serialization improvements: `body_html`/`embedding` stripping, `fields` parameter, `offset` pagination, response size warnings. Bug fixes: Received header date fallback, null-date exclusion from unreplied, `limit` on `long_threads`.

### 9.2 Phase 6: Multi-Account Support

Delivered across the original multi-account implementation plus three follow-ups:

**Core (issues #11–#15):** Schema columns (`emails.source_account`, `emails.import_id`, `ingest_tasks.import_id`), `imports` table, Typer CLI, `ingest migrate` backfill command, NOT NULL self-tightening, `account` parameter on all relevant query methods, new `accounts()` and `import_history()` methods, MCP tool exposure, `MAILDB_USER_EMAILS` config.

**Follow-up #37 — Auto-derived user identities.** `MailDB._identity_addresses()` now merges configured `user_emails` with `DISTINCT source_account FROM imports`, so `unreplied` / `top_contacts` are correct-by-default for any ingested account. Env var stays as an override for aliases.

**Follow-up #38 — Resume-by-key imports.** `run_pipeline` adopts an existing `status='running'` imports row for the same `(source_account, source_file)` instead of unconditionally creating a new one. `--force-new-import` flag added as forensic escape hatch.

**Follow-up #36 — `email_accounts` join table.** The "first-import-wins" deduplication semantic was replaced with true many-to-many attribution. The same `message_id` ingested under multiple accounts now surfaces under every account via `EXISTS` on `email_accounts`. Legacy `emails.source_account` / `emails.import_id` are preserved as read-only "first seen" columns; `init_db` backfills the join table from them.

---

## 10. Future Considerations

- **Attachment content indexing:** Attachments are currently stored on disk with metadata in the database. A future enhancement could extract text from PDF and document attachments and include it in the embedding, enabling searches like "find the email with the contract PDF that mentioned the termination clause."
- **Embedding model upgrades:** As local embedding models improve, the system can be re-embedded with a better model. The `import_id` tracking makes it possible to re-embed selectively.
- **Multi-user support:** The current design is single-user (one person's email accounts). Multi-user support would require a `user_id` column and row-level security.
- **Incremental embedding:** New messages ingested after the initial load should be embedded immediately at insert time, avoiding the need for batch backfills.
- **Account auto-detection:** For Gmail Takeout mbox files, the source account could potentially be inferred from the file metadata or message headers, reducing the need to specify `--account` manually.
- **Deprecating `emails.source_account` / `emails.import_id`:** Once all writers and readers use `email_accounts`, these columns and their indexes can be dropped. The current version keeps them for one release as a compatibility surface and a self-tightening invariant.
- **Hybrid query planning:** `search()` currently runs structured filters and vector similarity in the same SQL statement, relying on PostgreSQL's planner to order them. A future enhancement could add explicit query planning based on selectivity statistics.
- **Orphan-running-import reaper:** A process that crashes without reaching its exception handler leaves a `status='running'` imports row. The resume-by-key logic will adopt it on the next matching invocation, but a `maildb ingest reap --older-than 24h` command could close out long-idle rows explicitly.
