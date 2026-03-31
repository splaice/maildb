# Context-Efficient MCP Response Pattern — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make maildb MCP responses context-efficient by defaulting to headers-only (no body text), adding a response wrapper with total count, providing a `get_emails` tool for body retrieval by ID, and adding recipient count filters.

**Architecture:** Four independent changes to the serialization layer (`server.py`), the query layer (`maildb.py`), and the MCP tool handlers. No schema/DDL changes. The `_serialize_email` function becomes the control point for field defaults, body truncation, and body_length computation. All list tools get a `_wrap_response` helper that adds `{total, offset, limit, results}`. A new `get_emails` tool provides ID-based full-email fetching.

**Tech Stack:** Python 3.12, psycopg3, FastMCP, pytest

**Spec:** `docs/superpowers/specs/2026-03-31-context-efficient-mcp-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/maildb/server.py` | Modify | Serialization defaults, `body_length`, `body_max_chars`, `_wrap_response`, `get_emails` tool, recipient filter params on handlers |
| `src/maildb/maildb.py` | Modify | `_build_filters` gains recipient count params, `find`/`search`/etc return `(results, total)` tuples, new `get_emails` method |
| `tests/unit/test_server.py` | Modify | Tests for serialization changes, body_length, body_max_chars, wrap_response, field defaults |
| `tests/integration/test_maildb.py` | Modify | Tests for recipient filters, total count, get_emails |

---

### Task 1: Serialization — `body_length` and headers-by-default

**Files:**
- Modify: `tests/unit/test_server.py`
- Modify: `src/maildb/server.py`

This task changes `_serialize_email` to: (1) compute `body_length`, (2) exclude `body_text` by default when no explicit `fields` are passed, and (3) support `body_max_chars` truncation.

- [ ] **Step 1: Write failing tests for body_length and headers-by-default**

Add to `tests/unit/test_server.py`:

```python
def test_serialize_email_default_includes_body_length() -> None:
    email = _make_email()
    email.body_text = "Hello world"
    d = _serialize_email(email)
    assert d["body_length"] == 11
    assert "body_text" not in d


def test_serialize_email_default_null_body_length() -> None:
    email = _make_email()
    email.body_text = None
    d = _serialize_email(email)
    assert d["body_length"] is None
    assert "body_text" not in d


def test_serialize_email_explicit_fields_with_body_text() -> None:
    email = _make_email()
    email.body_text = "Hello world"
    d = _serialize_email(email, fields=frozenset({"subject", "body_text"}))
    assert d["body_text"] == "Hello world"
    assert "body_length" not in d


def test_serialize_email_explicit_fields_with_body_length() -> None:
    email = _make_email()
    email.body_text = "Hello world"
    d = _serialize_email(email, fields=frozenset({"subject", "body_length"}))
    assert d["body_length"] == 11
    assert "body_text" not in d
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_server.py::test_serialize_email_default_includes_body_length tests/unit/test_server.py::test_serialize_email_default_null_body_length tests/unit/test_server.py::test_serialize_email_explicit_fields_with_body_text tests/unit/test_server.py::test_serialize_email_explicit_fields_with_body_length -v`

Expected: FAIL — `body_length` not in output, `body_text` is in default output.

- [ ] **Step 3: Implement headers-by-default in `_serialize_email`**

In `src/maildb/server.py`, add `body_length` to `SERIALIZABLE_EMAIL_FIELDS` and add a new constant for default fields. Then update `_serialize_email`:

```python
SERIALIZABLE_EMAIL_FIELDS = frozenset(
    {
        "id",
        "message_id",
        "thread_id",
        "subject",
        "sender_name",
        "sender_address",
        "sender_domain",
        "recipients",
        "date",
        "body_text",
        "body_length",
        "has_attachment",
        "attachments",
        "labels",
        "in_reply_to",
        "references",
        "created_at",
    }
)

# Default fields for list tools: everything except body_text (replaced by body_length)
DEFAULT_LIST_FIELDS = SERIALIZABLE_EMAIL_FIELDS - {"body_text"}


def _serialize_email(
    email: Any,
    fields: frozenset[str] | None = None,
    body_max_chars: int | None = None,
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
    # Compute body_length from raw body_text
    raw_body = d.get("body_text")
    d["body_length"] = len(raw_body) if raw_body is not None else None
    # Apply body truncation if requested
    if body_max_chars is not None and raw_body is not None and len(raw_body) > body_max_chars:
        d["body_text"] = raw_body[:body_max_chars] + "..."
        d["body_truncated"] = True
    # Apply field selection
    if fields is not None:
        d = {k: v for k, v in d.items() if k in fields}
    else:
        # Default: exclude body_text (use body_length instead)
        d = {k: v for k, v in d.items() if k in DEFAULT_LIST_FIELDS}
    return d
```

- [ ] **Step 4: Update existing tests that expect body_text in default output**

The test `test_serialize_email_with_fields_none_returns_all` expects `body_text` to not be explicitly checked but does check for `subject` and `sender_address`. Update it to also verify the new default behavior:

```python
def test_serialize_email_with_fields_none_returns_all() -> None:
    email = _make_email()
    d = _serialize_email(email, fields=None)
    # Should have all default fields (no embedding, no body_html, no body_text)
    assert "subject" in d
    assert "sender_address" in d
    assert "body_length" in d
    assert "embedding" not in d
    assert "body_html" not in d
    assert "body_text" not in d
```

Also update `test_serializable_email_fields_constant` to include `body_length`:

```python
def test_serializable_email_fields_constant() -> None:
    """SERIALIZABLE_EMAIL_FIELDS contains exactly the expected fields."""
    expected = {
        "id",
        "message_id",
        "thread_id",
        "subject",
        "sender_name",
        "sender_address",
        "sender_domain",
        "recipients",
        "date",
        "body_text",
        "body_length",
        "has_attachment",
        "attachments",
        "labels",
        "in_reply_to",
        "references",
        "created_at",
    }
    assert expected == SERIALIZABLE_EMAIL_FIELDS
```

- [ ] **Step 5: Run all server tests to verify they pass**

Run: `uv run pytest tests/unit/test_server.py -v`

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_server.py src/maildb/server.py
git commit -m "feat: headers-by-default serialization with body_length"
```

---

### Task 2: Serialization — `body_max_chars` truncation

**Files:**
- Modify: `tests/unit/test_server.py`
- Modify: `src/maildb/server.py` (already modified in Task 1)

- [ ] **Step 1: Write failing tests for body_max_chars**

Add to `tests/unit/test_server.py`:

```python
def test_serialize_email_body_max_chars_truncates() -> None:
    email = _make_email()
    email.body_text = "Hello world, this is a long email body"
    d = _serialize_email(email, fields=frozenset({"body_text", "body_truncated"}), body_max_chars=11)
    assert d["body_text"] == "Hello world..."
    assert d["body_truncated"] is True


def test_serialize_email_body_max_chars_no_truncation_needed() -> None:
    email = _make_email()
    email.body_text = "Short"
    d = _serialize_email(email, fields=frozenset({"body_text", "body_truncated"}), body_max_chars=100)
    assert d["body_text"] == "Short"
    assert "body_truncated" not in d


def test_serialize_email_body_max_chars_null_body() -> None:
    email = _make_email()
    email.body_text = None
    d = _serialize_email(email, fields=frozenset({"body_text"}), body_max_chars=10)
    assert d["body_text"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_server.py::test_serialize_email_body_max_chars_truncates tests/unit/test_server.py::test_serialize_email_body_max_chars_no_truncation_needed tests/unit/test_server.py::test_serialize_email_body_max_chars_null_body -v`

Expected: FAIL if Task 1 implementation isn't complete yet; PASS if Task 1 is done (the `body_max_chars` logic was included there). If they pass, skip to step 4.

- [ ] **Step 3: Verify `_serialize_email` handles `body_truncated` field in field selection**

The `body_truncated` field is only added when truncation occurs. It needs to be in `SERIALIZABLE_EMAIL_FIELDS` to survive field selection. Add it:

```python
SERIALIZABLE_EMAIL_FIELDS = frozenset(
    {
        "id",
        "message_id",
        "thread_id",
        "subject",
        "sender_name",
        "sender_address",
        "sender_domain",
        "recipients",
        "date",
        "body_text",
        "body_length",
        "body_truncated",
        "has_attachment",
        "attachments",
        "labels",
        "in_reply_to",
        "references",
        "created_at",
    }
)
```

Update `test_serializable_email_fields_constant` to include `body_truncated`.

- [ ] **Step 4: Run all server tests**

Run: `uv run pytest tests/unit/test_server.py -v`

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_server.py src/maildb/server.py
git commit -m "feat: add body_max_chars truncation to _serialize_email"
```

---

### Task 3: Response wrapper with total count

**Files:**
- Modify: `src/maildb/server.py`
- Modify: `src/maildb/maildb.py`
- Modify: `tests/unit/test_server.py`
- Modify: `tests/integration/test_maildb.py`

This task adds `COUNT(*) OVER()` to queries that support limit/offset, returns `(results, total)` from the DB layer, and wraps MCP tool responses in `{total, offset, limit, results}`.

- [ ] **Step 1: Write failing unit test for `_wrap_response` helper**

Add to `tests/unit/test_server.py`:

```python
from maildb.server import _wrap_response


def test_wrap_response() -> None:
    results = [{"a": 1}, {"a": 2}]
    wrapped = _wrap_response(results, total=10, offset=0, limit=50)
    assert wrapped == {"total": 10, "offset": 0, "limit": 50, "results": results}


def test_wrap_response_empty() -> None:
    wrapped = _wrap_response([], total=0, offset=0, limit=50)
    assert wrapped == {"total": 0, "offset": 0, "limit": 50, "results": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_server.py::test_wrap_response tests/unit/test_server.py::test_wrap_response_empty -v`

Expected: FAIL — `_wrap_response` does not exist.

- [ ] **Step 3: Implement `_wrap_response` in `server.py`**

Add to `src/maildb/server.py`:

```python
def _wrap_response(
    results: list[dict[str, Any]],
    *,
    total: int,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """Wrap a list of results with pagination metadata."""
    return {"total": total, "offset": offset, "limit": limit, "results": results}
```

- [ ] **Step 4: Run unit tests to verify pass**

Run: `uv run pytest tests/unit/test_server.py::test_wrap_response tests/unit/test_server.py::test_wrap_response_empty -v`

Expected: PASS.

- [ ] **Step 5: Modify `MailDB.find` to return `(list[Email], int)` using `COUNT(*) OVER()`**

In `src/maildb/maildb.py`, update the `find` method:

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
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
    ) -> tuple[list[Email], int]:
        """Structured query with dynamic WHERE clauses. Returns (emails, total_count)."""
        if order not in VALID_ORDERS:
            msg = f"Invalid order '{order}'. Must be one of: {', '.join(sorted(VALID_ORDERS))}"
            raise ValueError(msg)

        conditions, params = self._build_filters(
            sender=sender,
            sender_domain=sender_domain,
            recipient=recipient,
            after=after,
            before=before,
            has_attachment=has_attachment,
            subject_contains=subject_contains,
            labels=labels,
            max_to=max_to,
            max_cc=max_cc,
            max_recipients=max_recipients,
            direct_only=direct_only,
        )

        where = " AND ".join(conditions) if conditions else "TRUE"
        query = f"SELECT {SELECT_COLS}, COUNT(*) OVER() AS _total FROM emails WHERE {where} ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s"
        params["limit"] = limit
        params["offset"] = offset

        rows = _query_dicts(self._pool, query, params)
        total = rows[0]["_total"] if rows else 0
        for row in rows:
            row.pop("_total", None)
        return [Email.from_row(row) for row in rows], total
```

Note: The recipient filter params (`max_to`, etc.) are included here but `_build_filters` won't support them until Task 4. Implement this step with the params passed through but `_build_filters` unchanged for now — the new params will simply be unused kwargs. Actually, to avoid errors, add the params to `_build_filters` signature in this step but with no logic (just accept and ignore them). The logic will be added in Task 4.

- [ ] **Step 6: Add `max_to`, `max_cc`, `max_recipients`, `direct_only` as no-op params to `_build_filters`**

In `src/maildb/maildb.py`, update `_build_filters` signature only (no logic yet):

```python
    @staticmethod
    def _build_filters(
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        has_attachment: bool | None = None,
        subject_contains: str | None = None,
        labels: list[str] | None = None,
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
    ) -> tuple[list[str], dict[str, Any]]:
```

The body stays the same — these params are accepted but ignored until Task 4.

- [ ] **Step 7: Apply the same `(list, total)` return pattern to other DB methods**

Update the following methods in `src/maildb/maildb.py` to add `COUNT(*) OVER() AS _total` and return `tuple[list[Email], int]`:

- `search` — add `COUNT(*) OVER() AS _total` to the SELECT, return `(list[SearchResult], int)`
- `correspondence` — same pattern
- `mention_search` — same pattern
- `unreplied` — same pattern (add to the SELECT in both inbound and outbound branches)

For `topics_with` and `cluster` (which do Python-side selection), the total is the count of DB rows before selection:

```python
    def topics_with(self, ...) -> tuple[list[Email], int]:
        ...
        rows = _query_dicts(self._pool, sql, params)
        if not rows:
            return [], 0
        total = len(rows)
        emails = [Email.from_row(row) for row in rows]
        selected = self._farthest_point_select(emails, limit + offset)
        return selected[offset:], total
```

For `long_threads` and `top_contacts` which return `list[dict]`, same pattern — add `COUNT(*) OVER()` and return `(list[dict], int)`.

- [ ] **Step 8: Update all MCP tool handlers in `server.py` to use `_wrap_response`**

Update each tool handler to unpack `(results, total)` and wrap. Example for `find`:

```python
@mcp.tool()
@log_tool
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
    offset: int = 0,
    order: str = "date DESC",
    fields: list[str] | None = None,
    max_to: int | None = None,
    max_cc: int | None = None,
    max_recipients: int | None = None,
    direct_only: bool = False,
) -> dict[str, Any]:
    db = _get_db(ctx)
    results, total = db.find(
        sender=sender,
        sender_domain=sender_domain,
        recipient=recipient,
        after=after,
        before=before,
        has_attachment=has_attachment,
        subject_contains=subject_contains,
        labels=labels,
        limit=limit,
        offset=offset,
        order=order,
        max_to=max_to,
        max_cc=max_cc,
        max_recipients=max_recipients,
        direct_only=direct_only,
    )
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    serialized = [_serialize_email(e, valid) for e in results]
    return _wrap_response(serialized, total=total, offset=offset, limit=limit)
```

Apply the same pattern to: `search`, `correspondence`, `mention_search`, `unreplied`, `topics_with`, `cluster`, `long_threads`, `top_contacts`. Each handler now returns `dict[str, Any]` instead of `list[...]`.

Update `log_tool` decorator to handle the new dict return shape for row counting:

```python
        # Compute result stats
        if isinstance(result, dict) and "results" in result:
            row_count = len(result["results"])
        elif isinstance(result, list):
            row_count = len(result)
        else:
            row_count = 1
```

- [ ] **Step 9: Write integration test for total count on find**

Add to `tests/integration/test_maildb.py`:

```python
def test_find_returns_total(test_pool, seed_emails) -> None:
    db = MailDB._from_pool(test_pool)
    results, total = db.find(limit=1)
    assert len(results) == 1
    assert total == 3  # seed_emails has 3 emails
```

- [ ] **Step 10: Update existing integration tests for new return signatures**

All existing integration tests that call `db.find(...)`, `db.search(...)`, `db.correspondence(...)`, `db.mention_search(...)`, `db.unreplied(...)`, `db.topics_with(...)`, `db.cluster(...)`, `db.long_threads(...)`, `db.top_contacts(...)` need to unpack the tuple.

For example, change:
```python
results = db.find(sender="alice@example.com")
assert len(results) == 1
```
to:
```python
results, total = db.find(sender="alice@example.com")
assert len(results) == 1
```

Apply this to every test that calls these methods. The `total` value can be ignored in most existing tests — just unpack it.

- [ ] **Step 11: Update `test_mcp_has_all_tools` if needed**

No change needed yet — `get_emails` is added in Task 5.

- [ ] **Step 12: Run full test suite**

Run: `uv run just test`

Expected: All PASS.

- [ ] **Step 13: Commit**

```bash
git add src/maildb/server.py src/maildb/maildb.py tests/unit/test_server.py tests/integration/test_maildb.py
git commit -m "feat: add response wrapper with total count to all list tools"
```

---

### Task 4: Recipient count filters

**Files:**
- Modify: `src/maildb/maildb.py`
- Modify: `tests/integration/test_maildb.py`

- [ ] **Step 1: Write failing integration tests for recipient filters**

Add new seed data and tests to `tests/integration/test_maildb.py`:

```python
@pytest.fixture
def seed_recipient_counts(test_pool):
    """Seed data for recipient count filter tests."""
    emails = [
        # Direct message: 1 To, 0 CC
        {
            "message_id": "rcpt-1@example.com",
            "thread_id": "rcpt-1@example.com",
            "subject": "Direct message",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["bob@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 1, 10, 0, tzinfo=UTC),
            "body_text": "Just for you.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": None,
            "references": [],
            "embedding": None,
        },
        # Group message: 2 To, 1 CC
        {
            "message_id": "rcpt-2@example.com",
            "thread_id": "rcpt-2@example.com",
            "subject": "Group thread",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({
                "to": ["bob@example.com", "carol@example.com"],
                "cc": ["dave@example.com"],
                "bcc": [],
            }),
            "date": datetime(2025, 1, 2, 10, 0, tzinfo=UTC),
            "body_text": "Group discussion.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": None,
            "references": [],
            "embedding": None,
        },
        # BCC message: 1 To, 0 CC, 1 BCC
        {
            "message_id": "rcpt-3@example.com",
            "thread_id": "rcpt-3@example.com",
            "subject": "BCC message",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({
                "to": ["bob@example.com"],
                "cc": [],
                "bcc": ["secret@example.com"],
            }),
            "date": datetime(2025, 1, 3, 10, 0, tzinfo=UTC),
            "body_text": "Secret copy.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": None,
            "references": [],
            "embedding": None,
        },
    ]

    insert_sql = """
    INSERT INTO emails (
        message_id, thread_id, subject, sender_name, sender_address, sender_domain,
        recipients, date, body_text, body_html, has_attachment, attachments,
        labels, in_reply_to, "references", embedding
    ) VALUES (
        %(message_id)s, %(thread_id)s, %(subject)s, %(sender_name)s, %(sender_address)s,
        %(sender_domain)s, %(recipients)s, %(date)s, %(body_text)s, %(body_html)s,
        %(has_attachment)s, %(attachments)s, %(labels)s, %(in_reply_to)s,
        %(references)s, %(embedding)s
    )
    """

    with test_pool.connection() as conn:
        for e in emails:
            conn.execute(insert_sql, e)
        conn.commit()


def test_find_direct_only(test_pool, seed_recipient_counts) -> None:
    db = MailDB._from_pool(test_pool)
    results, total = db.find(sender="alice@example.com", direct_only=True)
    # rcpt-1 (1 To, 0 CC) and rcpt-3 (1 To, 0 CC, 1 BCC — BCC unconstrained)
    message_ids = [e.message_id for e in results]
    assert "rcpt-1@example.com" in message_ids
    assert "rcpt-3@example.com" in message_ids
    assert "rcpt-2@example.com" not in message_ids
    assert total == 2


def test_find_max_to(test_pool, seed_recipient_counts) -> None:
    db = MailDB._from_pool(test_pool)
    results, total = db.find(sender="alice@example.com", max_to=1)
    message_ids = [e.message_id for e in results]
    assert "rcpt-1@example.com" in message_ids
    assert "rcpt-3@example.com" in message_ids
    assert "rcpt-2@example.com" not in message_ids


def test_find_max_cc(test_pool, seed_recipient_counts) -> None:
    db = MailDB._from_pool(test_pool)
    results, total = db.find(sender="alice@example.com", max_cc=0)
    message_ids = [e.message_id for e in results]
    assert "rcpt-1@example.com" in message_ids
    assert "rcpt-3@example.com" in message_ids
    assert "rcpt-2@example.com" not in message_ids


def test_find_max_recipients(test_pool, seed_recipient_counts) -> None:
    db = MailDB._from_pool(test_pool)
    results, total = db.find(sender="alice@example.com", max_recipients=2)
    message_ids = [e.message_id for e in results]
    # rcpt-1: 1 total, rcpt-3: 2 total (1 To + 1 BCC), rcpt-2: 3 total
    assert "rcpt-1@example.com" in message_ids
    assert "rcpt-3@example.com" in message_ids
    assert "rcpt-2@example.com" not in message_ids


def test_find_direct_only_conflicts_with_max_to(test_pool, seed_recipient_counts) -> None:
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="direct_only"):
        db.find(direct_only=True, max_to=2)


def test_find_direct_only_conflicts_with_max_cc(test_pool, seed_recipient_counts) -> None:
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="direct_only"):
        db.find(direct_only=True, max_cc=1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maildb.py::test_find_direct_only tests/integration/test_maildb.py::test_find_max_to tests/integration/test_maildb.py::test_find_max_cc tests/integration/test_maildb.py::test_find_max_recipients tests/integration/test_maildb.py::test_find_direct_only_conflicts_with_max_to tests/integration/test_maildb.py::test_find_direct_only_conflicts_with_max_cc -v`

Expected: FAIL — filters not implemented.

- [ ] **Step 3: Implement recipient count filters in `_build_filters`**

In `src/maildb/maildb.py`, update `_build_filters` to add the filter logic:

```python
    @staticmethod
    def _build_filters(
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        has_attachment: bool | None = None,
        subject_contains: str | None = None,
        labels: list[str] | None = None,
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
    ) -> tuple[list[str], dict[str, Any]]:
        """Build WHERE-clause conditions and params from common filter kwargs."""
        if direct_only and (max_to is not None or max_cc is not None):
            msg = "Cannot combine direct_only with max_to or max_cc"
            raise ValueError(msg)

        if direct_only:
            max_to = 1
            max_cc = 0

        conditions: list[str] = []
        params: dict[str, Any] = {}

        if sender is not None:
            conditions.append("sender_address = %(sender)s")
            params["sender"] = sender
        if sender_domain is not None:
            conditions.append("sender_domain = %(sender_domain)s")
            params["sender_domain"] = sender_domain
        if recipient is not None:
            conditions.append(
                "(recipients->'to' @> %(recipient_json)s "
                "OR recipients->'cc' @> %(recipient_json)s "
                "OR recipients->'bcc' @> %(recipient_json)s)"
            )
            params["recipient_json"] = json.dumps([recipient])
        if after is not None:
            conditions.append("date >= %(after)s")
            params["after"] = after
        if before is not None:
            conditions.append("date < %(before)s")
            params["before"] = before
        if has_attachment is not None:
            conditions.append("has_attachment = %(has_attachment)s")
            params["has_attachment"] = has_attachment
        if subject_contains is not None:
            conditions.append("subject ILIKE %(subject_pattern)s ESCAPE '\\'")
            escaped = (
                subject_contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            params["subject_pattern"] = f"%{escaped}%"
        if labels is not None:
            conditions.append("labels @> %(labels)s")
            params["labels"] = labels
        if max_to is not None:
            conditions.append(
                "jsonb_array_length(COALESCE(recipients->'to', '[]'::jsonb)) <= %(max_to)s"
            )
            params["max_to"] = max_to
        if max_cc is not None:
            conditions.append(
                "jsonb_array_length(COALESCE(recipients->'cc', '[]'::jsonb)) <= %(max_cc)s"
            )
            params["max_cc"] = max_cc
        if max_recipients is not None:
            conditions.append(
                "(jsonb_array_length(COALESCE(recipients->'to', '[]'::jsonb))"
                " + jsonb_array_length(COALESCE(recipients->'cc', '[]'::jsonb))"
                " + jsonb_array_length(COALESCE(recipients->'bcc', '[]'::jsonb))"
                ") <= %(max_recipients)s"
            )
            params["max_recipients"] = max_recipients

        return conditions, params
```

- [ ] **Step 4: Run integration tests to verify they pass**

Run: `uv run pytest tests/integration/test_maildb.py::test_find_direct_only tests/integration/test_maildb.py::test_find_max_to tests/integration/test_maildb.py::test_find_max_cc tests/integration/test_maildb.py::test_find_max_recipients tests/integration/test_maildb.py::test_find_direct_only_conflicts_with_max_to tests/integration/test_maildb.py::test_find_direct_only_conflicts_with_max_cc -v`

Expected: All PASS.

- [ ] **Step 5: Propagate recipient filter params to other DB methods that use `_build_filters`**

Add `max_to`, `max_cc`, `max_recipients`, `direct_only` parameters to: `search`, `correspondence`, `mention_search`, `unreplied`. Each method already calls `_build_filters` — just pass the new params through.

Also add these params to the corresponding MCP tool handlers in `server.py` and pass them through to the DB methods.

- [ ] **Step 6: Run full test suite**

Run: `uv run just test`

Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add src/maildb/maildb.py src/maildb/server.py tests/integration/test_maildb.py
git commit -m "feat: add recipient count filters (max_to, max_cc, max_recipients, direct_only)"
```

---

### Task 5: New `get_emails` tool

**Files:**
- Modify: `src/maildb/maildb.py`
- Modify: `src/maildb/server.py`
- Modify: `tests/integration/test_maildb.py`
- Modify: `tests/unit/test_server.py`

- [ ] **Step 1: Write failing integration test for `MailDB.get_emails`**

Add to `tests/integration/test_maildb.py`:

```python
def test_get_emails_by_message_ids(test_pool, seed_emails) -> None:
    db = MailDB._from_pool(test_pool)
    results = db.get_emails(["find-test-1@example.com", "find-test-3@stripe.com"])
    assert len(results) == 2
    # Should preserve input order
    assert results[0].message_id == "find-test-1@example.com"
    assert results[1].message_id == "find-test-3@stripe.com"


def test_get_emails_missing_ids_skipped(test_pool, seed_emails) -> None:
    db = MailDB._from_pool(test_pool)
    results = db.get_emails(["find-test-1@example.com", "nonexistent@x.com"])
    assert len(results) == 1
    assert results[0].message_id == "find-test-1@example.com"


def test_get_emails_empty_list(test_pool, seed_emails) -> None:
    db = MailDB._from_pool(test_pool)
    results = db.get_emails([])
    assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maildb.py::test_get_emails_by_message_ids tests/integration/test_maildb.py::test_get_emails_missing_ids_skipped tests/integration/test_maildb.py::test_get_emails_empty_list -v`

Expected: FAIL — `get_emails` method does not exist.

- [ ] **Step 3: Implement `MailDB.get_emails`**

Add to `src/maildb/maildb.py`:

```python
    def get_emails(self, message_ids: list[str]) -> list[Email]:
        """Fetch full email objects by message_id, preserving input order."""
        if not message_ids:
            return []
        placeholders = ", ".join(f"%(mid_{i})s" for i in range(len(message_ids)))
        params: dict[str, Any] = {f"mid_{i}": mid for i, mid in enumerate(message_ids)}
        sql = f"SELECT {SELECT_COLS} FROM emails WHERE message_id IN ({placeholders})"
        rows = _query_dicts(self._pool, sql, params)
        emails_by_id: dict[str, Email] = {}
        for row in rows:
            email = Email.from_row(row)
            emails_by_id[email.message_id] = email
        return [emails_by_id[mid] for mid in message_ids if mid in emails_by_id]
```

- [ ] **Step 4: Run integration tests to verify they pass**

Run: `uv run pytest tests/integration/test_maildb.py::test_get_emails_by_message_ids tests/integration/test_maildb.py::test_get_emails_missing_ids_skipped tests/integration/test_maildb.py::test_get_emails_empty_list -v`

Expected: All PASS.

- [ ] **Step 5: Write failing unit test for `get_emails` MCP tool handler**

Add to `tests/unit/test_server.py`:

```python
def test_mcp_has_get_emails_tool() -> None:
    tool_names = set(mcp._tool_manager._tools.keys())
    assert "get_emails" in tool_names
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_server.py::test_mcp_has_get_emails_tool -v`

Expected: FAIL — tool not registered.

- [ ] **Step 7: Implement `get_emails` MCP tool handler**

Add to `src/maildb/server.py`:

```python
@mcp.tool()
@log_tool
def get_emails(
    ctx: Context,
    ids: list[str],
    body_max_chars: int | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch full email objects by message ID, with optional body truncation.

    Parameters:
      ids: list of RFC 2822 Message-ID strings
      body_max_chars: truncate body_text to N characters (None = full body).
        When truncated, body_truncated=true is added.
      fields: list of field names to return (default: all including body_text)

    Returns {total, results: [{email}, ...]}.
    Results include body_text by default. Order matches input ids list.

    Example: get_emails(ids=["abc@mail.gmail.com", "def@mail.gmail.com"], body_max_chars=500)
    """
    db = _get_db(ctx)
    results = db.get_emails(ids)
    valid = frozenset(fields) & SERIALIZABLE_EMAIL_FIELDS if fields else None
    # For get_emails, include body_text by default (unlike list tools)
    serialized = [
        _serialize_email(e, fields=valid or SERIALIZABLE_EMAIL_FIELDS, body_max_chars=body_max_chars)
        for e in results
    ]
    return _wrap_response(serialized, total=len(serialized), offset=0, limit=len(ids))
```

- [ ] **Step 8: Update `test_mcp_has_all_tools` to include `get_emails`**

```python
def test_mcp_has_all_tools() -> None:
    tool_names = set(mcp._tool_manager._tools.keys())

    expected = {
        "find",
        "search",
        "get_thread",
        "get_thread_for",
        "top_contacts",
        "topics_with",
        "unreplied",
        "long_threads",
        "correspondence",
        "mention_search",
        "query",
        "cluster",
        "get_emails",
    }

    assert expected <= tool_names, f"Missing tools: {expected - tool_names}"
```

- [ ] **Step 9: Run full test suite**

Run: `uv run just test`

Expected: All PASS.

- [ ] **Step 10: Commit**

```bash
git add src/maildb/maildb.py src/maildb/server.py tests/integration/test_maildb.py tests/unit/test_server.py
git commit -m "feat: add get_emails tool for ID-based email retrieval with body truncation"
```

---

### Task 6: Update tool docstrings and run final check

**Files:**
- Modify: `src/maildb/server.py`

- [ ] **Step 1: Update docstrings on all modified MCP tool handlers**

Update the docstrings for `find`, `search`, `correspondence`, `mention_search`, `unreplied` to document:
- New params: `max_to`, `max_cc`, `max_recipients`, `direct_only`
- New return shape: `{total, offset, limit, results: [...]}`
- Default behavior: `body_text` excluded, `body_length` included
- `fields` override to get `body_text` when needed

Example for `find`:

```python
    """Search emails by structured attribute filters.

    Parameters:
      sender: exact email address (e.g. "alice@acme.com")
      sender_domain: domain portion (e.g. "acme.com")
      recipient: address in To/CC/BCC fields
      after: ISO date string, inclusive (e.g. "2025-01-01")
      before: ISO date string, exclusive
      has_attachment: filter by attachment presence
      subject_contains: case-insensitive substring match in subject
      labels: array containment filter (AND logic, e.g. ["INBOX", "Finance"])
      max_to: max number of To recipients (e.g. 1 for direct messages)
      max_cc: max number of CC recipients (e.g. 0 for no-CC messages)
      max_recipients: max total recipients across To + CC + BCC
      direct_only: shorthand for max_to=1, max_cc=0 (cannot combine with max_to/max_cc)
      limit: max results (default 50)
      offset: skip first N results for pagination (default 0)
      order: "date DESC" | "date ASC" | "sender_address ASC" | "sender_address DESC"
      fields: list of field names to return. Default returns headers + body_length (no body_text).
        Pass ["body_text", ...] to include body content.

    Returns {total, offset, limit, results: [{email headers + body_length}, ...]}.

    Example: find(sender="disney@postmates.com", direct_only=True, limit=100)
    """
```

- [ ] **Step 2: Run `uv run just check`**

Run: `uv run just check`

Expected: fmt, lint, and tests all pass.

- [ ] **Step 3: Commit**

```bash
git add src/maildb/server.py
git commit -m "docs: update MCP tool docstrings for new response shape and filters"
```
