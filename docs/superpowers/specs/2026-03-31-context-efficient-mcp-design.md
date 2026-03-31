# Context-Efficient MCP Response Pattern

## Problem

The maildb MCP server returns full email objects (including body text) by default on all list queries. This creates three compounding problems:

1. **Payload bloat** — a 100-email result set produces 83K+ characters, exceeding inline return limits and forcing file-based workarounds.
2. **Missing server-side filters** — queries like "direct messages only" (sole recipient, no CC) cannot be expressed and require client-side post-processing with jq.
3. **No result count metadata** — hitting a limit cap gives no indication of how many total results exist, forcing blind pagination.

## Design

### Response Wrapper

All list-returning tools (`find`, `search`, `correspondence`, `mention_search`, `unreplied`, `topics_with`, `cluster`, `long_threads`, `top_contacts`) change from returning a flat list to a metadata wrapper:

```json
{
  "total": 147,
  "offset": 0,
  "limit": 50,
  "results": [...]
}
```

- `total` is the full count of rows matching the filters, ignoring `limit`/`offset`.
- Computed via `COUNT(*) OVER()` window function — single query, no duplication.
- For `topics_with` and `cluster` (which do Python-side farthest-point selection after DB fetch), `total` reflects the number of candidates fetched from the DB before selection, since the post-selection count equals `len(results)`.
- For `search`, results inside `results` remain `{email, similarity}` objects.
- The `query` DSL tool is unchanged (power-user escape hatch).

### Headers-by-Default

All list tools stop returning `body_text` by default. A new `body_length` field (integer character count, null if no body) is returned instead.

**Default fields on list tools:**
`id`, `message_id`, `thread_id`, `subject`, `sender_name`, `sender_address`, `sender_domain`, `recipients`, `date`, `body_length`, `has_attachment`, `attachments`, `labels`, `in_reply_to`, `references`, `created_at`

**Excluded by default:** `body_text`

**Override:** The existing `fields` parameter still works. Passing `fields: ["subject", "body_text"]` returns body_text. When `fields` is not provided, the default set (without body_text) applies.

**Implementation:** The `body_text` column is still selected from the DB (needed to compute `body_length`) but stripped during serialization in `_serialize_email`. The function gets a new default field set and computes `body_length` from the underlying data. This is purely a serialization-layer change — no schema changes needed.

### New `get_emails` Tool

A new MCP tool for fetching full email objects by ID with optional body truncation.

```
get_emails(
    ids: list[str],                    # message_id values (RFC 2822 Message-ID)
    body_max_chars: int | None = None, # truncate body_text to N chars, None = full
    fields: list[str] | None = None,   # field selection, same as other tools
)
```

**Returns:** `{total: N, results: [{email with body_text}, ...]}` — same wrapper shape for consistency. `total` equals `len(results)` (no pagination since fetching specific IDs).

**Behavior:**
- Uses `message_id` (not UUID `id`) since that's what other tools return and what callers chain from.
- Returns `body_text` by default (opposite of list tools — the caller is asking for specific emails, they want the content).
- When `body_max_chars` is set, `body_text` is truncated to that length with `...` appended, and a `body_truncated: true` flag is added.
- Results returned in the same order as the input `ids` list.
- Missing IDs are silently skipped.

**DB layer:** New `MailDB.get_emails(message_ids: list[str]) -> list[Email]` method using `WHERE message_id IN (...)`.

**Serialization:** `_serialize_email` gains a `body_max_chars` parameter. When set, it truncates `body_text` and sets `body_truncated: True` in the output dict.

### Recipient Count Filters

Three new optional parameters added to `_build_filters` (available on `find`, `search`, `correspondence`, `mention_search`, `unreplied`):

```
max_to: int | None           # max recipients in To field
max_cc: int | None           # max recipients in CC field
max_recipients: int | None   # max total across To + CC + BCC
direct_only: bool = False    # sugar for max_to=1, max_cc=0
```

**SQL implementation** using `jsonb_array_length`:

```sql
-- max_to: 1
jsonb_array_length(COALESCE(recipients->'to', '[]'::jsonb)) <= 1

-- max_cc: 0
jsonb_array_length(COALESCE(recipients->'cc', '[]'::jsonb)) <= 0

-- max_recipients: 2
(jsonb_array_length(COALESCE(recipients->'to', '[]'::jsonb))
 + jsonb_array_length(COALESCE(recipients->'cc', '[]'::jsonb))
 + jsonb_array_length(COALESCE(recipients->'bcc', '[]'::jsonb))) <= 2
```

**`direct_only` behavior:**
- `direct_only=True` is equivalent to `max_to=1, max_cc=0`. BCC is unconstrained (a direct message could have a BCC the recipient doesn't see).
- Passing `direct_only=True` alongside explicit `max_to` or `max_cc` raises `ValueError`.

**Performance:** `jsonb_array_length` on JSONB arrays is O(1). These filters will almost always combine with indexed columns (sender, date) that narrow the scan first.

## Example Workflow

The query "find all emails sent directly to me from disney@postmates.com and analyze themes" becomes:

**Step 1 — Search with filters:**
```
find(sender="disney@postmates.com", direct_only=true, limit=100)
```
Returns ~5K payload: `{total: 29, results: [{message_id, subject, date, body_length, ...}, ...]}`.

**Step 2 — Fetch bodies for analysis:**
```
get_emails(ids=["abc@gmail.com", "def@gmail.com", ...])
```
Or skim first: `get_emails(ids=[...], body_max_chars=300)`, then fetch full bodies for interesting subset.

## Scope

### In scope
- Response wrapper with `total` on all list tools
- Headers-by-default serialization with `body_length`
- New `get_emails` tool with `body_max_chars` truncation
- `max_to`, `max_cc`, `max_recipients`, `direct_only` filters on `_build_filters`
- Propagating new filters to MCP tool handler signatures
- Tests for all new functionality

### Out of scope
- Schema/DDL changes (all changes are in the query and serialization layers)
- Changes to the `query` DSL tool
- New indexes (existing indexes are sufficient)
- Changes to ingest pipeline
