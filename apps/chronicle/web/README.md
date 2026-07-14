# Life Chronicle — Web

Vite + React + TypeScript + Tailwind v4 analyst workstation UI.

## Develop

Terminal 1 — API (from `apps/chronicle/server`):

```bash
# Required env (see chronicle_server.config.ChronicleSettings)
export CHRONICLE_SECRET_KEY=dev-secret-change-me
export CHRONICLE_USERNAME=analyst
export CHRONICLE_PASSWORD_HASH='…'   # argon2 hash of the password
export CHRONICLE_DATABASE_URL=postgresql://…   # maildb database

uv run python -m chronicle_server   # listens on http://127.0.0.1:8400
```

Terminal 2 — UI (from this directory):

```bash
pnpm install   # once
pnpm dev       # http://127.0.0.1:5173 — proxies /api → :8400
```

Sign in with the configured single user. The Chronicle canvas shows **Archive coverage** from `GET /api/archive/summary`.

## Checks

From repo root:

```bash
just check-app
```

Runs server ruff/mypy/pytest, then web `tsc`, vitest, and production build.
