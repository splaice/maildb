import pytest

from maildb.ingest.progress import ProgressTracker


def test_rate_calculation() -> None:
    tracker = ProgressTracker(total=1000)
    tracker.update(100, elapsed_seconds=10.0)
    assert tracker.rate() == pytest.approx(10.0)


def test_eta_calculation() -> None:
    tracker = ProgressTracker(total=1000)
    tracker.update(100, elapsed_seconds=10.0)
    eta = tracker.eta_seconds()
    assert eta == pytest.approx(90.0)


def test_eta_returns_none_when_no_progress() -> None:
    tracker = ProgressTracker(total=1000)
    assert tracker.eta_seconds() is None


def test_format_eta() -> None:
    tracker = ProgressTracker(total=1000)
    tracker.update(100, elapsed_seconds=10.0)
    formatted = tracker.format_eta()
    assert "1m 30s" in formatted


def test_format_eta_hours() -> None:
    tracker = ProgressTracker(total=100_000)
    tracker.update(100, elapsed_seconds=10.0)
    formatted = tracker.format_eta()
    assert "h" in formatted


def test_summary_line() -> None:
    tracker = ProgressTracker(total=841_930)
    tracker.update(125_000, elapsed_seconds=2588.0)
    line = tracker.summary_line()
    assert "125,000" in line
    assert "841,930" in line
    assert "%" in line
    assert "msg/s" in line
    assert "ETA" in line


def test_percentage() -> None:
    tracker = ProgressTracker(total=1000)
    tracker.update(148, elapsed_seconds=10.0)
    assert tracker.percentage() == pytest.approx(14.8)
