set dotenv-load

default:
    @just --list

fmt:
    uv run ruff format .
    uv run ruff check . --fix

lint:
    uv run ruff check .
    uv run mypy src/

test *ARGS:
    uv run pytest {{ARGS}}

test-unit *ARGS:
    uv run pytest tests/unit/ {{ARGS}}

test-integration *ARGS:
    uv run pytest tests/integration/ -m integration {{ARGS}}

test-cov:
    uv run pytest --cov --cov-report=term-missing:skip-covered

check: fmt lint test-cov

# Apply (or revert / check status of) the vendored surya MPS .max() fix.
# Required after `uv sync` reinstalls surya-ocr until upstream PR #493 ships.
patch-surya *ARGS="apply":
    uv run python scripts/surya_mps_patch.py {{ARGS}}

# Verify required services are up (PostgreSQL + pgvector, test DB, Ollama +
# embedding model, venv sanity, cheap-coder CLI). Run before a work session
# or drain to catch environment blockers early.
verify-env:
    uv run python scripts/verify_env.py

# Smoke-test the Marker extraction pipeline after dependency changes.
# Pass --extract to also run a fixture PDF end-to-end (slower, warmer caches).
# Catches the cv2-stub class of breakage from bad `uv add` / `uv remove`.
smoke-marker *ARGS:
    uv run python scripts/smoke_marker.py {{ARGS}}

check-app:
    cd apps/chronicle/server && uv run ruff check . && uv run mypy src/ && uv run pytest
    cd apps/chronicle/web && pnpm exec tsc -b --noEmit && pnpm exec vitest run && pnpm build

# Live-archive timing harness for §16.2 targets (NOT part of check-app).
# Two-terminal flow:
#   Terminal 1: cd apps/chronicle/server && uv run python -m chronicle_server
#   Terminal 2: just perf-app --user <user> --password <password>
# Optional: --base-url http://127.0.0.1:8400  -n 5  --out-dir …
# Writes apps/chronicle/server/perf/results-<date>.json
perf-app *ARGS:
    cd apps/chronicle/server && uv run python perf/harness.py {{ARGS}}
