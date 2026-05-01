"""Smoke-test the Marker extraction pipeline after dependency changes.

The Apr 2026 drain hit a half-installed ``opencv-python-headless`` after
``uv remove docling`` — ``cv2`` became an empty stub and Marker started
failing immediately on every doc with ``module 'cv2' has no attribute
'INTER_LANCZOS4'``. Lost ~30 minutes diagnosing.

This script catches that class of breakage in <30 seconds:

  1. Import cv2 and check INTER_LANCZOS4 (the specific attribute that
     was missing on the broken stub).
  2. Import marker, marker.converters.pdf.PdfConverter, and
     marker.models.create_model_dict.
  3. With ``--extract``, additionally run extract_markdown on the
     hello.pdf fixture (warmer caches, slower).

Usage:

    just smoke-marker             # imports + cv2 attr check
    just smoke-marker --extract   # also exercises the full pipeline

Exit code 0 on PASS, 1 on FAIL. Run after any ``uv add`` / ``uv remove``
involving heavy ML deps.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PDF = REPO_ROOT / "tests" / "fixtures" / "attachments" / "hello.pdf"


def _check(label: str, fn) -> tuple[bool, str]:
    t0 = time.monotonic()
    try:
        msg = fn() or ""
    except Exception:
        return False, f"FAIL {label} ({time.monotonic() - t0:.2f}s)\n{traceback.format_exc()}"
    return True, f"PASS {label} ({time.monotonic() - t0:.2f}s){' — ' + msg if msg else ''}"


def _check_cv2() -> str:
    import cv2  # type: ignore[import-not-found]

    if not hasattr(cv2, "INTER_LANCZOS4"):
        msg = "cv2 missing INTER_LANCZOS4 — likely an empty stub from a bad uv resolve"
        raise AttributeError(msg)
    return f"cv2 {cv2.__version__}"


def _check_marker_imports() -> str:
    import marker  # type: ignore[import-untyped]
    from marker.converters.pdf import (
        PdfConverter,  # type: ignore[import-untyped]  # noqa: F401
    )
    from marker.models import (
        create_model_dict,  # type: ignore[import-untyped]  # noqa: F401
    )

    return f"marker {getattr(marker, '__version__', '?')}"


def _check_extract() -> str:
    if not FIXTURE_PDF.exists():
        msg = f"fixture missing: {FIXTURE_PDF}"
        raise FileNotFoundError(msg)
    from maildb.ingest.extraction import extract_markdown

    result = extract_markdown(FIXTURE_PDF, content_type="application/pdf")
    if not result.markdown.strip():
        msg = "extracted markdown was empty"
        raise RuntimeError(msg)
    return f"extracted {len(result.markdown)} chars via {result.extractor_version}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the Marker pipeline.")
    parser.add_argument(
        "--extract",
        action="store_true",
        help="also run extract_markdown on the hello.pdf fixture (slower)",
    )
    args = parser.parse_args()

    checks = [("cv2", _check_cv2), ("marker imports", _check_marker_imports)]
    if args.extract:
        checks.append(("extract hello.pdf", _check_extract))

    overall = True
    for label, fn in checks:
        ok, line = _check(label, fn)
        print(line)
        overall = overall and ok

    print("---")
    print("PASS" if overall else "FAIL")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
