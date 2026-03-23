# Python Tooling & Package Standards

## Package & Environment Management — uv

uv is the sole tool for dependency management, virtual environments, and running commands.

- `uv init` to scaffold new projects
- `uv add <pkg>` to add dependencies (writes to pyproject.toml, updates uv.lock)
- `uv add --dev <pkg>` for dev dependencies
- `uv sync` to install from lockfile
- `uv run <cmd>` to run any command in the project virtualenv
- Commit both `pyproject.toml` and `uv.lock` to version control
- Never use `pip install`, `pip freeze`, `poetry`, or `pipenv`
- Pin Python version in pyproject.toml: `requires-python = ">=3.12"`

## Project Configuration — pyproject.toml

All tool configuration lives in `pyproject.toml`. No standalone config files.

```toml
[project]
name = "myproject"
version = "0.1.0"
requires-python = ">=3.12"

[tool.ruff]
target-version = "py312"
line-length = 99

[tool.ruff.lint]
select = [
    "E",     # pycodestyle errors
    "W",     # pycodestyle warnings
    "F",     # pyflakes
    "I",     # isort
    "N",     # pep8-naming
    "UP",    # pyupgrade
    "B",     # flake8-bugbear
    "SIM",   # flake8-simplify
    "RUF",   # ruff-specific
    "S",     # flake8-bandit (security)
    "T20",   # flake8-print (no print statements)
    "PTH",   # flake8-use-pathlib
    "ERA",   # eradicate (commented-out code)
]
ignore = ["E501"]  # line length handled by formatter

[tool.ruff.lint.isort]
known-first-party = ["myproject"]

[tool.mypy]
python_version = "3.12"
strict = false
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
plugins = ["pydantic.mypy", "sqlalchemy.ext.mypy.plugin"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "--strict-markers --tb=short -q"

[tool.coverage.run]
source = ["src"]
branch = true

[tool.coverage.report]
fail_under = 80
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "if __name__",
]
```

## Linting & Formatting — Ruff

Ruff replaces flake8, black, isort, pyflakes, and pyupgrade in a single tool.

- `ruff check .` — lint
- `ruff check . --fix` — lint and auto-fix
- `ruff format .` — format (replaces black)
- Run both in CI and as a pre-commit hook
- Never add flake8, black, isort, or autopep8 as dependencies

## Type Checking — mypy

mypy is the type checker. It has first-party plugins for both Pydantic and SQLAlchemy, giving deeper type inference for the core stack.

- Configure in pyproject.toml under `[tool.mypy]`
- Enable `disallow_untyped_defs = true` — all function signatures must have type annotations
- Enable the `pydantic.mypy` and `sqlalchemy.ext.mypy.plugin` plugins
- Do not enable full `strict = true` unless the team agrees — the per-flag approach above is a good baseline
- Use `from __future__ import annotations` at the top of every module for modern annotation syntax
- Use `typing.TYPE_CHECKING` for import-only types to avoid circular imports
- Add per-module overrides in pyproject.toml for third-party libraries missing stubs:
  ```toml
  [[tool.mypy.overrides]]
  module = ["some_untyped_lib.*"]
  ignore_missing_imports = true
  ```

## Web Framework — FastAPI

- Use `lifespan` context manager for startup/shutdown — not deprecated `on_event`
- Organize routes with `APIRouter`, grouped by domain in `src/myproject/api/`
- All request/response bodies must be Pydantic v2 models — never raw dicts
- Use `Annotated[Depends(...)]` pattern for dependency injection
- Use `HTTPException` for error responses; define custom exception handlers for domain errors
- Use async def for all route handlers and service functions that touch I/O

## Data Validation & Serialization — Pydantic v2

- Use Pydantic v2 (`model_config`, `model_validator`, `field_validator`) — never v1 patterns
- Define separate schemas for Create, Update, and Read operations
- Use `model_config = ConfigDict(from_attributes=True)` for ORM integration
- Prefer `Annotated` types with `Field()` for constraints: `Annotated[str, Field(min_length=1)]`
- Use `pydantic-settings` with `.env` files for all configuration — never raw os.environ

## Database — SQLAlchemy 2.0 + Alembic

### SQLAlchemy

- Use SQLAlchemy 2.0 style exclusively:
  - `DeclarativeBase` (not `declarative_base()`)
  - `Mapped[type]` and `mapped_column()` (not `Column()`)
  - `select()` function (not `session.query()`)
- Use `async` sessions with `asyncpg` for PostgreSQL
- Define models in `src/myproject/models/`
- Use a shared `Base` class with common fields (id, created_at, updated_at)
- Always use `AsyncSession` from `sqlalchemy.ext.asyncio`

### Alembic

- Store migrations in `alembic/versions/`
- Use `--autogenerate` for schema changes, but always review generated migrations
- Write data migrations as separate migration files — never mix schema and data migrations
- Every migration must be reversible (implement both `upgrade()` and `downgrade()`)
- Use descriptive migration messages: `"add_users_email_index"` not `"update"`

## HTTP Client — httpx

- Use `httpx.AsyncClient` for all external HTTP calls — never `requests`
- Use a shared client instance with connection pooling via FastAPI's lifespan
- Set timeouts explicitly on every client: `httpx.AsyncClient(timeout=10.0)`

## Logging — structlog

- Use `structlog` for structured, JSON-formatted logging
- Configure once at app startup; use `structlog.get_logger()` everywhere
- Log with key-value context: `log.info("order_created", order_id=order.id, total=order.total)`
- Never use `print()` for logging (enforced by Ruff rule T20)
- Use correlation IDs in middleware for request tracing

## Task Runner — just (justfile)

`just` is the local task runner. All common operations are justfile targets.

```justfile
# justfile

set dotenv-load

default:
    @just --list

dev:
    uv run uvicorn src.myproject.main:app --reload --host 0.0.0.0 --port 8000

fmt:
    uv run ruff format .
    uv run ruff check . --fix

lint:
    uv run ruff check .
    uv run mypy src/

test *ARGS:
    uv run pytest {{ARGS}}

test-cov:
    uv run pytest --cov --cov-report=term-missing --cov-report=html

check: fmt lint test

db-migrate MSG:
    uv run alembic revision --autogenerate -m "{{MSG}}"

db-upgrade:
    uv run alembic upgrade head

db-downgrade:
    uv run alembic downgrade -1

db-reset:
    uv run alembic downgrade base
    uv run alembic upgrade head

clean:
    find . -type d -name __pycache__ -exec rm -rf {} +
    rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
```

## Project Layout

```
├── CLAUDE.md
├── pyproject.toml
├── uv.lock
├── justfile
├── alembic/
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
├── src/
│   └── myproject/
│       ├── __init__.py
│       ├── main.py              # FastAPI app + lifespan
│       ├── config.py            # pydantic-settings
│       ├── database.py          # engine, session factory
│       ├── api/
│       │   ├── __init__.py
│       │   ├── deps.py          # shared dependencies
│       │   └── routes/
│       │       └── *.py         # one file per domain
│       ├── models/
│       │   ├── __init__.py
│       │   ├── base.py          # DeclarativeBase + mixins
│       │   └── *.py             # one file per domain
│       ├── schemas/
│       │   └── *.py             # Pydantic models per domain
│       └── services/
│           └── *.py             # business logic per domain
├── tests/
│   ├── conftest.py              # fixtures: async client, test db, factories
│   ├── unit/
│   └── integration/
└── docs/
    └── standards/
```

## Dependency Groups (pyproject.toml)

```toml
[project]
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.9",
    "pydantic-settings>=2.5",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "httpx>=0.27",
    "structlog>=24.4",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "pytest-cov>=6.0",
    "mypy>=1.13",
    "ruff>=0.8",
    "factory-boy>=3.3",
]
```
