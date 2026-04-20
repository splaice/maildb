from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from maildb.cli import app

runner = CliRunner()


def test_process_attachments_help_lists_subcommands():
    result = runner.invoke(app, ["process_attachments", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "status" in result.output
    assert "retry" in result.output
    assert "reembed" in result.output


def test_process_attachments_run_passes_workers_and_retry(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_run") as mock_run,
    ):
        pool_instance = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)
        pool_instance.connection.return_value.__enter__.return_value.execute.return_value = cursor
        mock_pool.return_value = pool_instance
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


def test_run_with_limit_passes_selector(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_run") as mock_run,
    ):
        pool_instance = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)
        pool_instance.connection.return_value.__enter__.return_value.execute.return_value = cursor
        mock_pool.return_value = pool_instance
        mock_run.return_value = {"extracted": 0, "failed": 0, "skipped": 0}
        result = runner.invoke(app, ["process_attachments", "run", "--limit", "5"])
    assert result.exit_code == 0
    kwargs = mock_run.call_args.kwargs
    # selector_sql should bound the claim and selector_params carry the limit
    assert "limit" in kwargs["selector_params"]
    assert kwargs["selector_params"]["limit"] == 5


def test_run_with_only_pdf(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_run") as mock_run,
    ):
        pool_instance = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)
        pool_instance.connection.return_value.__enter__.return_value.execute.return_value = cursor
        mock_pool.return_value = pool_instance
        mock_run.return_value = {"extracted": 0, "failed": 0, "skipped": 0}
        result = runner.invoke(app, ["process_attachments", "run", "--only", "pdf"])
    assert result.exit_code == 0
    kwargs = mock_run.call_args.kwargs
    assert "pdf" in str(kwargs["selector_params"].values())  # content_types list includes pdf MIME


def test_run_with_ids(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_run") as mock_run,
    ):
        pool_instance = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)
        pool_instance.connection.return_value.__enter__.return_value.execute.return_value = cursor
        mock_pool.return_value = pool_instance
        mock_run.return_value = {"extracted": 0, "failed": 0, "skipped": 0}
        result = runner.invoke(app, ["process_attachments", "run", "--ids", "1,2,3"])
    assert result.exit_code == 0
    kwargs = mock_run.call_args.kwargs
    assert list(kwargs["selector_params"]["ids"]) == [1, 2, 3]


def test_process_attachments_status_shows_counts(tmp_path):
    with patch("maildb.cli._build_process_pool") as mock_pool:
        pool_instance = MagicMock()
        connection = pool_instance.connection.return_value.__enter__.return_value

        cursors = [MagicMock(), MagicMock(), MagicMock()]
        cursors[0].fetchall.return_value = [
            ("pending", 5),
            ("extracted", 100),
            ("failed", 2),
            ("skipped", 12),
        ]
        cursors[1].fetchall.return_value = []
        cursors[2].fetchall.return_value = []
        connection.execute.side_effect = cursors
        mock_pool.return_value = pool_instance
        result = runner.invoke(app, ["process_attachments", "status"])
    assert result.exit_code == 0
    assert "extracted" in result.output.lower()
    assert "100" in result.output


def test_process_attachments_retry_runs_only_failed(tmp_path):
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_run") as mock_run,
    ):
        mock_pool.return_value = object()
        mock_run.return_value = {"extracted": 0, "failed": 0, "skipped": 0}
        result = runner.invoke(app, ["process_attachments", "retry"])
    assert result.exit_code == 0
    kwargs = mock_run.call_args.kwargs
    # retry command forces retry_failed=True and restricts to failed-only
    assert kwargs["retry_failed"] is True
    # selector_sql should filter to status='failed' only
    assert "status = 'failed'" in kwargs["selector_sql"]


def test_process_attachments_reembed_dry_run_reports_counts_and_does_not_write():
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli._count_zero_vector_chunks", return_value=42),
        patch("maildb.cli._count_empty_extractions", return_value=5),
        patch("maildb.cli.pa_sweep_empty_extractions") as mock_sweep,
        patch("maildb.cli.pa_reembed_zero_vectors") as mock_reembed,
    ):
        mock_pool.return_value = object()
        result = runner.invoke(app, ["process_attachments", "reembed", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "42" in result.output
    assert "5" in result.output
    mock_sweep.assert_not_called()
    mock_reembed.assert_not_called()


def test_process_attachments_reembed_runs_sweep_and_reembed():
    with (
        patch("maildb.cli._build_process_pool") as mock_pool,
        patch("maildb.cli.pa_sweep_empty_extractions", return_value=3) as mock_sweep,
        patch(
            "maildb.cli.pa_reembed_zero_vectors",
            return_value={"reembedded": 10, "failed": 1},
        ) as mock_reembed,
        patch("maildb.cli.create_hnsw_index_attachment_chunks") as mock_idx,
    ):
        mock_pool.return_value = object()
        result = runner.invoke(app, ["process_attachments", "reembed", "--limit", "100"])
    assert result.exit_code == 0, result.output
    assert "reembedded=10" in result.output
    assert "failed=1" in result.output
    assert "swept_empty=3" in result.output
    mock_sweep.assert_called_once()
    mock_reembed.assert_called_once_with(mock_pool.return_value, limit=100)
    mock_idx.assert_called_once()


def test_process_attachments_status_shows_per_content_type_throughput():
    """Status output includes avg extraction_ms per content-type bucket."""
    with patch("maildb.cli._build_process_pool") as mock_pool:
        pool_instance = MagicMock()
        connection = pool_instance.connection.return_value.__enter__.return_value

        # Queries execute in order: status counts, failure reasons, per-content-type throughput.
        cursors = [MagicMock(), MagicMock(), MagicMock()]
        cursors[0].fetchall.return_value = [("extracted", 100)]
        cursors[1].fetchall.return_value = []
        cursors[2].fetchall.return_value = [
            ("application/pdf", 50, 500.0, 8.0),
            ("text/plain", 50, 5.0, 1.0),
        ]
        connection.execute.side_effect = cursors
        mock_pool.return_value = pool_instance

        result = runner.invoke(app, ["process_attachments", "status"])

    assert result.exit_code == 0, result.output
    assert "application/pdf" in result.output
    assert "500" in result.output  # avg extraction_ms
