# Execution Plan — Attachment Extraction (First Full Run)

**Companion to** `attachment-extraction-migration.md` (procedure). This document is the **schedule** with decision gates and specific numbers for the current production corpus.

**Target corpus** (from `DESIGN.md §6`): ~841,930 messages in the DB; estimated ~12K attachments eligible after content-type filtering.

**Expected duration end-to-end:** 4–8 hours of wall-clock time, most of it unattended in Phase 4.

**Downtime:** none. DB stays readable/writable. Expect sustained CPU + GPU load.

---

## Phase 0 — Pre-flight (15 min)

- [ ] `just check` on `main` to confirm the release toolchain is green.
- [ ] `maildb --help` lists `process_attachments`.
- [ ] Ollama up: `ollama list` shows `nomic-embed-text` pulled.
- [ ] Marker models warm: first `process_attachments run --sample 1` will trigger a one-time download (~GB of Surya/layout weights) — budget 5–10 min the very first time.
- [ ] Free disk ≥ 5 GB. Markdown mirror lives under `$MAILDB_ATTACHMENT_DIR` (default `~/maildb/attachments`). Estimate: ~10× original attachment bytes for markdown (it's text).
- [ ] `pg_dump` snapshot of the DB to a safe location. Extraction is additive, but the cost of a dump is tiny vs. the cost of regenerating.
- [ ] Close heavy GUI apps; thermal headroom matters on M1 Max under sustained Marker load.

## Phase 1 — Schema + dry-run selection (5 min)

Schema self-applies via `init_db()` on first `process_attachments` invocation. No manual migration step.

```bash
maildb process_attachments status           # should report all zeros or pending
maildb process_attachments run --dry-run    # expect ~12K, record the exact number
```

**Gate:** the dry-run count is within 20% of the 12K estimate. If it's wildly different (say <1K or >30K), stop and investigate the selection SQL before burning compute.

## Phase 2 — Smoke test (30 min)

```bash
maildb process_attachments run --sample 50 --workers 1
maildb process_attachments status
```

**Inspect the status breakdown.** Acceptable:

- `extracted` ≥ 40 / 50
- `failed` ≤ 5, and reasons are intelligible (encrypted PDF, oversized, Marker OCR edge case)
- `skipped` reasons are expected unsupported formats (`doc_legacy`, `xls_legacy`)
- Per-content-type `avg_ms` roughly: PDF 2–15s, docx/xlsx < 2s, images 1–5s

**Gate:** if `failed` > 10 / 50 or any reason looks systemic (import error, missing Ollama, etc.), stop and fix before scaling.

## Phase 3 — Worker tuning (45–60 min)

```bash
# Baseline was workers=1; now sweep. Each sample reuses the same pool of pending rows,
# so throughput comparison is apples-to-apples at this scale.
maildb process_attachments run --sample 100 --workers 2
maildb process_attachments status     # note avg_ms, note wall time

maildb process_attachments run --sample 100 --workers 4
maildb process_attachments status

maildb process_attachments run --sample 100 --workers 6
maildb process_attachments status
```

Pick the workers value with best sample-wall-time. Watch `powermetrics` or `sudo powermetrics --samplers smc -i 1000 -n 5` during a burst — if CPU package temp hits thermal throttle (~100°C) consistently, back off by one.

**Expected sweet spot on M1 Max:** 4 workers. Document the choice.

## Phase 4 — Full run (1–6 hours, unattended)

```bash
# Kick off in a durable terminal (tmux or a persistent shell).
maildb process_attachments run --workers <TUNED> 2>&1 | tee /tmp/process_attachments.log &

# Periodically, in a second shell:
watch -n 60 'maildb process_attachments status'
```

**What to monitor:**

- `pending` decreasing monotonically.
- `failed` growth rate. A sudden spike usually means a batch of pathological files (giant PDFs). Expected to plateau < 5% of corpus.
- Disk usage under `$MAILDB_ATTACHMENT_DIR` and PG data dir.
- Ollama process memory — if it balloons past ~4 GB sustained, restart it (workers will retry).

**Gate (hourly):** if extraction rate drops below ~2 attachments / second for >15 min, investigate. Common culprits: one worker wedged on a huge PDF (kill the Python child; watchdog will reclaim).

## Phase 5 — Verification (30 min)

```bash
maildb process_attachments status
# extracted + failed + skipped should equal the Phase-1 dry-run count.

# HNSW index should exist (auto-created post-run):
psql $MAILDB_DSN -c "\d attachment_chunks" | grep hnsw
```

Spot-check semantic search against a known document:

```bash
uv run python -c '
from maildb.maildb import MailDB
db = MailDB()
hits, total = db.search_attachments("termination clause 30 days")
for h in hits[:3]:
    print(f"{h.similarity:.3f}  {h.filename}  →  {h.chunk.text[:80]}")
'
```

- [ ] Results are semantically relevant (not just keyword matches).
- [ ] `search_all("budget 2024")` returns both email and attachment sources.
- [ ] `get_attachment_markdown(<some extracted id>)` returns real text.

## Phase 6 — Retry + document (varies)

```bash
# Inspect failures by content-type.
psql $MAILDB_DSN -c "
  SELECT a.content_type, count(*), min(c.reason)
  FROM attachment_contents c JOIN attachments a ON a.id = c.attachment_id
  WHERE c.status = 'failed'
  GROUP BY 1 ORDER BY 2 DESC;
"

# Retry transient failures (OOM, timeout) after freeing resources.
maildb process_attachments retry --only pdf
```

Record in the PR/issue: final counts, wall time, workers chosen, notable failure classes.

---

## Decision gates — quick reference

| Gate | Condition | Action |
|------|-----------|--------|
| Phase 1 | dry-run count wildly off estimate | stop, investigate selection |
| Phase 2 | `failed` > 20% of sample | stop, fix systemic issue |
| Phase 2 | Marker/Ollama errors in first 50 | stop, fix environment |
| Phase 3 | thermal throttle observed | back off workers by 1 |
| Phase 4 | rate < 2/sec sustained | check wedged worker |
| Phase 5 | HNSW index missing | `maildb ingest migrate` or manually `CREATE INDEX ... USING hnsw` |

## Rollback

Extraction is additive. To reset:

```sql
DELETE FROM attachment_chunks;
UPDATE attachment_contents SET status = 'pending', markdown = NULL,
  markdown_bytes = NULL, reason = NULL, extraction_ms = NULL;
```

On-disk markdown mirror can stay — it'll be overwritten on re-run. A hard rollback (drop the tables) is in `attachment-extraction-migration.md §6`.

## After the run

- File follow-up issues for any systemic failure class worth fixing later (e.g. oversized-PDF OOM handling).
- Update `DESIGN.md §6` performance table with measured numbers.
- Close the attachment-search tracking issue with a link to this run's log.
