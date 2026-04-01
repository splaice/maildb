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
