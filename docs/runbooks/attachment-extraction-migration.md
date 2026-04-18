# Runbook — Attachment Content Extraction

**Audience:** operator running attachment extraction for the first time on a populated database.
**Downtime:** none required; DB remains usable during extraction. CPU + GPU are under load.

---

## 1. Preconditions

- [ ] `maildb` version includes the `process_attachments` command (`maildb --help` lists it).
- [ ] Schema has `attachment_contents` and `attachment_chunks` tables (a fresh `maildb process_attachments status` will complain clearly if not).
- [ ] Ollama reachable at `MAILDB_OLLAMA_URL`.
- [ ] Disk space: budget ~2 GB for DB growth + on-disk markdown mirror.

## 2. Smoke test

```bash
# Count pending selection
maildb process_attachments run --dry-run

# Process 50 random attachments to validate wiring end-to-end.
maildb process_attachments run --sample 50 --workers 1

# Summary
maildb process_attachments status
```

Expected status output: some mix of `extracted`, `skipped`, and (hopefully few) `failed`.

## 3. Benchmark worker count

The M1 Max can likely sustain multiple workers. Try:

```bash
maildb process_attachments run --sample 50 --workers 2
maildb process_attachments status  # note avg extraction_ms

maildb process_attachments run --sample 50 --workers 4
maildb process_attachments status
```

Pick the worker count with best throughput (watch for GPU thermal throttling on sustained runs).

## 4. Full run

```bash
maildb process_attachments run --workers <tuned>
```

Expected duration for ~12K attachments: 1–6 hours depending on content mix and workers.

## 5. Post-run verification

```bash
maildb process_attachments status

# A known phrase from a known PDF should return via search_attachments.
uv run python -c "from maildb.maildb import MailDB; db = MailDB(); print(db.search_attachments('known phrase')[0][:2])"
```

## 6. Rollback

Extraction is additive — `attachment_contents` and `attachment_chunks` only. To roll back, drop those tables; no other data is touched.

```sql
DROP TABLE IF EXISTS attachment_chunks CASCADE;
DROP TABLE IF EXISTS attachment_contents CASCADE;
ALTER TABLE attachments DROP COLUMN IF EXISTS reference_count;
```

(Re-running `maildb process_attachments run` after this will rebuild from scratch.)

## 7. Known edge cases

- **Password-protected PDFs** — `status='failed'` with a Marker-emitted reason mentioning encryption. No retry; skip them.
- **Scanned PDFs with no OCR layer** — Marker's Surya OCR covers these, but accuracy on low-quality scans can be poor. They still count as `extracted`; quality is a separate concern.
- **LibreOffice-dependent formats (.doc, .xls)** — marked `skipped` with reason noting LibreOffice isn't wired in v1. Deferred.
- **Oversized single attachment** — Marker may OOM on very large PDFs (hundreds of pages). These show up as `failed` with an OOM-style reason. Consider splitting those manually and re-ingesting.
