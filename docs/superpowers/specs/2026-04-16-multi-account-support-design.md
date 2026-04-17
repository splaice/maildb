# Multi-Account Support — Design Spec

**Date:** 2026-04-16
**Status:** Approved (pending user review of this doc)
**Tracking issues:** #11, #12, #13, #14, #15
**Supersedes/clarifies:** `docs/DESIGN.md` §9.2 (Phase 6)

---

## 1. Background

`docs/DESIGN.md` describes multi-account support as a Phase 6 query-layer addition, claiming the schema "already supports" `source_account` and `import_id`. In reality, neither column exists, the `imports` table is missing, and the ingest CLI has no `--account` flag. This spec covers the full work — schema migration, backfill of existing data, ingest pipeline changes, query API, and a CLI rework to Typer — that delivers the multi-account feature end to end.

The user has one production database (~840K messages from a single Gmail account) that must be preserved across the migration and tagged via backfill.

## 2. Goals

- Tag every email with the `source_account` it was imported from and the `import_id` of the session that created it.
- Allow query methods (`find`, `search`, `top_contacts`, `unreplied`, `long_threads`) to scope to a single account or query across all accounts.
- Track each ingest invocation as a row in a new `imports` table (start/end time, file, counts, status).
- Provide an `accounts()` method that summarizes what's in the database and an `import_history()` method that lists past ingests.
- Migrate the existing single-account database in place — no re-ingest required.
- Replace the two ad-hoc `__main__.py` CLIs with a single Typer-based `maildb` command.

## 3. Non-goals

- Per-account body/embedding differences. Embeddings remain account-agnostic.
- Multi-user support (different humans). The `MAILDB_USER_EMAILS` list still represents one person's accounts.
- Cross-account thread merging logic. Threading uses `thread_id` and is naturally account-agnostic — `get_thread()` returns every message with the matching `thread_id` regardless of account.
- Attachment deduplication changes. Content-addressed dedup already works across accounts.

## 4. Schema changes

### 4.1 New table: `imports`

```sql
CREATE TABLE IF NOT EXISTS imports (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_account    TEXT NOT NULL,
    source_file       TEXT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    messages_total    INT NOT NULL DEFAULT 0,
    messages_inserted INT NOT NULL DEFAULT 0,
    messages_skipped  INT NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_imports_source_account ON imports (source_account);
CREATE INDEX IF NOT EXISTS idx_imports_started_at ON imports (started_at DESC);
```

### 4.2 New columns on `emails`

```sql
ALTER TABLE emails ADD COLUMN IF NOT EXISTS source_account TEXT;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS import_id UUID REFERENCES imports(id);

CREATE INDEX IF NOT EXISTS idx_email_source_account ON emails (source_account);
CREATE INDEX IF NOT EXISTS idx_email_import_id ON emails (import_id);
```

The columns are added nullable so the migration step (§6) can populate existing rows. After backfill, `source_account` is tightened to `NOT NULL` (see §4.4). `import_id` remains nullable to allow a backfill row to reference a synthetic "migration" import without enforcing a hard FK on every legacy row.

### 4.3 New column on `ingest_tasks`

```sql
ALTER TABLE ingest_tasks ADD COLUMN IF NOT EXISTS import_id UUID REFERENCES imports(id);
```

Lets the status command attribute in-flight chunks/embeddings to a specific import session.

### 4.4 NOT NULL constraint (post-migration)

After the backfill migration completes successfully (§6), a follow-up step runs:

```sql
ALTER TABLE emails ALTER COLUMN source_account SET NOT NULL;
```

This is applied as part of the pipeline change (issue #12), guaranteeing every new ingest tags every row. The migration in §6 verifies zero rows have `source_account IS NULL` before this constraint is added.

## 5. Ingest pipeline changes (issue #12)

### 5.1 CLI surface (Typer rework — see §7)

```bash
maildb ingest run <mbox> --account you@example.com [--skip-embed]
maildb ingest status [--account you@example.com]
maildb ingest reset [--phase parse|index|embed] [--yes]
maildb ingest migrate --account you@example.com
```

`--account` is **required** for `run` and `migrate`. The value is validated as a syntactically-valid email address (single `@`, non-empty local + domain). The migration command is in §6.

### 5.2 Orchestrator changes

`run_pipeline(...)` gains two new parameters: `source_account: str` and `import_id: UUID`. At pipeline start:

1. Generate `import_id = uuid4()`.
2. INSERT a row into `imports` with `(id=import_id, source_account, source_file=str(mbox_path), status='running')`.
3. Pass both values through to every parse worker via the `ingest_tasks` row's `import_id` column.
4. On clean completion: UPDATE the `imports` row with final counts and `status='completed', completed_at=now()`.
5. On failure: UPDATE with `status='failed', completed_at=now()`. Final counts are best-effort.

### 5.3 Parse worker changes

`process_chunk()` reads `import_id` and `source_account` from its `ingest_tasks` row and includes both in every `INSERT INTO emails` statement. The existing `ON CONFLICT (message_id) DO NOTHING` clause is unchanged — first import wins, including the `source_account` of the first import.

### 5.4 Status command

`maildb ingest status` shows aggregate phase counts as today, plus a per-import breakdown:

```
Imports
  2026-04-10 14:22  you@gmail.com   completed  840,000 inserted   0 skipped
  2026-04-16 09:15  you@work.com    running     12,500 inserted   3 skipped
```

`--account` filters to a single account.

## 6. Migration: backfill existing data (issue #14)

### 6.1 Command

```bash
maildb ingest migrate --account you@example.com
```

### 6.2 Behavior

1. Verify the `source_account` column exists. If not, instruct the user to run the schema migration (init_db) first.
2. INSERT one synthetic `imports` row: `(source_account=<account>, source_file='migration', status='completed', started_at=now(), completed_at=now())`. Capture its `id` as `migration_import_id`.
3. UPDATE `emails SET source_account=<account>, import_id=<migration_import_id> WHERE source_account IS NULL`. Capture the affected row count.
4. UPDATE the `imports` row: `messages_inserted=<row_count>, messages_total=<row_count>`.
5. Print: `Backfilled <n> rows with source_account=<account>`.

### 6.3 Properties

- **Idempotent.** Re-running it INSERTs another `imports` row with `messages_inserted=0` (because no rows have NULL anymore) and is otherwise a no-op on `emails`.
- **Safe.** Only touches rows where `source_account IS NULL` — never overwrites previously-tagged data.
- **Single-account assumption.** The current production DB has one account's worth of mail. If a future user has a mixed-account legacy DB, they can run `migrate` once per account using a `WHERE` clause they craft manually — or we can extend the command later. Out of scope for this iteration.

### 6.4 NOT NULL tightening

Immediately after a successful migrate, the user runs `init_db` (or any subsequent `maildb ingest run`) which executes the idempotent schema bootstrap. The bootstrap checks `SELECT COUNT(*) FROM emails WHERE source_account IS NULL` — if zero, it adds the `NOT NULL` constraint; if non-zero, it logs a warning and skips. This makes the constraint self-applying once the DB is clean.

## 7. CLI rework: Typer (added scope)

### 7.1 Motivation

The current ingest CLI parses `sys.argv` manually with string comparisons. Adding `--account`, `migrate`, and per-import status filters compounds the mess. Typer is a 5-minute add that gives us autogenerated `--help`, type validation, subcommand grouping, and tab completion.

### 7.2 New unified entrypoint

A single console script `maildb` (declared in `pyproject.toml`):

```
maildb serve              # Run the MCP server (replaces `python -m maildb`)
maildb ingest run ...     # Run the ingest pipeline
maildb ingest status ...
maildb ingest reset ...
maildb ingest migrate ...
```

`python -m maildb` is preserved as an alias for `maildb serve` (since the MCP server is the most common invocation and existing tooling depends on the module-form invocation). `python -m maildb.ingest` is removed — its replacements are subcommands of `maildb ingest`.

### 7.3 Implementation shape

- New module `src/maildb/cli.py` defines the Typer app and subcommand groups.
- `src/maildb/__main__.py` becomes a 3-line shim: import the Typer app, call it with `["serve"] + sys.argv[1:]` if no subcommand is given, otherwise pass through.
- `src/maildb/ingest/__main__.py` is deleted.
- `pyproject.toml` adds `typer>=0.12` to dependencies and `[project.scripts] maildb = "maildb.cli:app"`.

### 7.4 Out of scope

No changes to MCP server invocation behavior or arguments. The Typer rework is mechanical — same operations, cleaner surface.

## 8. Query API changes (issue #13)

### 8.1 `_build_filters()`

Adds `account: str | None = None` parameter. When provided, appends `source_account = %(account)s` to the WHERE clause and `account` to the params dict.

### 8.2 Methods that gain an `account` parameter

- `find(account=None, ...)`
- `search(account=None, ...)`
- `top_contacts(account=None, ...)` — applied to both inbound and outbound branches
- `unreplied(account=None, ...)` — applied as `e.source_account = %(account)s`; reply-detection subquery is *not* account-filtered (a reply from any account counts as a reply)
- `long_threads(account=None, ...)` — applied; thread participant union remains cross-account

`get_thread()`, `get_thread_for()`, `correspondence()`, `mention_search()`, `topics_with()`, `cluster()`, and `query()` (DSL) do **not** gain account filters. Rationale per method:

- **`get_thread`/`get_thread_for`**: returning a partial thread when the conversation crosses accounts would be confusing. Threads are atomic.
- **`correspondence`/`mention_search`/`topics_with`/`cluster`**: these can be added in a follow-up if real use demands it. YAGNI for now.
- **`query` (DSL)**: the user can already filter by `source_account` via the DSL once it appears in the column whitelist (added in this work). No separate parameter needed.

### 8.3 New methods

```python
def accounts(self) -> list[AccountSummary]: ...
def import_history(self, account: str | None = None, limit: int = 50, offset: int = 0) -> list[ImportRecord]: ...
```

`AccountSummary` (new dataclass in `models.py`):

```python
@dataclass
class AccountSummary:
    source_account: str
    email_count: int
    first_date: datetime | None   # earliest message date for this account
    last_date: datetime | None    # latest message date
    import_count: int             # number of imports for this account
```

`accounts()` SQL:
```sql
SELECT
    source_account,
    COUNT(*)                       AS email_count,
    MIN(date)                      AS first_date,
    MAX(date)                      AS last_date,
    COUNT(DISTINCT import_id)      AS import_count
FROM emails
WHERE source_account IS NOT NULL
GROUP BY source_account
ORDER BY email_count DESC;
```

`ImportRecord` (new dataclass) mirrors the `imports` table columns 1:1.

`import_history()` SQL:
```sql
SELECT id, source_account, source_file, started_at, completed_at,
       messages_total, messages_inserted, messages_skipped, status
FROM imports
[WHERE source_account = %(account)s]
ORDER BY started_at DESC
LIMIT %(limit)s OFFSET %(offset)s;
```

### 8.4 `MAILDB_USER_EMAILS` config

`Settings` adds:

```python
user_emails: list[str] = []      # MAILDB_USER_EMAILS=a@x.com,b@y.com
user_email: str | None = None    # deprecated alias, merged into user_emails
```

`pydantic-settings` parses comma-separated env values into `list[str]` natively when the field is annotated as a list. A `model_validator(mode="after")` merges legacy values: if `user_email` is set and not already in `user_emails`, it is prepended. The list form is the canonical accessor; downstream code reads `self._config.user_emails` exclusively.

"You" semantics in `unreplied()` and `top_contacts()`:

- When `account` is **provided**: "you" = that single address. The query uses `sender_address = %(account)s` (and `!= %(account)s` for the inverse direction).
- When `account` is **not provided**: "you" = any address in `user_emails`. The query uses `sender_address = ANY(%(user_emails)s)` and `!= ALL(%(user_emails)s)`.

Both methods raise `ConfigError` only if neither `account` is passed nor `user_emails` is configured.

### 8.5 DSL whitelist

Add `source_account` and `import_id` to the column whitelist in `dsl.py` so DSL queries can filter and group by these. No other DSL changes needed.

### 8.6 MCP server

Every tool that wraps a method gaining an `account` parameter exposes it as an optional string. Two new tools wrap the new methods:

- `accounts()` → returns the `AccountSummary` list, serialized
- `import_history(account=None, limit=50, offset=0)` → returns the `ImportRecord` list, serialized

Tool docstrings explain the `account` parameter and the cross-account default.

### 8.7 `Email` model and `SELECT_COLS`

`Email` dataclass gains `source_account: str | None` and `import_id: UUID | None` fields (kept Optional so older serialized rows from tests don't break). `SELECT_COLS` in `maildb.py` adds both columns. `Email.from_row()` reads them.

## 9. Test coverage (issue #15)

### 9.1 Unit tests

- `tests/unit/test_config.py`: `MAILDB_USER_EMAILS` parses comma-separated values; `user_email` legacy value merges into `user_emails`; both empty is fine.
- `tests/unit/test_dsl.py`: `source_account` and `import_id` are valid filter columns; old whitelist behavior unchanged.
- `tests/unit/test_cli.py` (new): Typer command structure — `maildb ingest run` requires `--account`; `maildb ingest migrate` requires `--account`; `maildb serve` parses correctly. Use Typer's `CliRunner`.

### 9.2 Integration tests

`tests/conftest.py` updates: a new fixture `multi_account_db` seeds emails from two `source_account` values (e.g., `a@example.com` and `b@example.com`) including:
- one thread that spans both accounts (same `thread_id`, two messages, different `source_account`)
- one message_id that exists in only account A
- one outbound + one unreplied inbound per account

New tests in `tests/integration/test_maildb.py`:
- `find(account="a@example.com")` returns only A
- `find()` (no account) returns A + B
- `search(query, account="a@example.com")` scopes embedding hits
- `unreplied(account="a@...")` only returns A's unreplied (verifies the reply-detection subquery is cross-account: a reply from B to an A message marks the A message as replied)
- `top_contacts(account="a@...")` only counts A's exchanges
- `long_threads(account="a@...")` excludes the cross-account thread if account A's portion is below the threshold
- `get_thread(thread_id)` returns the full cross-account thread regardless of account filter
- `accounts()` returns both accounts with correct counts/date bounds
- `import_history()` returns both import sessions; `import_history(account="a@...")` filters

New tests in `tests/integration/test_ingest.py`:
- Running ingest creates an `imports` row, stamps every email with `import_id` and `source_account`
- Re-running ingest of the same mbox creates a new `imports` row but inserts zero new emails (ON CONFLICT) — `messages_skipped` reflects this
- `migrate --account` backfills NULL rows, skips already-tagged rows, is idempotent
- After backfill + ingest, `source_account` is NOT NULL across the table

## 10. Execution order

Work proceeds in four sequential PRs/commits, each leaving the system in a working state:

1. **Schema** (#11) — columns, table, indexes, model + SELECT_COLS update. Existing ingest still works (writes NULL into the new columns).
2. **Pipeline + CLI rework + backfill** (#12 + Typer + #14) — bundled because they all touch the CLI. Adds `--account` to `run`, the `migrate` subcommand (with its backfill SQL), the status filter, and the NOT NULL self-tightening logic in the schema bootstrap. After this lands, the user runs `maildb ingest migrate --account <their-account>` once on the production DB; the next `init_db` invocation tightens `source_account` to `NOT NULL`.
3. **Query API + MCP** (#13) — `account` parameter, `accounts()`, `import_history()`, `MAILDB_USER_EMAILS`, MCP tool surface, DSL whitelist additions.
4. **Test coverage** (#15) — unit + integration tests filling any gaps from steps 1–3 and adding the multi-account integration scenarios.

Each step ends with `uv run just check` passing.

## 11. Risks and mitigations

- **NOT NULL self-tightening surprises a half-migrated DB.** Mitigated: the bootstrap only adds the constraint when zero NULL rows remain. A migration in progress leaves NULLs and the constraint stays off.
- **`ON CONFLICT DO NOTHING` discards `source_account` on dup.** This is the documented "first import wins" behavior. Tests cover it explicitly so the behavior is locked in.
- **Existing tests that construct `Email` objects break** when `source_account`/`import_id` fields are added. Both fields default to `None`; existing fixtures should keep working without changes. Verify in step 1.
- **Typer dep adds startup overhead to MCP server.** Negligible (Typer adds <50ms cold start). Acceptable.
- **`MAILDB_USER_EMAIL` deprecated alias.** Kept indefinitely. Behavior is "merge into list" — no breaking change for users with the old env var set.

## 12. Open questions

None at design time. All decisions confirmed with the user 2026-04-16.
