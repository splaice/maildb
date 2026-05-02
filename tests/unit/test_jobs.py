from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from maildb import jobs
from maildb.cli import app
from maildb.ingest.run_logs import RunLogDir


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


def test_render_includes_active_log_path_when_present():
    """`maildb jobs` should point operators at the live drain log so they can
    tail it during a run (#72)."""
    pool = MagicMock()
    fake_run_log = RunLogDir(
        run_id="20260501T120000Z-deadbeef",
        dir=Path("/Users/x/.maildb/logs/20260501T120000Z-deadbeef"),
        drain_log=Path("/Users/x/.maildb/logs/20260501T120000Z-deadbeef/drain.log"),
        run_json=Path("/Users/x/.maildb/logs/20260501T120000Z-deadbeef/run.json"),
    )
    with (
        patch.object(jobs, "list_maildb_processes", return_value=[]),
        patch.object(jobs, "attachment_counts", return_value={}),
        patch.object(jobs, "in_flight_rows", return_value=[]),
        patch.object(jobs, "completed_in_window", return_value={}),
        patch.object(jobs, "yield_by_content_type", return_value=[]),
        patch.object(jobs, "find_orphan_workers", return_value=[]),
        patch.object(jobs, "find_active_run_log", return_value=fake_run_log),
    ):
        out = jobs.render(jobs.snapshot(pool, window_minutes=30))
    assert "Active drain log" in out
    assert "20260501T120000Z-deadbeef" in out


def test_render_omits_active_log_section_when_no_active_run():
    pool = MagicMock()
    with (
        patch.object(jobs, "list_maildb_processes", return_value=[]),
        patch.object(jobs, "attachment_counts", return_value={}),
        patch.object(jobs, "in_flight_rows", return_value=[]),
        patch.object(jobs, "completed_in_window", return_value={}),
        patch.object(jobs, "yield_by_content_type", return_value=[]),
        patch.object(jobs, "find_orphan_workers", return_value=[]),
        patch.object(jobs, "find_active_run_log", return_value=None),
    ):
        out = jobs.render(jobs.snapshot(pool, window_minutes=30))
    assert "Active drain log" not in out


def test_find_orphan_workers_detects_spawn_with_ppid_1():
    """multiprocessing.spawn workers whose parent died (ppid=1) and whose
    command mentions maildb are flagged as orphans (#69). Live processes
    with a real ppid are skipped."""
    ps_output = (
        "  PID  PPID  PGID    RSS     ELAPSED ARGS\n"
        # Live worker — parent supervisor still around (ppid != 1)
        " 1234  1200  1200 437200    07:06:12 /usr/bin/python -c "
        "from multiprocessing.spawn import spawn_main; spawn_main(...) maildb worker\n"
        # Orphan worker — parent died, ppid=1
        " 5555     1  5555 280000    04:22:00 /usr/bin/python -c "
        "from multiprocessing.spawn import spawn_main; spawn_main(...) maildb worker\n"
        # Unrelated orphan (no maildb signal) — must not be flagged
        " 6666     1  6666  10000    01:00:00 /usr/bin/python some/other/thing.py\n"
        # Random non-spawn process — must not be flagged
        " 7777     1  7777   1000    00:10:00 /usr/sbin/cron\n"
    )
    with patch.object(jobs.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0, stdout=ps_output)
        orphans = jobs.find_orphan_workers()

    pids = {o.pid for o in orphans}
    assert pids == {5555}
    o = orphans[0]
    assert o.pgid == 5555
    assert o.elapsed == "04:22:00"
    assert o.rss_kb == 280000


def test_snapshot_includes_orphans():
    pool = MagicMock()
    fake_orphans = [
        jobs.OrphanProcess(
            pid=5555, pgid=5555, elapsed="04:22:00", rss_kb=280000, command="python -c spawn_main"
        )
    ]
    with (
        patch.object(jobs, "list_maildb_processes", return_value=[]),
        patch.object(jobs, "attachment_counts", return_value={}),
        patch.object(jobs, "in_flight_rows", return_value=[]),
        patch.object(jobs, "completed_in_window", return_value={}),
        patch.object(jobs, "yield_by_content_type", return_value=[]),
        patch.object(jobs, "find_orphan_workers", return_value=fake_orphans),
    ):
        snap = jobs.snapshot(pool, window_minutes=30)
    assert snap.orphans == fake_orphans


def test_render_includes_orphan_warning_section():
    pool = MagicMock()
    with (
        patch.object(jobs, "list_maildb_processes", return_value=[]),
        patch.object(jobs, "attachment_counts", return_value={}),
        patch.object(jobs, "in_flight_rows", return_value=[]),
        patch.object(jobs, "completed_in_window", return_value={}),
        patch.object(jobs, "yield_by_content_type", return_value=[]),
        patch.object(
            jobs,
            "find_orphan_workers",
            return_value=[
                jobs.OrphanProcess(
                    pid=5555,
                    pgid=5555,
                    elapsed="04:22:00",
                    rss_kb=280000,
                    command="python -c spawn_main maildb worker",
                ),
            ],
        ),
    ):
        out = jobs.render(jobs.snapshot(pool, window_minutes=30))
    assert "Lingering subprocesses" in out
    assert "5555" in out
    assert "orphan" in out.lower()


def test_kill_orphans_sends_sigkill_to_each_pgid():
    """kill_orphans iterates the list and SIGKILLs each process group.

    Group kill (not direct pid) is required to reap grandchildren torch/marker
    spawned (same reason _killpg_quietly exists in the supervisor)."""
    orphans = [
        jobs.OrphanProcess(pid=1111, pgid=1111, elapsed="01:00:00", rss_kb=1, command="x"),
        jobs.OrphanProcess(pid=2222, pgid=2222, elapsed="02:00:00", rss_kb=1, command="y"),
    ]
    with patch("os.killpg") as killpg:
        killed = jobs.kill_orphans(orphans)
    assert killed == [1111, 2222]
    # signal value comes second; just confirm both pgids were killed
    sent_pgids = [c.args[0] for c in killpg.call_args_list]
    assert sent_pgids == [1111, 2222]


def test_kill_orphans_swallows_already_gone_processes():
    """ProcessLookupError on a since-dead orphan must not abort the rest."""
    orphans = [
        jobs.OrphanProcess(pid=1111, pgid=1111, elapsed="01:00:00", rss_kb=1, command="x"),
        jobs.OrphanProcess(pid=2222, pgid=2222, elapsed="02:00:00", rss_kb=1, command="y"),
    ]
    with patch("os.killpg", side_effect=[ProcessLookupError, None]):
        killed = jobs.kill_orphans(orphans)
    # First was already gone; second was killed. Both reported in killed list
    # so the operator sees what was attempted.
    assert killed == [1111, 2222]


def test_yield_by_content_type_buckets_by_route():
    """Raw (content_type, status, count) rows from PG bucket into the same
    names the extraction router uses (pdf/docx/xlsx/pptx/image/text/html/...).
    Yield% = extracted / (extracted + failed); skipped not counted."""
    pool = MagicMock()
    cur = pool.connection.return_value.__enter__.return_value.execute.return_value
    cur.fetchall.return_value = [
        ("application/pdf", "extracted", 3210),
        ("application/pdf", "failed", 150),
        ("application/pdf", "skipped", 801),
        ("application/pdf", "pending", 1200),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "extracted",
            957,
        ),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "failed",
            42,
        ),
        ("image/png", "extracted", 100),
        ("image/jpeg", "extracted", 50),
        ("image/jpeg", "failed", 5),
        ("audio/mpeg", "skipped", 17),  # unsupported -> "other"
    ]

    result = jobs.yield_by_content_type(pool)

    by_bucket = {y.bucket: y for y in result}
    assert by_bucket["pdf"].extracted == 3210
    assert by_bucket["pdf"].failed == 150
    assert by_bucket["pdf"].skipped == 801
    assert by_bucket["pdf"].pending == 1200
    assert by_bucket["docx"].extracted == 957
    assert by_bucket["docx"].failed == 42
    # png and jpeg fold into a single "image" bucket
    assert by_bucket["image"].extracted == 150
    assert by_bucket["image"].failed == 5
    # unsupported types collapse into "other"
    assert by_bucket["other"].skipped == 17


def test_yield_by_content_type_yield_percent():
    """Yield % = extracted / (extracted + failed) * 100."""
    y = jobs.TypeYield(bucket="pdf", extracted=80, failed=20, skipped=0, pending=0)
    assert y.yield_pct == 80.0
    y = jobs.TypeYield(bucket="x", extracted=0, failed=0, skipped=5, pending=0)
    assert y.yield_pct is None  # no completions -> can't compute


def test_snapshot_includes_yield_by_type():
    pool = MagicMock()
    fake_yield = [jobs.TypeYield(bucket="pdf", extracted=10, failed=2, skipped=1, pending=5)]
    with (
        patch.object(jobs, "list_maildb_processes", return_value=[]),
        patch.object(jobs, "attachment_counts", return_value={"pending": 5}),
        patch.object(jobs, "in_flight_rows", return_value=[]),
        patch.object(jobs, "completed_in_window", return_value={}),
        patch.object(jobs, "yield_by_content_type", return_value=fake_yield),
    ):
        snap = jobs.snapshot(pool, window_minutes=30)
    assert snap.yield_by_type == fake_yield


def test_render_includes_yield_by_type_section():
    pool = MagicMock()
    with (
        patch.object(jobs, "list_maildb_processes", return_value=[]),
        patch.object(jobs, "attachment_counts", return_value={"pending": 0}),
        patch.object(jobs, "in_flight_rows", return_value=[]),
        patch.object(jobs, "completed_in_window", return_value={}),
        patch.object(
            jobs,
            "yield_by_content_type",
            return_value=[
                jobs.TypeYield(
                    bucket="pdf", extracted=3210, failed=150, skipped=801, pending=1200
                ),
                jobs.TypeYield(bucket="docx", extracted=957, failed=42, skipped=12, pending=0),
                jobs.TypeYield(bucket="other", extracted=0, failed=0, skipped=17, pending=0),
            ],
        ),
    ):
        out = jobs.render(jobs.snapshot(pool, window_minutes=30))

    assert "Yield by content-type" in out
    assert "pdf" in out
    assert "docx" in out
    # numbers visible
    assert "3,210" in out or "3210" in out
    # yield % rendered for buckets with completions
    assert "95.5%" in out or "95.5" in out  # 3210/(3210+150) ≈ 95.5
    # buckets with no completions show a placeholder, not "0.0%"
    assert "0.0%" not in out


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
        patch.object(jobs, "yield_by_content_type", return_value=[]),
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


def test_jobs_cli_kill_orphans_does_not_open_db_pool():
    """--kill-orphans is OS-only — Postgres being unreachable must NOT block
    the operator from cleaning up runaway workers (it's exactly when they
    need it). Branch on kill_orphans before constructing Settings/create_pool."""
    runner = CliRunner()
    with (
        patch("maildb.cli.create_pool") as create_pool,
        patch("maildb.cli.jobs_mod.find_orphan_workers", return_value=[]),
    ):
        result = runner.invoke(app, ["jobs", "--kill-orphans"])
    assert result.exit_code == 0, result.output
    create_pool.assert_not_called()


def test_jobs_cli_kill_orphans_prompts_and_kills_on_confirm():
    """--kill-orphans lists orphans, prompts, then kills on 'y'."""
    runner = CliRunner()
    fake_orphans = [
        jobs.OrphanProcess(pid=5555, pgid=5555, elapsed="04:22:00", rss_kb=2_100_000, command="x"),
    ]
    with (
        patch("maildb.cli.create_pool"),
        patch("maildb.cli.jobs_mod.find_orphan_workers", return_value=fake_orphans),
        patch("maildb.cli.jobs_mod.kill_orphans", return_value=[5555]) as kill,
    ):
        result = runner.invoke(app, ["jobs", "--kill-orphans"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Found 1 orphan" in result.output
    assert "5555" in result.output
    assert "SIGKILL" in result.output
    kill.assert_called_once_with(fake_orphans)


def test_jobs_cli_kill_orphans_aborts_on_no():
    """Bare `--kill-orphans` with `n` answer must NOT kill anything."""
    runner = CliRunner()
    fake_orphans = [
        jobs.OrphanProcess(pid=5555, pgid=5555, elapsed="04:22:00", rss_kb=1, command="x"),
    ]
    with (
        patch("maildb.cli.create_pool"),
        patch("maildb.cli.jobs_mod.find_orphan_workers", return_value=fake_orphans),
        patch("maildb.cli.jobs_mod.kill_orphans") as kill,
    ):
        result = runner.invoke(app, ["jobs", "--kill-orphans"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    kill.assert_not_called()


def test_jobs_cli_kill_orphans_yes_skips_prompt():
    """--yes runs without prompting."""
    runner = CliRunner()
    fake_orphans = [
        jobs.OrphanProcess(pid=5555, pgid=5555, elapsed="04:22:00", rss_kb=1, command="x"),
    ]
    with (
        patch("maildb.cli.create_pool"),
        patch("maildb.cli.jobs_mod.find_orphan_workers", return_value=fake_orphans),
        patch("maildb.cli.jobs_mod.kill_orphans", return_value=[5555]) as kill,
    ):
        result = runner.invoke(app, ["jobs", "--kill-orphans", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Kill all?" not in result.output  # no prompt was issued
    kill.assert_called_once()


def test_jobs_cli_kill_orphans_no_orphans():
    runner = CliRunner()
    with (
        patch("maildb.cli.create_pool"),
        patch("maildb.cli.jobs_mod.find_orphan_workers", return_value=[]),
        patch("maildb.cli.jobs_mod.kill_orphans") as kill,
    ):
        result = runner.invoke(app, ["jobs", "--kill-orphans"])
    assert result.exit_code == 0, result.output
    assert "No orphan" in result.output
    kill.assert_not_called()


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
        yield_by_type=[],
        orphans=[],
    )
    with (
        patch("maildb.cli.create_pool"),
        patch("maildb.cli.jobs_mod.snapshot", return_value=fake_snap),
    ):
        result = runner.invoke(app, ["jobs"])
    assert result.exit_code == 0, result.output
    assert "Attachment extraction" in result.output
    assert "pending" in result.output
