# Bootstrap Script Design

**Environment bootstrap for maildb on macOS** — March 2026

---

## Overview

A single idempotent bash script (`scripts/bootstrap.sh`) that takes a fresh macOS machine with Homebrew installed and produces a fully working maildb development environment. Every step checks before acting — safe to re-run at any time.

---

## Dependencies

The script checks for and installs the following via Homebrew:

| Dependency | Check | Install |
|------------|-------|---------|
| uv | `command -v uv` | `brew install uv` |
| PostgreSQL (>= 16) | `command -v psql` + version check | `brew install postgresql@18` |
| pgvector | Extension availability query | `brew install pgvector` |
| Ollama | `command -v ollama` | `brew install ollama` |

Python 3.12 is handled automatically by `uv sync` using the project's `.python-version` file — no separate install step.

**Homebrew is the only prerequisite.** If missing, the script prints install instructions and exits.

**Service management:** The script ensures PostgreSQL and Ollama are running via `brew services start` before proceeding to database setup.

---

## Database Setup

### Roles

| Role | Privileges | Auth | Purpose |
|------|-----------|------|---------|
| `maildb` | LOGIN, CREATEDB | trust (no password) | Service/production operations |
| `maildb_test` | LOGIN, CREATEDB | trust (no password) | Test suite |

Created via `psql -d postgres`. Each role is checked with `SELECT 1 FROM pg_roles WHERE rolname = '...'` before creation.

Trust auth requires no `pg_hba.conf` changes — Homebrew PostgreSQL defaults to trust for local connections.

### Databases

| Database | Owner | Extensions |
|----------|-------|-----------|
| `maildb` | `maildb` | `vector` |
| `maildb_test` | `maildb_test` | `vector` |

Checked with `SELECT 1 FROM pg_database WHERE datname = '...'` before creation. Extensions are created by the superuser (`splaice`) since `CREATE EXTENSION` may require elevated privileges.

---

## Configuration

### Generated `.env`

Written only if `.env` does not already exist — never overwrites.

```
MAILDB_DATABASE_URL=postgresql://maildb@localhost:5432/maildb
MAILDB_TEST_DATABASE_URL=postgresql://maildb_test@localhost:5432/maildb_test
MAILDB_OLLAMA_URL=http://localhost:11434
MAILDB_EMBEDDING_MODEL=nomic-embed-text
```

### Code Changes

Two default values updated to match the new dedicated roles:

- **`src/maildb/config.py`** — default `database_url` changes from `postgresql://localhost:5432/maildb` to `postgresql://maildb@localhost:5432/maildb`
- **`tests/conftest.py`** — fallback changes from `postgresql://postgres:postgres@localhost:5432/maildb_test` to `postgresql://maildb_test@localhost:5432/maildb_test`

Env vars still override these defaults.

### Ollama Model

`ollama pull nomic-embed-text` — skipped if the model is already present (`ollama list | grep nomic-embed-text`).

---

## Validation

After all setup:

1. `uv sync` — installs Python 3.12 and all project dependencies
2. `uv run just test-unit` — smoke test confirming the environment works

A summary is printed at the end showing what was installed, created, or skipped.

---

## Error Handling

- **`set -euo pipefail`** — fail fast on any error
- Missing Homebrew: print install instructions, exit 1
- Service won't start: print diagnostic hint, exit 1
- All other failures: exit immediately with descriptive error from the failing command

### What the script does NOT do

- Modify `pg_hba.conf` — trust auth works by default
- Install Homebrew — too invasive; user handles this
- Run integration tests — unit smoke test is sufficient
- Apply database schema — that is `init_db()`'s job at runtime

### Idempotency

- Every step guarded by a check
- `.env` never overwritten if it exists
- Roles and databases skip creation if they exist
- `CREATE EXTENSION IF NOT EXISTS` is inherently safe
- `ollama pull` is a no-op if model is current

### Exit Codes

- **0:** everything succeeded or was already in place
- **1:** any failure, with descriptive error output
