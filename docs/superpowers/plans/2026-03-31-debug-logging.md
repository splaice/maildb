# Debug Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add dual-sink debug logging with PII scrubbing to the MCP server so tool invocations, SQL queries, and response stats are observable via a debug log file.

**Architecture:** structlog processors handle PII scrubbing and value truncation in a shared chain, then bridge to stdlib `logging` which routes INFO+ to stderr (existing behavior) and DEBUG+ to a file at `~/.maildb/debug.log`. A `@log_tool` decorator on all 13 MCP tool handlers logs entry params and exit stats. SQL logging is added to the two shared query helpers.

**Tech Stack:** structlog (existing), stdlib `logging`, pydantic-settings (existing), re (stdlib)

**Spec:** `docs/superpowers/specs/2026-03-31-debug-logging-design.md`

**GitHub Issue:** splaice/maildb#33

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

**Key conventions:**
- Python 3.12+, all type hints required (`disallow_untyped_defs = true` in mypy)
- Ruff for linting/formatting (line length 99)
- Tests in `tests/unit/` (no DB required) and `tests/integration/` (requires PostgreSQL)
- Use `uv run` for all commands
- All config in `pyproject.toml` — no separate tool config files
- structlog for logging, pydantic-settings for configuration
- Test pattern: `monkeypatch` env vars, construct `Settings(_env_file=None)` to avoid loading `.env`

**File layout:**
```
src/maildb/
  __main__.py      # MCP entry point, _configure_logging()
  config.py        # Settings(BaseSettings)
  server.py        # FastMCP tool handlers (13 tools)
  maildb.py        # MailDB class, _query_dicts(), _query_one_dict()
  models.py        # Email, Recipients, SearchResult dataclasses
tests/unit/
  test_config.py   # Settings tests
  test_server.py   # Serialization tests
```

---

## Task 1: Add configuration settings

**Files:**
- Modify: `src/maildb/config.py:10-35`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing tests for new settings**

Add to `tests/unit/test_config.py`:

```python
def test_debug_log_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAILDB_DEBUG_LOG", raising=False)
    monkeypatch.delenv("MAILDB_DEBUG_LOG_LEVEL", raising=False)
    monkeypatch.delenv("MAILDB_DEBUG_LOG_MAX_BYTES", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.debug_log.endswith(".maildb/debug.log")
    assert "~" not in settings.debug_log  # path should be expanded
    assert settings.debug_log_level == "DEBUG"
    assert settings.debug_log_max_bytes == 10_485_760


def test_debug_log_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILDB_DEBUG_LOG", "/tmp/custom-debug.log")
    monkeypatch.setenv("MAILDB_DEBUG_LOG_LEVEL", "INFO")
    monkeypatch.setenv("MAILDB_DEBUG_LOG_MAX_BYTES", "5242880")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.debug_log == "/tmp/custom-debug.log"
    assert settings.debug_log_level == "INFO"
    assert settings.debug_log_max_bytes == 5_242_880
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: FAIL — `debug_log` attribute does not exist on Settings

- [ ] **Step 3: Add settings to config.py**

In `src/maildb/config.py`, add three fields to the `Settings` class after `embed_batch_size`:

```python
    embed_batch_size: int = 50

    # Debug logging
    debug_log: str = "~/.maildb/debug.log"
    debug_log_level: str = "DEBUG"
    debug_log_max_bytes: int = 10_485_760  # 10MB
```

Update `_expand_paths` to also expand `debug_log`:

```python
    @model_validator(mode="after")
    def _expand_paths(self) -> Settings:
        """Expand ~ and resolve relative paths for directory settings."""
        self.attachment_dir = str(Path(self.attachment_dir).expanduser())
        self.ingest_tmp_dir = str(Path(self.ingest_tmp_dir).expanduser())
        self.debug_log = str(Path(self.debug_log).expanduser())
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/config.py tests/unit/test_config.py
git commit -m "feat: add debug_log, debug_log_level, debug_log_max_bytes settings"
```

---

## Task 2: PII scrubbing processor

**Files:**
- Create: `src/maildb/pii.py`
- Create: `tests/unit/test_pii.py`

- [ ] **Step 1: Write failing tests for PII scrubbing**

Create `tests/unit/test_pii.py`:

```python
from __future__ import annotations

from maildb.pii import scrub_pii


def _event(event: str = "test", **kwargs: object) -> dict[str, object]:
    """Build a minimal structlog event dict."""
    return {"event": event, **kwargs}


class TestFieldBasedRedaction:
    def test_sensitive_key_password(self) -> None:
        result = scrub_pii(None, "debug", _event(password="hunter2"))
        assert result["password"] == "[REDACTED]"

    def test_sensitive_key_token(self) -> None:
        result = scrub_pii(None, "debug", _event(token="abc123xyz"))
        assert result["token"] == "[REDACTED]"

    def test_sensitive_key_api_key(self) -> None:
        result = scrub_pii(None, "debug", _event(api_key="sk-1234"))
        assert result["api_key"] == "[REDACTED]"

    def test_sensitive_key_authorization(self) -> None:
        result = scrub_pii(None, "debug", _event(authorization="Bearer xyz"))
        assert result["authorization"] == "[REDACTED]"

    def test_non_sensitive_key_preserved(self) -> None:
        result = scrub_pii(None, "debug", _event(tool="find"))
        assert result["tool"] == "find"


class TestRegexScrubbing:
    def test_email_in_value(self) -> None:
        result = scrub_pii(None, "debug", _event(sender="alice@example.com"))
        assert result["sender"] == "[REDACTED-EMAIL]"

    def test_email_in_event_message(self) -> None:
        result = scrub_pii(None, "debug", _event("query for alice@example.com"))
        assert "alice@example.com" not in result["event"]
        assert "[REDACTED-EMAIL]" in result["event"]

    def test_ssn_redacted(self) -> None:
        result = scrub_pii(None, "debug", _event(data="SSN is 123-45-6789"))
        assert "123-45-6789" not in result["data"]
        assert "[REDACTED-SSN]" in result["data"]

    def test_credit_card_redacted(self) -> None:
        # 4111111111111111 is a standard test Visa number (passes Luhn)
        result = scrub_pii(None, "debug", _event(data="card 4111111111111111"))
        assert "4111111111111111" not in result["data"]
        assert "[REDACTED-CC]" in result["data"]

    def test_phone_redacted(self) -> None:
        result = scrub_pii(None, "debug", _event(data="call 555-123-4567"))
        assert "555-123-4567" not in result["data"]
        assert "[REDACTED-PHONE]" in result["data"]

    def test_phone_no_dashes(self) -> None:
        result = scrub_pii(None, "debug", _event(data="call 5551234567"))
        assert "5551234567" not in result["data"]
        assert "[REDACTED-PHONE]" in result["data"]


class TestValueTruncation:
    def test_short_value_unchanged(self) -> None:
        result = scrub_pii(None, "debug", _event(sql="SELECT 1"))
        assert result["sql"] == "SELECT 1"

    def test_long_value_truncated(self) -> None:
        long_val = "x" * 200
        result = scrub_pii(None, "debug", _event(sql=long_val))
        assert len(result["sql"]) < 200
        assert result["sql"].endswith("...")

    def test_non_string_value_unchanged(self) -> None:
        result = scrub_pii(None, "debug", _event(rows=42))
        assert result["rows"] == 42


class TestCombined:
    def test_pii_scrubbed_before_truncation(self) -> None:
        """If a long string contains PII, PII is scrubbed first, then truncated."""
        long_val = "prefix " + "alice@example.com " * 20
        result = scrub_pii(None, "debug", _event(data=long_val))
        assert "alice@example.com" not in result["data"]

    def test_event_key_preserved(self) -> None:
        """The 'event' key is always present and scrubbed but never field-redacted."""
        result = scrub_pii(None, "debug", _event("hello alice@example.com"))
        assert "[REDACTED-EMAIL]" in result["event"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pii.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'maildb.pii'`

- [ ] **Step 3: Implement the PII processor**

Create `src/maildb/pii.py`:

```python
"""PII scrubbing structlog processor."""

from __future__ import annotations

import re
from typing import Any

# --- Field-based redaction ---

SENSITIVE_KEYS = frozenset({
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "credential",
    "ssn",
    "credit_card",
    "card_number",
    "phone",
    "address",
    "first_name",
    "last_name",
})

REDACTED = "[REDACTED]"

# --- Regex-based scrubbing ---

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_RE = re.compile(r"\b\d{13,19}\b")
_PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")

MAX_VALUE_LENGTH = 100


def _luhn_check(digits: str) -> bool:
    """Validate a digit string with the Luhn algorithm."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _redact_cc(match: re.Match[str]) -> str:
    """Replace credit card numbers that pass Luhn validation."""
    digits = match.group()
    if _luhn_check(digits):
        return "[REDACTED-CC]"
    return digits


def _scrub_value(value: str) -> str:
    """Apply regex-based PII scrubbing to a string value."""
    value = _EMAIL_RE.sub("[REDACTED-EMAIL]", value)
    value = _SSN_RE.sub("[REDACTED-SSN]", value)
    value = _CC_RE.sub(_redact_cc, value)
    value = _PHONE_RE.sub("[REDACTED-PHONE]", value)
    return value


def _truncate(value: str) -> str:
    """Truncate strings over MAX_VALUE_LENGTH."""
    if len(value) > MAX_VALUE_LENGTH:
        return value[:MAX_VALUE_LENGTH] + "..."
    return value


def scrub_pii(
    logger: Any,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog processor: redact PII, then truncate long values."""
    for key in list(event_dict.keys()):
        # Field-based: redact entire value if key is sensitive
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
            continue

        value = event_dict[key]
        if not isinstance(value, str):
            continue

        # Regex-based: scrub PII patterns in string values
        value = _scrub_value(value)
        # Truncate long values
        value = _truncate(value)
        event_dict[key] = value

    return event_dict
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_pii.py -v`
Expected: All PASS

- [ ] **Step 5: Run lint and format**

Run: `uv run just fmt && uv run just lint`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add src/maildb/pii.py tests/unit/test_pii.py
git commit -m "feat: add PII scrubbing structlog processor"
```

---

## Task 3: Dual-sink logging setup

**Files:**
- Modify: `src/maildb/__main__.py:1-33`
- Create: `tests/unit/test_logging.py`

- [ ] **Step 1: Write failing tests for dual-sink logging**

Create `tests/unit/test_logging.py`:

```python
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import pytest


def test_configure_logging_creates_debug_log_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Debug log directory is created if it doesn't exist."""
    from maildb.__main__ import _configure_logging
    from maildb.config import Settings

    log_path = tmp_path / "subdir" / "debug.log"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        debug_log=str(log_path),
    )
    _configure_logging(settings)
    assert log_path.parent.exists()


def test_configure_logging_truncates_oversized_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Debug log file is truncated on startup if it exceeds max_bytes."""
    from maildb.__main__ import _configure_logging
    from maildb.config import Settings

    log_path = tmp_path / "debug.log"
    log_path.write_text("x" * 200)

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        debug_log=str(log_path),
        debug_log_max_bytes=100,
    )
    _configure_logging(settings)
    assert log_path.stat().st_size == 0


def test_debug_log_receives_debug_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """DEBUG-level messages are written to the debug log file."""
    from maildb.__main__ import _configure_logging
    from maildb.config import Settings

    log_path = tmp_path / "debug.log"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        debug_log=str(log_path),
    )
    _configure_logging(settings)

    logger = structlog.get_logger()
    logger.debug("test_debug_event", tool="find")

    # Flush handlers
    for handler in logging.getLogger("maildb").handlers:
        handler.flush()

    content = log_path.read_text()
    assert "test_debug_event" in content
    assert "find" in content


def test_stderr_does_not_receive_debug_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """DEBUG-level messages do NOT appear on stderr (only INFO+)."""
    from maildb.__main__ import _configure_logging
    from maildb.config import Settings

    log_path = tmp_path / "debug.log"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        debug_log=str(log_path),
    )
    _configure_logging(settings)

    logger = structlog.get_logger()
    logger.debug("should_not_appear_on_stderr")

    captured = capsys.readouterr()
    assert "should_not_appear_on_stderr" not in captured.err


def test_pii_scrubbed_in_debug_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PII is scrubbed before reaching the debug log file."""
    from maildb.__main__ import _configure_logging
    from maildb.config import Settings

    log_path = tmp_path / "debug.log"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        debug_log=str(log_path),
    )
    _configure_logging(settings)

    logger = structlog.get_logger()
    logger.debug("query_params", sender="alice@example.com")

    for handler in logging.getLogger("maildb").handlers:
        handler.flush()

    content = log_path.read_text()
    assert "alice@example.com" not in content
    assert "[REDACTED-EMAIL]" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_logging.py -v`
Expected: FAIL — `_configure_logging()` does not accept a Settings argument

- [ ] **Step 3: Rewrite _configure_logging for dual-sink**

Replace the entire contents of `src/maildb/__main__.py`:

```python
"""Entry point for running the MCP server: python -m maildb"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog

from maildb.config import Settings
from maildb.pii import scrub_pii
from maildb.server import mcp


def _configure_logging(settings: Settings | None = None) -> None:
    """Set up dual-sink logging: stderr (INFO+) and debug log file (DEBUG+).

    PII scrubbing is applied before events reach either sink.
    """
    settings = settings or Settings()
    log_path = Path(settings.debug_log)

    # Ensure parent directory exists
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Truncate if oversized
    if log_path.exists() and log_path.stat().st_size > settings.debug_log_max_bytes:
        log_path.write_text("")

    # --- stdlib logging setup (sinks) ---
    root_logger = logging.getLogger("maildb")
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)

    # Shared formatter using structlog's ConsoleRenderer
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    # Sink 1: stderr at INFO+
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.INFO)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)

    # Sink 2: debug log file at configurable level
    file_level = getattr(logging, settings.debug_log_level.upper(), logging.DEBUG)
    file_handler = logging.FileHandler(str(log_path))
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # --- structlog setup (shared processors) ---
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            scrub_pii,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def main() -> None:
    """Run the MailDB MCP server."""
    _configure_logging()
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_logging.py -v`
Expected: All PASS

- [ ] **Step 5: Run the full test suite**

Run: `uv run just check`
Expected: All PASS. The existing code uses `structlog.get_logger()` which will now route through the new stdlib bridge. Verify no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/maildb/__main__.py tests/unit/test_logging.py
git commit -m "feat: dual-sink logging with PII scrubbing (stderr + debug file)"
```

---

## Task 4: SQL query debug logging

**Files:**
- Modify: `src/maildb/maildb.py:39-59`
- Modify: `tests/unit/test_logging.py`

- [ ] **Step 1: Write failing test for SQL logging**

Add to `tests/unit/test_logging.py`:

```python
import time
from unittest.mock import MagicMock, patch


def test_query_dicts_logs_sql(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_query_dicts logs SQL statement and result stats to debug log."""
    from maildb.__main__ import _configure_logging
    from maildb.config import Settings
    from maildb.maildb import _query_dicts

    log_path = tmp_path / "debug.log"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        debug_log=str(log_path),
    )
    _configure_logging(settings)

    # Mock the pool + connection + cursor
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"id": 1}, {"id": 2}]
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    _query_dicts(mock_pool, "SELECT * FROM emails WHERE sender_domain = %(d)s", {"d": "stripe.com"})

    for handler in logging.getLogger("maildb").handlers:
        handler.flush()

    content = log_path.read_text()
    assert "sql_execute" in content
    assert "SELECT * FROM emails" in content
    assert "sql_complete" in content
    assert "rows" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_logging.py::test_query_dicts_logs_sql -v`
Expected: FAIL — no "sql_execute" in log output

- [ ] **Step 3: Add debug logging to query helpers**

In `src/maildb/maildb.py`, replace the two query helper functions (lines 39-59):

First, add `import time` to the imports at the top of `src/maildb/maildb.py` (after `import math`):

```python
import time
```

Then replace the two query helper functions (lines 39-59):

```python
def _query_dicts(
    pool: ConnectionPool,
    sql: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute a query and return rows as dicts."""
    logger.debug("sql_execute", sql=sql, params=params)
    t0 = time.monotonic()
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = [dict(row) for row in cur.fetchall()]
    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    logger.debug("sql_complete", rows=len(rows), elapsed_ms=elapsed_ms)
    return rows


def _query_one_dict(
    pool: ConnectionPool,
    sql: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Execute a query and return a single row as dict, or None."""
    logger.debug("sql_execute", sql=sql, params=params)
    t0 = time.monotonic()
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    result = dict(row) if row else None
    logger.debug("sql_complete", rows=1 if result else 0, elapsed_ms=elapsed_ms)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_logging.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/maildb/maildb.py tests/unit/test_logging.py
git commit -m "feat: add SQL debug logging to query helpers"
```

---

## Task 5: MCP tool handler logging decorator

**Files:**
- Modify: `src/maildb/server.py`
- Create: `tests/unit/test_log_tool.py`

- [ ] **Step 1: Write failing tests for the @log_tool decorator**

Create `tests/unit/test_log_tool.py`:

```python
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

if TYPE_CHECKING:
    import pytest


def test_log_tool_logs_entry_and_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """@log_tool decorator logs tool entry params and exit stats."""
    from maildb.__main__ import _configure_logging
    from maildb.config import Settings
    from maildb.server import log_tool

    log_path = tmp_path / "debug.log"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        debug_log=str(log_path),
    )
    _configure_logging(settings)

    @log_tool
    def find(ctx: Any, sender_domain: str = "stripe.com", limit: int = 10) -> list[dict[str, Any]]:
        return [{"id": "1", "subject": "test"}]

    mock_ctx = MagicMock()
    result = find(mock_ctx, sender_domain="stripe.com", limit=10)

    for handler in logging.getLogger("maildb").handlers:
        handler.flush()

    content = log_path.read_text()
    assert "tool_entry" in content
    assert "find" in content
    assert "stripe.com" in content
    assert "tool_exit" in content
    assert "rows=1" in content or "rows" in content


def test_log_tool_warns_on_large_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """@log_tool emits a warning when response exceeds 50KB."""
    from maildb.__main__ import _configure_logging
    from maildb.config import Settings
    from maildb.server import log_tool

    log_path = tmp_path / "debug.log"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        debug_log=str(log_path),
    )
    _configure_logging(settings)

    @log_tool
    def big_result(ctx: Any) -> list[dict[str, Any]]:
        return [{"body": "x" * 100_000}]

    mock_ctx = MagicMock()
    big_result(mock_ctx)

    for handler in logging.getLogger("maildb").handlers:
        handler.flush()

    content = log_path.read_text()
    assert "response exceeds 50KB" in content


def test_log_tool_excludes_ctx_from_params(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """@log_tool does not log the ctx parameter."""
    from maildb.__main__ import _configure_logging
    from maildb.config import Settings
    from maildb.server import log_tool

    log_path = tmp_path / "debug.log"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        debug_log=str(log_path),
    )
    _configure_logging(settings)

    @log_tool
    def search(ctx: Any, query: str = "test") -> list[dict[str, Any]]:
        return []

    mock_ctx = MagicMock()
    search(mock_ctx, query="test")

    for handler in logging.getLogger("maildb").handlers:
        handler.flush()

    content = log_path.read_text()
    assert "ctx" not in content.lower() or "context" not in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_log_tool.py -v`
Expected: FAIL — `cannot import name 'log_tool' from 'maildb.server'`

- [ ] **Step 3: Implement the @log_tool decorator**

Add to `src/maildb/server.py`, after the imports and before the serialization helpers. Add necessary imports first:

```python
import inspect
import json
import time
from functools import wraps
from typing import Any, Callable, TypeVar

import structlog

logger = structlog.get_logger()

F = TypeVar("F", bound=Callable[..., Any])

RESPONSE_SIZE_WARNING_BYTES = 50_000  # 50KB


def log_tool(func: F) -> F:
    """Decorator that logs MCP tool entry params and exit stats."""
    sig = inspect.signature(func)

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Bind args to param names, excluding 'ctx'
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        params = {k: v for k, v in bound.arguments.items() if k != "ctx" and v is not None}

        tool_name = func.__name__
        logger.debug("tool_entry", tool=tool_name, **params)

        t0 = time.monotonic()
        result = func(*args, **kwargs)
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)

        # Compute result stats
        row_count = len(result) if isinstance(result, list) else 1
        response_bytes = len(json.dumps(result, default=str).encode())

        if response_bytes > RESPONSE_SIZE_WARNING_BYTES:
            logger.warning(
                "tool_exit",
                tool=tool_name,
                rows=row_count,
                response_bytes=response_bytes,
                elapsed_ms=elapsed_ms,
                warning="response exceeds 50KB",
            )
        else:
            logger.debug(
                "tool_exit",
                tool=tool_name,
                rows=row_count,
                response_bytes=response_bytes,
                elapsed_ms=elapsed_ms,
            )

        return result

    return wrapper  # type: ignore[return-value]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_log_tool.py -v`
Expected: All PASS

- [ ] **Step 5: Apply @log_tool to all 13 tool handlers**

In `src/maildb/server.py`, add `@log_tool` below every `@mcp.tool()` decorator. The order matters — `@mcp.tool()` must be outermost:

```python
@mcp.tool()
@log_tool
def find(ctx: Context, ...) -> list[dict[str, Any]]:
    ...

@mcp.tool()
@log_tool
def search(ctx: Context, ...) -> list[dict[str, Any]]:
    ...

@mcp.tool()
@log_tool
def get_thread(ctx: Context, ...) -> list[dict[str, Any]]:
    ...

@mcp.tool()
@log_tool
def get_thread_for(ctx: Context, ...) -> list[dict[str, Any]]:
    ...

@mcp.tool()
@log_tool
def top_contacts(ctx: Context, ...) -> list[dict[str, Any]]:
    ...

@mcp.tool()
@log_tool
def topics_with(ctx: Context, ...) -> list[dict[str, Any]]:
    ...

@mcp.tool()
@log_tool
def unreplied(ctx: Context, ...) -> list[dict[str, Any]]:
    ...

@mcp.tool()
@log_tool
def correspondence(ctx: Context, ...) -> list[dict[str, Any]]:
    ...

@mcp.tool()
@log_tool
def mention_search(ctx: Context, ...) -> list[dict[str, Any]]:
    ...

@mcp.tool()
@log_tool
def cluster(ctx: Context, ...) -> list[dict[str, Any]]:
    ...

@mcp.tool()
@log_tool
def long_threads(ctx: Context, ...) -> list[dict[str, Any]]:
    ...

@mcp.tool()
@log_tool
def query(ctx: Context, ...) -> list[dict[str, Any]]:
    ...
```

All 13 tool handler function bodies remain unchanged — only the decorator is added. There are 12 functions listed above because `search` returns `list[dict]` after serialization just like the others. Double-check by counting: `find`, `search`, `get_thread`, `get_thread_for`, `top_contacts`, `topics_with`, `unreplied`, `correspondence`, `mention_search`, `cluster`, `long_threads`, `query` = 12 tools. (The tool list in the spec says 13 but the server.py file has 12 `@mcp.tool()` decorated functions.)

- [ ] **Step 6: Run the full test suite**

Run: `uv run just check`
Expected: All PASS. Verify the existing `test_server.py` tests still pass — the decorator is transparent.

- [ ] **Step 7: Commit**

```bash
git add src/maildb/server.py tests/unit/test_log_tool.py
git commit -m "feat: add @log_tool decorator to all MCP tool handlers"
```

---

## Task 6: Integration smoke test

**Files:**
- No new files — manual verification

- [ ] **Step 1: Run the full check suite**

Run: `uv run just check`
Expected: All PASS — fmt, lint, tests all green.

- [ ] **Step 2: Verify debug log file is created on server start**

Start the server manually to verify the log file is created:

```bash
MAILDB_DEBUG_LOG=/tmp/maildb-test-debug.log uv run python -m maildb &
sleep 2
ls -la /tmp/maildb-test-debug.log
kill %1
```

Expected: File exists at `/tmp/maildb-test-debug.log`.

- [ ] **Step 3: Final commit (if any fixups needed)**

If any adjustments were needed during smoke testing, commit them:

```bash
git add -u
git commit -m "fix: address integration issues from debug logging smoke test"
```

If no fixups were needed, skip this step.
