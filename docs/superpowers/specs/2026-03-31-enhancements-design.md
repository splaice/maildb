# Field Selection and Offset Pagination for MCP Tools

**Date:** 2026-03-31
**Issues:** splaice/maildb#31, splaice/maildb#32

## Overview

Two enhancements that reduce MCP response size and enable result browsing: field selection lets callers request only the fields they need, and offset pagination lets callers page through results.

---

## Enhancement #31: Field Selection

**Problem:** Every tool returns the full Email schema (16 fields after body_html removal). Most use cases need a subset. Excess fields waste LLM context tokens and increase response size.

### Design

Add an optional `fields` parameter to `_serialize_email()` in `src/maildb/server.py:74-87`. Filtering happens at serialization — the database still fetches all columns, but only requested fields appear in the response.

**`_serialize_email()` changes:**

```python
SERIALIZABLE_EMAIL_FIELDS = frozenset({
    "id", "message_id", "thread_id", "subject", "sender_name",
    "sender_address", "sender_domain", "recipients", "date",
    "body_text", "has_attachment", "attachments", "labels",
    "in_reply_to", "references", "created_at",
})

def _serialize_email(email: Any, fields: frozenset[str] | None = None) -> dict[str, Any]:
    d = asdict(email)
    # Convert non-serializable types
    if isinstance(d.get("id"), UUID):
        d["id"] = str(d["id"])
    if isinstance(d.get("date"), datetime):
        d["date"] = d["date"].isoformat() if d["date"] else None
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
    # Always drop these
    d.pop("embedding", None)
    d.pop("body_html", None)
    # Apply field selection
    if fields is not None:
        d = {k: v for k, v in d.items() if k in fields}
    return d
```

**Tool handler changes:**

Each of the 9 tools that return Email objects gains `fields: list[str] | None = None`. The handler converts the list to a `frozenset` intersected with `SERIALIZABLE_EMAIL_FIELDS` and passes it to `_serialize_email()`:

```python
def find(ctx: Context, ..., fields: list[str] | None = None) -> list[dict[str, Any]]:
    db = _get_db(ctx)
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_email(e, valid) for e in db.find(...)]
```

**`_serialize_search_result()` changes:**

Pass `fields` through to the inner `_serialize_email()` call. The `similarity` field is always included.

```python
def _serialize_search_result(sr: Any, fields: frozenset[str] | None = None) -> dict[str, Any]:
    return {
        "email": _serialize_email(sr.email, fields),
        "similarity": sr.similarity,
    }
```

**Invalid field names** are silently ignored — the `frozenset` intersection handles this naturally. No error for unknown fields.

**Tools affected (9):** `find`, `search`, `get_thread`, `get_thread_for`, `mention_search`, `correspondence`, `unreplied`, `topics_with`, `cluster`.

**Not affected (3):** `top_contacts`, `long_threads`, `query` — return dicts, not Email objects.

### Files Changed

| File | Change |
|------|--------|
| `src/maildb/server.py` | Add `SERIALIZABLE_EMAIL_FIELDS`, update `_serialize_email()`, `_serialize_search_result()`, add `fields` param to 9 tool handlers |
| `tests/unit/test_server.py` | Tests for field selection in serialization |

---

## Enhancement #32: Offset Pagination

**Problem:** Tools support `limit` but not `offset`. No way to page through results — callers can only get the first N results.

### Design

Add `offset: int = 0` to every `MailDB` method and MCP tool handler that accepts `limit`. Append `OFFSET %(offset)s` to the SQL query.

**SQL pattern:**

```sql
-- Before
LIMIT %(limit)s

-- After
LIMIT %(limit)s OFFSET %(offset)s
```

All offset values use parameterized queries (`%(offset)s`), not string interpolation.

**MailDB method changes (9 methods):**

Each method gains `offset: int = 0` in its signature, adds `"offset": offset` to the params dict, and appends `OFFSET %(offset)s` after the existing `LIMIT`:

```python
def find(self, *, ..., limit: int = 50, offset: int = 0) -> list[Email]:
    ...
    params["offset"] = offset
    ...
    sql = f"""
        SELECT ... FROM emails WHERE {where}
        ORDER BY {order}
        LIMIT %(limit)s OFFSET %(offset)s
    """
```

**Tool handler changes (9 tools):**

Each handler gains `offset: int = 0` and passes it through:

```python
def find(ctx: Context, ..., limit: int = 50, offset: int = 0, ...) -> list[dict[str, Any]]:
    db = _get_db(ctx)
    return [_serialize_email(e) for e in db.find(..., limit=limit, offset=offset)]
```

**Tools affected (9):** `find`, `search`, `mention_search`, `correspondence`, `unreplied`, `topics_with`, `cluster`, `long_threads`, `top_contacts`.

**Not affected:** `get_thread`, `get_thread_for` (return a single thread, no pagination), `query` (already has offset via DSL).

### Files Changed

| File | Change |
|------|--------|
| `src/maildb/maildb.py` | Add `offset` param to 9 methods, add `OFFSET` to SQL |
| `src/maildb/server.py` | Add `offset` param to 9 tool handlers |
| `tests/integration/test_maildb.py` | Test offset behavior |

---

## Implementation Order

1. **#31 first** (field selection) — touches serialization + tool handler signatures
2. **#32 second** (offset pagination) — touches MailDB methods + tool handler signatures

This ordering avoids conflicts: #31 modifies `_serialize_email()` and adds `fields` to handlers, while #32 modifies SQL methods and adds `offset` to handlers. Both touch `server.py` handler signatures but different parameters.
