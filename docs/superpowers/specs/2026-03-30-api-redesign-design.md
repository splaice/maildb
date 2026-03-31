# Sprint 3: API Redesign Design Spec

## Architecture

Two-tier design:
- **Tier 1** — Purpose-built methods for correlated subqueries and embedding operations
- **Tier 2** — Generalized JSON DSL for flat filtering, aggregation, and grouping

### Why this split
Tier 1 handles patterns that require correlated subqueries (`unreplied()` uses NOT EXISTS), embedding operations (`cluster()`, `search()`), or JSONB unnesting with specific semantics (`correspondence()`, `top_contacts()`). These are hard to express safely in a general DSL.

Tier 2 covers the long tail: ad-hoc aggregation, grouping, date extraction, and filtering that would otherwise require raw SQL. Uses parameterized SQL construction with strict column/operator whitelists for safety.

### What the DSL cannot do
- Joins, subqueries, window functions
- Mutations (INSERT/UPDATE/DELETE)
- Access to body_text in grouped queries
- Queries returning more than 1000 rows

## Tier 1 Tool Specifications

### Enhanced Tools

**unreplied(direction, recipient, after, before, sender, sender_domain, limit)**
- `direction="inbound"` (default): Current behavior — inbound messages with no user reply
- `direction="outbound"`: User's messages where recipient never replied in same thread
- `recipient`: Filter by specific recipient (outbound only)

**top_contacts(group_by, exclude_domains, period, limit, direction)**
- `group_by="address"` (default) or `"domain"` for domain-level aggregation
- `exclude_domains`: List of domains to filter out

**long_threads(participant, min_messages, after)**
- `participant`: Only threads where this address appears as sender

### New Tools

**correspondence(address, after, before, limit, order)**
- All emails exchanged with a person (sent by them OR where they're a recipient)
- Default order: `date ASC`, default limit: 500

**mention_search(text, sender, sender_domain, after, before, limit)**
- Case-insensitive ILIKE search in body_text and subject
- No Ollama dependency (unlike semantic search())

**cluster(where, message_ids, limit)**
- Farthest-point selection on embeddings for diverse topic extraction
- `where`: DSL filter syntax (reuses Tier 2 engine)
- `message_ids`: Explicit UUID list for chaining with Tier 1 tools

## Tier 2 DSL Specification

### Input Schema

```json
{
  "from": "emails | sent_to | email_labels",
  "select": [...],
  "where": {...},
  "group_by": [...],
  "having": {...},
  "order_by": [...],
  "limit": 50,
  "offset": 0
}
```

All fields optional. `from` defaults to `"emails"`.

### Sources

| Source | Description | Extra Columns |
|--------|-------------|---------------|
| `emails` | Base table | — |
| `sent_to` | CTE: unnests recipients from JSONB | `recipient_address`, `recipient_domain`, `recipient_type` |
| `email_labels` | CTE: unnests labels array | `label` |

### Column Whitelist

**`emails`**: `id`, `message_id`, `thread_id`, `subject`, `sender_name`, `sender_address`, `sender_domain`, `date`, `body_text` (filter only when grouping), `has_attachment`, `labels`, `in_reply_to`, `created_at`

**`sent_to`**: all `emails` columns + `recipient_address`, `recipient_domain`, `recipient_type`

**`email_labels`**: all `emails` columns + `label`

### Where Operators

| Operator | Example |
|----------|---------|
| `eq` | `{"field": "sender_address", "eq": "alice@acme.com"}` |
| `neq` | `{"field": "sender_address", "neq": "alice@acme.com"}` |
| `gt`, `gte`, `lt`, `lte` | `{"field": "date", "gte": "2025-01-01"}` |
| `ilike`, `not_ilike` | `{"field": "subject", "ilike": "%budget%"}` |
| `in`, `not_in` | `{"field": "sender_domain", "in": ["a.com", "b.com"]}` |
| `contains` | `{"field": "labels", "contains": ["INBOX"]}` |
| `is_null` | `{"field": "in_reply_to", "is_null": true}` |

**Boolean combinators:** `and`, `or`, `not`

```json
{"and": [{"field": "sender_domain", "eq": "a.com"}, {"field": "date", "gte": "2025-01-01"}]}
```

### Select Expressions

```json
{"field": "sender_address"}
{"field": "sender_address", "as": "addr"}
{"count": "*", "as": "total"}
{"count_distinct": "sender_address", "as": "unique_senders"}
{"min": "date", "as": "first_date"}
{"max": "date", "as": "last_date"}
{"sum": "column_name", "as": "total_size"}
{"array_agg_distinct": "sender_address", "as": "participants"}
{"date_trunc": "month", "field": "date", "as": "period"}
```

Default (no select): full rows with `body_text` truncated to 500 chars as `body_preview`.

### Guardrails (non-negotiable)

- **Read-only**: SELECT only
- **Statement timeout**: 5 second execution timeout
- **Row limit**: Hard cap at 1000 rows
- **Column whitelist**: Only columns defined per source accepted
- **Operator whitelist**: Only operators in the table above
- **No body_text in SELECT for aggregation queries**: When `group_by` is present
- **Parameterized queries**: All values passed as psycopg parameters

## Design Decisions

1. **Parameterized SQL vs PyPika:** Direct parameterized SQL string construction with strict column/operator whitelists. PyPika's parameterization model doesn't cleanly integrate with psycopg's `%(name)s` style. Same safety guarantees without the impedance mismatch.
2. **cluster() uses message_ids:** Enables chaining — e.g., `unreplied()` output fed to `cluster()` for diverse topic extraction.
3. **Virtual sources as CTEs:** `sent_to` and `email_labels` use CTEs with LATERAL/unnest to flatten JSONB and arrays.
4. **Default body_text truncation:** Non-aggregation queries without explicit select return `body_text` truncated to 500 chars as `body_preview`.
5. **HAVING validates against SELECT aliases:** The HAVING clause dynamically collects aliases from SELECT items, so user-defined aliases work in HAVING conditions.

## Session Query Replay Validation

All 10 original session queries are coverable by the new API:
- Queries 1-7: Covered by existing Tier 1 tools (find, search, get_thread, top_contacts, unreplied, long_threads) with new enhancements
- Queries 8-9: Covered by Tier 2 DSL (aggregation, grouping, date extraction)
- Query 10: Covered by new mention_search() for keyword body search
