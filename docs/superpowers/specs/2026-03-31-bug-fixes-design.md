# Bug Fix Batch: Response Overflow, Missing Limit, Null Dates, Outbound Direction

**Date:** 2026-03-31
**Issues:** splaice/maildb#27, splaice/maildb#28, splaice/maildb#29, splaice/maildb#30

## Overview

Four bugs that degrade MCP tool usability. Fixes are ordered by impact: response overflow first (biggest win, smallest change), then missing limit, null dates, and finally outbound direction verification.

---

## Bug #27: Response Overflow from `body_html`

**Problem:** Tools like `cluster`, `mention_search`, and `topics_with` return emails with full `body_html` (often tens of KB per message). Even with `limit=5`, responses can exceed 1MB and overflow the MCP transport.

**Fix:** Drop `body_html` from `_serialize_email()` in `src/maildb/server.py:74-86`, the same way `embedding` is already dropped:

```python
d.pop("embedding", None)
d.pop("body_html", None)  # add this line
```

The `body_text` field already carries readable content. The DSL `query()` tool already excludes `body_html` (it's not in the allowed columns list in `dsl.py`). No opt-in flag needed — if a user needs raw HTML they can query the database directly.

**Files changed:**
| File | Change |
|------|--------|
| `src/maildb/server.py` | Add `d.pop("body_html", None)` in `_serialize_email()` |
| `tests/unit/test_server.py` | Add test verifying `body_html` is excluded from serialized output |

---

## Bug #28: `long_threads` Has No Limit Parameter

**Problem:** `long_threads()` in `src/maildb/maildb.py:749-778` has no `LIMIT` clause. With `min_messages=5`, it returns every matching thread — up to 3.1M characters.

**Fix:** Add `limit: int = 50` parameter to both the `MailDB.long_threads()` method and the `long_threads` MCP tool handler. Append `LIMIT %(limit)s` to the SQL query. Results are already ordered by `count(*) DESC`, so the longest threads come first.

In `src/maildb/maildb.py`, the SQL becomes:

```sql
SELECT thread_id, count(*) AS message_count,
       min(date) AS first_date, max(date) AS last_date,
       array_agg(DISTINCT sender_address) AS participants
FROM emails WHERE {where}
GROUP BY thread_id
HAVING count(*) >= %(min_messages)s {having_participant}
ORDER BY count(*) DESC
LIMIT %(limit)s
```

In `src/maildb/server.py`, the tool handler gains `limit: int = 50` and passes it through.

**Files changed:**
| File | Change |
|------|--------|
| `src/maildb/maildb.py` | Add `limit` param to `long_threads()`, add `LIMIT` to SQL |
| `src/maildb/server.py` | Add `limit: int = 50` to `long_threads` tool handler |
| `tests/unit/test_server.py` or `tests/integration/test_maildb.py` | Test that limit is respected |

---

## Bug #30: Null Dates Cause Noise in Queries

**Problem:** 244 emails have `date = NULL` (Google Chat transcripts without a `Date` header). These pollute `unreplied` results and create null buckets in aggregation queries.

### Fix Part 1: Query layer — exclude null dates from `unreplied`

In `src/maildb/maildb.py`, the `unreplied()` method (lines 514-637) constructs SQL for both inbound and outbound branches. Add `e.date IS NOT NULL` to both branches' conditions list:

```python
# Inbound branch (line 558)
conditions: list[str] = [
    "e.sender_address != %(user_email)s",
    "e.date IS NOT NULL",
]

# Outbound branch (line 591)
conditions = [
    "e.sender_address = %(user_email)s",
    "e.date IS NOT NULL",
]
```

This ensures null-date emails never appear in unreplied results. The `NOT EXISTS` subquery comparison (`reply.date > e.date`) also behaves correctly when `e.date` is guaranteed non-null.

### Fix Part 2: Ingest fallback — parse `Received` header when `Date` is missing

In `src/maildb/parsing.py:179-187`, when `Date` header parsing fails or is missing, fall back to the first `Received` header's timestamp:

```python
date: datetime | None = None
raw_date = _safe_header(msg, "Date")
if raw_date:
    try:
        date = email.utils.parsedate_to_datetime(raw_date)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        logger.warning("unparseable_date", message_id=message_id, raw_date=raw_date)

# Fallback: extract date from first Received header
if date is None:
    received = msg.get("Received")
    if received:
        # Received headers end with "; <date>"
        parts = received.rsplit(";", 1)
        if len(parts) == 2:
            try:
                date = email.utils.parsedate_to_datetime(parts[1].strip())
                if date.tzinfo is None:
                    date = date.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                pass
```

This prevents future null-date records. The 244 existing null-date rows remain in the database but are excluded from `unreplied` by the query-layer fix.

**Files changed:**
| File | Change |
|------|--------|
| `src/maildb/maildb.py` | Add `e.date IS NOT NULL` to both `unreplied` branches |
| `src/maildb/parsing.py` | Add `Received` header fallback when `Date` is missing |
| `tests/unit/test_parsing.py` | Test date fallback to `Received` header |
| `tests/integration/test_maildb.py` | Test that null-date emails are excluded from `unreplied` |

---

## Bug #29: Outbound Direction Returns Empty Results

**Problem:** `top_contacts(direction="outbound")` and `unreplied(direction="outbound")` returned empty results.

**Root cause:** The `user_email` config value didn't match the actual sender addresses in the database. Now that `MAILDB_USER_EMAIL=splaice@postmates.com` is set correctly in the environment, the outbound queries should work.

**Fix:** Verify the fix by adding integration tests that explicitly set `user_email` to match test fixture sender addresses and assert outbound results are non-empty. No code change expected — just test coverage to prevent regression.

**Files changed:**
| File | Change |
|------|--------|
| `tests/integration/test_maildb.py` | Add tests for outbound `top_contacts` and `unreplied` with correct `user_email` |

---

## Implementation Order

1. **#27** — Drop `body_html` (1 line + test)
2. **#28** — Add `limit` to `long_threads` (small change + test)
3. **#30** — Null date handling (query fix + parser fallback + tests)
4. **#29** — Verify outbound direction (tests only)

Each fix is committed separately with `Closes #N` in the commit message.
