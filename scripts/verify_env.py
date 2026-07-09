"""Verify required development/runtime services are up and functioning.

Born from a session where two silent environment breakages blocked work:
PostgreSQL crash-looping on a stale ``postmaster.pid`` (PID reuse after
the Apr 2026 kernel panic), and a dangling ``codex`` symlink that hung
for ten minutes instead of erroring. This script catches that class of
blocker in seconds, before a work session or drain.

Required checks (any failure → exit 1):

  1. PostgreSQL reachable at MAILDB_DATABASE_URL.
  2. pgvector extension installed in that database.
  3. Test database reachable (tests/conftest.py DSN) — `just check` needs it.
  4. Ollama server responding and the configured embedding model pulled.
  5. cv2 imports with INTER_LANCZOS4 (the half-installed-venv stub class).

Optional checks (WARN only):

  6. codex CLI resolves on PATH and actually executes (catches dangling
     symlinks that pass `which` but hang or fail on invocation).

Usage:

    just verify-env

Exit code 0 on PASS (warnings allowed), 1 on any required FAIL.
For a deeper extraction-pipeline check, run ``just smoke-marker``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# Matches the DSN hardcoded in tests/conftest.py.
TEST_DATABASE_URL = "postgresql://maildb_test@localhost:5432/maildb_test"


def _check(label: str, fn: Callable[[], str], *, required: bool = True) -> bool:
    """Run one check; print PASS/FAIL/WARN with timing. Returns overall ok."""
    t0 = time.monotonic()
    try:
        detail = fn()
    except Exception as exc:
        elapsed = time.monotonic() - t0
        status = "FAIL" if required else "WARN"
        print(f"  {status}  {label} ({elapsed:.1f}s) — {exc}")
        return not required
    else:
        elapsed = time.monotonic() - t0
        print(f"  PASS  {label} ({elapsed:.1f}s) — {detail}")
        return True


def check_postgres_main() -> str:
    import psycopg

    from maildb.config import Settings

    with psycopg.connect(Settings().database_url, connect_timeout=5) as conn:
        version = conn.execute("SELECT version()").fetchone()
        return str(version[0]).split(" on ")[0] if version else "connected"


def check_pgvector() -> str:
    import psycopg

    from maildb.config import Settings

    with psycopg.connect(Settings().database_url, connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        if row is None:
            raise RuntimeError("pgvector extension not installed in main database")
        return f"pgvector {row[0]}"


def check_postgres_test() -> str:
    import psycopg

    with psycopg.connect(TEST_DATABASE_URL, connect_timeout=5):
        return "maildb_test reachable"


def check_ollama() -> str:
    from maildb.config import Settings

    settings = Settings()
    url = f"{settings.ollama_url.rstrip('/')}/api/tags"
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 — local URL
        data = json.load(resp)
    names = [m.get("name", "") for m in data.get("models", [])]
    model = settings.embedding_model
    if not any(n == model or n.startswith(f"{model}:") for n in names):
        raise RuntimeError(f"embedding model '{model}' not pulled (available: {names or 'none'})")
    return f"server up, '{model}' available"


def check_cv2() -> str:
    import cv2

    if not hasattr(cv2, "INTER_LANCZOS4"):
        raise RuntimeError("cv2 imports but is a broken stub (no INTER_LANCZOS4)")
    return f"cv2 {cv2.__version__}"


def check_codex() -> str:
    path = shutil.which("codex")
    if path is None:
        raise RuntimeError("codex not on PATH (cheap-coder implementer unavailable)")
    result = subprocess.run(
        [path, "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"codex at {path} fails to execute: {result.stderr.strip()}")
    return result.stdout.strip() or path


def main() -> int:
    print("verify-env: checking required services\n")
    results = [
        _check("PostgreSQL (main database)", check_postgres_main),
        _check("pgvector extension", check_pgvector),
        _check("PostgreSQL (test database)", check_postgres_test),
        _check("Ollama + embedding model", check_ollama),
        _check("cv2 sanity", check_cv2),
        _check("codex CLI (cheap-coder)", check_codex, required=False),
    ]
    ok = all(results)
    print(f"\n{'PASS' if ok else 'FAIL'}: environment {'ready' if ok else 'not ready'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
