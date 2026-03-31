# Field Selection and Offset Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add field selection (`fields` parameter) and offset pagination (`offset` parameter) to MCP tools so callers can control response shape and page through results.

**Architecture:** Field selection is implemented at the serialization layer — `_serialize_email()` gains an optional `fields` parameter that filters the output dict. Offset pagination is implemented at the SQL layer — each `MailDB` method gains `offset: int = 0` appended to its `LIMIT` clause. Both features are added to tool handler signatures in `server.py`.

**Tech Stack:** structlog (existing), psycopg (existing), FastMCP (existing)

**Spec:** `docs/superpowers/specs/2026-03-31-enhancements-design.md`

**GitHub Issues:** splaice/maildb#31, splaice/maildb#32

---

## Context for the implementing agent

**Project setup:**
```bash
uv sync                    # Install dependencies
uv run just test           # Run tests (pytest)
uv run just fmt            # Format (ruff)
uv run just lint           # Lint (ruff + mypy)
uv run just check          # fmt + lint + test
```

**If `just` is not available** (e.g. in a sandbox), run the commands directly:
```bash
uv run ruff format .
uv run ruff check . --fix
uv run ruff check .
uv run mypy src/
uv run pytest tests/unit/
```

**Key conventions:**
- Python 3.12+, all type hints required (`disallow_untyped_defs = true` in mypy)
- Ruff for linting/formatting (line length 99)
- Tests in `tests/unit/` (no DB required) and `tests/integration/` (requires PostgreSQL)
- Use `uv run` for all commands
- structlog for logging, pydantic-settings for configuration

**File layout:**
```
src/maildb/
  server.py        # MCP tool handlers, _serialize_email(), _serialize_search_result()
  maildb.py        # MailDB class, query methods
  models.py        # Email, Recipients, SearchResult dataclasses
tests/unit/
  test_server.py   # Serialization tests, tool registration test
tests/integration/
  test_maildb.py   # Integration tests with PostgreSQL
```

---

## Task 1: Add field selection to `_serialize_email()` (#31)

**Files:**
- Modify: `src/maildb/server.py:70-95`
- Modify: `tests/unit/test_server.py`

- [ ] **Step 1: Write failing tests for field selection**

Add to `tests/unit/test_server.py`:

```python
from maildb.server import SERIALIZABLE_EMAIL_FIELDS


def test_serialize_email_with_fields_returns_only_requested() -> None:
    email = _make_email()
    d = _serialize_email(email, fields=frozenset({"subject", "date"}))
    assert set(d.keys()) == {"subject", "date"}
    assert d["subject"] == "Test Subject"


def test_serialize_email_with_fields_none_returns_all() -> None:
    email = _make_email()
    d = _serialize_email(email, fields=None)
    # Should have all serializable fields (no embedding, no body_html)
    assert "subject" in d
    assert "sender_address" in d
    assert "embedding" not in d
    assert "body_html" not in d


def test_serialize_email_with_invalid_field_ignores_it() -> None:
    email = _make_email()
    d = _serialize_email(email, fields=frozenset({"subject", "nonexistent_field"}))
    assert set(d.keys()) == {"subject"}


def test_serialize_search_result_with_fields() -> None:
    email = _make_email()
    sr = SearchResult(email=email, similarity=0.95)
    d = _serialize_search_result(sr, fields=frozenset({"subject", "date"}))
    assert d["similarity"] == 0.95
    assert set(d["email"].keys()) == {"subject", "date"}


def test_serializable_email_fields_constant() -> None:
    """SERIALIZABLE_EMAIL_FIELDS contains exactly the expected fields."""
    expected = {
        "id", "message_id", "thread_id", "subject", "sender_name",
        "sender_address", "sender_domain", "recipients", "date",
        "body_text", "has_attachment", "attachments", "labels",
        "in_reply_to", "references", "created_at",
    }
    assert SERIALIZABLE_EMAIL_FIELDS == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_server.py::test_serialize_email_with_fields_returns_only_requested -v`
Expected: FAIL — `_serialize_email() got an unexpected keyword argument 'fields'`

- [ ] **Step 3: Implement field selection**

In `src/maildb/server.py`, replace the serialization section (lines 70-95) with:

```python
# --- Serialization ---

SERIALIZABLE_EMAIL_FIELDS = frozenset({
    "id", "message_id", "thread_id", "subject", "sender_name",
    "sender_address", "sender_domain", "recipients", "date",
    "body_text", "has_attachment", "attachments", "labels",
    "in_reply_to", "references", "created_at",
})


def _serialize_email(
    email: Any, fields: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Convert an Email dataclass to a JSON-serializable dict."""
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


def _serialize_search_result(
    sr: Any, fields: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Convert a SearchResult to a JSON-serializable dict."""
    return {
        "email": _serialize_email(sr.email, fields),
        "similarity": sr.similarity,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_server.py -v`
Expected: All PASS

- [ ] **Step 5: Run full checks and commit**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy src/ && uv run pytest tests/unit/`

```bash
git add src/maildb/server.py tests/unit/test_server.py
git commit -m "feat: add field selection to _serialize_email and _serialize_search_result"
```

---

## Task 2: Add `fields` parameter to all email-returning tool handlers (#31)

**Files:**
- Modify: `src/maildb/server.py:130-444`

The 9 tools that return Email objects need `fields: list[str] | None = None`. Each handler converts the list to a validated `frozenset` and passes it through to `_serialize_email()` or `_serialize_search_result()`.

- [ ] **Step 1: Add `fields` to `find` handler**

In `src/maildb/server.py`, update the `find` function signature and body. Add `fields: list[str] | None = None` as the last parameter, and update the return line:

```python
def find(
    ctx: Context,
    sender: str | None = None,
    sender_domain: str | None = None,
    recipient: str | None = None,
    after: str | None = None,
    before: str | None = None,
    has_attachment: bool | None = None,
    subject_contains: str | None = None,
    labels: list[str] | None = None,
    limit: int = 50,
    order: str = "date DESC",
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
```

Update the docstring to include:
```
      fields: list of field names to return (default: all). Valid: id, message_id, thread_id,
        subject, sender_name, sender_address, sender_domain, recipients, date, body_text,
        has_attachment, attachments, labels, in_reply_to, references, created_at
```

Update the return line:
```python
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_email(e, valid) for e in results]
```

- [ ] **Step 2: Add `fields` to `search` handler**

Update `search` signature — add `fields: list[str] | None = None` as the last parameter. Update the return line:

```python
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_search_result(sr, valid) for sr in results]
```

- [ ] **Step 3: Add `fields` to `get_thread` handler**

Update `get_thread` signature — add `fields: list[str] | None = None`. Update the return line:

```python
def get_thread(ctx: Context, thread_id: str, fields: list[str] | None = None) -> list[dict[str, Any]]:
```

```python
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_email(e, valid) for e in results]
```

- [ ] **Step 4: Add `fields` to `get_thread_for` handler**

Update `get_thread_for` signature — add `fields: list[str] | None = None`. Update the return line:

```python
def get_thread_for(ctx: Context, message_id: str, fields: list[str] | None = None) -> list[dict[str, Any]]:
```

```python
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_email(e, valid) for e in results]
```

- [ ] **Step 5: Add `fields` to `topics_with` handler**

Update `topics_with` signature — add `fields: list[str] | None = None` as the last parameter. Update the return line:

```python
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_email(e, valid) for e in results]
```

- [ ] **Step 6: Add `fields` to `unreplied` handler**

Update `unreplied` signature — add `fields: list[str] | None = None` as the last parameter. Update the return line:

```python
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_email(e, valid) for e in results]
```

- [ ] **Step 7: Add `fields` to `correspondence` handler**

Update `correspondence` signature — add `fields: list[str] | None = None` as the last parameter. Update the return line:

```python
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_email(e, valid) for e in results]
```

- [ ] **Step 8: Add `fields` to `mention_search` handler**

Update `mention_search` signature — add `fields: list[str] | None = None` as the last parameter. Update the return line:

```python
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_email(e, valid) for e in results]
```

- [ ] **Step 9: Add `fields` to `cluster` handler**

Update `cluster` signature — add `fields: list[str] | None = None` as the last parameter. Update the return line:

```python
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    return [_serialize_email(e, valid) for e in results]
```

- [ ] **Step 10: Run full checks and commit**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy src/ && uv run pytest tests/unit/`

```bash
git add src/maildb/server.py
git commit -m "feat: add fields parameter to all email-returning MCP tool handlers

Closes #31"
```

---

## Task 3: Add `offset` to `MailDB` methods that use SQL LIMIT (#32)

**Files:**
- Modify: `src/maildb/maildb.py`
- Modify: `tests/integration/test_maildb.py`

Seven methods use `LIMIT %(limit)s` in SQL: `find`, `search`, `correspondence`, `mention_search`, `unreplied` (inbound + outbound), `long_threads`, and `top_contacts` (3 branches). Two methods (`topics_with`, `cluster`) use farthest-point selection where offset is applied post-selection.

- [ ] **Step 1: Write failing test for find offset**

Add to `tests/integration/test_maildb.py`:

```python
def test_find_offset(test_pool, seed_data) -> None:  # type: ignore[no-untyped-def]
    db = MailDB._from_pool(test_pool)
    all_results = db.find(limit=10)
    offset_results = db.find(limit=10, offset=2)
    assert len(offset_results) == len(all_results) - 2
    assert offset_results[0].message_id == all_results[2].message_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_maildb.py::test_find_offset -v`
Expected: FAIL — `TypeError: find() got an unexpected keyword argument 'offset'`

- [ ] **Step 3: Add `offset` to `find` method**

In `src/maildb/maildb.py`, update `find()` (lines 167-202):

Add `offset: int = 0` after `limit: int = 50` in the signature:

```python
    def find(
        self,
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        has_attachment: bool | None = None,
        subject_contains: str | None = None,
        labels: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str = "date DESC",
    ) -> list[Email]:
```

Update the query construction:
```python
        query = f"SELECT {SELECT_COLS} FROM emails WHERE {where} ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s"
        params["limit"] = limit
        params["offset"] = offset
```

- [ ] **Step 4: Add `offset` to `search` method**

In `src/maildb/maildb.py`, update `search()` (lines 204-252):

Add `offset: int = 0` after `limit: int = 20` in the signature. Update the SQL and params:

```python
            LIMIT %(limit)s OFFSET %(offset)s
```
```python
        params["limit"] = limit
        params["offset"] = offset
```

- [ ] **Step 5: Add `offset` to `correspondence` method**

In `src/maildb/maildb.py`, update `correspondence()` (lines 644-684):

Add `offset: int = 0` after `limit: int = 500` in the signature. Update the SQL and params:

```python
        sql = f"SELECT {SELECT_COLS} FROM emails WHERE {where} ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s"
```
```python
        params: dict[str, Any] = {
            "address": address,
            "address_json": json.dumps([address]),
            "limit": limit,
            "offset": offset,
        }
```

- [ ] **Step 6: Add `offset` to `mention_search` method**

In `src/maildb/maildb.py`, update `mention_search()` (lines 686-720):

Add `offset: int = 0` after `limit: int = 50` in the signature. Update the SQL and params:

```python
        sql = f"SELECT {SELECT_COLS} FROM emails WHERE {where} ORDER BY date DESC LIMIT %(limit)s OFFSET %(offset)s"
```
```python
        params: dict[str, Any] = {"pattern": pattern, "limit": limit, "offset": offset}
```

- [ ] **Step 7: Add `offset` to `unreplied` method**

In `src/maildb/maildb.py`, update `unreplied()` (lines 514-642):

Add `offset: int = 0` after `limit: int = 100` in the signature. Update both SQL branches:

In the inbound branch (around line 587):
```python
                LIMIT %(limit)s OFFSET %(offset)s
```

In the outbound branch (around line 636):
```python
                LIMIT %(limit)s OFFSET %(offset)s
```

Add to params (around line 575 and 629):
```python
            params["offset"] = offset
```

- [ ] **Step 8: Add `offset` to `long_threads` method**

In `src/maildb/maildb.py`, update `long_threads()` (lines 751-782):

Add `offset: int = 0` after `limit: int = 50` in the signature. Update params and SQL:

```python
        params: dict[str, Any] = {"min_messages": min_messages, "limit": limit, "offset": offset}
```
```python
            LIMIT %(limit)s OFFSET %(offset)s
```

- [ ] **Step 9: Add `offset` to `top_contacts` method**

In `src/maildb/maildb.py`, update `top_contacts()` (lines 281-395):

Add `offset: int = 0` after `limit: int = 10` in the signature. Add `"offset": offset` to the params dict (around line 303). Update all three SQL branches — each has `LIMIT %(limit)s`, append `OFFSET %(offset)s` to each:

Inbound branch (line 339):
```python
                LIMIT %(limit)s OFFSET %(offset)s
```

Outbound branch (line 360):
```python
                LIMIT %(limit)s OFFSET %(offset)s
```

Both branch (line 393):
```python
            LIMIT %(limit)s OFFSET %(offset)s
```

- [ ] **Step 10: Add `offset` to `topics_with` method**

In `src/maildb/maildb.py`, update `topics_with()` (lines 397-431):

Add `offset: int = 0` after `limit: int = 5` in the signature. Apply offset after farthest-point selection (line 431):

```python
        selected = self._farthest_point_select(emails, limit + offset)
        return selected[offset:]
```

- [ ] **Step 11: Add `offset` to `cluster` method**

In `src/maildb/maildb.py`, update `cluster()` (lines 460-502):

Add `offset: int = 0` after `limit: int = 5` in the signature. Apply offset after farthest-point selection (line 502):

```python
        selected = self._farthest_point_select(emails, limit + offset)
        return selected[offset:]
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_maildb.py -v`
Expected: All PASS (including new `test_find_offset`)

- [ ] **Step 13: Run full checks and commit**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy src/ && uv run pytest tests/unit/`

```bash
git add src/maildb/maildb.py tests/integration/test_maildb.py
git commit -m "feat: add offset parameter to all MailDB query methods"
```

---

## Task 4: Add `offset` parameter to all tool handlers in server.py (#32)

**Files:**
- Modify: `src/maildb/server.py`

- [ ] **Step 1: Add `offset` to `find` handler**

Add `offset: int = 0` to the `find` handler signature (after `limit`). Add `offset=offset` to the `db.find()` call. Add to docstring: `offset: skip first N results for pagination (default 0)`.

- [ ] **Step 2: Add `offset` to `search` handler**

Add `offset: int = 0` to the `search` handler signature. Add `offset=offset` to the `db.search()` call.

- [ ] **Step 3: Add `offset` to `unreplied` handler**

Add `offset: int = 0` to the `unreplied` handler signature. Add `offset=offset` to the `db.unreplied()` call.

- [ ] **Step 4: Add `offset` to `correspondence` handler**

Add `offset: int = 0` to the `correspondence` handler signature. Add `offset=offset` to the `db.correspondence()` call.

- [ ] **Step 5: Add `offset` to `mention_search` handler**

Add `offset: int = 0` to the `mention_search` handler signature. Add `offset=offset` to the `db.mention_search()` call.

- [ ] **Step 6: Add `offset` to `topics_with` handler**

Add `offset: int = 0` to the `topics_with` handler signature. Add `offset=offset` to the `db.topics_with()` call.

- [ ] **Step 7: Add `offset` to `cluster` handler**

Add `offset: int = 0` to the `cluster` handler signature. Add `offset=offset` to the `db.cluster()` call.

- [ ] **Step 8: Add `offset` to `long_threads` handler**

Add `offset: int = 0` to the `long_threads` handler signature. Add `offset=offset` to the `db.long_threads()` call.

- [ ] **Step 9: Add `offset` to `top_contacts` handler**

Add `offset: int = 0` to the `top_contacts` handler signature. Add `offset=offset` to the `db.top_contacts()` call.

- [ ] **Step 10: Run full checks and commit**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy src/ && uv run pytest tests/unit/`

```bash
git add src/maildb/server.py
git commit -m "feat: add offset parameter to all MCP tool handlers for pagination

Closes #32"
```
