#!/usr/bin/env python3
"""Live-archive timing harness for Life Chronicle §16.2 experience targets.

Not part of ``just check-app`` — needs a running server + live DB.

Usage::

    uv run python perf/harness.py \\
        --base-url http://127.0.0.1:8400 \\
        --user owner --password '…'

Or via the root justfile (two-terminal flow documented there)::

    just perf-app --user owner --password '…'

Scenarios (N=5 runs each; first = cold, rest = warm):

1. GET  /api/archive/summary
2. POST /api/chronicle/buckets  full extent, year unit, 3 lanes
3. POST /api/chronicle/buckets  1-year viewport, month unit
4. POST /api/search             exact mode, common term, limit 25
5. POST /api/search             hybrid (skipped cleanly if embeddings down)
6. POST /api/sources/list       first page + one cursor page
7. GET  /api/topics             list only (generation NOT run — too slow)

Writes ``perf/results-<date>.json`` with scenarios, timings, §16.2 targets,
and pass/fail. Exit nonzero only when any warm p50 exceeds target × 2
(hard floor); soft misses are listed in the JSON for the 5.5 report.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

# §16.2 / task 5.4 warm p50 targets (milliseconds).
TARGETS_MS: dict[str, float] = {
    "archive_summary": 1000.0,  # summary < 1000ms warm
    "buckets_full_extent": 1500.0,  # buckets < 1500ms warm
    "buckets_1y_month": 1500.0,
    "search_exact": 2000.0,  # search exact < 2000ms
    "search_hybrid": 3000.0,  # hybrid under 2–3s; soft target
    "sources_list": 1000.0,  # list page < 1000ms
    "topics_list": 1000.0,
}

# Map scenario name → target key (shared targets for related scenarios).
SCENARIO_TARGET_KEY: dict[str, str] = {
    "archive_summary": "archive_summary",
    "buckets_full_extent": "buckets_full_extent",
    "buckets_1y_month": "buckets_1y_month",
    "search_exact": "search_exact",
    "search_hybrid": "search_hybrid",
    "sources_list": "sources_list",
    "topics_list": "topics_list",
}

DEFAULT_N = 5
COMMON_SEARCH_TERM = "the"
HARD_FLOOR_MULTIPLIER = 2.0


def percentile(sorted_vals: list[float], p: float) -> float:
    """Nearest-rank percentile for *p* in [0, 100] on a pre-sorted list."""
    if not sorted_vals:
        return 0.0
    if p <= 0:
        return sorted_vals[0]
    if p >= 100:
        return sorted_vals[-1]
    k = math.ceil(p / 100.0 * len(sorted_vals)) - 1
    k = max(0, min(k, len(sorted_vals) - 1))
    return sorted_vals[k]


def scenario_stats(timings_ms: list[float]) -> dict[str, Any]:
    """Compute cold/warm/p50/p95 from N wall-clock samples (first = cold)."""
    if not timings_ms:
        return {
            "n": 0,
            "cold_ms": None,
            "warm_ms": [],
            "all_ms": [],
            "p50_ms": None,
            "p95_ms": None,
            "warm_p50_ms": None,
            "warm_p95_ms": None,
        }
    warm = timings_ms[1:] if len(timings_ms) > 1 else list(timings_ms)
    all_sorted = sorted(timings_ms)
    warm_sorted = sorted(warm)
    return {
        "n": len(timings_ms),
        "cold_ms": timings_ms[0],
        "warm_ms": list(warm),
        "all_ms": list(timings_ms),
        "p50_ms": percentile(all_sorted, 50),
        "p95_ms": percentile(all_sorted, 95),
        "warm_p50_ms": percentile(warm_sorted, 50),
        "warm_p95_ms": percentile(warm_sorted, 95),
    }


def evaluate_scenario(
    name: str,
    stats: dict[str, Any],
    *,
    targets: dict[str, float] | None = None,
    skipped: bool = False,
    skip_reason: str | None = None,
) -> dict[str, Any]:
    """Attach target + pass/fail for one scenario.

    *pass* is relative to the §16.2 target (warm p50 ≤ target).
    *hard_floor_fail* is warm p50 > target × 2 (exit-code floor).
    """
    tgt_map = targets if targets is not None else TARGETS_MS
    target_key = SCENARIO_TARGET_KEY.get(name, name)
    target = tgt_map.get(target_key)
    warm_p50 = stats.get("warm_p50_ms")

    if skipped:
        return {
            "name": name,
            "skipped": True,
            "skip_reason": skip_reason,
            "target_ms": target,
            "pass": None,
            "hard_floor_fail": False,
            **stats,
        }

    passed: bool | None
    hard_fail = False
    if target is None or warm_p50 is None:
        passed = None
    else:
        passed = float(warm_p50) <= float(target)
        hard_fail = float(warm_p50) > float(target) * HARD_FLOOR_MULTIPLIER

    return {
        "name": name,
        "skipped": False,
        "skip_reason": None,
        "target_ms": target,
        "pass": passed,
        "hard_floor_fail": hard_fail,
        **stats,
    }


def build_report(
    scenarios: list[dict[str, Any]],
    *,
    environment: dict[str, Any],
    targets: dict[str, float] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the machine-readable results document."""
    tgt = targets if targets is not None else dict(TARGETS_MS)
    failures = [s["name"] for s in scenarios if not s.get("skipped") and s.get("pass") is False]
    hard_floor_failures = [s["name"] for s in scenarios if s.get("hard_floor_fail")]
    return {
        "generated_at": generated_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "targets_ms": tgt,
        "hard_floor_multiplier": HARD_FLOOR_MULTIPLIER,
        "environment": environment,
        "scenarios": scenarios,
        "failures": failures,
        "hard_floor_failures": hard_floor_failures,
        "notes": [
            "topics generation is NOT run (too slow for the harness).",
            "search_hybrid is skipped cleanly when the embedding service is down.",
            "Warm p50 is computed over runs 2..N (first run is cold).",
            "Exit nonzero only when any warm p50 exceeds target × 2.",
        ],
    }


def hard_floor_failed(report: dict[str, Any]) -> bool:
    """True when any scenario tripped the target × 2 hard floor."""
    return bool(report.get("hard_floor_failures"))


def time_call(fn: Callable[[], None]) -> float:
    """Wall-clock milliseconds for one call."""
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000.0


def run_timed(
    fn: Callable[[], None],
    *,
    n: int = DEFAULT_N,
) -> list[float]:
    """Run *fn* N times; return list of wall-clock ms (first = cold)."""
    timings: list[float] = []
    for _ in range(n):
        timings.append(time_call(fn))
    return timings


class HarnessClient:
    """Thin cookie-session client over httpx."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def login(self, username: str, password: str) -> None:
        r = self._client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        r.raise_for_status()

    def get_summary(self) -> dict[str, Any]:
        r = self._client.get("/api/archive/summary")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    def post_buckets(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post("/api/chronicle/buckets", json=body)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    def post_search(self, body: dict[str, Any]) -> httpx.Response:
        return self._client.post("/api/search", json=body)

    def post_sources_list(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post("/api/sources/list", json=body)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]

    def get_topics(self) -> dict[str, Any]:
        r = self._client.get("/api/topics")
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]


def _extent_from_summary(summary: dict[str, Any]) -> tuple[str, str]:
    dr = summary.get("date_range") or {}
    raw_from = dr.get("from")
    raw_to = dr.get("to")
    if not raw_from or not raw_to:
        # Empty archive fallback: one year ending now.
        end = datetime.now(UTC)
        start = end - timedelta(days=365)
        return (
            start.isoformat().replace("+00:00", "Z"),
            end.isoformat().replace("+00:00", "Z"),
        )
    return _to_utc_iso(str(raw_from)), _to_utc_iso(str(raw_to))


def _to_utc_iso(raw: str) -> str:
    """Normalize any ISO-ish timestamp (offset-bearing, Z, or date-only) to UTC Z form."""
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if len(s) == 10:
        s += "T00:00:00+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _one_year_viewport(extent_to: str) -> tuple[str, str]:
    """1-year viewport ending at *extent_to* (or now)."""
    try:
        end = datetime.fromisoformat(extent_to.replace("Z", "+00:00"))
    except ValueError:
        end = datetime.now(UTC)
    start = end - timedelta(days=365)
    return (
        start.isoformat().replace("+00:00", "Z"),
        end.isoformat().replace("+00:00", "Z"),
    )


def run_scenarios(
    hc: HarnessClient,
    *,
    n: int = DEFAULT_N,
    targets: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute all harness scenarios; return (scenario results, environment)."""
    summary = hc.get_summary()
    counts = summary.get("counts") or {}
    environment = {
        "row_counts": counts,
        "date_range": summary.get("date_range"),
        "versions": summary.get("versions"),
        "n_runs": n,
    }

    extent_from, extent_to = _extent_from_summary(summary)
    year_from, year_to = _one_year_viewport(extent_to)

    results: list[dict[str, Any]] = []

    # 1. archive/summary
    timings = run_timed(lambda: hc.get_summary(), n=n)
    results.append(evaluate_scenario("archive_summary", scenario_stats(timings), targets=targets))

    # 2. buckets full extent, year unit, 3 lanes
    buckets_full = {
        "scope": {},
        "viewport": {"from": extent_from, "to": extent_to},
        "pixel_width": 920,
        "aggregation": "year",
        "lanes": ["messages", "attachments", "people"],
    }
    timings = run_timed(lambda: hc.post_buckets(buckets_full), n=n)
    results.append(
        evaluate_scenario("buckets_full_extent", scenario_stats(timings), targets=targets)
    )

    # 3. buckets 1-year viewport, month unit
    buckets_1y = {
        "scope": {},
        "viewport": {"from": year_from, "to": year_to},
        "pixel_width": 920,
        "aggregation": "month",
        "lanes": ["messages", "attachments", "people"],
    }
    timings = run_timed(lambda: hc.post_buckets(buckets_1y), n=n)
    results.append(evaluate_scenario("buckets_1y_month", scenario_stats(timings), targets=targets))

    # 4. search exact
    search_exact_body = {
        "query": COMMON_SEARCH_TERM,
        "mode": "exact",
        "limit": 25,
        "include_facets": False,
    }

    def _search_exact() -> None:
        r = hc.post_search(search_exact_body)
        r.raise_for_status()

    timings = run_timed(_search_exact, n=n)
    results.append(evaluate_scenario("search_exact", scenario_stats(timings), targets=targets))

    # 5. search hybrid — skip cleanly if embedding service is down
    search_hybrid_body = {
        "query": COMMON_SEARCH_TERM,
        "mode": "hybrid",
        "limit": 25,
        "include_facets": False,
    }
    hybrid_skipped = False
    hybrid_reason: str | None = None
    try:
        probe = hc.post_search(search_hybrid_body)
        if probe.status_code >= 500:
            hybrid_skipped = True
            hybrid_reason = f"search hybrid returned HTTP {probe.status_code}"
        else:
            body = probe.json()
            degraded = body.get("degraded") or {}
            if "embedding" in degraded or "semantic" in degraded:
                hybrid_skipped = True
                hybrid_reason = f"embedding degraded: {degraded}"
            elif probe.status_code >= 400:
                hybrid_skipped = True
                hybrid_reason = f"search hybrid HTTP {probe.status_code}"
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
        hybrid_skipped = True
        hybrid_reason = f"embedding service unavailable: {exc}"

    if hybrid_skipped:
        results.append(
            evaluate_scenario(
                "search_hybrid",
                scenario_stats([]),
                targets=targets,
                skipped=True,
                skip_reason=hybrid_reason,
            )
        )
    else:

        def _search_hybrid() -> None:
            r = hc.post_search(search_hybrid_body)
            r.raise_for_status()

        timings = run_timed(_search_hybrid, n=n)
        results.append(evaluate_scenario("search_hybrid", scenario_stats(timings), targets=targets))

    # 6. sources/list first page + one cursor page
    sources_body_base = {
        "scope": {},
        "date_from": year_from,
        "date_to": year_to,
        "limit": 50,
    }

    def _sources_two_pages() -> None:
        first = hc.post_sources_list(sources_body_base)
        cursor = first.get("next_cursor")
        if cursor:
            hc.post_sources_list({**sources_body_base, "cursor": cursor})

    timings = run_timed(_sources_two_pages, n=n)
    results.append(evaluate_scenario("sources_list", scenario_stats(timings), targets=targets))

    # 7. topics list (generation NOT run)
    timings = run_timed(lambda: hc.get_topics(), n=n)
    results.append(evaluate_scenario("topics_list", scenario_stats(timings), targets=targets))

    return results, environment


def write_report(report: dict[str, Any], out_dir: Path) -> Path:
    """Write ``results-<date>.json`` under *out_dir*; return path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    path = out_dir / f"results-{day}.json"
    path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def run_harness(
    *,
    base_url: str,
    username: str,
    password: str,
    n: int = DEFAULT_N,
    out_dir: Path | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Full harness: login, scenarios, write report. Returns the report dict."""
    owns_client = client is None
    if client is None:
        client = httpx.Client(base_url=base_url.rstrip("/"), timeout=120.0)
    try:
        hc = HarnessClient(client)
        hc.login(username, password)
        scenarios, environment = run_scenarios(hc, n=n)
        environment["base_url"] = base_url.rstrip("/")
        report = build_report(scenarios, environment=environment)
        dest = out_dir if out_dir is not None else Path(__file__).resolve().parent
        path = write_report(report, dest)
        report["results_path"] = str(path)
        return report
    finally:
        if owns_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Life Chronicle live-archive timing harness (§16.2 targets)."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8400",
        help="Chronicle server base URL (default: http://127.0.0.1:8400)",
    )
    parser.add_argument("--user", required=True, help="Login username")
    parser.add_argument("--password", required=True, help="Login password")
    parser.add_argument(
        "-n",
        type=int,
        default=DEFAULT_N,
        help=f"Runs per scenario (default {DEFAULT_N}; first is cold)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Directory for results-<date>.json (default: perf/)",
    )
    args = parser.parse_args(argv)

    try:
        report = run_harness(
            base_url=args.base_url,
            username=args.user,
            password=args.password,
            n=args.n,
            out_dir=args.out_dir,
        )
    except httpx.HTTPError as exc:
        print(f"harness HTTP error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"harness failed: {exc}", file=sys.stderr)
        return 2

    path = report.get("results_path", "")
    print(f"Wrote {path}")
    for s in report["scenarios"]:
        if s.get("skipped"):
            print(f"  SKIP  {s['name']}: {s.get('skip_reason')}")
            continue
        status = "PASS" if s.get("pass") else "FAIL"
        warm = s.get("warm_p50_ms")
        tgt = s.get("target_ms")
        print(f"  {status}  {s['name']}: warm_p50={warm:.1f}ms target={tgt}ms")

    if hard_floor_failed(report):
        print(
            "HARD FLOOR: warm p50 exceeded target × "
            f"{HARD_FLOOR_MULTIPLIER} for: {report['hard_floor_failures']}",
            file=sys.stderr,
        )
        return 1

    if report["failures"]:
        print(f"Soft misses (exit 0): {report['failures']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
