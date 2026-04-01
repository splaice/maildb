# MailDB

**Personal Email Database for Agent-Powered Retrieval**

Design Document — Version 3.0 — March 2026

PostgreSQL · pgvector · Ollama · Python

---

## 1. Executive Summary

MailDB is a personal email database that stores the full contents of a user's email history in PostgreSQL and exposes it to AI agents through a Python library. It combines traditional structured search (by sender, date, labels, attachments) with semantic vector search (by topic, intent, or fuzzy description) using the pgvector extension.

The system supports **multiple email accounts** within a single database. Each email is tagged with a `source_account` (the email address of the account it was imported from) and an `import_id` (a unique identifier for the import session). This allows queries to be scoped to a specific account ("search my work email for budget discussions") while also supporting cross-account search when no account filter is specified. Import sessions are tracked for debugging and auditability.

The system is designed to run entirely on local hardware with no external API dependencies. Embeddings are generated locally using an open-source model served by Ollama, ensuring complete privacy of email content.

The initial implementation targets local mailbox (.mbox) import, with Gmail API sync planned as a future phase.

---

## 2. Goals and Requirements

### 2.1 Functional Goals

The system must support five categories of queries, reflecting the natural ways a user or agent would search for emails:

- **Structured lookups:** Find emails by concrete attributes—sender address, domain, date ranges, attachment presence, subject keywords, and folder labels.
- **Semantic search:** Find emails related to a topic, concept, or vague description using natural language. Examples include "complaints about the deployment process" or "discussions about switching CI providers."
- **People and relationships:** Identify frequent correspondents, discover what topics are discussed with a specific contact, and find all participants in a conversation.
- **Summarization and synthesis:** Retrieve relevant emails so an agent can summarize threads, extract action items, or identify commitments. The database provides retrieval; the agent handles reasoning.
- **Temporal and pattern analysis:** Detect unreplied messages, long threads, and time-sensitive communications.

### 2.2 Non-Functional Requirements

- **Full local execution:** No email content leaves the local machine. Embedding inference runs on-device via Ollama.
- **Sub-second query latency:** Both structured and semantic queries should return results in under one second for a corpus of up to 1,000,000 messages across multiple accounts.
- **Agent-friendly Python API:** A clean Python library that an LLM agent can call directly via tool use. Also exposed as an MCP server for integration with AI assistants.
- **Multi-account support:** Multiple email accounts can be imported into the same database. Queries can be scoped to a single account or search across all accounts.
- **Import traceability:** Every import session is assigned a unique ID. Each message records which import session created it, enabling debugging and selective re-imports.
- **Incremental extensibility:** The architecture must accommodate Gmail API sync in a future phase without schema changes.

---

## 3. System Architecture

### 3.1 Technology Stack

- **Database:** PostgreSQL 16+ with the pgvector extension for vector similarity search.
- **Embedding model:** nomic-embed-text (768 dimensions), served locally via Ollama. Embedding text is truncated to 6,000 characters to stay within the model's 8,192 token context window.
- **Python library:** Custom MailDB class using psycopg (v3) with psycopg_pool for connection pooling, and the ollama Python package for embedding generation.
- **MCP server:** FastMCP server exposing all MailDB methods as tools for AI assistant integration. Includes field selection, offset pagination, and response size management.
- **DSL engine:** JSON-to-SQL translator (`dsl.py`) supporting arbitrary queries with strict column/operator whitelists, parameterized values, and a 5-second statement timeout.
- **Email parsing:** Python's standard library `mailbox` and `email` modules for mbox ingestion, with BeautifulSoup4 for HTML-to-text conversion.
- **Observability:** Dual-sink structured logging via structlog — INFO+ to stderr (safe for MCP stdio transport) and DEBUG+ to a rotating debug log file. A PII scrubbing processor redacts email addresses, SSNs, credit card numbers, and phone numbers from all log output before it reaches either sink.
- **Target hardware:** Tested on Apple M1 Max with 64GB unified memory. Ollama runs embedding inference on the Metal GPU.

### 3.2 Architecture Overview

The system consists of three layers:

- **Ingestion layer:** Reads raw email sources (mbox files initially, Gmail API in Phase 2), parses each message into structured fields, cleans the body text, generates an embedding vector, and upserts the row into PostgreSQL. Deduplication is enforced by the unique constraint on `message_id`.
- **Storage layer:** A single PostgreSQL database containing one table (`emails`) with B-tree indexes for structured queries, GIN indexes for JSONB and array columns, and an HNSW index on the vector column for approximate nearest neighbor search.
- **Query layer:** The MailDB Python class, which translates high-level method calls (`find`, `search`, `get_thread`, etc.) into SQL queries. Semantic search queries are first embedded using the same Ollama model, then compared against stored vectors using cosine distance. A DSL engine supports arbitrary structured queries beyond the fixed method signatures.
- **MCP server layer:** A FastMCP server wraps the MailDB class, serializing results to JSON with automatic stripping of `body_html` and `embedding` fields. All email-returning tools support a `fields` parameter for selective field return and an `offset` parameter for pagination. A `log_tool` decorator instruments every tool call with entry parameters, exit stats, and response size warnings (>50KB).

### 3.3 Data Flow

The ingestion pipeline processes each email message through the following steps:

1. Parse the message using Python's `mailbox` module to extract raw headers and body parts.
2. Extract structured metadata: sender name, address, and domain; recipient lists (to/cc/bcc); date; subject; message-id; in-reply-to; references; attachment metadata.
3. Derive the `thread_id` from threading headers (see Section 4.3).
4. Clean the body text: convert HTML to plain text, strip quoted reply blocks and email signatures, and produce a focused `body_text` representing only the new content of this message.
5. Generate the embedding by concatenating subject, sender name, and cleaned body text, then passing this string to nomic-embed-text via Ollama. The resulting 768-dimensional vector is stored in the `embedding` column.
6. Upsert the row into PostgreSQL, using the `message_id` unique constraint for deduplication.

---

## 4. Database Schema

### 4.1 Table: `emails`

All email data is stored in a single flat table. This intentional denormalization avoids joins for the common query patterns and keeps the agent-facing API simple. One row represents one email message.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `message_id` | TEXT UNIQUE | RFC 2822 Message-ID header |
| `thread_id` | TEXT | Derived thread root identifier |
| `source_account` | TEXT | Email address of the account this message was imported from |
| `import_id` | UUID | Unique identifier for the import session that created this row |
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
- **`source_account` for multi-account support:** Each message records the email address of the account it was imported from (e.g., `you@work.com` or `you@gmail.com`). This enables account-scoped queries ("search only my personal email") while keeping all data in a single table. When the same message appears in multiple accounts (e.g., both sender and recipient imported their mail), the `UNIQUE (message_id)` constraint deduplicates it — the first import wins. The `source_account` reflects whichever import created the row.
- **`import_id` for traceability:** Every invocation of the ingest pipeline generates a UUID that is stamped on all rows created in that session. This supports debugging ("which import produced this data?"), selective re-imports ("delete everything from import X and re-run"), and auditability. Import metadata (source file, account, start time, row counts) is recorded in the `imports` table.
- **`sender_domain` as a denormalized column:** Queries like "all emails from anyone at stripe.com" are extremely common. Extracting the domain at ingestion time and indexing it avoids per-query string operations.
- **`recipients` as JSONB:** Keeps the schema flat (no join table) while supporting containment queries. The GIN index on this column enables efficient lookups like "emails where alice@example.com is in the to or cc list."
- **`body_text` vs `body_html`:** `body_text` is the cleaned version used for embedding and display. `body_html` is the raw original preserved for fidelity. Only `body_text` is embedded; the raw HTML is never fed to the embedding model.
- **`labels` as a text array:** Mailbox folder names (e.g., INBOX, Sent, Drafts) and Gmail labels are stored as a PostgreSQL array, queryable via GIN index and the `@>` containment operator.

### 4.3 Threading Strategy

Email threading is reconstructed using RFC 2822 headers. The `thread_id` column is derived at ingestion time using the following rules:

1. If the message has a `References` header, the first message-id in that list is the thread root. Use that value as `thread_id`.
2. If the message has only an `In-Reply-To` header (no References), use that value as `thread_id`.
3. If the message has neither header, it is a thread root. Use its own `message_id` as `thread_id`.

This approach mirrors the threading logic used by most email clients. It ensures that `get_thread(thread_id)` returns all messages in a conversation, ordered chronologically. The `in_reply_to` and `references` columns are additionally preserved for building full reply trees and detecting unreplied messages.

### 4.4 Table: `imports`

The `imports` table records metadata for each ingest pipeline invocation. Every row in the `emails` table references an import via `import_id`.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key, also used as `import_id` on email rows |
| `source_account` | TEXT | Email address of the account being imported |
| `source_file` | TEXT | Path to the mbox file (or other source identifier) |
| `started_at` | TIMESTAMPTZ | When the import began |
| `completed_at` | TIMESTAMPTZ | When the import finished (NULL if in progress) |
| `messages_total` | INT | Total messages processed |
| `messages_inserted` | INT | New messages inserted |
| `messages_skipped` | INT | Duplicates skipped |
| `status` | TEXT | `running`, `completed`, `failed` |

### 4.5 Indexes

The following indexes support the query patterns required by the Python API:

| Index Name | Column(s) | Type | Purpose |
|------------|-----------|------|---------|
| `idx_email_sender_address` | `sender_address` | B-tree | Lookup by exact sender |
| `idx_email_sender_domain` | `sender_domain` | B-tree | Lookup by domain (@stripe.com) |
| `idx_email_date` | `date` | B-tree | Date range filtering and ordering |
| `idx_email_thread_id` | `thread_id` | B-tree | Thread reconstruction |
| `idx_email_message_id` | `message_id` | B-tree (unique) | Deduplication and reply chains |
| `idx_email_in_reply_to` | `in_reply_to` | B-tree | Unreplied message detection |
| `idx_email_has_attachment` | `has_attachment` | B-tree (partial) | Attachment filtering |
| `idx_email_labels` | `labels` | GIN | Array containment queries |
| `idx_email_recipients` | `recipients` | GIN | Recipient searches |
| `idx_email_source_account` | `source_account` | B-tree | Account-scoped queries |
| `idx_email_import_id` | `import_id` | B-tree | Import session lookups |
| `idx_email_embedding` | `embedding` | HNSW | Approximate nearest neighbor search |

The HNSW index on the embedding column uses cosine distance as the similarity metric. For a corpus of up to 1,000,000 messages across multiple accounts, HNSW provides sub-second approximate nearest neighbor search with high recall. The index is built with default parameters (`m=16`, `ef_construction=64`) which can be tuned if the corpus grows significantly.

---

## 5. Embedding Strategy

### 5.1 Model Selection

The chosen model is **nomic-embed-text**, producing 768-dimensional vectors. This model was selected for several reasons:

- **Local execution:** Runs efficiently on Apple Silicon via Ollama with Metal GPU acceleration. No data leaves the device.
- **Quality:** Scores competitively on MTEB retrieval benchmarks, particularly for short-to-medium text passages typical of email messages.
- **Speed:** Expected throughput of 100–300 messages per second on M4 Max, meaning a full corpus of 200,000 messages can be embedded in approximately 15–30 minutes.
- **Dimensionality:** 768 dimensions is a good balance between retrieval quality and storage/index efficiency. At 200,000 rows, the embedding column will consume approximately 600MB of storage.

The embedding model is treated as a swappable component. Changing models requires re-embedding all rows (a one-time batch operation) and updating the vector column dimension, but no schema or API changes.

### 5.2 Embedding Content

For each message, the following string is constructed and passed to the embedding model:

```
Subject: {subject}
From: {sender_name}

{body_text}
```

Including the subject and sender name in the embedded text provides semantic signal for queries that reference both topic and person (e.g., "budget email from Sarah"). The `body_text` has already been cleaned of quoted reply blocks and signatures at ingestion time, so the embedding reflects only the new content contributed by this specific message.

This means a query like "complaining about the deployment process" will match the message that actually contained the complaint, not every subsequent reply that quoted it.

### 5.3 Query-Time Embedding

When the user or agent calls `search()`, the query string is embedded using the same nomic-embed-text model via Ollama. The resulting vector is compared against the stored embeddings using cosine distance, leveraging the HNSW index. The top-N results are returned, optionally filtered by structured predicates (sender, date range, etc.) applied before or after the vector search depending on selectivity.

---

## 6. Python API Design

### 6.1 Overview

The agent-facing interface is a Python class called `MailDB`. It wraps all database and embedding operations behind a clean method-based API. Every method returns `Email` objects (or lists of them) with all columns accessible as attributes, including the full body text. This ensures the agent has enough context to summarize, extract action items, or reason about message content.

The primary external interface is the MCP server, which wraps MailDB and adds serialization, field selection, pagination, and observability (see Section 6.6).

### 6.2 Method Reference

The MailDB class provides three tiers of query methods:

**Tier 1 — Fixed-signature methods** for the most common query patterns. All methods accepting results support `offset` for pagination.

| Method | Description | Parameters |
|--------|-------------|------------|
| `find()` | Structured attribute-based lookup | `sender`, `sender_domain`, `recipient`, `after`, `before`, `has_attachment`, `subject_contains`, `labels`, `limit`, `offset`, `order` |
| `search()` | Semantic search with optional structured filters | `query` (text), plus all `find()` filters. Requires Ollama |
| `get_thread()` | Retrieve full conversation by thread_id | `thread_id` |
| `get_thread_for()` | Find thread containing a specific message | `message_id` |
| `correspondence()` | All emails exchanged with a person (sent by them or to them) | `address`, `after`, `before`, `limit`, `offset`, `order` |
| `mention_search()` | Case-insensitive keyword search in body and subject (ILIKE) | `text`, `sender`, `sender_domain`, `after`, `before`, `limit`, `offset` |
| `top_contacts()` | Most frequent correspondents | `period`, `limit`, `offset`, `direction` (inbound/outbound/both), `group_by` (address/domain), `exclude_domains` |
| `topics_with()` | Diverse topic representatives for a contact (farthest-point selection on embeddings) | `sender` or `sender_domain`, `limit`, `offset` |
| `cluster()` | Diverse topic extraction from arbitrary email subsets | `where` (DSL filter) or `message_ids`, `limit`, `offset` |
| `unreplied()` | Messages with no reply in the same thread | `direction` (inbound/outbound), `recipient`, `after`, `before`, `sender`, `sender_domain`, `limit`, `offset` |
| `long_threads()` | Threads exceeding a message count | `min_messages`, `after`, `participant`, `limit`, `offset` |

**Tier 2 — DSL query engine** for arbitrary structured queries beyond the fixed method signatures (see Section 6.5).

| Method | Description | Parameters |
|--------|-------------|------------|
| `query()` | Execute a structured query using the JSON DSL | `spec` (dict) |

### 6.3 Usage Examples

Structured lookup — all emails from a domain in a date range:

```python
results = db.find(sender_domain="stripe.com", after="2025-01-01",
                  before="2025-03-01")
```

Semantic search — find emails about a topic:

```python
results = db.search("complaints about the deployment process")
```

Hybrid query — semantic search scoped to a contact:

```python
results = db.search("budget concerns",
                    sender_domain="acme.com", after="2024-07-01")
```

Thread expansion — retrieve full conversation from a search hit:

```python
hits = db.search("the office move")
thread = db.get_thread(hits[0].thread_id)
```

Full correspondence with a person:

```python
history = db.correspondence(address="scott@banister.com",
                            after="2024-01-01")
```

Keyword search without Ollama:

```python
results = db.mention_search(text="quarterly review",
                            sender_domain="acme.com")
```

Unreplied outbound messages to a specific recipient:

```python
awaiting = db.unreplied(direction="outbound",
                        recipient="bob@corp.com")
```

Paginating through results:

```python
page1 = db.find(sender_domain="stripe.com", limit=50, offset=0)
page2 = db.find(sender_domain="stripe.com", limit=50, offset=50)
```

Diverse topic clustering from a filtered subset:

```python
topics = db.cluster(
    where={"and": [
        {"field": "sender_domain", "eq": "stripe.com"},
        {"field": "date", "gte": "2024-01-01"},
    ]},
    limit=5,
)
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

### 6.4 Hybrid Query Execution

When `search()` is called with both a query string and structured filters, the execution strategy depends on the selectivity of the structured predicates. If the filters are highly selective (e.g., a specific sender address), the database first applies the structured filter via B-tree index to narrow the candidate set, then performs the vector similarity search within that subset. If the filters are broad, the vector search runs first with a larger candidate pool, and structured filters are applied as a post-filter. The MailDB class handles this optimization automatically based on query planning heuristics.

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

**Sources:** The `from` field selects the base relation. `"emails"` queries the table directly. `"sent_to"` expands recipients into one row per recipient address via a LATERAL join, adding `recipient_address`, `recipient_domain`, and `recipient_type` columns. `"email_labels"` unnests the labels array, adding a `label` column.

**Safety:** All column names, operators, aggregate functions, and date-trunc precisions are validated against strict whitelists. All values are parameterized. A 5-second `statement_timeout` and 1,000-row hard cap are enforced server-side.

**Operators:** `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `ilike`, `not_ilike`, `in`, `not_in`, `contains`, `is_null`. Boolean combinators: `and`, `or`, `not`.

**Aggregates:** `count`, `count_distinct`, `min`, `max`, `sum`, `array_agg_distinct`.

### 6.6 MCP Server Layer

The MCP server (`server.py`) is the primary external interface, wrapping all MailDB methods as tools accessible to AI assistants via the Model Context Protocol.

**Serialization:** All `Email` objects are converted to JSON-serializable dicts. The `embedding` vector and `body_html` fields are always stripped from responses to prevent context overflow. UUIDs and datetimes are serialized to strings.

**Field selection:** All email-returning tools accept an optional `fields` parameter — a list of field names to include in the response. This allows agents to request only the columns they need, reducing response size. Valid fields: `id`, `message_id`, `thread_id`, `subject`, `sender_name`, `sender_address`, `sender_domain`, `recipients`, `date`, `body_text`, `has_attachment`, `attachments`, `labels`, `in_reply_to`, `references`, `created_at`.

**Offset pagination:** All list-returning tools accept an `offset` parameter (default 0) for cursor-free pagination. Combined with `limit`, this enables agents to page through large result sets.

**Observability:** The `@log_tool` decorator instruments every tool call, logging entry parameters and exit stats (row count, response bytes, elapsed time). Responses exceeding 50KB trigger a warning-level log entry.

---

## 7. Ingestion Pipeline

### 7.1 Mbox Import

The initial data source is one or more local .mbox files. The ingest pipeline is a 4-phase process coordinated by a PostgreSQL `ingest_tasks` table, making it restartable and parallelizable.

**Phase 1: Split.** The mbox file is split into chunks (default 50 MB) for parallel processing.

**Phase 2: Parse.** Multiple worker processes claim chunks via `SKIP LOCKED` and parse each message:

- Parses all relevant headers (From, To, Cc, Bcc, Date, Subject, Message-ID, In-Reply-To, References). All header values are sanitized: `Header` objects are coerced to `str`, NUL bytes are stripped. If the `Date` header is missing or unparseable, the parser falls back to extracting the timestamp from the first `Received` header.
- Extracts the best plain-text body: prefers text/plain parts; falls back to HTML-to-text conversion for HTML-only messages.
- Cleans the body by removing quoted reply blocks (lines beginning with `>`) and common email signature delimiters ("-- ").
- Extracts attachments and stores them to disk using content-addressed (SHA-256) deduplication.
- Derives `thread_id` per the threading rules in Section 4.3.
- Stamps each row with the `source_account` and `import_id` for the current import session.
- Upserts the row into PostgreSQL, skipping duplicates based on `message_id`.

**Phase 3: Index.** B-tree, GIN, and partial indexes are created (or rebuilt) on the parsed data. Indexes are dropped before the parse phase and rebuilt after to optimize bulk insert performance.

**Phase 4: Embed.** Multiple worker processes generate embeddings via Ollama. Batches of 50 rows are selected with `SKIP LOCKED` for parallel processing. If a batch fails (e.g., context length exceeded), the worker falls back to single-row embedding and marks permanently failing rows with a zero-vector sentinel so they are not retried.

Each invocation of the pipeline:

1. Creates a row in the `imports` table with a new UUID, the `source_account`, and the source file path.
2. The `import_id` and `source_account` are passed through to all parsed rows.
3. On completion, the `imports` row is updated with final counts and status.

**CLI usage:**

```bash
python -m maildb.ingest --account you@work.com /path/to/work.mbox
python -m maildb.ingest --account you@gmail.com /path/to/personal.mbox
python -m maildb.ingest status
```

### 7.2 Body Text Cleaning

Cleaning the body text is critical for embedding quality. The following transformations are applied in order:

- **HTML to text:** If only an HTML body part is available, it is converted to plain text using BeautifulSoup4, preserving paragraph structure but stripping tags.
- **Quoted reply removal:** Lines beginning with ">" (any depth of nesting) are removed. Outlook-style quoted blocks (delineated by "-----Original Message-----") are also stripped.
- **Signature removal:** Content below the standard signature delimiter ("-- " on its own line) is removed.
- **Whitespace normalization:** Excessive blank lines and trailing whitespace are collapsed.

The result is a focused `body_text` that represents only the new content this message contributed to the conversation. This prevents redundant information from inflating embeddings and ensures semantic search matches the actual source message.

### 7.3 Gmail Sync (Phase 2 — Future)

A future phase will add incremental sync from Gmail via the Gmail API. This will involve OAuth 2.0 authentication, label-to-labels column mapping, and a sync cursor to efficiently pull only new or modified messages. The database schema is designed to accommodate this without changes—Gmail messages will populate the same table with the same columns. The sync mechanism will be defined in a separate design document when that phase begins.

---

## 8. Performance Estimates

Based on the first real import (49 GB mbox, 841,930 messages) on Apple M1 Max with 64 GB RAM:

- **Split phase:** 2 minutes for 49 GB, producing 886 chunks.
- **Parse phase:** ~2 minutes for 841,930 messages across parallel workers. Remarkably fast — CPU-bound on parsing, I/O-bound on PostgreSQL writes.
- **Index phase:** Seconds. B-tree and GIN indexes on 841K rows.
- **Embedding generation:** ~12 hours at ~20 messages/second with 4 Ollama workers and batch fallback for oversized messages. This is the dominant bottleneck.
- **Structured query latency:** < 10ms for indexed lookups (sender, date, thread_id). Aggregation queries like `top_contacts` may take 50–200ms depending on the time window. Adding an `account` filter uses the B-tree index and does not degrade performance.
- **Semantic query latency:** < 100ms for HNSW approximate nearest neighbor search. Hybrid queries add the structured filter overhead, typically < 150ms total.
- **Storage:** At 841,930 rows the database (including embeddings, text, metadata, and indexes) consumes approximately 8 GB. The attachment directory (content-addressed, deduplicated) adds additional storage depending on email content.
- **Multi-account overhead:** The `source_account` and `import_id` columns add negligible storage (~50 bytes/row). The B-tree index on `source_account` enables efficient account-scoped queries without scanning the full table.

---

## 9. Implementation Plan

The project is structured in phases, each independently testable. Phases 1–5 are complete. Phase 6 adds multi-account query support. Phase 7 (Gmail sync) is deferred.

| # | Phase | Deliverables | Status |
|---|-------|-------------|--------|
| 1 | Schema & Database Setup | PostgreSQL database, pgvector extension, table creation, index definitions | Complete |
| 2 | Mbox Ingestion | 4-phase parallel pipeline (split → parse → index → embed) with content-addressed attachment deduplication | Complete |
| 3 | Embedding Generation | Ollama integration, nomic-embed-text inference, batch embedding with fallback | Complete |
| 4 | Python Library (Core) | MailDB class with `find()`, `search()`, `get_thread()`, hybrid queries | Complete |
| 5 | Python Library (Advanced) + MCP Server | See Section 9.1 for full scope | Complete |
| 6 | Multi-Account Query Support | Account-scoped query parameters, `accounts()` and `import_history()` methods | Planned |
| 7 | Gmail Sync (Future) | Gmail API integration, incremental sync, OAuth, label mapping | Deferred |

### 9.1 Phase 5: Full Scope

Phase 5 was delivered across three sprints, expanding well beyond the initial `top_contacts` / `unreplied` / `long_threads` scope:

**Sprint 1:** Core advanced methods — `top_contacts()` with direction/group_by/exclude_domains, `topics_with()` with farthest-point embedding selection, `unreplied()` with bidirectional support (inbound/outbound) and recipient filtering, `long_threads()` with participant filtering. FastMCP server with tool registration.

**Sprint 2:** Observability — dual-sink structured logging (stderr + debug file) with PII scrubbing, `@log_tool` decorator, SQL debug logging.

**Sprint 3:** API surface expansion — `correspondence()`, `mention_search()`, `cluster()`, `query()` DSL engine, `sent_to` and `email_labels` virtual sources. MCP serialization improvements: `body_html`/`embedding` stripping, `fields` parameter, `offset` pagination, response size warnings. Bug fixes: Received header date fallback, null-date exclusion from unreplied, `limit` on long_threads.

### 9.2 Phase 6: Multi-Account Query Support

The schema already supports multi-account imports — `source_account` and `import_id` columns exist on the `emails` table, and the `imports` table records session metadata. This phase adds account-scoped querying.

**Query changes:**
- All query methods (`find`, `search`, `top_contacts`, `unreplied`, `long_threads`) accept an optional `account` parameter
- `_build_filters()` adds a `source_account = %(account)s` condition when `account` is provided
- New `accounts()` method returns list of imported accounts with email counts
- New `import_history()` method returns import session metadata
- MCP server tools expose the `account` parameter

Phase 6 is a non-breaking addition — existing single-account databases will continue to work.

---

## 10. Future Considerations

- **Attachment content indexing:** Attachments are currently stored on disk with metadata in the database. A future enhancement could extract text from PDF and document attachments and include it in the embedding, enabling searches like "find the email with the contract PDF that mentioned the termination clause."
- **Embedding model upgrades:** As local embedding models improve, the system can be re-embedded with a better model. The `import_id` tracking makes it possible to re-embed selectively. This is a batch operation that requires no schema changes beyond updating the vector dimension.
- **Token-aware truncation:** The current 6,000-character truncation limit is conservative. A token-aware truncation strategy would allow longer English-prose emails to use more of the context window while still protecting against token-dense content.
- **Multi-user support:** The current design is single-user (one person's email accounts). Multi-user support would require a `user_id` column and row-level security, which PostgreSQL supports natively.
- **Incremental embedding:** New messages ingested after the initial load should be embedded immediately at insert time, avoiding the need for batch backfills.
- **Account auto-detection:** For Gmail Takeout mbox files, the source account could potentially be inferred from the file metadata or message headers, reducing the need to specify `--account` manually.
