from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from maildb.cli import app

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
