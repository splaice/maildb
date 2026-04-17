from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from maildb.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def test_serve_invokes_mcp_run() -> None:
    with patch("maildb.cli._configure_logging"), patch("maildb.cli.mcp.run") as mock_run:
        result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()


def test_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output
    assert "ingest" in result.output


def test_ingest_run_requires_account(tmp_path: Path) -> None:
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    result = runner.invoke(app, ["ingest", "run", str(mbox)])
    assert result.exit_code != 0
    assert "account" in result.output.lower() or "missing" in result.output.lower()


def test_ingest_run_validates_account_format(tmp_path: Path) -> None:
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    result = runner.invoke(app, ["ingest", "run", str(mbox), "--account", "not-an-email"])
    assert result.exit_code != 0
    assert "email" in result.output.lower()


def test_ingest_run_passes_account_through(tmp_path: Path) -> None:
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    with (
        patch("maildb.cli.run_pipeline") as mock_pipeline,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        mock_pipeline.return_value = {}
        result = runner.invoke(app, ["ingest", "run", str(mbox), "--account", "you@example.com"])
    assert result.exit_code == 0, result.output
    mock_pipeline.assert_called_once()
    kwargs = mock_pipeline.call_args[1]
    assert kwargs["source_account"] == "you@example.com"
    assert kwargs["skip_embed"] is False


def test_ingest_run_skip_embed_flag(tmp_path: Path) -> None:
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    with (
        patch("maildb.cli.run_pipeline") as mock_pipeline,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        mock_pipeline.return_value = {}
        result = runner.invoke(
            app,
            ["ingest", "run", str(mbox), "--account", "you@example.com", "--skip-embed"],
        )
    assert result.exit_code == 0, result.output
    kwargs = mock_pipeline.call_args[1]
    assert kwargs["skip_embed"] is True


def test_ingest_status_invokes_get_status() -> None:
    with (
        patch("maildb.cli.get_status") as mock_status,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        mock_status.return_value = {
            "split": {},
            "parse": {},
            "index": {},
            "embed": {},
            "total_emails": 0,
        }
        result = runner.invoke(app, ["ingest", "status"])
    assert result.exit_code == 0, result.output
    mock_status.assert_called_once()


def test_ingest_reset_requires_yes_or_aborts() -> None:
    with (
        patch("maildb.cli.reset_pipeline") as mock_reset,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        # Without --yes, prompt is auto-aborted by CliRunner with "n".
        runner.invoke(app, ["ingest", "reset"], input="n\n")
    assert mock_reset.call_count == 0


def test_ingest_reset_with_yes_calls_reset() -> None:
    with (
        patch("maildb.cli.reset_pipeline") as mock_reset,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        result = runner.invoke(app, ["ingest", "reset", "--yes"])
    assert result.exit_code == 0, result.output
    mock_reset.assert_called_once()
