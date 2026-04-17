# MailDB

Personal email database with semantic search. Stores your full email history in PostgreSQL and exposes it to AI agents through an MCP server.

MailDB combines structured search (by sender, date, labels, attachments) with semantic vector search (by topic, intent, or fuzzy description) using pgvector. Embeddings are generated locally via Ollama — no email content leaves your machine.

## Prerequisites

- Python 3.12+
- PostgreSQL 16+ with the [pgvector](https://github.com/pgvector/pgvector) extension
- [Ollama](https://ollama.com) with the `nomic-embed-text` model
- [uv](https://docs.astral.sh/uv/) for Python package management

## Installation

```bash
# Clone and install dependencies
git clone <repo-url> && cd maildb
uv sync

# Copy and edit environment config
cp .env.example .env
# Edit .env — set MAILDB_DATABASE_URL and MAILDB_USER_EMAIL at minimum
```

### Database setup

```bash
createuser maildb
createdb -O maildb maildb
psql -d maildb -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### Ollama setup

```bash
ollama pull nomic-embed-text
```

## Importing a mailbox

MailDB imports `.mbox` files (e.g., from Gmail Takeout). The ingest pipeline has four phases: split, parse, index, and embed. It is restartable — if interrupted, re-running the same command picks up where it left off.

```bash
# Import an mbox file (--account is required)
uv run maildb ingest run --account you@example.com /path/to/mail.mbox

# Import without generating embeddings (much faster, semantic search won't work)
uv run maildb ingest run --account you@example.com /path/to/mail.mbox --skip-embed

# Check pipeline progress (optionally filter by account)
uv run maildb ingest status
uv run maildb ingest status --account you@example.com

# Reset pipeline state (to re-import)
uv run maildb ingest reset
uv run maildb ingest reset --phase embed   # reset only the embed phase
uv run maildb ingest reset --yes           # skip the confirmation prompt
```

Embedding is the slowest phase (~20 messages/second with 4 Ollama workers). A 50 GB mbox with ~840K messages takes roughly 12 hours to embed on an M1 Max.

### Migrating an existing database

If you have a pre-existing database without account tagging, run:

```bash
uv run maildb ingest migrate --account you@example.com
```

This tags every untagged email with the given account. It's idempotent and only touches rows where `source_account IS NULL`.

## Running the MCP server

```bash
uv run python -m maildb
```

This starts a FastMCP server over stdio. Configure your AI assistant (e.g., Claude Desktop) to launch MailDB as an MCP server.

## Configuration

All settings are controlled via environment variables (prefixed `MAILDB_`) or a `.env` file. See `.env.example` for the full list.

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILDB_DATABASE_URL` | `postgresql://maildb@localhost:5432/maildb` | PostgreSQL connection string |
| `MAILDB_USER_EMAIL` | (none) | Your email address — required for `unreplied()` and `top_contacts()` |
| `MAILDB_OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `MAILDB_EMBEDDING_MODEL` | `nomic-embed-text` | Ollama model for embeddings |

## Claude Code skill

MailDB ships with a `using-maildb` skill for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). The skill teaches Claude how to choose the right MCP tool, construct filters, and paginate through results when querying your email.

Install it by adding the skill path to your Claude Code settings:

```bash
claude skill add /path/to/maildb/skills/using-maildb
```

Once installed, Claude Code will automatically invoke the skill whenever you ask it to search, retrieve, or analyze your email.

## Development

```bash
uv run just check    # format + lint + test (run before committing)
uv run just fmt      # format with Ruff
uv run just lint     # lint with Ruff + type check with mypy
uv run just test     # run pytest
```
