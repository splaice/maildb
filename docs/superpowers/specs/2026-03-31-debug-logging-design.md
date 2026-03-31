# Debug Logging for MCP Tool Observability

**Date:** 2026-03-31
**Issue:** splaice/maildb#33

## Problem

When MCP tools return unexpected results (empty responses, overflows, wrong data), there is no way to inspect what happened. The MCP transport only surfaces the final JSON result — no SQL, no filter values, no intermediate state.

During stress testing, this was a concrete problem:
- `top_contacts(direction="outbound")` returned `[]` — no way to see what SQL or filter was applied (#29)
- `unreplied(direction="inbound")` returned chat transcripts — couldn't see why null-date records weren't filtered (#30)
- `mention_search(text="acquisition", limit=5)` overflowed — couldn't see response size before it hit the transport (#27)

## Design

### Configuration

Three new fields in `Settings` (`src/maildb/config.py`):

| Setting | Env var | Default | Description |
|---------|---------|---------|-------------|
| `debug_log` | `MAILDB_DEBUG_LOG` | `~/.maildb/debug.log` | Path to debug log file |
| `debug_log_level` | `MAILDB_DEBUG_LOG_LEVEL` | `DEBUG` | Minimum level for the file sink |
| `debug_log_max_bytes` | `MAILDB_DEBUG_LOG_MAX_BYTES` | `10485760` (10MB) | Truncate file on startup if it exceeds this size |

The existing `_expand_paths()` validator handles `~` expansion for `debug_log`.

### PII Scrubbing Processor

New module: `src/maildb/pii.py`

A structlog processor that redacts PII before log events reach any sink. Two layers:

**Field-based redaction** — if a log event key matches a sensitive name, the entire value is replaced with `[REDACTED]`:

```python
SENSITIVE_KEYS = {
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "authorization", "auth", "credential", "ssn", "credit_card",
    "card_number", "phone", "address", "first_name", "last_name",
}
```

**Regex-based scrubbing** — string values (including the `event` message) are scanned for patterns:

| Pattern | Example | Replacement |
|---------|---------|-------------|
| Email addresses | `user@domain.com` | `[REDACTED-EMAIL]` |
| US SSNs | `123-45-6789` | `[REDACTED-SSN]` |
| Credit card numbers | 13-19 digit sequences, Luhn-validated | `[REDACTED-CC]` |
| US phone numbers | `555-123-4567` | `[REDACTED-PHONE]` |

**Value truncation** — string values over 100 characters are truncated to `first 100 chars...`. This lives in the same processor.

All regexes are compiled once at module level. The processor runs in microseconds per log line. No external dependencies.

### Dual-Sink Logging Architecture

Bridge structlog to stdlib `logging` for multi-sink routing:

```
structlog processors (shared):
  1. merge_contextvars
  2. add_log_level
  3. scrub_pii
  4. ProcessorFormatter bridge to stdlib logging

stdlib logging handlers:
  ├── StreamHandler(stderr)               → INFO+, ConsoleRenderer
  └── FileHandler(~/.maildb/debug.log)    → DEBUG+, ConsoleRenderer
```

Startup sequence in `_configure_logging()` (`src/maildb/__main__.py`):
1. Ensure `~/.maildb/` directory exists
2. Check debug log file size; truncate if over `debug_log_max_bytes`
3. Configure stdlib handlers with appropriate levels
4. Configure structlog processor chain with PII scrubber and stdlib bridge

### Debug Log Points

**Layer 1: MCP tool handlers (`src/maildb/server.py`)**

Log entry and exit for every tool call:

```
# Entry
debug tool=find sender_domain=stripe.com after=2019-01-01 limit=5

# Exit (success)
debug tool=find rows=0 response_bytes=14 elapsed_ms=12

# Exit (large response)
warning tool=cluster rows=5 response_bytes=1077588 warning="response exceeds 50KB"
```

Implementation: a `@log_tool` decorator applied to all 13 tool handlers. Extracts the function name as the tool name, logs all non-`ctx` arguments at entry, measures elapsed time, and logs row count + response byte size at exit. Emits a warning if response bytes exceed 50KB.

**Layer 2: Query execution (`src/maildb/maildb.py`)**

Log SQL in `_query_dicts()` and `_query_one_dict()`:

```
# Before execution
debug sql="SELECT ... FROM emails WHERE sender_domain = %(p1)s LIMIT %(p2)s" params={p1: "stripe.com", p2: 5}

# After execution
debug sql_complete rows=0 elapsed_ms=8
```

All 13 tools funnel through these two helpers, so SQL logging covers every tool with 4 log lines added.

**What we don't log:** Email body content. SQL params contain filter values (domain names, search terms, dates) but not result bodies. PII scrubbing handles anything sensitive in filter values.

## Files Changed

| File | Change |
|------|--------|
| `src/maildb/config.py` | Add `debug_log`, `debug_log_level`, `debug_log_max_bytes` settings |
| `src/maildb/pii.py` | New — PII scrubbing structlog processor |
| `src/maildb/__main__.py` | Rewrite `_configure_logging()` for dual-sink + PII scrub |
| `src/maildb/server.py` | Add tool-level entry/exit debug logging (decorator or wrapper) |
| `src/maildb/maildb.py` | Add SQL debug logging in `_query_dicts()` and `_query_one_dict()` |
| `tests/unit/test_pii.py` | New — tests for PII scrubbing processor |
| `tests/unit/test_logging.py` | New — tests for dual-sink setup and log routing |
