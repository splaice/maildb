from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from maildb.__main__ import _configure_logging
from maildb.config import Settings
from maildb.server import log_tool

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_log_tool_logs_entry_and_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """@log_tool decorator logs tool entry params and exit stats."""
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

    for handler in logging.getLogger().handlers:
        handler.flush()

    content = log_path.read_text()
    assert "tool_entry" in content
    assert "find" in content
    assert "stripe.com" in content
    assert "tool_exit" in content
    assert "rows" in content
    assert result == [{"id": "1", "subject": "test"}]


def test_log_tool_warns_on_large_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """@log_tool emits a warning when response exceeds 50KB."""
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

    for handler in logging.getLogger().handlers:
        handler.flush()

    content = log_path.read_text()
    assert "response exceeds 50KB" in content


def test_log_tool_excludes_ctx_from_params(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """@log_tool does not log the ctx parameter."""
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

    for handler in logging.getLogger().handlers:
        handler.flush()

    content = log_path.read_text()
    # "ctx" should not appear as a logged parameter
    lines = content.split("\n")
    entry_lines = [line for line in lines if "tool_entry" in line]
    for line in entry_lines:
        assert "ctx=" not in line
