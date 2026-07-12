from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from uuid import uuid4

from typer.testing import CliRunner

from maildb.cli import app
from maildb.models import ImportRecord

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def test_serve_invokes_mcp_run():
    with patch("maildb.cli._configure_logging"), patch("maildb.cli.mcp.run") as mock_run:
        result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()


def test_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output
    assert "ingest" in result.output


def test_ingest_run_requires_account(tmp_path: Path):
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    result = runner.invoke(app, ["ingest", "run", str(mbox)])
    assert result.exit_code != 0
    assert "account" in result.output.lower() or "missing" in result.output.lower()


def test_ingest_run_validates_account_format(tmp_path: Path):
    mbox = tmp_path / "test.mbox"
    mbox.touch()
    result = runner.invoke(app, ["ingest", "run", str(mbox), "--account", "not-an-email"])
    assert result.exit_code != 0
    assert "email" in result.output.lower()


def test_ingest_run_passes_account_through(tmp_path: Path):
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


def test_ingest_run_skip_embed_flag(tmp_path: Path):
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


def test_ingest_status_invokes_get_status():
    record = ImportRecord(
        id=uuid4(),
        source_account="you@example.com",
        source_file=None,
        started_at=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        completed_at=None,
        messages_total=45,
        messages_inserted=42,
        messages_skipped=3,
        status="completed",
    )
    with (
        patch("maildb.cli.get_status") as mock_status,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
        patch("maildb.cli.MailDB.import_history", return_value=[record]) as mock_history,
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
    mock_history.assert_called_once_with(account=None, limit=20)
    # Confirms _print_imports_summary actually ran the loop body.
    assert "Imports" in result.output
    assert "you@example.com" in result.output
    assert "completed" in result.output
    assert "42" in result.output


def test_ingest_status_filters_by_account():
    """--account is passed through to import_history."""
    with (
        patch("maildb.cli.get_status") as mock_status,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
        patch("maildb.cli.MailDB.import_history", return_value=[]) as mock_history,
    ):
        mock_pool.return_value = MagicMock()
        mock_status.return_value = {
            "split": {},
            "parse": {},
            "index": {},
            "embed": {},
            "total_emails": 0,
        }
        result = runner.invoke(app, ["ingest", "status", "--account", "you@example.com"])

    assert result.exit_code == 0, result.output
    mock_history.assert_called_once_with(account="you@example.com", limit=20)


def test_ingest_reset_requires_yes_or_aborts():
    """Declining the confirm prompt aborts with exit code 1 and 'Aborted.' message."""
    with (
        patch("maildb.cli.reset_pipeline") as mock_reset,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        result = runner.invoke(app, ["ingest", "reset"], input="n\n")

    assert result.exit_code == 1
    assert "Aborted." in result.output
    assert mock_reset.call_count == 0


def test_ingest_reset_with_yes_calls_reset():
    with (
        patch("maildb.cli.reset_pipeline") as mock_reset,
        patch("maildb.cli.create_pool") as mock_pool,
        patch("maildb.cli.init_db"),
    ):
        mock_pool.return_value = MagicMock()
        result = runner.invoke(app, ["ingest", "reset", "--yes"])
    assert result.exit_code == 0, result.output
    mock_reset.assert_called_once()
