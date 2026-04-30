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
    uv run pytest --cov --cov-report=term-missing

check: fmt lint test

# Apply (or revert / check status of) the vendored surya MPS .max() fix.
# Required after `uv sync` reinstalls surya-ocr until upstream PR #493 ships.
patch-surya *ARGS="apply":
    uv run python scripts/surya_mps_patch.py {{ARGS}}

# Smoke-test the Marker extraction pipeline after dependency changes.
# Pass --extract to also run a fixture PDF end-to-end (slower, warmer caches).
# Catches the cv2-stub class of breakage from bad `uv add` / `uv remove`.
smoke-marker *ARGS:
    uv run python scripts/smoke_marker.py {{ARGS}}
