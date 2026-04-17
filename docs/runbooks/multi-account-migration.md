# Runbook — Multi-Account Schema Migration

**Audience:** operator executing the migration on a production MailDB database.
**Target release:** any commit ≥ `7d351e7` on `main` (the three follow-up fixes #36/#37/#38 + DSL fix #40 are all in).
**Downtime required:** yes — the database should not be ingested to or queried against while the backfill UPDATE runs. Reads are technically safe but may see inconsistent per-account counts until the migration finishes.

---

## 1. What This Migration Does

Adds multi-account support to an existing MailDB database. In order:

1. `init_db` applies idempotent DDL:
   - Creates the `imports` table.
   - Creates the `email_accounts` join table (+ indexes).
   - Adds nullable `source_account` / `import_id` columns to `emails` (fast — no table rewrite).
   - Adds nullable `import_id` column to `ingest_tasks`.
   - Adds new indexes.
2. `init_db` mirrors any legacy `(emails.source_account, emails.import_id)` pairs into `email_accounts` (no-op if you've never set those columns).
3. `maildb ingest migrate --account <addr>` does the **big UPDATE**: inserts a synthetic `imports` row with `source_file='migration'`, stamps every `emails` row that has `source_account IS NULL`, then mirrors into `email_accounts`.
4. The next `init_db` run sees zero NULL rows and promotes `emails.source_account` to `NOT NULL`.

Each step is idempotent and transactional. A crash mid-migration leaves a clean, re-runnable state.

---

## 2. Preconditions

- [ ] **Backup.** `pg_dump` the database (see §3.1). This is the only rollback path.
- [ ] **Downtime scheduled.** No other process should be writing to `emails` while `maildb ingest migrate` runs. For 800K+ rows, plan 10–30 minutes; scale linearly with row count.
- [ ] **Code version.** The machine running the migration has the new `maildb` package installed (commit on main ≥ `7d351e7`). Check with `git log -1` in the repo.
- [ ] **Know your primary account address.** You'll tag every pre-existing email with it. Pick the one that best reflects what the emails *were* for (typically the mbox owner).
- [ ] **PostgreSQL superuser or table-owner.** The migration runs `ALTER TABLE` — need the privilege.

---

## 3. Execution

### 3.1 Backup

Always first. Do not skip.

```bash
# Adjust db name, user, and path as needed.
pg_dump \
  --host=localhost \
  --username=maildb \
  --format=custom \
  --file=/var/backups/maildb-pre-multi-account-$(date +%Y%m%d-%H%M%S).dump \
  maildb
```

Verify the backup exists and is non-zero size:

```bash
ls -lh /var/backups/maildb-pre-multi-account-*.dump
```

### 3.2 Capture pre-migration row counts

Used later to verify nothing was lost.

```bash
uv run python - <<'PY'
from maildb.config import Settings
from maildb.db import create_pool
s = Settings()  # reads MAILDB_DATABASE_URL from env / .env
pool = create_pool(s)
with pool.connection() as conn:
    cur = conn.execute("SELECT count(*) FROM emails")
    print(f"emails: {cur.fetchone()[0]:,}")
    cur = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name IN ('imports', 'email_accounts')"
    )
    print(f"new tables already present: {cur.fetchone()[0]} / 2")
pool.close()
PY
```

Record the email count. Example output:

```
emails: 841,930
new tables already present: 0 / 2
```

### 3.3 Apply schema (fast — safe to run while DB is live)

Any command that constructs a `MailDB` or calls `create_pool` + `init_db` applies the DDL. The simplest is:

```bash
uv run maildb ingest status
```

Expected:
- Prints phase counts (all zero or old ingest state).
- On first run after upgrade, appends a new `imports` header line to stdout with no rows (since no ingests have happened under the new schema yet).
- Logs include `database_initialized`.

Verify the new tables exist:

```bash
uv run python - <<'PY'
from maildb.config import Settings
from maildb.db import create_pool
pool = create_pool(Settings())
with pool.connection() as conn:
    for t in ("imports", "email_accounts"):
        cur = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = %s",
            (t,),
        )
        print(f"{t}: {'EXISTS' if cur.fetchone()[0] else 'MISSING'}")
pool.close()
PY
```

Both should print `EXISTS`. If either is missing, do not proceed — investigate.

### 3.4 Run the backfill

**This is the slow step.** It's a single transaction; it holds an exclusive lock on the rows it updates.

```bash
uv run maildb ingest migrate --account you@example.com
```

Replace `you@example.com` with your primary account address.

Expected output (for ~841K rows):

```
Backfilled 841930 rows with source_account=you@example.com
```

If it crashes partway through, PostgreSQL rolls the transaction back — the `imports` row is `status='running'` (orphaned), all `emails` rows still have `source_account IS NULL`. Safe to rerun; the orphan row is harmless (it'll just sit there as a no-op record).

### 3.5 Trigger the NOT NULL self-tightening

After backfill, every row has `source_account` set. Running `init_db` one more time promotes the column to `NOT NULL`:

```bash
uv run maildb ingest status
```

This should complete in seconds. Confirms that the schema is now in its fully-multi-account shape.

---

## 4. Verification

Run all of these after §3.5. Every one should return the expected result.

### 4.1 All emails are tagged

```sql
SELECT count(*) FROM emails WHERE source_account IS NULL;
-- Expected: 0
```

### 4.2 `email_accounts` join table is populated

```sql
SELECT count(*) FROM email_accounts;
-- Expected: ≥ row count from §3.2 (may be equal if single-account; more if any duplicates cross accounts)
```

### 4.3 `source_account` is NOT NULL

```sql
SELECT is_nullable FROM information_schema.columns
WHERE table_name = 'emails' AND column_name = 'source_account';
-- Expected: 'NO'
```

### 4.4 `imports` has the migration row

```sql
SELECT source_account, source_file, status, messages_total, messages_inserted
FROM imports ORDER BY started_at DESC LIMIT 5;
-- Expected: one row with source_file='migration', status='completed',
-- messages_total matching the row count from §3.2.
```

### 4.5 Account-scoped queries work

```bash
uv run python - <<'PY'
from maildb.maildb import MailDB
db = MailDB()
# Should match §3.2 total.
results, total = db.find(account="you@example.com", limit=1)
print(f"find(account=you@...) total: {total:,}")

# Per-account summary.
for s in db.accounts():
    print(f"  {s.source_account}: {s.email_count:,} emails, "
          f"{s.import_count} import session(s)")
db.close()
PY
```

### 4.6 MCP server smoke test (optional)

Start the server and list the accounts tool from a client:

```bash
uv run maildb serve
```

In a separate client, call `accounts()` — should return `[{source_account: "you@...", email_count: N, ...}]`.

---

## 5. Expected Timings (indicative, M1 Max / 64 GB / NVMe)

| Step | Time for 841K rows |
|------|-------------------|
| §3.1 `pg_dump` | 2–5 min |
| §3.3 `init_db` (DDL) | < 1 sec |
| §3.4 `ingest migrate` UPDATE | 10–30 min |
| §3.5 `init_db` re-run (NOT NULL) | < 1 sec |

Bulk of the downtime is step 3.4. Scale roughly linearly with row count and inverse to disk IOPS.

---

## 6. Rollback

There is no forward-undo. The rollback path is **restore from the backup taken in §3.1**.

```bash
# Stop any maildb processes first.
dropdb --host=localhost --username=maildb maildb
createdb --host=localhost --username=maildb maildb
pg_restore \
  --host=localhost \
  --username=maildb \
  --dbname=maildb \
  --no-owner --no-privileges \
  /var/backups/maildb-pre-multi-account-YYYYMMDD-HHMMSS.dump
```

After restore, verify with the row count from §3.2. Ingest and queries should resume normally with the pre-multi-account code.

If you need to roll forward again later, repeat from §3.3.

---

## 7. Known Edge Cases

**Multiple accounts being migrated.** `ingest migrate --account` tags every untagged row with one address. If your corpus legitimately came from multiple accounts, tag them all with the primary first, then use `ingest run --account <other>` with the *same* mbox files to register the secondary account in `email_accounts` (the emails themselves are de-duped via `ON CONFLICT`, but new `email_accounts` rows get inserted for the new account). See §5 of the DESIGN.md for the multi-account attribution model.

**Idempotent re-runs.** Re-running `ingest migrate --account` after a successful first pass is safe — it creates a new empty `imports` row (status `completed`, `messages_inserted=0`) and updates zero emails. Not harmful but clutters the imports table; avoid unless you're deliberately testing.

**Orphaned running import after a crash.** If §3.4 crashes, the migration's `imports` row is stuck at `status='running'`. Harmless (it's not keyed to the resume-by-key logic — that only applies to `ingest run` with a `source_file` other than `'migration'`). Leave it alone or delete it manually: `DELETE FROM imports WHERE source_file = 'migration' AND status = 'running';`

**The NOT NULL tightening failed with a warning.** Log line `source_account_not_null_constraint_skipped` means `init_db` tried but hit an unexpected error (rare — usually a lock contention). Rerun `maildb ingest status` when the DB is quiet; the ALTER is idempotent.

**Very large corpora.** For databases with ≥ 5M emails, the single-transaction UPDATE in §3.4 may not be ideal — it holds locks and writes a lot of WAL. Not an issue for the current corpus but file a new issue with `batched-migrate` if you see trouble (the fix would be to chunk the UPDATE by `id` range).

---

## 8. Post-Migration

After §4 all passes:

- [ ] Merge the migration into your deploy pipeline so the next deploy doesn't need manual steps.
- [ ] If you use `MAILDB_USER_EMAIL`, consider switching to `MAILDB_USER_EMAILS` (comma-separated) now that multi-account is live — though the legacy singular still works and is merged in automatically.
- [ ] After a release or two of prod stability, issue #43 will drop the transitional `emails.source_account` / `emails.import_id` scalar columns. When that ships, it'll be a separate, smaller migration.
