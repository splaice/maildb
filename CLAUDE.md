# Project Standards

Python 3.12+ project — PostgreSQL, psycopg3, pgvector, Ollama, Pydantic v2, managed with uv.

## Quick Reference

```bash
# Environment
uv sync                         # Install/update all dependencies
uv run just <target>            # Run any justfile target

# Development
uv run just fmt                 # Format with Ruff
uv run just lint                # Lint with Ruff + type check with mypy
uv run just test                # Run pytest
uv run just check               # fmt + lint + test (run before committing)
```

## Design Document

`docs/DESIGN.md` contains the full system design: schema, API, embedding strategy, ingestion pipeline, and implementation roadmap. Read it when planning or implementing changes to these areas.

## Critical Rules

- ALWAYS use `uv` for dependency management. Never use pip directly.
- ALWAYS use `uv run` to execute commands (ensures correct virtualenv).
- ALL config lives in `pyproject.toml` — no setup.cfg, setup.py, or tool-specific config files.
- ALL linting/formatting uses Ruff. No flake8, black, or isort.
- NEVER commit without running `uv run just check`.
- Use `pydantic-settings` for all environment/configuration management.
- Write a test for every new MailDB method, MCP tool, and ingestion/parsing function.
