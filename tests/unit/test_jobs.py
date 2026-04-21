from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from maildb import jobs
from maildb.cli import app


def test_format_duration_buckets():
    assert jobs.format_duration(0) == "0s"
    assert jobs.format_duration(42) == "42s"
    assert jobs.format_duration(90) == "1m 30s"
    assert jobs.format_duration(3600) == "1h 00m"
    assert jobs.format_duration(3600 * 24 + 7200) == "1d 02h"


def test_list_maildb_processes_parses_ps_output():
    """ps output is parsed into ProcessInfo records; own pid and grep lines skipped."""
    ps_output = (
        "  PID  %CPU   RSS     ELAPSED ARGS\n"
        " 1234  45.2 437200    07:06:12 /bin/maildb process_attachments retry\n"
        " 5678   0.1  32640    07:06:10 uv run maildb process_attachments retry\n"
        " 9999   0.0   1000    07:06:10 grep maildb\n"
        "11111   0.0   1000 1-03:45:21 /bin/python -m some_other_thing\n"
        "22222   0.0   1000    00:00:01 /bin/maildb jobs\n"
    )
    with patch.object(jobs.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0, stdout=ps_output)
        procs = jobs.list_maildb_processes(exclude_pid=22222)

    pids = {p.pid for p in procs}
    assert pids == {1234, 5678}  # not the "jobs" self, not grep, not non-maildb
    by_pid = {p.pid: p for p in procs}
    assert by_pid[1234].cpu_pct == 45.2
    assert by_pid[1234].rss_kb == 437200
    assert by_pid[1234].elapsed == "07:06:12"


def test_attachment_counts():
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [
        ("extracted", 5059),
        ("failed", 6480),
        ("skipped", 24030),
    ]
    counts = jobs.attachment_counts(pool)
    assert counts == {"extracted": 5059, "failed": 6480, "skipped": 24030}


def test_in_flight_rows_returns_extracting_with_stuck_for():
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [
        (11, "Investment Deck.pdf", 4133781, 25460),
    ]
    result = jobs.in_flight_rows(pool)
    assert len(result) == 1
    assert result[0].attachment_id == 11
    assert result[0].filename == "Investment Deck.pdf"
    assert result[0].size_bytes == 4133781
    assert result[0].stuck_for_s == 25460


def test_completed_in_window_filters_by_status_and_time():
    pool = MagicMock()
    cur = pool.connection.return_value.__enter__.return_value.execute.return_value
    cur.fetchall.return_value = [("extracted", 42), ("failed", 3)]

    result = jobs.completed_in_window(pool, window_minutes=30)

    assert result == {"extracted": 42, "failed": 3}
    # SQL should filter to the right statuses and use the window
    sql = pool.connection.return_value.__enter__.return_value.execute.call_args.args[0]
    assert "'extracted'" in sql
    assert "'failed'" in sql
    assert "'skipped'" in sql
    assert "extracted_at" in sql


def test_snapshot_computes_rate_and_eta():
    """Rate = completed/window_minutes; ETA = remaining/rate converted to seconds."""
    pool = MagicMock()
    with (
        patch.object(jobs, "list_maildb_processes", return_value=[]),
        patch.object(
            jobs,
            "attachment_counts",
            return_value={"pending": 100, "failed": 6000, "extracted": 5000},
        ),
        patch.object(jobs, "in_flight_rows", return_value=[]),
        patch.object(
            jobs,
            "completed_in_window",
            return_value={"extracted": 30, "failed": 10, "skipped": 20},
        ),
    ):
        snap = jobs.snapshot(pool, window_minutes=30)

    # 60 completed in 30 min -> 2 docs/min
    assert snap.rate_per_min == 2.0
    # pending=100 / 2 per min = 50 min = 3000 s
    assert snap.eta_for_status["pending"] == 3000
    # failed=6000 / 2 per min = 3000 min = 180000 s
    assert snap.eta_for_status["failed"] == 180000


def test_snapshot_eta_none_when_no_completions():
    pool = MagicMock()
    with (
        patch.object(jobs, "list_maildb_processes", return_value=[]),
        patch.object(jobs, "attachment_counts", return_value={"pending": 100}),
        patch.object(jobs, "in_flight_rows", return_value=[]),
        patch.object(jobs, "completed_in_window", return_value={}),
    ):
        snap = jobs.snapshot(pool, window_minutes=30)

    assert snap.rate_per_min == 0.0
    assert snap.eta_for_status["pending"] is None


def test_render_includes_headline_sections():
    pool = MagicMock()
    with (
        patch.object(
            jobs,
            "list_maildb_processes",
            return_value=[
                jobs.ProcessInfo(
                    pid=4242,
                    elapsed="01:23:45",
                    cpu_pct=33.3,
                    rss_kb=500000,
                    command="maildb process_attachments retry",
                )
            ],
        ),
        patch.object(
            jobs,
            "attachment_counts",
            return_value={"pending": 0, "extracting": 1, "failed": 100},
        ),
        patch.object(
            jobs,
            "in_flight_rows",
            return_value=[
                jobs.InFlight(
                    attachment_id=11,
                    filename="deck.pdf",
                    size_bytes=4_000_000,
                    stuck_for_s=3600,
                )
            ],
        ),
        patch.object(
            jobs,
            "completed_in_window",
            return_value={"extracted": 8, "failed": 2},
        ),
    ):
        out = jobs.render(jobs.snapshot(pool, window_minutes=30))

    assert "Active processes" in out
    assert "4242" in out
    assert "Attachment extraction" in out
    assert "In flight" in out
    assert "deck.pdf" in out
    assert "1h 00m" in out
    assert "Throughput (last 30m)" in out
    assert "ETA" in out


def test_jobs_cli_prints_snapshot_and_exits_without_watch():
    runner = CliRunner()
    fake_snap = jobs.JobsSnapshot(
        processes=[],
        counts={"pending": 5},
        in_flight=[],
        completed_in_window={"extracted": 10},
        window_minutes=30,
        rate_per_min=10 / 30,
        eta_for_status={"pending": 900, "failed": None},
    )
    with (
        patch("maildb.cli.create_pool"),
        patch("maildb.cli.jobs_mod.snapshot", return_value=fake_snap),
    ):
        result = runner.invoke(app, ["jobs"])
    assert result.exit_code == 0, result.output
    assert "Attachment extraction" in result.output
    assert "pending" in result.output
