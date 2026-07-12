from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import maildb.server as server_mod
from maildb.cli import _configure_logging
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
    assert "response_bytes" in content
    assert result == [{"id": "1", "subject": "test"}]


def test_log_tool_warns_on_large_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Root DEBUG + handler at DEBUG: response_bytes present; 50KB warning fires."""
    # CLI shape: root always DEBUG; handler levels do the gating.
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    root.addHandler(handler)

    warning_events: list[dict[str, Any]] = []
    real_warning = server_mod.logger.warning

    def spy_warning(event: str, *args: Any, **kwargs: Any) -> Any:
        if event == "tool_exit":
            warning_events.append(dict(kwargs))
        return real_warning(event, *args, **kwargs)

    monkeypatch.setattr(server_mod.logger, "warning", spy_warning)

    @log_tool
    def big_result(ctx: Any) -> list[dict[str, Any]]:
        return [{"body": "x" * 100_000}]

    big_result(MagicMock())

    assert warning_events
    assert "response_bytes" in warning_events[0]
    assert warning_events[0].get("warning") == "response exceeds 50KB"


def test_log_tool_skips_response_measurement_when_not_debug(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Root DEBUG + handler at INFO: no json.dumps, exit omits response_bytes."""
    # CLI shape: root always DEBUG; handler levels do the gating.
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    root.addHandler(handler)

    dumps_calls: list[Any] = []
    real_dumps = server_mod.json.dumps

    def spy_dumps(obj: Any, *args: Any, **kwargs: Any) -> str:
        dumps_calls.append(obj)
        return real_dumps(obj, *args, **kwargs)

    monkeypatch.setattr(server_mod.json, "dumps", spy_dumps)

    exit_events: list[dict[str, Any]] = []
    real_debug = server_mod.logger.debug

    def spy_debug(event: str, *args: Any, **kwargs: Any) -> Any:
        if event == "tool_exit":
            exit_events.append(dict(kwargs))
        return real_debug(event, *args, **kwargs)

    monkeypatch.setattr(server_mod.logger, "debug", spy_debug)

    @log_tool
    def find(ctx: Any) -> list[dict[str, Any]]:
        return [{"id": "1"}]

    find(MagicMock())

    assert dumps_calls == []
    assert exit_events
    assert "response_bytes" not in exit_events[0]
    assert "rows" in exit_events[0]
    assert "elapsed_ms" in exit_events[0]


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
