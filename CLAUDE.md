@_shared/prompts/base-persona.md

# Project Standards

Python 3.12+ project using FastAPI, SQLAlchemy, Pydantic v2, managed with uv.

## Quick Reference

```bash
# Environment
uv sync                         # Install/update all dependencies
uv run just <target>            # Run any justfile target

# Development
uv run just dev                 # Start FastAPI dev server
uv run just fmt                 # Format with Ruff
uv run just lint                # Lint with Ruff + type check with mypy
uv run just test                # Run pytest with coverage
uv run just check               # fmt + lint + test (run before committing)

# Database
uv run just db-migrate "msg"    # Generate Alembic migration
uv run just db-upgrade          # Apply migrations
uv run just db-downgrade        # Rollback one migration
```

## Critical Rules

- ALWAYS use `uv` for dependency management. Never use pip directly.
- ALWAYS use `uv run` to execute commands (ensures correct virtualenv).
- ALL config lives in `pyproject.toml` — no setup.cfg, setup.py, or tool-specific config files.
- ALL linting/formatting uses Ruff. No flake8, black, or isort.
- NEVER commit without running `uv run just check`.
- Use Pydantic v2 models for all data validation and serialization, including API schemas.
- Use `pydantic-settings` for all environment/configuration management.
- Use SQLAlchemy 2.0 style (mapped_column, DeclarativeBase) — never legacy 1.x patterns.
- Write a test for every new endpoint, service function, and model method.
- Use `httpx.AsyncClient` with FastAPI's async test pattern — not the sync TestClient.

## Detailed Standards

- Python tooling, packages, and project structure: @docs/standards/python-tooling.md
- Testing strategy and patterns: @docs/standards/testing.md
