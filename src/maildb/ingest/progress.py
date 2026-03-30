from __future__ import annotations


class ProgressTracker:
    """Tracks progress, rate, and ETA for long-running operations."""

    def __init__(self, total: int) -> None:
        self._total = total
        self._processed = 0
        self._elapsed: float = 0.0

    def update(self, processed: int, elapsed_seconds: float) -> None:
        self._processed = processed
        self._elapsed = elapsed_seconds

    def rate(self) -> float:
        if self._elapsed <= 0:
            return 0.0
        return self._processed / self._elapsed

    def percentage(self) -> float:
        if self._total <= 0:
            return 0.0
        return (self._processed / self._total) * 100

    def eta_seconds(self) -> float | None:
        r = self.rate()
        if r <= 0:
            return None
        remaining = self._total - self._processed
        return remaining / r

    def format_eta(self) -> str:
        eta = self.eta_seconds()
        if eta is None:
            return "ETA: unknown"
        seconds = int(eta)
        if seconds >= 3600:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            return f"ETA: {h}h {m:02d}m"
        m = seconds // 60
        s = seconds % 60
        return f"ETA: {m}m {s:02d}s"

    def summary_line(self) -> str:
        pct = self.percentage()
        r = self.rate()
        eta = self.format_eta()
        return f"{self._processed:,} / {self._total:,} ({pct:.1f}%) — {r:.1f} msg/s — {eta}"
