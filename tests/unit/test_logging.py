from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import structlog

from maildb.__main__ import _configure_logging
from maildb.config import Settings

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_configure_logging_creates_debug_log_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Debug log directory is created if it doesn't exist."""
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
    log_path = tmp_path / "debug.log"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        debug_log=str(log_path),
    )
    _configure_logging(settings)

    logger = structlog.get_logger()
    logger.debug("test_debug_event", tool="find")

    # Flush handlers
    for handler in logging.getLogger().handlers:
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
    log_path = tmp_path / "debug.log"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        debug_log=str(log_path),
    )
    _configure_logging(settings)

    logger = structlog.get_logger()
    logger.debug("query_params", sender="alice@example.com")

    for handler in logging.getLogger().handlers:
        handler.flush()

    content = log_path.read_text()
    assert "alice@example.com" not in content
    assert "[REDACTED-EMAIL]" in content
