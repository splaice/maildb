from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from maildb.cli import app

runner = CliRunner()


def test_process_attachments_help_lists_subcommands():
    result = runner.invoke(app, ["process_attachments", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "status" in result.output
    assert "retry" in result.output


def test_process_attachments_run_passes_workers_and_retry(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_run") as mock_run,
    ):
        mock_pool.return_value = object()
        mock_run.return_value = {"extracted": 3, "failed": 0, "skipped": 0}
        result = runner.invoke(
            app,
            [
                "process_attachments",
                "run",
                "--workers",
                "4",
                "--no-retry-failed",
            ],
        )
    assert result.exit_code == 0, result.output
    kwargs = mock_run.call_args.kwargs
    assert kwargs["workers"] == 4
    assert kwargs["retry_failed"] is False


def test_process_attachments_run_dry_run_counts_only(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli._count_selected", return_value=17),
        patch("maildb.cli.pa_run") as mock_run,
    ):
        mock_pool.return_value = object()
        result = runner.invoke(app, ["process_attachments", "run", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "17" in result.output
    assert not mock_run.called
