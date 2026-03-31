# Sprint 3: API Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a two-tier API redesign: enhance existing Tier 1 purpose-built methods (#16-#20), add a Tier 2 generalized JSON DSL (#22-#23), add cluster() (#21), and update all documentation (#24-#26).

**Architecture:** Tier 1 tools handle correlated subqueries and embedding operations via dedicated methods. Tier 2 provides a generalized JSON DSL (PyPika-backed) for flat filtering, aggregation, and grouping. Each issue gets a branch `sprint3/<N>-<slug>` and a PR with `Closes #N`.

**Tech Stack:** Python 3.12, psycopg3, Pydantic v2, pytest, structlog, FastMCP

**Note on DSL implementation:** Issue #22 specifies PyPika as the SQL builder. During planning, we determined that PyPika's parameterization model (inline literals or its `Parameter` class) does not cleanly integrate with psycopg's `%(name)s` parameter style. The DSL engine instead uses direct parameterized SQL string construction with strict column/operator whitelists. This achieves the same safety guarantees (no SQL injection surface) without the impedance mismatch. No PyPika dependency is needed.

**Commands:**
- Test: `uv run just test`
- Unit only: `uv run just test-unit`
- Integration: `uv run just test-integration`
- Single test: `uv run pytest tests/path/test.py::test_name -v`
- Full check: `uv run just check`

**Parallelization:** Tasks 2-6 (issues #16-#20) are fully independent and can be executed in parallel. Task 7 (#22) must complete before Tasks 8 (#23) and 9 (#21). Tasks 10-11 (#24, #26) come last.

---

## File Map

| File | Role | Tasks |
|------|------|-------|
| `src/maildb/maildb.py` | Core library methods | 2-6, 8-9 |
| `src/maildb/server.py` | MCP tool definitions | 2-6, 8-10 |
| `src/maildb/dsl.py` | **New** — DSL parser/SQL generator | 7 |
| `tests/integration/test_maildb.py` | Integration tests for library methods | 2-6, 8-9 |
| `tests/unit/test_dsl.py` | **New** — Unit tests for DSL engine | 7 |
| `tests/integration/test_dsl.py` | **New** — Integration tests for DSL | 7-8 |
| `tests/unit/test_server.py` | MCP tool registration tests | 2-6, 8-10 |
| `docs/superpowers/specs/2026-03-30-api-redesign-design.md` | **New** — Design spec | 1 |
| `skills/using-maildb/SKILL.md` | AI agent skill reference | 11 |

---

## Task 1: Write Design Spec (Issue #25)

**Branch:** `sprint3/25-design-spec`
**Files:**
- Create: `docs/superpowers/specs/2026-03-30-api-redesign-design.md`

- [ ] **Step 1: Write the design spec document**

Create `docs/superpowers/specs/2026-03-30-api-redesign-design.md` with these sections:

```markdown
# Sprint 3: API Redesign Design Spec

## Architecture

Two-tier design:
- **Tier 1** — Purpose-built methods for correlated subqueries and embedding operations
- **Tier 2** — Generalized JSON DSL for flat filtering, aggregation, and grouping

### Why this split
Tier 1 handles patterns that require correlated subqueries (`unreplied()` uses NOT EXISTS),
embedding operations (`cluster()`, `search()`), or JSONB unnesting with specific semantics
(`correspondence()`, `top_contacts()`). These are hard to express safely in a general DSL.

Tier 2 covers the long tail: ad-hoc aggregation, grouping, date extraction, and filtering
that would otherwise require raw SQL. Uses PyPika for safe parameterized SQL generation.

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
{from, select, where, group_by, having, order_by, limit, offset}

### Sources
- `emails`: Base table columns
- `sent_to`: emails + recipient_address, recipient_domain, recipient_type (CTE)
- `email_labels`: emails + label (CTE)

### Operators
eq, neq, gt, gte, lt, lte, ilike, not_ilike, in, not_in, contains, is_null
Boolean: and, or, not

### Aggregations
count, count_distinct, min, max, sum, array_agg_distinct, date_trunc

### Guardrails
- Read-only (SELECT only via PyPika)
- 5s statement timeout
- 1000-row hard cap
- Column whitelist per source
- No body_text in grouped selects
- Parameterized queries

## Design Decisions

1. **JSON DSL vs SQL:** JSON is safe to expose via MCP — no injection surface. PyPika compiles to parameterized SQL internally.
2. **cluster() uses message_ids:** Enables chaining — e.g., unreplied() output fed to cluster() for diverse topic extraction.
3. **Virtual sources as CTEs:** sent_to and email_labels use CTEs with LATERAL/unnest to flatten JSONB and arrays, avoiding complex unnesting in WHERE clauses.
4. **Default body_text truncation:** Non-aggregation queries without explicit select return body_text truncated to 500 chars as body_preview.

## Session Query Replay Validation

All 10 original session queries are coverable by the new API.
Queries 8 and 9 (aggregation/grouping) map to the Tier 2 DSL.
```

- [ ] **Step 2: Commit**

```bash
git checkout -b sprint3/25-design-spec
git add docs/superpowers/specs/2026-03-30-api-redesign-design.md
git commit -m "docs: add Sprint 3 API redesign design spec

Closes #25"
```

---

## Task 2: Enhance unreplied() with Direction (Issue #16)

**Branch:** `sprint3/16-unreplied-direction`
**Files:**
- Modify: `src/maildb/maildb.py:439-493` — add `direction` and `recipient` params
- Modify: `src/maildb/server.py:177-191` — expose new params in MCP tool
- Test: `tests/integration/test_maildb.py` — add outbound direction tests
- No changes to `tests/unit/test_server.py` — tool name `unreplied` is unchanged
- [ ] **Step 1: Write failing integration tests for outbound unreplied**

Add to `tests/integration/test_maildb.py`. Requires new seed data with outbound messages where recipient didn't reply:

```python
@pytest.fixture
def seed_unreplied_outbound(test_pool):  # type: ignore[no-untyped-def]
    """Seed data for outbound unreplied tests. user=alice@example.com."""
    emails = [
        # Alice sends to Dave — Dave never replies (unreplied outbound)
        {
            "message_id": "unr-out-1@example.com",
            "thread_id": "unr-out-1@example.com",
            "subject": "Follow up",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["dave@corp.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 2, 1, 10, 0, tzinfo=UTC),
            "body_text": "Following up on our chat.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["Sent"],
            "in_reply_to": None,
            "references": [],
            "embedding": None,
        },
        # Alice sends to Eve — Eve replies (NOT unreplied)
        {
            "message_id": "unr-out-2@example.com",
            "thread_id": "unr-out-2@example.com",
            "subject": "Question",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps({"to": ["eve@corp.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 2, 2, 10, 0, tzinfo=UTC),
            "body_text": "Quick question about the report.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["Sent"],
            "in_reply_to": None,
            "references": [],
            "embedding": None,
        },
        # Eve replies to Alice
        {
            "message_id": "unr-out-3@corp.com",
            "thread_id": "unr-out-2@example.com",
            "subject": "Re: Question",
            "sender_name": "Eve",
            "sender_address": "eve@corp.com",
            "sender_domain": "corp.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 2, 3, 10, 0, tzinfo=UTC),
            "body_text": "Here's the answer.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": "unr-out-2@example.com",
            "references": ["unr-out-2@example.com"],
            "embedding": None,
        },
        # Inbound from Frank — Alice never replies (unreplied inbound)
        {
            "message_id": "unr-in-1@corp.com",
            "thread_id": "unr-in-1@corp.com",
            "subject": "Hey",
            "sender_name": "Frank",
            "sender_address": "frank@corp.com",
            "sender_domain": "corp.com",
            "recipients": json.dumps({"to": ["alice@example.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 2, 5, 10, 0, tzinfo=UTC),
            "body_text": "Are you free?",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": None,
            "references": [],
            "embedding": None,
        },
        # Alice sends to Dave via CC — Dave never replies (unreplied outbound via CC)
        {
            "message_id": "unr-out-4@example.com",
            "thread_id": "unr-out-4@example.com",
            "subject": "FYI",
            "sender_name": "Alice",
            "sender_address": "alice@example.com",
            "sender_domain": "example.com",
            "recipients": json.dumps(
                {"to": ["eve@corp.com"], "cc": ["dave@corp.com"], "bcc": []}
            ),
            "date": datetime(2025, 2, 6, 10, 0, tzinfo=UTC),
            "body_text": "FYI on this topic.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["Sent"],
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


def test_unreplied_outbound(test_pool, seed_unreplied_outbound) -> None:
    """Outbound unreplied: Alice's messages where recipient didn't reply."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    results = db.unreplied(direction="outbound")
    msg_ids = [e.message_id for e in results]
    # unr-out-1: Dave never replied; unr-out-4: Dave (CC) never replied
    assert "unr-out-1@example.com" in msg_ids
    assert "unr-out-4@example.com" in msg_ids
    # unr-out-2: Eve replied, so should NOT appear
    assert "unr-out-2@example.com" not in msg_ids


def test_unreplied_outbound_with_recipient(test_pool, seed_unreplied_outbound) -> None:
    """Outbound unreplied filtered to specific recipient."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    results = db.unreplied(direction="outbound", recipient="dave@corp.com")
    msg_ids = [e.message_id for e in results]
    assert "unr-out-1@example.com" in msg_ids
    assert "unr-out-4@example.com" in msg_ids


def test_unreplied_inbound_default(test_pool, seed_unreplied_outbound) -> None:
    """Default direction=inbound still works — backward compatible."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    results = db.unreplied()
    msg_ids = [e.message_id for e in results]
    assert "unr-in-1@corp.com" in msg_ids
    # Outbound messages should not appear for inbound direction
    assert "unr-out-1@example.com" not in msg_ids


def test_unreplied_outbound_multi_recipient_partial_reply(
    test_pool, seed_unreplied_outbound,
) -> None:
    """Thread with multiple recipients — only some replied.

    unr-out-4: Alice sent to Eve (to) and Dave (cc). Eve didn't reply to that thread.
    Should appear as unreplied for recipient=dave@corp.com.
    Should NOT appear for recipient=eve@corp.com if Eve replied in that thread.
    """
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)

    # Dave never replied to unr-out-4 thread
    results_dave = db.unreplied(direction="outbound", recipient="dave@corp.com")
    msg_ids_dave = [e.message_id for e in results_dave]
    assert "unr-out-4@example.com" in msg_ids_dave

    # Eve also never replied to unr-out-4 thread (she's only in 'to')
    results_eve = db.unreplied(direction="outbound", recipient="eve@corp.com")
    msg_ids_eve = [e.message_id for e in results_eve]
    # unr-out-4 has eve in 'to', and eve never replied to THAT thread
    assert "unr-out-4@example.com" in msg_ids_eve
    # But unr-out-2 (eve replied in that thread) should NOT appear
    assert "unr-out-2@example.com" not in msg_ids_eve
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maildb.py::test_unreplied_outbound -v`
Expected: TypeError — `unreplied() got an unexpected keyword argument 'direction'`

- [ ] **Step 3: Implement direction and recipient params in unreplied()**

In `src/maildb/maildb.py`, replace the `unreplied()` method:

```python
def unreplied(
    self,
    *,
    direction: str = "inbound",
    recipient: str | None = None,
    after: str | None = None,
    before: str | None = None,
    sender: str | None = None,
    sender_domain: str | None = None,
    limit: int = 100,
) -> list[Email]:
    """Messages with no reply in the same thread.

    direction='inbound': messages FROM others where user never replied.
    direction='outbound': messages FROM user where recipient never replied.
    """
    user_email = self._require_user_email()

    if direction not in ("inbound", "outbound"):
        msg = f"direction must be 'inbound' or 'outbound', got '{direction}'"
        raise ValueError(msg)

    select_cols_aliased = """
        e.id, e.message_id, e.thread_id, e.subject, e.sender_name, e.sender_address,
        e.sender_domain, e.recipients, e.date, e.body_text, e.body_html, e.has_attachment,
        e.attachments, e.labels, e.in_reply_to, e."references", e.embedding, e.created_at
    """

    conditions: list[str] = []
    params: dict[str, Any] = {"user_email": user_email, "limit": limit}

    if after:
        conditions.append("e.date >= %(after)s")
        params["after"] = after
    if before:
        conditions.append("e.date < %(before)s")
        params["before"] = before

    if direction == "inbound":
        conditions.append("e.sender_address != %(user_email)s")
        if sender:
            conditions.append("e.sender_address = %(sender)s")
            params["sender"] = sender
        if sender_domain:
            conditions.append("e.sender_domain = %(sender_domain)s")
            params["sender_domain"] = sender_domain

        not_exists = """
            NOT EXISTS (
                SELECT 1 FROM emails reply
                WHERE reply.thread_id = e.thread_id
                  AND reply.sender_address = %(user_email)s
                  AND reply.date > e.date
            )
        """
    else:  # outbound
        conditions.append("e.sender_address = %(user_email)s")

        if recipient:
            conditions.append(
                "(e.recipients->'to' @> %(recipient_json)s "
                "OR e.recipients->'cc' @> %(recipient_json)s "
                "OR e.recipients->'bcc' @> %(recipient_json)s)"
            )
            params["recipient_json"] = json.dumps([recipient])
            # No reply from this specific recipient
            not_exists = """
                NOT EXISTS (
                    SELECT 1 FROM emails reply
                    WHERE reply.thread_id = e.thread_id
                      AND reply.sender_address = %(recipient)s
                      AND reply.date > e.date
                )
            """
            params["recipient"] = recipient
        else:
            # No reply from anyone in the same thread
            not_exists = """
                NOT EXISTS (
                    SELECT 1 FROM emails reply
                    WHERE reply.thread_id = e.thread_id
                      AND reply.sender_address != %(user_email)s
                      AND reply.date > e.date
                )
            """

    where = " AND ".join(conditions) if conditions else "TRUE"

    sql = f"""
        SELECT {select_cols_aliased}
        FROM emails e
        WHERE {where}
          AND {not_exists}
        ORDER BY e.date DESC
        LIMIT %(limit)s
    """

    rows = _query_dicts(self._pool, sql, params)
    return [Email.from_row(row) for row in rows]
```

- [ ] **Step 4: Update MCP tool in server.py**

Replace the `unreplied` tool in `src/maildb/server.py`:

```python
@mcp.tool()
def unreplied(
    ctx: Context,
    direction: str = "inbound",
    recipient: str | None = None,
    after: str | None = None,
    before: str | None = None,
    sender: str | None = None,
    sender_domain: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Find emails with no reply in the same thread.

    direction='inbound': messages from others where you never replied.
    direction='outbound': your messages where recipient never replied.
    recipient: filter outbound by specific recipient address.
    """
    db = _get_db(ctx)
    results = db.unreplied(
        direction=direction,
        recipient=recipient,
        after=after,
        before=before,
        sender=sender,
        sender_domain=sender_domain,
        limit=limit,
    )
    return [_serialize_email(e) for e in results]
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/integration/test_maildb.py -k unreplied -v`
Expected: All unreplied tests pass (both new and existing)

- [ ] **Step 6: Run full check**

Run: `uv run just check`
Expected: All green

- [ ] **Step 7: Commit**

```bash
git checkout -b sprint3/16-unreplied-direction
git add src/maildb/maildb.py src/maildb/server.py tests/integration/test_maildb.py
git commit -m "feat: add direction and recipient params to unreplied()

Supports outbound unreplied queries: find user messages where
a specific recipient (or anyone) never replied in the same thread.
Default direction='inbound' preserves backward compatibility.

Closes #16"
```

---

## Task 3: Enhance top_contacts() with Domain Grouping & Exclusion (Issue #17)

**Branch:** `sprint3/17-top-contacts-domain`
**Files:**
- Modify: `src/maildb/maildb.py:266-363` — add `group_by` and `exclude_domains` params
- Modify: `src/maildb/server.py:152-162` — expose new params
- Test: `tests/integration/test_maildb.py` — add domain grouping/exclusion tests

- [ ] **Step 1: Write failing integration tests**

Add to `tests/integration/test_maildb.py`:

```python
def test_top_contacts_domain_grouping_outbound(test_pool, seed_advanced) -> None:
    """group_by='domain' groups outbound recipients by domain."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(group_by="domain", direction="outbound")
    # Alice sent to bob@corp.com in adv-1 → corp.com domain
    domains = {c["domain"] for c in contacts}
    assert "corp.com" in domains


def test_top_contacts_domain_grouping_inbound(test_pool, seed_advanced) -> None:
    """group_by='domain' groups inbound senders by domain."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(group_by="domain", direction="inbound")
    domains = {c["domain"] for c in contacts}
    assert "corp.com" in domains  # bob@corp.com sent 2 messages
    assert "other.com" in domains  # carol@other.com sent 1


def test_top_contacts_exclude_domains(test_pool, seed_advanced) -> None:
    """exclude_domains filters out specified domains."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(direction="inbound", exclude_domains=["corp.com"])
    addresses = {c["address"] for c in contacts}
    assert "bob@corp.com" not in addresses
    assert "carol@other.com" in addresses


def test_top_contacts_domain_grouping_with_exclusion(test_pool, seed_advanced) -> None:
    """Domain grouping combined with exclusion."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(
        group_by="domain", direction="inbound", exclude_domains=["other.com"]
    )
    domains = {c["domain"] for c in contacts}
    assert "corp.com" in domains
    assert "other.com" not in domains


def test_top_contacts_domain_grouping_both(test_pool, seed_advanced) -> None:
    """group_by='domain' with direction='both' combines inbound + outbound."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(group_by="domain", direction="both")
    domains = {c["domain"] for c in contacts}
    assert "corp.com" in domains


def test_top_contacts_exclude_multiple_domains(test_pool, seed_advanced) -> None:
    """Excluding multiple domains filters all of them."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(
        direction="inbound", exclude_domains=["corp.com", "other.com"]
    )
    assert len(contacts) == 0


def test_top_contacts_default_unchanged(test_pool, seed_advanced) -> None:
    """Default group_by='address' with no exclusions still works."""
    config = Settings(user_email="alice@example.com", _env_file=None)  # type: ignore[call-arg]
    db = MailDB._from_pool(test_pool, config=config)
    contacts = db.top_contacts(direction="inbound")
    assert contacts[0]["address"] == "bob@corp.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maildb.py::test_top_contacts_domain_grouping_outbound -v`
Expected: TypeError — unexpected keyword argument `group_by`

- [ ] **Step 3: Implement domain grouping and exclusion in top_contacts()**

Replace the `top_contacts()` method in `src/maildb/maildb.py`. The method is complex due to three directions, so the refactored version consolidates the logic:

```python
def top_contacts(
    self,
    *,
    group_by: str = "address",
    exclude_domains: list[str] | None = None,
    period: str | None = None,
    limit: int = 10,
    direction: str = "both",
) -> list[dict[str, Any]]:
    """Most frequent correspondents via GROUP BY aggregation.

    group_by: 'address' (default) groups by email address, 'domain' groups by domain.
    exclude_domains: list of domains to exclude from results.
    """
    user_email = self._require_user_email()

    if group_by not in ("address", "domain"):
        msg = f"group_by must be 'address' or 'domain', got '{group_by}'"
        raise ValueError(msg)

    params: dict[str, Any] = {"user_email": user_email, "limit": limit}

    period_cond = ""
    if period:
        period_cond = "AND date >= %(period_start)s"
        params["period_start"] = period

    exclude_cond_addr = ""
    exclude_cond_domain = ""
    if exclude_domains:
        params["exclude_domains"] = exclude_domains
        exclude_cond_addr = "AND split_part({col}, '@', 2) != ALL(%(exclude_domains)s)"
        exclude_cond_domain = "AND {col} != ALL(%(exclude_domains)s)"

    if direction == "inbound":
        if group_by == "domain":
            excl = exclude_cond_domain.format(col="sender_domain") if exclude_domains else ""
            sql = f"""
                SELECT sender_domain AS domain, count(*) AS count
                FROM emails
                WHERE sender_address != %(user_email)s
                  {period_cond} {excl}
                GROUP BY sender_domain
                ORDER BY count DESC
                LIMIT %(limit)s
            """
        else:
            excl = exclude_cond_addr.format(col="sender_address") if exclude_domains else ""
            sql = f"""
                SELECT sender_address AS address, count(*) AS count
                FROM emails
                WHERE sender_address != %(user_email)s
                  {period_cond} {excl}
                GROUP BY sender_address
                ORDER BY count DESC
                LIMIT %(limit)s
            """
        return _query_dicts(self._pool, sql, params)

    elif direction == "outbound":
        if group_by == "domain":
            excl = (
                "AND split_part(r.addr, '@', 2) != ALL(%(exclude_domains)s)"
                if exclude_domains
                else ""
            )
            sql = f"""
                SELECT split_part(r.addr, '@', 2) AS domain, count(*) AS count
                FROM emails,
                     LATERAL (
                         SELECT jsonb_array_elements_text(recipients->'to') AS addr
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'cc')
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'bcc')
                     ) AS r(addr)
                WHERE sender_address = %(user_email)s
                  AND r.addr != %(user_email)s
                  {period_cond} {excl}
                GROUP BY split_part(r.addr, '@', 2)
                ORDER BY count DESC
                LIMIT %(limit)s
            """
        else:
            excl = (
                "AND split_part(r.addr, '@', 2) != ALL(%(exclude_domains)s)"
                if exclude_domains
                else ""
            )
            sql = f"""
                SELECT r.addr AS address, count(*) AS count
                FROM emails,
                     LATERAL (
                         SELECT jsonb_array_elements_text(recipients->'to') AS addr
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'cc')
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'bcc')
                     ) AS r(addr)
                WHERE sender_address = %(user_email)s
                  AND r.addr != %(user_email)s
                  {period_cond} {excl}
                GROUP BY r.addr
                ORDER BY count DESC
                LIMIT %(limit)s
            """
        return _query_dicts(self._pool, sql, params)

    else:  # both
        group_col = "domain" if group_by == "domain" else "address"

        if group_by == "domain":
            excl_inbound = (
                "AND sender_domain != ALL(%(exclude_domains)s)" if exclude_domains else ""
            )
            excl_outbound = (
                "AND split_part(r.addr, '@', 2) != ALL(%(exclude_domains)s)"
                if exclude_domains
                else ""
            )
            sql = f"""
                SELECT {group_col}, sum(count) AS count
                FROM (
                    SELECT sender_domain AS domain, count(*) AS count
                    FROM emails
                    WHERE sender_address != %(user_email)s
                      {period_cond} {excl_inbound}
                    GROUP BY sender_domain

                    UNION ALL

                    SELECT split_part(r.addr, '@', 2) AS domain, count(*) AS count
                    FROM emails,
                         LATERAL (
                             SELECT jsonb_array_elements_text(recipients->'to') AS addr
                             UNION ALL
                             SELECT jsonb_array_elements_text(recipients->'cc')
                             UNION ALL
                             SELECT jsonb_array_elements_text(recipients->'bcc')
                         ) AS r(addr)
                    WHERE sender_address = %(user_email)s
                      AND r.addr != %(user_email)s
                      {period_cond} {excl_outbound}
                    GROUP BY split_part(r.addr, '@', 2)
                ) AS combined
                GROUP BY {group_col}
                ORDER BY count DESC
                LIMIT %(limit)s
            """
        else:
            excl_inbound = (
                "AND split_part(sender_address, '@', 2) != ALL(%(exclude_domains)s)"
                if exclude_domains
                else ""
            )
            excl_outbound = (
                "AND split_part(r.addr, '@', 2) != ALL(%(exclude_domains)s)"
                if exclude_domains
                else ""
            )
            sql = f"""
                SELECT address, sum(count) AS count
                FROM (
                    SELECT sender_address AS address, count(*) AS count
                    FROM emails
                    WHERE sender_address != %(user_email)s
                      {period_cond} {excl_inbound}
                    GROUP BY sender_address

                    UNION ALL

                    SELECT r.addr AS address, count(*) AS count
                    FROM emails,
                         LATERAL (
                             SELECT jsonb_array_elements_text(recipients->'to') AS addr
                             UNION ALL
                             SELECT jsonb_array_elements_text(recipients->'cc')
                             UNION ALL
                             SELECT jsonb_array_elements_text(recipients->'bcc')
                         ) AS r(addr)
                    WHERE sender_address = %(user_email)s
                      AND r.addr != %(user_email)s
                      {period_cond} {excl_outbound}
                    GROUP BY r.addr
                ) AS combined
                GROUP BY address
                ORDER BY count DESC
                LIMIT %(limit)s
            """
        return _query_dicts(self._pool, sql, params)
```

- [ ] **Step 4: Update MCP tool in server.py**

```python
@mcp.tool()
def top_contacts(
    ctx: Context,
    group_by: str = "address",
    exclude_domains: list[str] | None = None,
    period: str | None = None,
    limit: int = 10,
    direction: str = "both",
) -> list[dict[str, Any]]:
    """Find most frequent email correspondents.

    group_by: 'address' (default) or 'domain' for domain-level aggregation.
    exclude_domains: list of domains to filter out (e.g. ['mycompany.com']).
    direction: 'inbound', 'outbound', or 'both'.
    """
    db = _get_db(ctx)
    return db.top_contacts(
        group_by=group_by,
        exclude_domains=exclude_domains,
        period=period,
        limit=limit,
        direction=direction,
    )
```

- [ ] **Step 5: Run tests and full check**

Run: `uv run pytest tests/integration/test_maildb.py -k top_contacts -v`
Then: `uv run just check`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git checkout -b sprint3/17-top-contacts-domain
git add src/maildb/maildb.py src/maildb/server.py tests/integration/test_maildb.py
git commit -m "feat: add domain grouping and exclusion to top_contacts()

group_by='domain' aggregates by domain instead of individual address.
exclude_domains filters out specified domains from results.
Default behavior unchanged.

Closes #17"
```

---

## Task 4: Enhance long_threads() with Participant Filter (Issue #18)

**Branch:** `sprint3/18-long-threads-participant`
**Files:**
- Modify: `src/maildb/maildb.py:495-524` — add `participant` param
- Modify: `src/maildb/server.py:194-203` — expose new param
- Test: `tests/integration/test_maildb.py`

- [ ] **Step 1: Write failing integration tests**

Add to `tests/integration/test_maildb.py`:

```python
def test_long_threads_with_participant(test_pool, seed_advanced) -> None:
    """long_threads with participant filters to threads with that sender."""
    db = MailDB._from_pool(test_pool)
    threads = db.long_threads(min_messages=2, participant="bob@corp.com")
    assert len(threads) >= 1
    assert threads[0]["thread_id"] == "adv-1@example.com"


def test_long_threads_participant_no_match(test_pool, seed_advanced) -> None:
    """Participant not in any long thread returns empty."""
    db = MailDB._from_pool(test_pool)
    threads = db.long_threads(min_messages=2, participant="nobody@nowhere.com")
    assert len(threads) == 0


def test_long_threads_no_participant_unchanged(test_pool, seed_advanced) -> None:
    """Without participant, current behavior preserved."""
    db = MailDB._from_pool(test_pool)
    threads = db.long_threads(min_messages=2)
    assert len(threads) >= 1


def test_long_threads_participant_cc_only_no_match(test_pool, seed_advanced) -> None:
    """Participant who is only in CC (not as sender) should NOT match.

    Carol is in CC on adv-1 thread but never sent a message in it.
    Participant filter checks sender_address only.
    """
    db = MailDB._from_pool(test_pool)
    # carol@other.com only sent adv-4 (a single-message thread, won't hit min_messages=2)
    threads = db.long_threads(min_messages=2, participant="carol@other.com")
    assert len(threads) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maildb.py::test_long_threads_with_participant -v`
Expected: TypeError — unexpected keyword argument `participant`

- [ ] **Step 3: Implement participant filter**

Replace `long_threads()` in `src/maildb/maildb.py`:

```python
def long_threads(
    self,
    *,
    participant: str | None = None,
    min_messages: int = 5,
    after: str | None = None,
) -> list[dict[str, Any]]:
    """Threads exceeding a message count threshold.

    participant: only threads where this address appears as sender.
    """
    conditions: list[str] = []
    params: dict[str, Any] = {"min_messages": min_messages}

    if after:
        conditions.append("date >= %(after)s")
        params["after"] = after

    where = " AND ".join(conditions) if conditions else "TRUE"

    having_participant = ""
    if participant:
        having_participant = "AND %(participant)s = ANY(array_agg(sender_address))"
        params["participant"] = participant

    sql = f"""
        SELECT thread_id,
               count(*) AS message_count,
               min(date) AS first_date,
               max(date) AS last_date,
               array_agg(DISTINCT sender_address) AS participants
        FROM emails
        WHERE {where}
        GROUP BY thread_id
        HAVING count(*) >= %(min_messages)s
           {having_participant}
        ORDER BY count(*) DESC
    """

    return _query_dicts(self._pool, sql, params)
```

- [ ] **Step 4: Update MCP tool in server.py**

```python
@mcp.tool()
def long_threads(
    ctx: Context,
    participant: str | None = None,
    min_messages: int = 5,
    after: str | None = None,
) -> list[dict[str, Any]]:
    """Find email threads with many messages.

    participant: only threads where this address appears as sender.
    """
    db = _get_db(ctx)
    return db.long_threads(participant=participant, min_messages=min_messages, after=after)
```

- [ ] **Step 5: Run tests and full check**

Run: `uv run pytest tests/integration/test_maildb.py -k long_threads -v`
Then: `uv run just check`

- [ ] **Step 6: Commit**

```bash
git checkout -b sprint3/18-long-threads-participant
git add src/maildb/maildb.py src/maildb/server.py tests/integration/test_maildb.py
git commit -m "feat: add participant filter to long_threads()

Filters threads to only those where the specified address
appears as sender. Without participant, behavior unchanged.

Closes #18"
```

---

## Task 5: Add correspondence() Method (Issue #19)

**Branch:** `sprint3/19-correspondence`
**Files:**
- Modify: `src/maildb/maildb.py` — add `correspondence()` method
- Modify: `src/maildb/server.py` — add MCP tool
- Test: `tests/integration/test_maildb.py`
- Modify: `tests/unit/test_server.py` — add to tool list

- [ ] **Step 1: Write failing integration tests**

Add to `tests/integration/test_maildb.py`:

```python
def test_correspondence(test_pool, seed_advanced) -> None:
    """correspondence() returns all emails exchanged with a person."""
    db = MailDB._from_pool(test_pool)
    results = db.correspondence(address="bob@corp.com")
    msg_ids = [e.message_id for e in results]
    # adv-1: Alice sent TO bob; adv-2: bob sent; adv-3: bob sent
    assert "adv-1@example.com" in msg_ids  # sent TO bob
    assert "adv-2@corp.com" in msg_ids     # sent BY bob
    assert "adv-3@corp.com" in msg_ids     # sent BY bob
    # adv-4: carol, not bob
    assert "adv-4@other.com" not in msg_ids


def test_correspondence_chronological_order(test_pool, seed_advanced) -> None:
    """Default order is date ASC (chronological)."""
    db = MailDB._from_pool(test_pool)
    results = db.correspondence(address="bob@corp.com")
    dates = [e.date for e in results]
    assert dates == sorted(dates)


def test_correspondence_with_date_filter(test_pool, seed_advanced) -> None:
    """Date filters narrow results."""
    db = MailDB._from_pool(test_pool)
    results = db.correspondence(address="bob@corp.com", after="2025-01-12")
    msg_ids = [e.message_id for e in results]
    # Only adv-3 (Jan 15) — adv-1 (Jan 10) and adv-2 (Jan 11) excluded
    assert "adv-3@corp.com" in msg_ids
    assert "adv-1@example.com" not in msg_ids


def test_correspondence_limit(test_pool, seed_advanced) -> None:
    """Limit restricts result count."""
    db = MailDB._from_pool(test_pool)
    results = db.correspondence(address="bob@corp.com", limit=1)
    assert len(results) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maildb.py::test_correspondence -v`
Expected: AttributeError — `MailDB` has no attribute `correspondence`

- [ ] **Step 3: Implement correspondence() method**

Add to `src/maildb/maildb.py` after `unreplied()`:

```python
def correspondence(
    self,
    *,
    address: str,
    after: str | None = None,
    before: str | None = None,
    limit: int = 500,
    order: str = "date ASC",
) -> list[Email]:
    """All emails exchanged with a specific person.

    Returns emails where address is sender OR is in recipients (to/cc/bcc).
    Default chronological order, higher limit than find().
    """
    if order not in VALID_ORDERS:
        msg = f"Invalid order '{order}'. Must be one of: {', '.join(sorted(VALID_ORDERS))}"
        raise ValueError(msg)

    conditions: list[str] = [
        "(sender_address = %(address)s "
        "OR recipients->'to' @> %(address_json)s "
        "OR recipients->'cc' @> %(address_json)s "
        "OR recipients->'bcc' @> %(address_json)s)"
    ]
    params: dict[str, Any] = {
        "address": address,
        "address_json": json.dumps([address]),
        "limit": limit,
    }

    if after:
        conditions.append("date >= %(after)s")
        params["after"] = after
    if before:
        conditions.append("date < %(before)s")
        params["before"] = before

    where = " AND ".join(conditions)
    sql = f"SELECT {SELECT_COLS} FROM emails WHERE {where} ORDER BY {order} LIMIT %(limit)s"

    rows = _query_dicts(self._pool, sql, params)
    return [Email.from_row(row) for row in rows]
```

- [ ] **Step 4: Add MCP tool in server.py**

Add after the `unreplied` tool in `src/maildb/server.py`:

```python
@mcp.tool()
def correspondence(
    ctx: Context,
    address: str,
    after: str | None = None,
    before: str | None = None,
    limit: int = 500,
    order: str = "date ASC",
) -> list[dict[str, Any]]:
    """Get all emails exchanged with a specific person (sent by or to them).

    Returns chronological by default with higher limit (500) for full history.
    """
    db = _get_db(ctx)
    results = db.correspondence(
        address=address, after=after, before=before, limit=limit, order=order
    )
    return [_serialize_email(e) for e in results]
```

- [ ] **Step 5: Update test_server.py tool list**

In `tests/unit/test_server.py`, add `"correspondence"` to the `expected` set in `test_mcp_has_all_tools()`.

- [ ] **Step 6: Run tests and full check**

Run: `uv run pytest tests/integration/test_maildb.py -k correspondence -v && uv run just check`

- [ ] **Step 7: Commit**

```bash
git checkout -b sprint3/19-correspondence
git add src/maildb/maildb.py src/maildb/server.py tests/integration/test_maildb.py tests/unit/test_server.py
git commit -m "feat: add correspondence() for bidirectional email history

Returns all emails where the specified address is sender or recipient.
Default order date ASC, limit 500 for full relationship history.

Closes #19"
```

---

## Task 6: Add mention_search() Method (Issue #20)

**Branch:** `sprint3/20-mention-search`
**Files:**
- Modify: `src/maildb/maildb.py` — add `mention_search()` method
- Modify: `src/maildb/server.py` — add MCP tool
- Test: `tests/integration/test_maildb.py`
- Modify: `tests/unit/test_server.py`

- [ ] **Step 1: Write failing integration tests**

Add to `tests/integration/test_maildb.py`:

```python
def test_mention_search_body(test_pool, seed_emails) -> None:
    """mention_search finds text in body_text."""
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="spreadsheet")
    assert len(results) == 1
    assert results[0].message_id == "find-test-2@example.com"


def test_mention_search_subject(test_pool, seed_emails) -> None:
    """mention_search finds text in subject."""
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="Invoice")
    assert len(results) == 1
    assert results[0].message_id == "find-test-3@stripe.com"


def test_mention_search_case_insensitive(test_pool, seed_emails) -> None:
    """Search is case-insensitive."""
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="BUDGET")
    assert len(results) >= 1


def test_mention_search_with_sender_filter(test_pool, seed_emails) -> None:
    """Combine text search with sender filter."""
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="budget", sender="alice@example.com")
    assert len(results) == 1
    assert results[0].sender_address == "alice@example.com"


def test_mention_search_escapes_like_chars(test_pool, seed_emails) -> None:
    """LIKE special characters are escaped."""
    db = MailDB._from_pool(test_pool)
    # Should not error; % and _ should be treated literally
    results = db.mention_search(text="100%_done")
    assert len(results) == 0


def test_mention_search_ordered_by_date_desc(test_pool, seed_emails) -> None:
    """Results ordered by date DESC."""
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="budget")
    if len(results) >= 2:
        assert results[0].date >= results[1].date


def test_mention_search_limit(test_pool, seed_emails) -> None:
    """Limit parameter restricts results."""
    db = MailDB._from_pool(test_pool)
    results = db.mention_search(text="budget", limit=1)
    assert len(results) <= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maildb.py::test_mention_search_body -v`
Expected: AttributeError

- [ ] **Step 3: Implement mention_search()**

Add to `src/maildb/maildb.py` after `correspondence()`:

```python
def mention_search(
    self,
    *,
    text: str,
    sender: str | None = None,
    sender_domain: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 50,
) -> list[Email]:
    """Case-insensitive keyword search in body_text and subject.

    Unlike search(), this uses ILIKE (substring match) and does not require Ollama.
    """
    # Escape LIKE special characters
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"

    conditions: list[str] = [
        "(body_text ILIKE %(pattern)s ESCAPE '\\' OR subject ILIKE %(pattern)s ESCAPE '\\')"
    ]
    params: dict[str, Any] = {"pattern": pattern, "limit": limit}

    if sender:
        conditions.append("sender_address = %(sender)s")
        params["sender"] = sender
    if sender_domain:
        conditions.append("sender_domain = %(sender_domain)s")
        params["sender_domain"] = sender_domain
    if after:
        conditions.append("date >= %(after)s")
        params["after"] = after
    if before:
        conditions.append("date < %(before)s")
        params["before"] = before

    where = " AND ".join(conditions)
    sql = f"""
        SELECT {SELECT_COLS} FROM emails
        WHERE {where}
        ORDER BY date DESC
        LIMIT %(limit)s
    """

    rows = _query_dicts(self._pool, sql, params)
    return [Email.from_row(row) for row in rows]
```

- [ ] **Step 4: Add MCP tool in server.py**

```python
@mcp.tool()
def mention_search(
    ctx: Context,
    text: str,
    sender: str | None = None,
    sender_domain: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search for emails containing specific text in body or subject (case-insensitive ILIKE).

    Unlike search(), does not need Ollama — uses substring matching, not semantic similarity.
    """
    db = _get_db(ctx)
    results = db.mention_search(
        text=text, sender=sender, sender_domain=sender_domain,
        after=after, before=before, limit=limit,
    )
    return [_serialize_email(e) for e in results]
```

- [ ] **Step 5: Update test_server.py tool list**

Add `"mention_search"` to the `expected` set in `test_mcp_has_all_tools()`.

- [ ] **Step 6: Run tests and full check**

Run: `uv run pytest tests/integration/test_maildb.py -k mention_search -v && uv run just check`

- [ ] **Step 7: Commit**

```bash
git checkout -b sprint3/20-mention-search
git add src/maildb/maildb.py src/maildb/server.py tests/integration/test_maildb.py tests/unit/test_server.py
git commit -m "feat: add mention_search() for keyword search in body/subject

Case-insensitive ILIKE search across body_text and subject.
No Ollama dependency. Properly escapes LIKE special characters.

Closes #20"
```

---

## Task 7: Implement Tier 2 DSL Engine (Issue #22)

**Branch:** `sprint3/22-dsl-engine`
**Files:**
- Create: `src/maildb/dsl.py` — DSL parser and SQL generator
- Create: `tests/unit/test_dsl.py` — unit tests (SQL generation, no DB)
- Create: `tests/integration/test_dsl.py` — integration tests (real DB)
- Modify: `pyproject.toml` — add pypika dependency

This is the most complex task. It implements the full JSON DSL → PyPika → SQL pipeline.

- [ ] **Step 1: Add ruff exception for dsl.py**

Add `"src/maildb/dsl.py" = ["S608"]` to ruff per-file-ignores in `pyproject.toml` (SQL construction is safe — column whitelist + parameterized values).

No new dependencies needed — the DSL uses direct parameterized SQL string construction with strict whitelists instead of PyPika (see note in plan header).

- [ ] **Step 2: Write unit tests for DSL validation and basic parsing**

Create `tests/unit/test_dsl.py`:

```python
from __future__ import annotations

import pytest

from maildb.dsl import parse_query


class TestValidation:
    def test_rejects_unknown_source(self) -> None:
        with pytest.raises(ValueError, match="source"):
            parse_query({"from": "unknown_table"})

    def test_rejects_unknown_column(self) -> None:
        with pytest.raises(ValueError, match="column"):
            parse_query({"where": {"field": "nonexistent", "eq": "x"}})

    def test_rejects_unknown_operator(self) -> None:
        with pytest.raises(ValueError, match="operator"):
            parse_query({"where": {"field": "sender_address", "badop": "x"}})

    def test_enforces_row_limit_cap(self) -> None:
        sql, params = parse_query({"limit": 9999})
        # Hard cap at 1000
        assert params["__limit"] <= 1000

    def test_rejects_body_text_in_grouped_select(self) -> None:
        with pytest.raises(ValueError, match="body_text"):
            parse_query({
                "select": [{"field": "body_text"}],
                "group_by": ["sender_domain"],
            })

    def test_default_source_is_emails(self) -> None:
        sql, params = parse_query({"where": {"field": "sender_domain", "eq": "x.com"}})
        assert "emails" in sql.lower()


class TestWhereOperators:
    def test_eq(self) -> None:
        sql, params = parse_query({"where": {"field": "sender_address", "eq": "a@b.com"}})
        assert "sender_address" in sql
        # Value should be parameterized
        assert "a@b.com" in params.values()

    def test_neq(self) -> None:
        sql, params = parse_query({"where": {"field": "sender_address", "neq": "a@b.com"}})
        assert "a@b.com" in params.values()

    def test_gt_gte_lt_lte(self) -> None:
        for op in ("gt", "gte", "lt", "lte"):
            sql, params = parse_query({"where": {"field": "date", op: "2025-01-01"}})
            assert "2025-01-01" in params.values()

    def test_ilike(self) -> None:
        sql, params = parse_query({"where": {"field": "subject", "ilike": "%budget%"}})
        assert "%budget%" in params.values()

    def test_in_list(self) -> None:
        sql, params = parse_query({
            "where": {"field": "sender_domain", "in": ["a.com", "b.com"]}
        })
        assert any(isinstance(v, (list, tuple)) for v in params.values())

    def test_is_null_true(self) -> None:
        sql, _ = parse_query({"where": {"field": "in_reply_to", "is_null": True}})
        assert "null" in sql.lower() or "is null" in sql.lower()

    def test_is_null_false(self) -> None:
        sql, _ = parse_query({"where": {"field": "in_reply_to", "is_null": False}})
        assert "not null" in sql.lower() or "is not null" in sql.lower()

    def test_contains_array(self) -> None:
        sql, params = parse_query({"where": {"field": "labels", "contains": ["INBOX"]}})
        assert "labels" in sql


class TestBooleanCombinators:
    def test_and(self) -> None:
        sql, params = parse_query({
            "where": {"and": [
                {"field": "sender_domain", "eq": "a.com"},
                {"field": "date", "gte": "2025-01-01"},
            ]}
        })
        assert "sender_domain" in sql
        assert "date" in sql

    def test_or(self) -> None:
        sql, params = parse_query({
            "where": {"or": [
                {"field": "sender_domain", "eq": "a.com"},
                {"field": "sender_domain", "eq": "b.com"},
            ]}
        })
        assert "sender_domain" in sql

    def test_not(self) -> None:
        sql, _ = parse_query({
            "where": {"not": {"field": "sender_domain", "eq": "a.com"}}
        })
        assert "not" in sql.lower()

    def test_nested_combinators(self) -> None:
        sql, _ = parse_query({
            "where": {"and": [
                {"or": [
                    {"field": "sender_domain", "eq": "a.com"},
                    {"field": "sender_domain", "eq": "b.com"},
                ]},
                {"field": "date", "gte": "2025-01-01"},
            ]}
        })
        assert "sender_domain" in sql
        assert "date" in sql


class TestSelect:
    def test_field_reference(self) -> None:
        sql, _ = parse_query({"select": [{"field": "sender_address"}]})
        assert "sender_address" in sql

    def test_field_with_alias(self) -> None:
        sql, _ = parse_query({"select": [{"field": "sender_address", "as": "addr"}]})
        assert "addr" in sql

    def test_count_star(self) -> None:
        sql, _ = parse_query({
            "select": [{"count": "*", "as": "total"}],
            "group_by": ["sender_domain"],
        })
        assert "count" in sql.lower()

    def test_count_distinct(self) -> None:
        sql, _ = parse_query({
            "select": [{"count_distinct": "sender_address", "as": "n"}],
            "group_by": ["sender_domain"],
        })
        assert "distinct" in sql.lower()

    def test_min_max(self) -> None:
        sql, _ = parse_query({
            "select": [
                {"min": "date", "as": "first"},
                {"max": "date", "as": "last"},
            ],
            "group_by": ["sender_domain"],
        })
        assert "min" in sql.lower()
        assert "max" in sql.lower()

    def test_date_trunc(self) -> None:
        sql, _ = parse_query({
            "select": [{"date_trunc": "month", "field": "date", "as": "period"}],
            "group_by": ["period"],
        })
        assert "date_trunc" in sql.lower()


class TestGroupByHavingOrderBy:
    def test_group_by(self) -> None:
        sql, _ = parse_query({
            "select": [{"field": "sender_domain"}, {"count": "*", "as": "n"}],
            "group_by": ["sender_domain"],
        })
        assert "group by" in sql.lower()

    def test_having(self) -> None:
        sql, _ = parse_query({
            "select": [{"field": "sender_domain"}, {"count": "*", "as": "n"}],
            "group_by": ["sender_domain"],
            "having": {"field": "n", "gte": 5},
        })
        assert "having" in sql.lower()

    def test_order_by(self) -> None:
        sql, _ = parse_query({
            "select": [{"field": "sender_domain"}, {"count": "*", "as": "n"}],
            "group_by": ["sender_domain"],
            "order_by": [{"field": "n", "dir": "desc"}],
        })
        assert "order by" in sql.lower()


class TestSources:
    def test_sent_to_source_has_cte(self) -> None:
        sql, _ = parse_query({
            "from": "sent_to",
            "select": [{"field": "recipient_address"}],
        })
        assert "recipient_address" in sql.lower()

    def test_email_labels_source_has_cte(self) -> None:
        sql, _ = parse_query({
            "from": "email_labels",
            "select": [{"field": "label"}],
        })
        assert "label" in sql.lower()

    def test_sent_to_allows_recipient_columns(self) -> None:
        sql, _ = parse_query({
            "from": "sent_to",
            "where": {"field": "recipient_domain", "eq": "example.com"},
        })
        assert "recipient_domain" in sql

    def test_emails_rejects_recipient_columns(self) -> None:
        with pytest.raises(ValueError, match="column"):
            parse_query({"where": {"field": "recipient_address", "eq": "x"}})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_dsl.py -v`
Expected: ImportError — `maildb.dsl` does not exist

- [ ] **Step 4: Implement dsl.py — column whitelists, validation, and basic structure**

Create `src/maildb/dsl.py`:

```python
"""Tier 2 DSL: JSON spec → parameterized SQL.

Translates structured JSON query specifications into safe, parameterized SQL.
Safety is enforced via strict column whitelists, operator whitelists, and
parameterized values (never string-interpolated).
"""
from __future__ import annotations

from typing import Any

# --- Column whitelists per source ---

_EMAILS_COLUMNS: set[str] = {
    "id", "message_id", "thread_id", "subject", "sender_name",
    "sender_address", "sender_domain", "date", "body_text",
    "has_attachment", "labels", "in_reply_to", "created_at",
}

_SENT_TO_COLUMNS: set[str] = _EMAILS_COLUMNS | {
    "recipient_address", "recipient_domain", "recipient_type",
}

_EMAIL_LABELS_COLUMNS: set[str] = _EMAILS_COLUMNS | {"label"}

_SOURCE_COLUMNS: dict[str, set[str]] = {
    "emails": _EMAILS_COLUMNS,
    "sent_to": _SENT_TO_COLUMNS,
    "email_labels": _EMAIL_LABELS_COLUMNS,
}

_VALID_SOURCES = set(_SOURCE_COLUMNS.keys())

_COMPARISON_OPS: set[str] = {
    "eq", "neq", "gt", "gte", "lt", "lte",
    "ilike", "not_ilike", "in", "not_in",
    "contains", "is_null",
}

_AGG_KEYS: set[str] = {"count", "count_distinct", "min", "max", "sum", "array_agg_distinct"}

_DATE_TRUNC_INTERVALS: set[str] = {"year", "month", "week", "day"}

_MAX_ROWS = 1000

# --- Virtual source CTEs ---

_SENT_TO_CTE = """
WITH source AS (
    SELECT e.id, e.message_id, e.thread_id, e.subject, e.sender_name,
           e.sender_address, e.sender_domain, e.recipients, e.date,
           e.body_text, e.body_html, e.has_attachment, e.attachments,
           e.labels, e.in_reply_to, e."references", e.embedding, e.created_at,
           r.addr AS recipient_address,
           split_part(r.addr, '@', 2) AS recipient_domain,
           r.type AS recipient_type
    FROM emails e,
    LATERAL (
        SELECT jsonb_array_elements_text(e.recipients->'to') AS addr, 'to'::text AS type
        UNION ALL
        SELECT jsonb_array_elements_text(e.recipients->'cc'), 'cc'
        UNION ALL
        SELECT jsonb_array_elements_text(e.recipients->'bcc'), 'bcc'
    ) AS r
)
"""

_EMAIL_LABELS_CTE = """
WITH source AS (
    SELECT e.id, e.message_id, e.thread_id, e.subject, e.sender_name,
           e.sender_address, e.sender_domain, e.recipients, e.date,
           e.body_text, e.body_html, e.has_attachment, e.attachments,
           e.labels, e.in_reply_to, e."references", e.embedding, e.created_at,
           unnest(e.labels) AS label
    FROM emails e
    WHERE e.labels IS NOT NULL AND array_length(e.labels, 1) > 0
)
"""


def parse_query(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Parse a DSL spec dict into (sql_string, params_dict).

    Validates the spec, builds a SQL query using PyPika for the core structure,
    and uses raw CTE SQL for virtual sources. Returns parameterized SQL safe
    for psycopg execution.
    """
    params: dict[str, Any] = {}
    param_counter = [0]

    def _next_param(value: Any) -> str:
        """Register a parameter and return its placeholder name."""
        name = f"__p{param_counter[0]}"
        param_counter[0] += 1
        params[name] = value
        return name

    source_name = spec.get("from", "emails")
    _validate_source(source_name)
    allowed_columns = _SOURCE_COLUMNS[source_name]

    select_items = spec.get("select")
    where_clause = spec.get("where")
    group_by = spec.get("group_by")
    having_clause = spec.get("having")
    order_by = spec.get("order_by")
    limit = min(spec.get("limit", 50), _MAX_ROWS)
    offset = spec.get("offset", 0)

    # Validate body_text not in grouped selects
    if group_by and select_items:
        for item in select_items:
            if isinstance(item, dict) and item.get("field") == "body_text":
                if "count" not in item and "count_distinct" not in item:
                    msg = "body_text cannot appear in SELECT when GROUP BY is used"
                    raise ValueError(msg)

    # Build table reference
    table_name = "source" if source_name != "emails" else "emails"
    table = Table(table_name)

    # Build SELECT clause
    select_fields = _build_select(select_items, table, allowed_columns, group_by)

    # Build WHERE clause
    where_sql = ""
    if where_clause:
        where_sql = _build_where(where_clause, allowed_columns, _next_param)

    # Build GROUP BY
    group_by_sql = ""
    if group_by:
        _validate_columns(group_by, allowed_columns)
        group_by_sql = "GROUP BY " + ", ".join(group_by)

    # Collect aliases from SELECT for HAVING validation
    select_aliases: set[str] = set()
    if select_items:
        for item in select_items:
            if isinstance(item, dict) and "as" in item:
                select_aliases.add(item["as"])

    # Build HAVING
    having_sql = ""
    if having_clause:
        having_sql = "HAVING " + _build_where(having_clause, allowed_columns | select_aliases, _next_param)

    # Build ORDER BY
    order_by_sql = ""
    if order_by:
        order_parts = []
        for item in order_by:
            col = item["field"]
            direction = item.get("dir", "asc").upper()
            if direction not in ("ASC", "DESC"):
                msg = f"Invalid order direction: {direction}"
                raise ValueError(msg)
            order_parts.append(f"{col} {direction}")
        order_by_sql = "ORDER BY " + ", ".join(order_parts)
    elif not group_by:
        order_by_sql = "ORDER BY date DESC"

    # Build LIMIT / OFFSET
    params["__limit"] = limit
    limit_sql = "LIMIT %(__limit)s"
    offset_sql = ""
    if offset:
        params["__offset"] = offset
        offset_sql = "OFFSET %(__offset)s"

    # Assemble SQL
    cte = ""
    if source_name == "sent_to":
        cte = _SENT_TO_CTE
    elif source_name == "email_labels":
        cte = _EMAIL_LABELS_CTE

    select_clause = ", ".join(select_fields)
    parts = [
        cte,
        f"SELECT {select_clause}",
        f"FROM {table_name}",
    ]
    if where_sql:
        parts.append(f"WHERE {where_sql}")
    if group_by_sql:
        parts.append(group_by_sql)
    if having_sql:
        parts.append(having_sql)
    if order_by_sql:
        parts.append(order_by_sql)
    parts.append(limit_sql)
    if offset_sql:
        parts.append(offset_sql)

    sql = "\n".join(parts)
    return sql, params


def _validate_source(source: str) -> None:
    if source not in _VALID_SOURCES:
        msg = f"Invalid source '{source}'. Must be one of: {', '.join(sorted(_VALID_SOURCES))}"
        raise ValueError(msg)


def _validate_columns(columns: list[str], allowed: set[str]) -> None:
    for col in columns:
        if col not in allowed:
            msg = f"Invalid column '{col}'. Allowed: {', '.join(sorted(allowed))}"
            raise ValueError(msg)


def _build_select(
    select_items: list[dict[str, Any]] | None,
    table: Table,
    allowed_columns: set[str],
    group_by: list[str] | None,
) -> list[str]:
    """Build SELECT field expressions."""
    if not select_items:
        if group_by:
            return ["*"]
        # Default: all columns with body_text truncated
        cols = [
            "id", "message_id", "thread_id", "subject", "sender_name",
            "sender_address", "sender_domain", "date", "has_attachment",
            "labels", "in_reply_to", "created_at",
            "left(body_text, 500) AS body_preview",
        ]
        return cols

    fields: list[str] = []
    for item in select_items:
        if "date_trunc" in item:
            interval = item["date_trunc"]
            if interval not in _DATE_TRUNC_INTERVALS:
                msg = f"Invalid date_trunc interval: {interval}"
                raise ValueError(msg)
            col = item["field"]
            if col not in allowed_columns:
                msg = f"Invalid column '{col}' for date_trunc"
                raise ValueError(msg)
            alias = item.get("as", "period")
            fields.append(f"date_trunc('{interval}', {col}) AS {alias}")
        elif any(k in item for k in _AGG_KEYS):
            fields.append(_build_agg_select(item, allowed_columns))
        elif "field" in item:
            col = item["field"]
            if col not in allowed_columns:
                msg = f"Invalid column '{col}'"
                raise ValueError(msg)
            alias = item.get("as")
            if alias:
                fields.append(f"{col} AS {alias}")
            else:
                fields.append(col)
        else:
            msg = f"Invalid select item: {item}"
            raise ValueError(msg)

    return fields


def _build_agg_select(item: dict[str, Any], allowed_columns: set[str]) -> str:
    """Build an aggregation expression."""
    alias = item.get("as", "value")

    if "count" in item:
        col = item["count"]
        if col == "*":
            return f"count(*) AS {alias}"
        if col not in allowed_columns:
            msg = f"Invalid column '{col}' for count"
            raise ValueError(msg)
        return f"count({col}) AS {alias}"
    elif "count_distinct" in item:
        col = item["count_distinct"]
        if col not in allowed_columns:
            msg = f"Invalid column '{col}' for count_distinct"
            raise ValueError(msg)
        return f"count(DISTINCT {col}) AS {alias}"
    elif "min" in item:
        col = item["min"]
        if col not in allowed_columns:
            msg = f"Invalid column '{col}' for min"
            raise ValueError(msg)
        return f"min({col}) AS {alias}"
    elif "max" in item:
        col = item["max"]
        if col not in allowed_columns:
            msg = f"Invalid column '{col}' for max"
            raise ValueError(msg)
        return f"max({col}) AS {alias}"
    elif "sum" in item:
        col = item["sum"]
        if col not in allowed_columns:
            msg = f"Invalid column '{col}' for sum"
            raise ValueError(msg)
        return f"sum({col}) AS {alias}"
    elif "array_agg_distinct" in item:
        col = item["array_agg_distinct"]
        if col not in allowed_columns:
            msg = f"Invalid column '{col}' for array_agg_distinct"
            raise ValueError(msg)
        return f"array_agg(DISTINCT {col}) AS {alias}"
    else:
        msg = f"Unknown aggregation in: {item}"
        raise ValueError(msg)


def _build_where(
    clause: dict[str, Any],
    allowed_columns: set[str],
    next_param: Any,
) -> str:
    """Recursively build WHERE clause SQL with parameterized values."""
    # Boolean combinators
    if "and" in clause:
        parts = [_build_where(c, allowed_columns, next_param) for c in clause["and"]]
        return "(" + " AND ".join(parts) + ")"
    if "or" in clause:
        parts = [_build_where(c, allowed_columns, next_param) for c in clause["or"]]
        return "(" + " OR ".join(parts) + ")"
    if "not" in clause:
        inner = _build_where(clause["not"], allowed_columns, next_param)
        return f"NOT ({inner})"

    # Single comparison
    field = clause.get("field")
    if not field:
        msg = f"Missing 'field' in condition: {clause}"
        raise ValueError(msg)

    if field not in allowed_columns:
        msg = f"Invalid column '{field}'"
        raise ValueError(msg)

    # Find the operator
    op = None
    value = None
    for key in clause:
        if key in ("field", "as"):
            continue
        if key not in _COMPARISON_OPS:
            msg = f"Invalid operator '{key}'"
            raise ValueError(msg)
        op = key
        value = clause[key]
        break

    if op is None:
        msg = f"No operator found in condition: {clause}"
        raise ValueError(msg)

    if op == "eq":
        p = next_param(value)
        return f"{field} = %({p})s"
    elif op == "neq":
        p = next_param(value)
        return f"{field} != %({p})s"
    elif op == "gt":
        p = next_param(value)
        return f"{field} > %({p})s"
    elif op == "gte":
        p = next_param(value)
        return f"{field} >= %({p})s"
    elif op == "lt":
        p = next_param(value)
        return f"{field} < %({p})s"
    elif op == "lte":
        p = next_param(value)
        return f"{field} <= %({p})s"
    elif op == "ilike":
        p = next_param(value)
        return f"{field} ILIKE %({p})s"
    elif op == "not_ilike":
        p = next_param(value)
        return f"{field} NOT ILIKE %({p})s"
    elif op == "in":
        p = next_param(tuple(value))
        return f"{field} IN %({p})s"
    elif op == "not_in":
        p = next_param(tuple(value))
        return f"{field} NOT IN %({p})s"
    elif op == "contains":
        p = next_param(value)
        return f"{field} @> %({p})s"
    elif op == "is_null":
        if value:
            return f"{field} IS NULL"
        else:
            return f"{field} IS NOT NULL"
    else:
        msg = f"Unhandled operator: {op}"
        raise ValueError(msg)
```

Add a public function at the end of `dsl.py` for use by `cluster()` in `maildb.py`:

```python
def build_where_clause(
    where: dict[str, Any],
    source: str = "emails",
) -> tuple[str, dict[str, Any]]:
    """Build a WHERE clause from a DSL filter spec.

    Public API for other modules (e.g. cluster()) that need DSL filter
    parsing without a full query. Returns (where_sql, params_dict).
    """
    _validate_source(source)
    allowed_columns = _SOURCE_COLUMNS[source]
    params: dict[str, Any] = {}
    counter = [0]

    def _next_param(value: Any) -> str:
        name = f"__p{counter[0]}"
        counter[0] += 1
        params[name] = value
        return name

    sql = _build_where(where, allowed_columns, _next_param)
    return sql, params
```

- [ ] **Step 5: Run unit tests**

Run: `uv run pytest tests/unit/test_dsl.py -v`
Expected: All pass

- [ ] **Step 6: Write integration tests**

Create `tests/integration/test_dsl.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from maildb.dsl import parse_query

pytestmark = pytest.mark.integration


@pytest.fixture
def seed_dsl(test_pool):  # type: ignore[no-untyped-def]
    """Seed data for DSL integration tests."""
    emails = [
        {
            "message_id": "dsl-1@example.com",
            "thread_id": "dsl-1@example.com",
            "subject": "Q1 Report",
            "sender_name": "Alice",
            "sender_address": "alice@acme.com",
            "sender_domain": "acme.com",
            "recipients": json.dumps({"to": ["bob@corp.com"], "cc": ["carol@acme.com"], "bcc": []}),
            "date": datetime(2025, 1, 15, 10, 0, tzinfo=UTC),
            "body_text": "Here is the Q1 report.",
            "body_html": None,
            "has_attachment": True,
            "attachments": json.dumps([{"filename": "report.pdf", "content_type": "application/pdf", "size": 1024}]),
            "labels": ["INBOX", "Reports"],
            "in_reply_to": None,
            "references": [],
            "embedding": None,
        },
        {
            "message_id": "dsl-2@corp.com",
            "thread_id": "dsl-1@example.com",
            "subject": "Re: Q1 Report",
            "sender_name": "Bob",
            "sender_address": "bob@corp.com",
            "sender_domain": "corp.com",
            "recipients": json.dumps({"to": ["alice@acme.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 1, 16, 14, 0, tzinfo=UTC),
            "body_text": "Thanks, looks great.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX"],
            "in_reply_to": "dsl-1@example.com",
            "references": ["dsl-1@example.com"],
            "embedding": None,
        },
        {
            "message_id": "dsl-3@acme.com",
            "thread_id": "dsl-3@acme.com",
            "subject": "Budget Meeting",
            "sender_name": "Alice",
            "sender_address": "alice@acme.com",
            "sender_domain": "acme.com",
            "recipients": json.dumps({"to": ["dave@acme.com"], "cc": [], "bcc": []}),
            "date": datetime(2025, 2, 1, 9, 0, tzinfo=UTC),
            "body_text": "Let's discuss the budget.",
            "body_html": None,
            "has_attachment": False,
            "attachments": json.dumps([]),
            "labels": ["INBOX", "Finance"],
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


def _execute_dsl(test_pool, spec: dict) -> list[dict]:
    """Helper: parse DSL spec and execute against test DB."""
    from psycopg.rows import dict_row

    sql, params = parse_query(spec)
    with test_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def test_simple_filter(test_pool, seed_dsl) -> None:
    rows = _execute_dsl(test_pool, {
        "where": {"field": "sender_domain", "eq": "acme.com"},
    })
    assert len(rows) == 2
    assert all(r["sender_domain"] == "acme.com" for r in rows)


def test_aggregation_count_by_domain(test_pool, seed_dsl) -> None:
    rows = _execute_dsl(test_pool, {
        "select": [
            {"field": "sender_domain"},
            {"count": "*", "as": "total"},
        ],
        "group_by": ["sender_domain"],
        "order_by": [{"field": "total", "dir": "desc"}],
    })
    assert len(rows) >= 2
    acme = next(r for r in rows if r["sender_domain"] == "acme.com")
    assert acme["total"] == 2


def test_date_range_filter(test_pool, seed_dsl) -> None:
    rows = _execute_dsl(test_pool, {
        "where": {"and": [
            {"field": "date", "gte": "2025-01-16"},
            {"field": "date", "lt": "2025-02-01"},
        ]}
    })
    assert len(rows) == 1
    assert rows[0]["message_id"] == "dsl-2@corp.com"


def test_sent_to_source(test_pool, seed_dsl) -> None:
    rows = _execute_dsl(test_pool, {
        "from": "sent_to",
        "select": [
            {"field": "recipient_domain"},
            {"count": "*", "as": "total"},
        ],
        "group_by": ["recipient_domain"],
        "order_by": [{"field": "total", "dir": "desc"}],
    })
    domains = {r["recipient_domain"] for r in rows}
    assert "corp.com" in domains
    assert "acme.com" in domains


def test_email_labels_source(test_pool, seed_dsl) -> None:
    rows = _execute_dsl(test_pool, {
        "from": "email_labels",
        "select": [
            {"field": "label"},
            {"count": "*", "as": "total"},
        ],
        "group_by": ["label"],
        "order_by": [{"field": "total", "dir": "desc"}],
    })
    labels = {r["label"] for r in rows}
    assert "INBOX" in labels


def test_having_filter(test_pool, seed_dsl) -> None:
    rows = _execute_dsl(test_pool, {
        "select": [
            {"field": "sender_domain"},
            {"count": "*", "as": "total"},
        ],
        "group_by": ["sender_domain"],
        "having": {"field": "total", "gte": 2},
    })
    assert len(rows) == 1
    assert rows[0]["sender_domain"] == "acme.com"


def test_ilike_search(test_pool, seed_dsl) -> None:
    rows = _execute_dsl(test_pool, {
        "where": {"field": "subject", "ilike": "%budget%"},
    })
    assert len(rows) == 1
    assert rows[0]["message_id"] == "dsl-3@acme.com"


def test_row_limit_cap(test_pool, seed_dsl) -> None:
    """Even with limit=9999, should return at most 1000."""
    sql, params = parse_query({"limit": 9999})
    assert params["__limit"] == 1000


def test_default_body_preview(test_pool, seed_dsl) -> None:
    """Default select truncates body_text to body_preview."""
    rows = _execute_dsl(test_pool, {
        "where": {"field": "sender_domain", "eq": "acme.com"},
    })
    assert "body_preview" in rows[0]


def test_statement_timeout(test_pool, seed_dsl) -> None:
    """Verify that a 5s statement timeout is enforced by the caller, not by parse_query.
    parse_query just returns SQL; the caller (MailDB.query()) sets the timeout."""
    sql, params = parse_query({})
    # Just verify it returns valid SQL
    assert "SELECT" in sql
```

- [ ] **Step 7: Run integration tests**

Run: `uv run pytest tests/integration/test_dsl.py -v`
Expected: All pass

- [ ] **Step 8: Run full check**

Run: `uv run just check`
Expected: All green

- [ ] **Step 9: Commit**

```bash
git checkout -b sprint3/22-dsl-engine
git add pyproject.toml src/maildb/dsl.py tests/unit/test_dsl.py tests/integration/test_dsl.py
git commit -m "feat: implement Tier 2 DSL engine for generalized queries

JSON DSL parser that translates structured query specifications into
parameterized SQL. Supports three sources (emails, sent_to, email_labels),
comparison/boolean operators, aggregations, date_trunc, GROUP BY/HAVING.
Enforced guardrails: column whitelist, 1000-row cap, no body_text in groups.

Closes #22"
```

---

## Task 8: Add query() Method and MCP Tool (Issue #23)

**Branch:** `sprint3/23-query-method`
**Files:**
- Modify: `src/maildb/maildb.py` — add `query()` method
- Modify: `src/maildb/server.py` — add MCP tool with serialization
- Test: `tests/integration/test_maildb.py`
- Modify: `tests/unit/test_server.py`

- [ ] **Step 1: Write failing integration tests**

Add to `tests/integration/test_maildb.py`:

```python
def test_query_simple_filter(test_pool, seed_emails) -> None:
    """query() with simple filter returns dicts."""
    db = MailDB._from_pool(test_pool)
    results = db.query({"where": {"field": "sender_domain", "eq": "stripe.com"}})
    assert len(results) == 1
    assert results[0]["sender_domain"] == "stripe.com"


def test_query_aggregation(test_pool, seed_emails) -> None:
    """query() with aggregation returns correct counts."""
    db = MailDB._from_pool(test_pool)
    results = db.query({
        "select": [
            {"field": "sender_domain"},
            {"count": "*", "as": "total"},
        ],
        "group_by": ["sender_domain"],
        "order_by": [{"field": "total", "dir": "desc"}],
    })
    assert len(results) >= 1
    example_row = next(r for r in results if r["sender_domain"] == "example.com")
    assert example_row["total"] == 2


def test_query_row_limit(test_pool, seed_emails) -> None:
    """query() enforces 1000-row limit."""
    db = MailDB._from_pool(test_pool)
    results = db.query({"limit": 9999})
    # We only have 3 seed emails, but the limit should be capped
    assert len(results) <= 1000


def test_query_invalid_spec(test_pool, seed_emails) -> None:
    """query() with invalid spec raises ValueError."""
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError):
        db.query({"from": "nonexistent"})


def test_query_serialization(test_pool, seed_emails) -> None:
    """query() returns JSON-serializable dicts."""
    import json as json_mod

    db = MailDB._from_pool(test_pool)
    results = db.query({"where": {"field": "sender_domain", "eq": "stripe.com"}})
    # Should not raise
    json_mod.dumps(results, default=str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maildb.py::test_query_simple_filter -v`
Expected: AttributeError — `MailDB` has no attribute `query`

- [ ] **Step 3: Implement query() method**

Add to `src/maildb/maildb.py`:

```python
def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute a Tier 2 DSL query and return results as dicts.

    Accepts a DSL specification dict. See dsl.py for full schema.
    Enforces 5s statement timeout and 1000-row hard cap.
    """
    from maildb.dsl import parse_query

    sql, params = parse_query(spec)

    with self._pool.connection() as conn:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = [dict(row) for row in cur.fetchall()]
        conn.commit()  # releases the SET LOCAL

    return self._serialize_query_results(rows)

@staticmethod
def _serialize_query_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make query results JSON-serializable."""
    from datetime import date, datetime
    from decimal import Decimal
    from uuid import UUID

    def _convert(v: Any) -> Any:
        if isinstance(v, UUID):
            return str(v)
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if isinstance(v, Decimal):
            return float(v)
        return v

    return [{k: _convert(v) for k, v in row.items()} for row in rows]
```

Add `from datetime import date, datetime` and `from decimal import Decimal` and `from uuid import UUID` to the TYPE_CHECKING imports — or keep them in the static method for simplicity.

- [ ] **Step 4: Add MCP tool in server.py**

```python
@mcp.tool()
def query(
    ctx: Context,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """Execute a structured query using the maildb DSL.

    spec: JSON object with optional keys:
      from: "emails" | "sent_to" | "email_labels" (default: "emails")
      select: [{field: "col"}, {count: "*", as: "n"}, {date_trunc: "month", field: "date", as: "period"}]
      where: {field: "col", op: value} or {and/or/not: [...]}
        Operators: eq, neq, gt, gte, lt, lte, ilike, not_ilike, in, not_in, contains, is_null
      group_by: ["col1", "col2"]
      having: same syntax as where
      order_by: [{field: "col", dir: "asc|desc"}]
      limit: int (max 1000)
      offset: int

    Returns list of dicts. 5s timeout, 1000-row cap enforced.
    """
    db = _get_db(ctx)
    return db.query(spec)
```

- [ ] **Step 5: Update test_server.py tool list**

Add `"query"` to the `expected` set in `test_mcp_has_all_tools()`.

- [ ] **Step 6: Run tests and full check**

Run: `uv run pytest tests/integration/test_maildb.py -k query -v && uv run just check`

- [ ] **Step 7: Commit**

```bash
git checkout -b sprint3/23-query-method
git add src/maildb/maildb.py src/maildb/server.py tests/integration/test_maildb.py tests/unit/test_server.py
git commit -m "feat: add query() method exposing Tier 2 DSL

Integration layer for the DSL engine. Executes parsed queries with
5s statement timeout. Returns JSON-serializable dicts with proper
type conversion (UUID, datetime, Decimal).

Closes #23"
```

---

## Task 9: Add cluster() Method (Issue #21)

**Branch:** `sprint3/21-cluster`
**Files:**
- Modify: `src/maildb/maildb.py` — add `cluster()`, extract `_farthest_point_select()` from `topics_with()`
- Modify: `src/maildb/server.py` — add MCP tool
- Test: `tests/integration/test_maildb.py`
- Modify: `tests/unit/test_server.py`

- [ ] **Step 1: Write failing integration tests**

Add to `tests/integration/test_maildb.py`:

```python
def test_cluster_with_message_ids(test_pool, seed_advanced) -> None:
    """cluster() with explicit message_ids returns diverse emails."""
    db = MailDB._from_pool(test_pool)
    # adv-1, adv-2, adv-3, adv-4 all have embeddings
    results = db.cluster(
        message_ids=["adv-1@example.com", "adv-2@corp.com", "adv-3@corp.com", "adv-4@other.com"],
        limit=2,
    )
    assert len(results) == 2
    assert all(isinstance(e, Email) for e in results)


def test_cluster_with_where(test_pool, seed_advanced) -> None:
    """cluster() with DSL where filter."""
    db = MailDB._from_pool(test_pool)
    results = db.cluster(
        where={"field": "sender_domain", "eq": "corp.com"},
        limit=2,
    )
    assert len(results) >= 1
    assert all(e.sender_domain == "corp.com" for e in results)


def test_cluster_fewer_than_limit(test_pool, seed_advanced) -> None:
    """cluster() returns all candidates when fewer than limit."""
    db = MailDB._from_pool(test_pool)
    results = db.cluster(
        where={"field": "sender_address", "eq": "carol@other.com"},
        limit=10,
    )
    assert len(results) == 1  # Only one carol email


def test_cluster_requires_where_or_ids(test_pool, seed_advanced) -> None:
    """cluster() raises when neither where nor message_ids provided."""
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="where.*message_ids"):
        db.cluster()


def test_cluster_rejects_both_where_and_ids(test_pool, seed_advanced) -> None:
    """cluster() raises when both where and message_ids provided."""
    db = MailDB._from_pool(test_pool)
    with pytest.raises(ValueError, match="where.*message_ids"):
        db.cluster(
            where={"field": "sender_domain", "eq": "corp.com"},
            message_ids=["adv-1@example.com"],
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maildb.py::test_cluster_with_message_ids -v`
Expected: AttributeError

- [ ] **Step 3: Extract `_farthest_point_select()` helper and implement `cluster()`**

First, extract the farthest-point algorithm from `topics_with()` into a static method. Then add `cluster()`:

```python
@staticmethod
def _farthest_point_select(emails: list[Email], limit: int) -> list[Email]:
    """Greedy farthest-point selection on embeddings for diverse topic extraction.

    Starts with the first email (most recent), then iteratively selects
    the candidate with maximum minimum cosine distance to already-selected emails.
    """
    if len(emails) <= limit:
        return emails

    selected: list[Email] = [emails[0]]
    remaining = list(emails[1:])

    while len(selected) < limit and remaining:
        best_idx = -1
        best_dist = -1.0

        for i, candidate in enumerate(remaining):
            if candidate.embedding is None:
                continue
            min_dist = float("inf")
            for sel in selected:
                if sel.embedding is None:
                    continue
                dist = MailDB._cosine_distance(candidate.embedding, sel.embedding)
                min_dist = min(min_dist, dist)
            if min_dist > best_dist:
                best_dist = min_dist
                best_idx = i

        if best_idx < 0:
            break
        selected.append(remaining.pop(best_idx))

    return selected
```

Update `topics_with()` to use the helper:

```python
def topics_with(self, *, sender=None, sender_domain=None, limit=5):
    # ... existing query logic to fetch emails ...
    emails = [Email.from_row(row) for row in rows]
    if not emails:
        return []
    return self._farthest_point_select(emails, limit)
```

Add `cluster()`:

```python
def cluster(
    self,
    *,
    where: dict[str, Any] | None = None,
    message_ids: list[str] | None = None,
    limit: int = 5,
) -> list[Email]:
    """Diverse topic extraction from arbitrary email subsets.

    Provide either `where` (DSL filter) or `message_ids` (explicit list), not both.
    Uses farthest-point selection on embeddings.
    """
    if where is None and message_ids is None:
        msg = "Either where or message_ids must be provided"
        raise ValueError(msg)
    if where is not None and message_ids is not None:
        msg = "Provide either where or message_ids, not both"
        raise ValueError(msg)

    if message_ids is not None:
        # Fetch by explicit message_ids
        if not message_ids:
            return []
        placeholders = ", ".join(
            f"%(mid_{i})s" for i in range(len(message_ids))
        )
        params: dict[str, Any] = {
            f"mid_{i}": mid for i, mid in enumerate(message_ids)
        }
        sql = f"""
            SELECT {SELECT_COLS} FROM emails
            WHERE message_id IN ({placeholders})
              AND embedding IS NOT NULL
            ORDER BY date DESC
        """
        rows = _query_dicts(self._pool, sql, params)
    else:
        # Build WHERE from DSL filter using the public API
        from maildb.dsl import build_where_clause

        where_sql, params = build_where_clause(where, source="emails")
        sql = f"""
            SELECT {SELECT_COLS} FROM emails
            WHERE {where_sql}
              AND embedding IS NOT NULL
            ORDER BY date DESC
            LIMIT 500
        """
        rows = _query_dicts(self._pool, sql, params)

    emails = [Email.from_row(row) for row in rows]
    if not emails:
        return []

    return self._farthest_point_select(emails, limit)
```

- [ ] **Step 4: Add MCP tool in server.py**

```python
@mcp.tool()
def cluster(
    ctx: Context,
    where: dict[str, Any] | None = None,
    message_ids: list[str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Extract diverse topic representatives from an email subset.

    Provide either where (DSL filter dict) or message_ids (list of message_id strings).
    Uses farthest-point selection on embeddings to maximize topic diversity.
    """
    db = _get_db(ctx)
    results = db.cluster(where=where, message_ids=message_ids, limit=limit)
    return [_serialize_email(e) for e in results]
```

- [ ] **Step 5: Update test_server.py tool list**

Add `"cluster"` to the `expected` set.

- [ ] **Step 6: Run tests and full check**

Run: `uv run pytest tests/integration/test_maildb.py -k cluster -v && uv run pytest tests/integration/test_maildb.py -k topics_with -v && uv run just check`

- [ ] **Step 7: Commit**

```bash
git checkout -b sprint3/21-cluster
git add src/maildb/maildb.py src/maildb/server.py tests/integration/test_maildb.py tests/unit/test_server.py
git commit -m "feat: add cluster() for diverse topic extraction

Generalized farthest-point selection on embeddings. Accepts DSL where
filter or explicit message_ids for chaining with Tier 1 tools.
Refactors topics_with() to share _farthest_point_select() helper.

Closes #21"
```

---

## Task 10: Add MCP Tool Descriptions with Inline API Reference (Issue #24)

**Branch:** `sprint3/24-mcp-descriptions`
**Files:**
- Modify: `src/maildb/server.py` — update all tool docstrings

- [ ] **Step 1: Update all tool docstrings in server.py**

Replace each `@mcp.tool()` function's docstring with rich inline documentation. Example for `find`:

```python
@mcp.tool()
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
) -> list[dict[str, Any]]:
    """Search emails by structured attribute filters.

    Parameters:
      sender: exact email address (e.g. "alice@acme.com")
      sender_domain: domain portion (e.g. "acme.com")
      recipient: address in To/CC/BCC
      after: ISO date string, inclusive (e.g. "2025-01-01")
      before: ISO date string, exclusive
      has_attachment: filter by attachment presence
      subject_contains: case-insensitive substring match
      labels: array containment (AND logic, e.g. ["INBOX", "Finance"])
      limit: max results (default 50)
      order: "date DESC" | "date ASC" | "sender_address ASC" | "sender_address DESC"

    Returns: list of email dicts with id, message_id, thread_id, subject, sender_name,
    sender_address, sender_domain, recipients, date, body_text, has_attachment,
    attachments, labels, in_reply_to.

    Example: find(sender_domain="stripe.com", after="2025-01-01", has_attachment=True)
    """
```

Apply similar rich docstrings to ALL tools: `search`, `get_thread`, `get_thread_for`, `top_contacts`, `topics_with`, `unreplied`, `long_threads`, `correspondence`, `mention_search`, `cluster`, `query`. Each should include: purpose, parameter descriptions, return shape, one example.

- [ ] **Step 2: Run full check**

Run: `uv run just check`

- [ ] **Step 3: Commit**

```bash
git checkout -b sprint3/24-mcp-descriptions
git add src/maildb/server.py
git commit -m "docs: add rich inline API reference to all MCP tool descriptions

Each tool now includes parameter descriptions, return shape, and
an example. Enables LLM discovery without external file reads.

Closes #24"
```

---

## Task 11: Update using-maildb Skill (Issue #26)

**Branch:** `sprint3/26-update-skill`
**Files:**
- Modify: `skills/using-maildb/SKILL.md`

- [ ] **Step 1: Update the skill document**

Update `skills/using-maildb/SKILL.md` to include:

1. New Tier 1 tools in the Quick Reference table: `correspondence()`, `mention_search()`, `cluster()`
2. Enhanced tools with new params: `unreplied()` direction, `top_contacts()` group_by/exclude_domains, `long_threads()` participant
3. Updated "Choosing a Method" flowchart with new decision paths
4. Tier 2 `query()` DSL compact reference (table of operators + one example)
5. Updated "Things to Know" section

Keep the skill concise (~800 words target). The DSL reference should be a compact table of operators and one example, not the full spec.

Key additions to the flowchart:
- "Need keyword search in body?" → `mention_search()`
- "Need full correspondence with a person?" → `correspondence()`
- "Need diverse topics from a subset?" → `cluster()`
- "Need aggregation/grouping?" → `query()` DSL

Key additions to Quick Reference:
```
| correspondence(address, after, before) | list[Email] | All emails with a person | No |
| mention_search(text, sender, after) | list[Email] | Keyword search in body/subject | No |
| cluster(where|message_ids, limit) | list[Email] | Diverse topic extraction | No |
| query(spec) | list[dict] | Generalized DSL queries | No |
```

- [ ] **Step 2: Run full check**

Run: `uv run just check`

- [ ] **Step 3: Commit**

```bash
git checkout -b sprint3/26-update-skill
git add skills/using-maildb/SKILL.md
git commit -m "docs: update using-maildb skill for Sprint 3 API surface

Adds correspondence(), mention_search(), cluster(), query() DSL.
Documents enhanced params for unreplied, top_contacts, long_threads.
Updates method selection flowchart and compact DSL reference.

Closes #26"
```

---

## Dependency Graph

```
Task 1 (#25 design spec) ─── no deps, do first

Tasks 2-6 (#16-#20) ─── independent, parallelizable
  Task 2: unreplied direction (#16)
  Task 3: top_contacts domain (#17)
  Task 4: long_threads participant (#18)
  Task 5: correspondence (#19)
  Task 6: mention_search (#20)

Task 7 (#22 DSL engine) ─── can run in parallel with Tasks 2-6

Task 8 (#23 query method) ─── depends on Task 7
Task 9 (#21 cluster) ─── depends on Task 7

Task 10 (#24 MCP descriptions) ─── after Tasks 2-9
Task 11 (#26 skill update) ─── after Tasks 2-10
```
