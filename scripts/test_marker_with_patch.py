"""Run Marker against a CSV of known-failed PDFs and report what changed.

Reads (id, storage_path, reason) rows, calls maildb's existing
extract_markdown wrapper around Marker once per file, prints a per-file
verdict (ok / same-error / new-error / timeout) plus a summary.
"""

from __future__ import annotations

import argparse
import csv
import signal
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True, type=Path)
    p.add_argument("--attachment-dir", required=True, type=Path)
    p.add_argument("--timeout-s", default=300, type=int)
    return p.parse_args()


class Timeout(Exception):
    pass


def _alarm(_s, _f):
    raise Timeout


def main() -> int:
    args = parse_args()

    print("loading marker…", file=sys.stderr)
    from maildb.ingest.extraction import extract_markdown

    rows: list[dict[str, str]] = []
    with args.csv.open() as f:
        rows.extend(csv.DictReader(f))

    results: list[dict] = []
    for i, row in enumerate(rows, 1):
        path = args.attachment_dir / row["storage_path"]
        prior_reason = row.get("reason", "")
        print(f"[{i}/{len(rows)}] id={row['id']} {row['filename'][:60]}", file=sys.stderr)
        old = signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(args.timeout_s)
        t0 = time.monotonic()
        try:
            r = extract_markdown(path, content_type="application/pdf")
            elapsed = time.monotonic() - t0
            results.append(
                {
                    "id": row["id"],
                    "verdict": "ok",
                    "elapsed_s": round(elapsed, 1),
                    "out_kb": len(r.markdown.encode("utf-8")) // 1024,
                    "prior_reason": prior_reason[:60],
                    "new_error": "",
                }
            )
            print(f"   → ok  {elapsed:.1f}s  out_kb={len(r.markdown) // 1024}", file=sys.stderr)
        except Timeout:
            results.append(
                {
                    "id": row["id"],
                    "verdict": "timeout",
                    "elapsed_s": float(args.timeout_s),
                    "out_kb": 0,
                    "prior_reason": prior_reason[:60],
                    "new_error": f"timeout {args.timeout_s}s",
                }
            )
            print("   → TIMEOUT", file=sys.stderr)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            err = f"{type(exc).__name__}: {str(exc)[:200]}"
            same = "out of bounds" in str(exc).lower() and "out of bounds" in prior_reason.lower()
            results.append(
                {
                    "id": row["id"],
                    "verdict": "same-error" if same else "new-error",
                    "elapsed_s": round(elapsed, 1),
                    "out_kb": 0,
                    "prior_reason": prior_reason[:60],
                    "new_error": err[:120],
                }
            )
            print(f"   → {('SAME' if same else 'NEW')}  {err[:100]}", file=sys.stderr)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)

    print("\n# Patch test results\n")
    print("| id | verdict | elapsed_s | out_kb | prior_reason | new_error |")
    print("|---|---|---|---|---|---|")
    for r in results:
        print(
            f"| {r['id']} | {r['verdict']} | {r['elapsed_s']} | {r['out_kb']} | "
            f"{r['prior_reason']} | {r['new_error']} |"
        )

    n = len(results)
    counts = {
        v: sum(1 for r in results if r["verdict"] == v)
        for v in ("ok", "same-error", "new-error", "timeout")
    }
    print(
        f"\nTotal: {n}  ok={counts['ok']}  same-error={counts['same-error']}  "
        f"new-error={counts['new-error']}  timeout={counts['timeout']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
