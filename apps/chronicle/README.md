# Life Chronicle

Private, desktop-first analyst workstation over a maildb email/attachment archive.
Chronicle is the root experience; Research Desk, Topics, People, Files, Workspaces,
and Data Health share one working set.

## Prerequisites

- **PostgreSQL + pgvector** with a maildb schema (messages, threads, attachments, contacts)
- **Python 3.12+** and [uv](https://github.com/astral-sh/uv)
- **Node.js** and [pnpm](https://pnpm.io) (web UI)
- **Ollama** (optional) — Ask / interpret / event generation; search and Chronicle remain usable without it

## Environment

All settings use the `CHRONICLE_` prefix (`pydantic-settings`). Required:

| Variable | Purpose |
| --- | --- |
| `CHRONICLE_SECRET_KEY` | Session cookie signing secret |
| `CHRONICLE_PASSWORD_HASH` | Argon2 hash of the single-user password |
| `CHRONICLE_DATABASE_URL` | Postgres URL (default `postgresql://localhost/maildb`) |
| `CHRONICLE_USERNAME` | Login username (default `owner`) |
| `CHRONICLE_ATTACHMENT_ROOT` | Attachment binary root (default `~/maildb/attachments`) |

Optional: `CHRONICLE_OLLAMA_HOST`, `CHRONICLE_ANSWER_MODEL`, `CHRONICLE_COOKIE_SECURE`,
session/rate-limit tunables — see `apps/chronicle/server/src/chronicle_server/config.py`.

Generate a password hash:

```bash
uv run python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('your-password'))"
```

Example (from `apps/chronicle/server`):

```bash
export CHRONICLE_SECRET_KEY=dev-secret-change-me
export CHRONICLE_USERNAME=analyst
export CHRONICLE_PASSWORD_HASH='$argon2id$…'   # from the one-liner above
export CHRONICLE_DATABASE_URL=postgresql://localhost/maildb
export CHRONICLE_ATTACHMENT_ROOT=~/maildb/attachments
```

## Run the API

```bash
cd apps/chronicle/server
uv sync
uv run python -m chronicle_server   # http://127.0.0.1:8400
```

## Run the web UI (development)

```bash
cd apps/chronicle/web
pnpm install
pnpm dev   # http://127.0.0.1:5173 — Vite proxies /api → :8400
```

Sign in with the configured username/password. The root route is Chronicle.

## Production build

```bash
cd apps/chronicle/web
pnpm build   # writes web/dist/
```

Serve `web/dist` as static files **on the same origin as the API** (so session cookies and
`/api/*` resolve without CORS). Typical reverse-proxy layout:

- `/api/*` → chronicle_server `:8400`
- `/*` → static files from `apps/chronicle/web/dist`

Dev alternative: `pnpm preview` after build, still proxied to the API.

## Gates

From the repository root:

```bash
just check-app
```

Runs server `ruff` + `mypy` + `pytest`, then web `tsc`, vitest, and production build.

Root library gate (maildb core):

```bash
just check
```

## Performance harness

Live-archive timings against §16.2 targets (not part of `check-app`):

```bash
# Terminal 1
cd apps/chronicle/server && uv run python -m chronicle_server

# Terminal 2 (repo root)
just perf-app --user <user> --password <password>
```

Writes `apps/chronicle/server/perf/results-<date>.json`. See
`docs/superpowers/specs/2026-07-14-life-chronicle-verification-report.md` for the
2026-07-14 run and search soft-miss analysis.
