# MailDB

**Personal Email Database for Agent-Powered Retrieval**

Architecture Document — Version 1.0 — March 2026

PostgreSQL · pgvector · Ollama · Python

---

## 1. Executive Summary

MailDB is a personal email database that stores the full contents of a user's email history in PostgreSQL and exposes it to AI agents through a Python library. It combines traditional structured search (by sender, date, labels, attachments) with semantic vector search (by topic, intent, or fuzzy description) using the pgvector extension.

The system is designed to run entirely on local hardware—specifically an Apple M4 Max with 128GB RAM—with no external API dependencies. Embeddings are generated locally using an open-source model served by Ollama, ensuring complete privacy of email content.

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
- **Sub-second query latency:** Both structured and semantic queries should return results in under one second for a corpus of up to 200,000 messages.
- **Agent-friendly Python API:** A clean Python library that an LLM agent can call directly via tool use. No MCP server required in Phase 1.
- **Incremental extensibility:** The architecture must accommodate Gmail API sync in a future phase without schema changes.

---

## 3. System Architecture

### 3.1 Technology Stack

- **Database:** PostgreSQL 16+ with the pgvector extension for vector similarity search.
- **Embedding model:** nomic-embed-text (768 dimensions), served locally via Ollama on Apple M4 Max.
- **Python library:** Custom MailDB class using psycopg (v3) for database access and the ollama Python package for embedding generation.
- **Email parsing:** Python's standard library `mailbox` and `email` modules for mbox ingestion.
- **Target hardware:** Apple M4 Max, 128GB unified memory. Ollama runs embedding inference on the Metal GPU.

### 3.2 Architecture Overview

The system consists of three layers:

- **Ingestion layer:** Reads raw email sources (mbox files initially, Gmail API in Phase 2), parses each message into structured fields, cleans the body text, generates an embedding vector, and upserts the row into PostgreSQL. Deduplication is enforced by the unique constraint on `message_id`.
- **Storage layer:** A single PostgreSQL database containing one table (`emails`) with B-tree indexes for structured queries, GIN indexes for JSONB and array columns, and an HNSW index on the vector column for approximate nearest neighbor search.
- **Query layer:** The MailDB Python class, which translates high-level method calls (`find`, `search`, `get_thread`, etc.) into SQL queries. Semantic search queries are first embedded using the same Ollama model, then compared against stored vectors using cosine distance.

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

### 4.4 Indexes

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
| `idx_email_embedding` | `embedding` | HNSW | Approximate nearest neighbor search |

The HNSW index on the embedding column uses cosine distance as the similarity metric. For a corpus of up to 200,000 messages, HNSW provides sub-second approximate nearest neighbor search with high recall. The index is built with default parameters (`m=16`, `ef_construction=64`) which can be tuned if the corpus grows significantly.

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

### 6.2 Method Reference

| Method | Description | Parameters |
|--------|-------------|------------|
| `find()` | Structured attribute-based lookup | `sender`, `sender_domain`, `after`, `before`, `has_attachment`, `subject_contains`, `labels`, `limit`, `order` |
| `search()` | Semantic search with optional structured filters | `query` (text), plus all `find()` filters |
| `get_thread()` | Retrieve full conversation by thread_id | `thread_id` |
| `get_thread_for()` | Find thread containing a specific message | `message_id` |
| `top_contacts()` | Most frequent correspondents | `period`, `limit`, `direction` (inbound/outbound/both) |
| `topics_with()` | Semantic topic clusters for a contact | `sender` or `sender_domain`, `limit` |
| `unreplied()` | Inbound messages with no outbound reply | `after`, `before`, `sender`, `sender_domain` |
| `long_threads()` | Threads exceeding a message count | `min_messages`, `after` |

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

Pattern query — unreplied inbound messages:

```python
unreplied = db.unreplied(after="2025-02-01")
```

### 6.4 Hybrid Query Execution

When `search()` is called with both a query string and structured filters, the execution strategy depends on the selectivity of the structured predicates. If the filters are highly selective (e.g., a specific sender address), the database first applies the structured filter via B-tree index to narrow the candidate set, then performs the vector similarity search within that subset. If the filters are broad, the vector search runs first with a larger candidate pool, and structured filters are applied as a post-filter. The MailDB class handles this optimization automatically based on query planning heuristics.

---

## 7. Ingestion Pipeline

### 7.1 Mbox Import (Phase 1)

The initial data source is one or more local .mbox files. Python's standard library `mailbox.mbox` is used to iterate over messages. For each message, the ingestion pipeline:

- Parses all relevant headers (From, To, Cc, Bcc, Date, Subject, Message-ID, In-Reply-To, References).
- Extracts the best plain-text body: prefers text/plain parts; falls back to HTML-to-text conversion for HTML-only messages.
- Cleans the body by removing quoted reply blocks (lines beginning with `>`) and common email signature delimiters ("-- ").
- Extracts attachment metadata (filename, content type, size) without storing attachment binary data in the database.
- Derives `thread_id` per the threading rules in Section 4.3.
- Generates the embedding vector via Ollama.
- Upserts the row into PostgreSQL, skipping duplicates based on `message_id`.

### 7.2 Body Text Cleaning

Cleaning the body text is critical for embedding quality. The following transformations are applied in order:

- **HTML to text:** If only an HTML body part is available, it is converted to plain text using a library like html2text or beautifulsoup4, preserving paragraph structure but stripping tags.
- **Quoted reply removal:** Lines beginning with ">" (any depth of nesting) are removed. Outlook-style quoted blocks (delineated by "-----Original Message-----") are also stripped.
- **Signature removal:** Content below the standard signature delimiter ("-- " on its own line) is removed.
- **Whitespace normalization:** Excessive blank lines and trailing whitespace are collapsed.

The result is a focused `body_text` that represents only the new content this message contributed to the conversation. This prevents redundant information from inflating embeddings and ensures semantic search matches the actual source message.

### 7.3 Gmail Sync (Phase 2 — Future)

A future phase will add incremental sync from Gmail via the Gmail API. This will involve OAuth 2.0 authentication, label-to-labels column mapping, and a sync cursor to efficiently pull only new or modified messages. The database schema is designed to accommodate this without changes—Gmail messages will populate the same table with the same columns. The sync mechanism will be defined in a separate design document when that phase begins.

---

## 8. Performance Estimates

Based on a corpus of approximately 1GB of email (~50,000–200,000 messages) running on the target M4 Max hardware:

- **Initial mbox ingestion (without embeddings):** 5–15 minutes. Parsing and inserting structured data is I/O-bound by disk speed and PostgreSQL write throughput.
- **Embedding generation (backfill):** 15–30 minutes at 100–300 messages/second. This is the bottleneck and runs as a separate batch step.
- **Structured query latency:** < 10ms for indexed lookups (sender, date, thread_id). Aggregation queries like `top_contacts` may take 50–200ms depending on the time window.
- **Semantic query latency:** < 100ms for HNSW approximate nearest neighbor search. Hybrid queries add the structured filter overhead, typically < 150ms total.
- **Storage:** The embedding column alone requires approximately 600MB at 200,000 rows × 768 dimensions × 4 bytes. Total database size including text, metadata, and indexes is estimated at 2–4GB.

---

## 9. Implementation Plan

The project is structured in six phases, each independently testable. Early phases deliver a working structured search system; later phases layer on semantic search and advanced analytics.

| # | Phase | Deliverables | Milestone |
|---|-------|-------------|-----------|
| 1 | Schema & Database Setup | PostgreSQL database, pgvector extension, table creation, index definitions | Testable with manual SQL inserts |
| 2 | Mbox Ingestion | Parse local .mbox files, extract metadata and body text, populate structured columns | Validates structured queries before adding vector complexity |
| 3 | Embedding Generation | Ollama integration, nomic-embed-text inference, backfill embedding column for all rows | Enables semantic search on existing data |
| 4 | Python Library (Core) | MailDB class with `find()`, `search()`, `get_thread()`, hybrid queries | Agent-usable API for structured and semantic retrieval |
| 5 | Python Library (Advanced) | `top_contacts()`, `topics_with()`, `unreplied()`, `long_threads()` | Pattern-based queries and relationship analytics |
| 6 | Gmail Sync (Future) | Gmail API integration, incremental sync, OAuth, label mapping | Deferred to Phase 2 of the project |

Each phase produces a working system that can be used immediately. Phase 2 delivers a fully functional structured email search; Phase 3 adds semantic capabilities; Phases 4 and 5 complete the agent-facing API. Phase 6 is deferred and will be scoped separately.

---

## 10. Future Considerations

- **MCP server wrapper:** The MailDB Python library can be wrapped as an MCP (Model Context Protocol) server, making it accessible to any Claude-based agent without custom tool definitions. This is a natural evolution once the core library is stable.
- **Attachment content indexing:** Phase 1 stores only attachment metadata. A future enhancement could extract text from PDF and document attachments and include it in the embedding, enabling searches like "find the email with the contract PDF that mentioned the termination clause."
- **Embedding model upgrades:** As local embedding models improve, the system can be re-embedded with a better model. This is a batch operation that requires no schema changes beyond updating the vector dimension.
- **Multi-user support:** The current design is single-user. Multi-user support would require a `user_id` column and row-level security, which PostgreSQL supports natively.
- **Incremental embedding:** New messages ingested after the initial load should be embedded immediately at insert time, avoiding the need for batch backfills.
