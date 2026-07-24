# maildb + Life Chronicle — Linux Migration Runbook

**Audience:** an agent (or human) on the TARGET Linux machine (RTX 3080, CUDA) with this
bundle already unpacked. Work top to bottom; every step has a verification. Source machine:
macOS, PostgreSQL 18.3, pgvector 0.8.2, packaged 2026-07-24.

## 0. Bundle contents & integrity

```
RUNBOOK.md            — this file
MANIFEST.sha256       — checksums of everything below
maildb.dump           — pg_dump -Fc of the `maildb` database (all data incl. embeddings,
                        contacts, and all Chronicle app_* tables)
attachments/          — the attachment file store (DB storage_path values are RELATIVE
                        to this directory's new home)
config/env            — the maildb .env from the source machine (secrets — see §6)
config/settings.local.json    — Claude Code local settings (place in repo .claude/)
config/cheap-coder-skill/     — untracked .claude/skills/cheap-coder (place in repo .claude/skills/)
config/maildb-logs/           — historical run logs (optional; ~/.maildb/logs)
```

First action:

```bash
cd <bundle-dir> && shasum -a 256 -c MANIFEST.sha256   # (sha256sum -c on most Linux)
```

Canonical row counts for post-restore verification: **emails 1,279,362 · attachments 48,467 ·
contacts 63,061**.

## 1. System packages

PostgreSQL **18** (match the source major — a PG18 dump is not guaranteed to restore into
older majors) + pgvector **≥ 0.8**. On Ubuntu/Debian with the PGDG repo:

```bash
sudo apt install -y postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt install -y postgresql-18 postgresql-18-pgvector
```

Also: `git`, `curl`, `just` (`apt install just` or prebuilt), **uv** (`curl -LsSf
https://astral.sh/uv/install.sh | sh`), **Node 22+** and **pnpm** (`corepack enable pnpm`
or npm i -g pnpm), **Ollama** (`curl -fsSL https://ollama.com/install.sh | sh` — installs
CUDA support; verify `nvidia-smi` works first).

Verify: `psql --version` → 18.x; `uv --version`; `node --version`; `ollama --version`.

## 2. Database restore

```bash
sudo -u postgres createuser --superuser "$USER" 2>/dev/null || true
createdb maildb
createdb maildb_test
pg_restore -d maildb --no-owner --no-privileges -j 4 maildb.dump
psql maildb -c "ANALYZE;"
```

Notes:
- The dump contains `CREATE EXTENSION vector` — it succeeds because step 1 installed
  pgvector. If restore errors on the extension, run `psql maildb -c "CREATE EXTENSION
  vector;"` and re-run pg_restore with `--clean --if-exists`.
- `-j 4` parallelizes; expect some minutes for 17 GB.

Verify (must match the canonical counts above):

```bash
psql maildb -t -c "SELECT count(*) FROM emails;"
psql maildb -t -c "SELECT count(*) FROM attachments;"
psql maildb -t -c "SELECT count(*) FROM contacts;"
psql maildb -c "SELECT count(*) FROM pg_indexes WHERE tablename='emails';"   # HNSW + friends restored
```

## 3. Attachment store

```bash
mkdir -p ~/maildb
mv <bundle-dir>/attachments ~/maildb/attachments
```

Any location works — it just must equal `MAILDB_ATTACHMENT_DIR` (default
`~/maildb/attachments`) and, for the Chronicle server, `CHRONICLE_ATTACHMENT_ROOT`.

Verify: `find ~/maildb/attachments -type f | wc -l` — should be ≈ 48k files (plus `.md`
extraction mirrors).

## 4. Repository

```bash
git clone https://github.com/splaice/maildb.git ~/Code/maildb && cd ~/Code/maildb
cp <bundle-dir>/config/env .env
mkdir -p .claude/skills
cp <bundle-dir>/config/settings.local.json .claude/
cp -R <bundle-dir>/config/cheap-coder-skill .claude/skills/cheap-coder
uv sync            # pulls CUDA torch wheels on Linux automatically
```

`.env` keys carried over: `MAILDB_DATABASE_URL`, `MAILDB_TEST_DATABASE_URL`,
`MAILDB_OLLAMA_URL`, `MAILDB_EMBEDDING_MODEL`. Adjust the two database URLs if your local
connection differs (peer auth usually means `postgresql://localhost/maildb` works as-is).

**CUDA note — read before touching extraction:** the surya MPS patch and its discipline
(`just patch-surya`, single-MPS-worker rules, `docs/runbooks/attachment-extraction-mps-discipline.md`)
are macOS-only. Do NOT apply the patch here. Marker/surya use CUDA natively on the 3080 and
extraction throughput should be far better than the source machine.

## 5. Ollama models

```bash
ollama pull nomic-embed-text        # embeddings (274 MB) — REQUIRED, matches stored vectors
ollama pull llama3.2                # Chronicle Ask default; the 3080 can carry much larger
```

Embeddings are already computed and stored in the DB — the model is only needed for new
mail and query-time embedding. If you choose a different generation model for Chronicle,
set `CHRONICLE_ANSWER_MODEL` accordingly.

## 6. Chronicle app server + web

```bash
cd ~/Code/maildb/apps/chronicle/server && uv sync
cd ../web && pnpm install
```

Chronicle env (systemd unit or shell profile). **Generate fresh secrets on this machine —
do not reuse the source machine's:**

```bash
export CHRONICLE_SECRET_KEY="$(openssl rand -hex 32)"
export CHRONICLE_PASSWORD_HASH="$(cd ~/Code/maildb/apps/chronicle/server && uv run python -c \
  'from argon2 import PasswordHasher; import getpass; print(PasswordHasher().hash(getpass.getpass("chronicle password: ")))')"
export CHRONICLE_ATTACHMENT_ROOT=~/maildb/attachments
# optional: CHRONICLE_ANSWER_MODEL, CHRONICLE_COOKIE_SECURE=false for LAN-only http
```

Run: server `uv run python -m chronicle_server` (port 8400); web dev `pnpm dev`;
production: `pnpm build` and serve `dist/` from the same origin as the API. Full details:
`apps/chronicle/README.md` in the repo.

## 7. Verification gates (run all)

```bash
cd ~/Code/maildb
uv run just verify-env      # services preflight — expect PASS on Postgres/pgvector/test-db/Ollama.
                            # cheap-coder CLI check may FAIL until codex/grok CLIs are installed — that
                            # gate is for the cheap-coder workflow, not for maildb itself.
uv run just check           # full suite (~700 tests) — integration tests hit maildb_test
just check-app              # Chronicle server + web gates
cd apps/chronicle/server && uv run python perf/harness.py --base-url http://127.0.0.1:8400 \
  --user owner --password <pw>   # with the server running; compare against perf/results-2026-07-14.json in git history
```

Functional smoke: start the MCP server (`uv run maildb serve` or via Claude Code MCP
config) and run a `contacts(query="quintero")` — should return 11 results in well under a
second (the contacts indexes are in the dump; nothing to rebuild).

## 8. Known state you're inheriting (context for the agent)

- Index-drift issue **#112** is open: `schema_indexes.sql` indexes only materialize during
  a full ingest's index phase; the restored dump carries all current indexes, so you start
  clean — but read #112 before adding new indexes.
- Attachment extraction: 10,746 extracted / 790 failed (681 = the "slow tail" that exceeded
  the 900s budget on macOS). With CUDA, re-running the retry pass on the slow tail
  (`process_attachments retry`, see issue #63 / `docs/retrospectives/`) is likely to
  recover most of them — a good first CUDA win.
- Search perf: common-term substring search is ~3.2s (trigram selectivity, documented in
  `docs/superpowers/specs/2026-07-14-life-chronicle-verification-report.md` §3); the
  recommended tsvector FTS index is designed but not yet applied.
- The Life Chronicle build plan and state tracker: `docs/superpowers/plans/2026-07-13-life-chronicle-plan.md`.

## 9. Source-machine decommission checklist (optional, later)

Nothing on the source is auto-disabled by this migration. When satisfied: stop the source
MCP/Chronicle servers, keep the source DB as a cold backup for a while, and rotate the
Chronicle secrets if the source machine is retired.
