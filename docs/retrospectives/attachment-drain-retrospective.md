# Attachment Extraction Drain Retrospective

**Date:** 2026-04-20 → 2026-04-27 (8 calendar days)
**Hardware:** Apple M1 Max, 64 GB unified memory, macOS 26
**Stack at start:** marker-pdf 1.10.2, surya-ocr 0.17.1, no patch, soft (SIGALRM) timeout
**Stack at end:** same versions + vendored surya MPS patch + supervisor with hard SIGKILL + per-worker claim isolation
**Result:** **9,546 attachments fully extracted, chunked, embedded. 1,991 failed. 24,033 skipped. 0 pending.** Of 6,337 rows we explicitly drained from `pending`, **~80% yield (4,344 success / 1,989 fail)**. We did not reach 100%; ~1,138 office-format failures and ~721 hard-timeouts remain unrecovered.

---

## Timeline

| Date | Event |
|------|-------|
| 2026-04-20 00:32 | Kernel panic — `watchdog timeout: no checkins from watchdogd in 91 seconds`. Earlier extraction run killed by macOS resource exhaustion. |
| 2026-04-20 morning | Audit: 5,073 extracted, **10,039 zero-vector chunks (~39%) from silent embed-failure fallback**, 45 "extracted" rows with no chunks. |
| 2026-04-20 afternoon | PR #54 — harden `_embed_chunks` (raise instead of write zero vectors), reclassify empty extractions as `skipped`, add `process_attachments reembed`. Reembed drains the 10,039 backlog. |
| 2026-04-20 evening | Investigate the dominant `marker: index 8192 is out of bounds: 2, range 0 to 4560` failures (6,470 of 6,479). Root cause: PyTorch MPS bug in `tensor.max().item()`, fixed in unmerged datalab-to/surya PR #493. |
| 2026-04-20 night | PR #56 — vendor PR #493 as `scripts/patches/surya-mps-fix.patch` plus `scripts/surya_mps_patch.py` (apply/revert/status). Smoke test: 8/10 previously-failed PDFs now extract. |
| 2026-04-20 night | First production retry under patch: hangs forever on attachment 11 (4MB investor deck PDF). SIGALRM timeout doesn't interrupt marker because it's deep in PyTorch C extension — Python signal handlers can't run between bytecodes there. |
| 2026-04-21 morning | PR #58 — supervisor that spawns `_subprocess_worker` in its own process, polls DB for stuck `extracting` rows, SIGKILLs the child via `Process.kill()`, marks row `failed` with `hard-timeout:` reason, respawns. Adds `maildb jobs` CLI: processes / counts / in-flight / rate / ETA. |
| 2026-04-21 14:00 | Single-supervisor drain begins (300s timeout). |
| 2026-04-21 17:30 | Re-tuned to 600s after seeing 36% hard-timeout rate at 300s with one success at 278s — borderline cases were getting cut off. |
| 2026-04-21 23:42 | Switch to **two parallel supervisors** for 2× throughput. |
| 2026-04-22 07:00 | Two-supervisor disaster discovered. **5,705 rows false-marked as `hard-timeout:`** (parallel supervisors raced on shared `_find_stuck_extracting` and killed each other's actively-running rows). **152 orphan multiprocessing.spawn processes consuming ~38 GB RAM** (`Process.kill()` doesn't reap grandchildren). Both supervisors at 0% CPU, machine deadlocked. |
| 2026-04-22 08:00 | Triage: nuke all maildb processes. Flip the 5,705 false hard-timeouts and the 552 real failures back to `pending`. Restart with single supervisor. |
| 2026-04-22 mid-day | PR #60 — per-supervisor `claimed_by` UUID column scopes stuck-row detection to the calling supervisor only; `os.setsid()` + `os.killpg()` reaps grandchildren on kill. Smoke test: 0 orphans after kill, 0 false hard-timeouts. |
| 2026-04-22 afternoon | Resume single-supervisor drain (300s timeout, `run --no-retry-failed` semantics — every row gets exactly one attempt per session). |
| 2026-04-23 → 26 | Drain runs continuously. Yield evolves with attachment-id range: ~3% in the bug-prone band (id 11–648), ~37% mid-band, **91% in id 14k+** once past the worst content. |
| 2026-04-27 04:46 | Drain exits 0. HNSW index rebuilt automatically. Final: 9,546 extracted, 1,991 failed, 24,033 skipped. |

Total wall clock: ~5 days (drain itself), inside an 8-day window dominated by infrastructure firefighting and rebuild. Three production PRs (#54, #56, #58, #60) shipped along the way.

---

## What Went Well

### The vendored upstream patch worked exactly as advertised
PR #56 vendored datalab-to/surya#493 into `scripts/patches/`. Smoke test on 10 known-failed PDFs: 0 reproduced the surya error. Across the full drain, the residual surya MPS bug only surfaced 64 times out of ~9,500 successful extractions — and none of those were on the patched call sites; they were on other PyTorch ops we didn't have visibility into. The `apply | revert | status` script made the patch durable across `uv sync` cycles.

### Hardening the embed pipeline removed a major silent-data-corruption class
Before PR #54, ~39% of chunks were zero-vector sentinels written silently when Ollama errored. Semantic search results on those rows were arbitrary. After PR #54, embed failures raise `EmbedFailedError` and the row is marked `failed` with reason `embed failed: …` — visible, queryable, retryable. Throughout the 5-day drain, **zero zero-vector chunks were created**. The `reembed` command is now an empty no-op, which is the right outcome.

### The supervisor caught real hangs
The SIGALRM-based timeout was an illusion of safety. The supervisor caught dozens of genuine multi-minute marker hangs over the drain that would otherwise have pinned the worker indefinitely. Single-supervisor mode (after PR #60 isolation fix) ran 5 days without a single false positive — `claimed_by` filtering and process-group kill made it operationally boring, which is the highest compliment.

### `maildb jobs` was the highest-leverage tool we built
A throwaway-feeling 200-line module became the single source of truth for "is the drain healthy?" for the entire 5-day grind. Process listing, in-flight rows with stuck-for, throughput in a rolling window, and rough ETA — all from one command. Made it possible to step away for 12+ hours and re-orient in 10 seconds.

### Single-worker memory profile stayed well under prior kernel-pressure levels
Each supervised worker peaked at ~5–8 GB RSS, gradually grew, occasionally got respawned by the supervisor's hangs. Never approached the memory pressure that triggered the original Apr 20 kernel panic. Five days of continuous operation, zero kernel events.

### We surfaced and adapted to content distribution in real time
When yield dropped from 91% → 33% as the drain advanced, we recognized it wasn't a regression — it was hitting a different content stratum. The decision to NOT keep tweaking timeouts and just let it run was the right one. Wasted hours of further tweaking would not have reclaimed those failures.

---

## What Went Wrong

### 1. Soft timeout did nothing inside marker

**Symptom:** Worker pinned for 7 hours of CPU on a single 4 MB PDF without the 300s `_run_with_timeout` ever firing.

**Cause:** Python signal handlers run between bytecodes. Marker spends most of its time inside PyTorch C extensions where Python never gets control. `signal.alarm()` fires, but the handler doesn't run until the C call returns — which it never does on a hang.

**Fix:** PR #58 — supervisor in its own process, observes via DB, kills the worker subprocess (which can be force-killed regardless of where it is in C code).

**Lesson:** A timeout that depends on cooperative checkpointing is not a timeout. For any operation that calls into native code, the only real timeout is "kill the process from outside."

### 2. `retry` subcommand had a re-claim livelock for fast failures

**Symptom:** A single bad row (id 16, an XLSX) re-claimed itself ~900 times in 15 minutes during the first post-patch retry. Drain made zero forward progress.

**Cause:** `retry` claims `WHERE status='failed'` ordered by `attachment_id`. After a fast failure (sub-second marker error), the row goes back to `failed` with the same ordering and gets immediately reclaimed. The supervisor's hard-timeout exclusion only protected against *long* hangs.

**Fix (operational):** Switch to `run --no-retry-failed` semantics — flip `failed → pending` once at session start, then claim only `pending`. Each row gets exactly one attempt per session. Failures stay failed and are not re-claimed. The retry subcommand still exists but is best avoided in favor of explicit-flip-then-run for full passes.

**Lesson:** A "retry until done" loop on a shared queue without per-attempt state is unsafe. The session-bounded "one shot per row" model is simpler and provable.

### 3. The two-supervisor disaster

**Symptom:** Within 7 hours of starting two parallel supervisors, **5,705 rows false-marked as `hard-timeout:`** and **152 orphan worker processes** consumed ~38 GB RAM. Both supervisors went to 0% CPU. Machine deadlocked.

**Two compounding bugs:**

**(a) Shared stuck-row detection.** `_find_stuck_extracting` returned every row past the timeout regardless of which supervisor owned the claim. Supervisor A's child gets killed; meanwhile A finds B's actively-running row past the timeout and writes `hard-timeout:` over it.

**(b) Grandchild leak.** `multiprocessing.Process.kill()` only signals the direct child. Marker/torch spawn their own helper processes; those become orphans of init when the parent dies. Each supervisor cycle leaked 5–10 processes; 24 cycles/hour × 2 supervisors × 7 hours = 152 zombies × ~250 MB each.

**Fix:** PR #60 — `claimed_by` UUID column scopes stuck-row detection per supervisor; child calls `os.setsid()` so its pid is the process-group id; supervisor uses `os.killpg(worker.pid, SIGKILL)` to reap the entire group.

**Lesson:** "Should be safe to run in parallel" without explicit ownership semantics is a bug. And `Process.kill()` is not a clean reap when the child can spawn its own children.

### 4. MPS contention destroys yield with two workers

**Symptom:** After PR #60 made parallel supervisors safe, two workers achieved 152 docs/hour but with **2.5% yield** (mostly the surya bug recurring at unpatched call sites). Single-worker yield was ~73% on the same content distribution. Net useful throughput was *worse* with two workers.

**Cause hypothesis:** Two processes hammering Apple Silicon MPS simultaneously trigger MPS reduce-kernel bugs at PyTorch ops the surya patch doesn't cover (argmax, gather, topk, etc.). The patched `.max().item()` sites are clean; other sites surface the same `index N is out of bounds` error class under contention.

**Fix:** Run a single MPS worker. Two-MPS extraction is strictly bad for this workload until upstream MPS kernels stabilize.

**Lesson:** "More parallelism → more throughput" is a CPU heuristic. On unstable accelerator backends, contention can flip the sign.

### 5. Office formats are functionally unsupported by Marker

**Symptom:** 1,138 of 1,991 failures (57%) are `marker: Failed to convert ...` — almost all DOCX, XLSX, PPTX. Marker's flow for these is to call out to LibreOffice (or PDF conversion) first, then run the same Surya pipeline. Both legs fail on a wide range of office files.

**Cause:** Marker isn't really an office-format extractor. The Docling spike (earlier in this corpus' history) showed Docling extracts ~1,170 of these natively in seconds without LibreOffice.

**Fix:** None applied yet. Office formats remain in the failure pool.

**Lesson:** A single extractor will never cover the full mime-type space. We need a fallback chain.

### 6. Killing a supervisor parent leaves the worker as init-orphan

**Symptom:** When manually `kill`ing the supervisor parent during cleanup, the worker child kept running, claiming new rows, with no one watching. Required explicit `kill -9 -- -<pid>` on the process group.

**Cause:** PR #60 made the worker a session leader (via `os.setsid()`) so its pgid = its own pid. SIGTERM to the parent doesn't propagate; the worker becomes a child of init.

**Fix:** When manually stopping, kill the process group: `kill -9 -- -<worker_pid>`. Document this in the runbook.

**Lesson:** The same `setsid` trick that lets the supervisor reap grandchildren also makes the worker survive its parent. Operationally, kill the group not the parent.

### 7. `uv remove docling` corrupted opencv

**Symptom:** Mid-test, marker started failing immediately with `module 'cv2' has no attribute 'INTER_LANCZOS4'`. The cv2 module was a half-installed empty stub.

**Cause:** Removing docling apparently affected a transitive dependency that left `opencv-python-headless` broken.

**Fix:** `uv pip install --force-reinstall opencv-python-headless`.

**Lesson:** After any `uv add`/`uv remove` of a heavy ML dep, smoke-test the existing extraction pipeline before trusting the venv.

### 8. NUL bytes in marker output break PG inserts

**Symptom:** 8 rows failed with `PostgreSQL text fields cannot contain NUL (0x00) bytes` — same class of bug we hit during email parsing back in March.

**Cause:** Marker can emit raw NUL bytes in extracted text from certain PDFs. We sanitize email text but not extracted markdown.

**Fix:** None applied — 8 rows is small enough that we accepted it. Should add `_sanitize_markdown()` analogous to `_sanitize_row()`.

**Lesson:** Every text field that crosses into PG must be NUL-stripped, not just email bodies.

---

## Improvements to Make

Listed in priority order. The first cluster is what we'd want before another full extraction pass; the rest are quality-of-life and follow-ups.

### Top priority (do these before the next phase)

1. **Add a Docling fallback for marker-failed office formats.** Of 1,991 failures, ~1,170 are DOCX/XLSX/PPTX that Docling handles natively. Wire `extract_markdown()` to attempt Marker, and on `ExtractionFailedError` for office buckets, attempt Docling. Keep Marker primary for PDFs where its layout/heading fidelity is materially better. Estimated yield from this fix alone: ~1,100 additional successful extractions.

2. **Sanitize marker output for NUL bytes before INSERT.** Add `_sanitize_markdown(text: str) -> str` that strips `\x00` and apply it in `process_one` before any `_set_status(markdown=...)` call. Trivially cheap; recovers ~8 rows now and prevents the same class of failure forever.

3. **Reclassify "hard-timeout" rows with bumped budget on a separate pass.** Of 1,991 failures, ~721 are `hard-timeout: killed after 300s`. Some fraction would complete at 600s or 900s. A `process_attachments retry --hard-timeouts-only --extract-timeout 900` pass with the supervisor would recover an unknown subset cheaply; estimate ~30–50% based on the spike data.

4. **Document the parallel-worker hazard.** PR #60 made parallel supervisors *safe*, but the multi-MPS yield collapse means parallel-on-Apple-Silicon is operationally bad regardless. Add a comment in `_run_supervised_single_worker` and a note in the runbook: single MPS worker until upstream MPS stabilizes.

### Coverage and quality

5. **Pre-extraction filter on tiny images.** Read image dimensions with Pillow (microseconds) before sending to Marker. Skip images < 100×100 px or < 5 KB as `skipped` reason `below-minimum-useful-size`. Documents the skip vs the current "extract and produce nothing." Saves a small amount of OCR work and improves telemetry honesty.

6. **Build a per-content-type yield dashboard.** `maildb jobs` is great for live drain status but doesn't show "what fraction of PDFs / DOCX / images are currently extracted vs failed vs skipped." Adding this view would have made the office-format problem visible months ago.

7. **Sanitize `_set_status(reason=...)` to NUL-strip too.** Same risk surface — a failure reason from a buggy native lib could contain NUL bytes.

### Supervisor and worker lifecycle

8. **Multi-worker supervisor inside a single invocation.** Right now the supervised path is single-worker only; `workers > 1` falls back to the unsupervised `ProcessPoolExecutor` path. For non-MPS workloads (e.g. embedding-only re-runs, future Docling), proper multi-worker supervision would unlock parallelism. Tracked but not urgent because MPS contention makes it moot for marker.

9. **Surface orphan processes in `maildb jobs`.** Add a "lingering subprocess" detector — any `multiprocessing.spawn` process whose ppid is 1 and whose start time predates the active supervisor by more than a minute. Surface as a warning in `jobs` output. Would have caught the dual-supervisor disaster ~5 hours earlier.

10. **`maildb jobs --kill-orphans`.** Operational shortcut for the cleanup we did manually three times during the drain.

### Resilience and ops

11. **Watch upstream surya for the PR #493 release.** Add a quarterly check (or watch the PR) — when a `surya-ocr` release ships including it, drop the local patch, bump the pin, remove `scripts/surya_mps_patch.py` and the justfile target.

12. **Persist supervisor activity log to disk.** During the drain we relied on `retry-drain.log` files in the project root for kill events. These should land in a known location (`~/.maildb/logs/<run-id>/`) with rotation, so post-mortem analysis is easier.

13. **Add a `--max-runtime` flag to the supervisor.** A safety net for unattended drains — if the supervisor has been running more than N hours, exit cleanly instead of grinding indefinitely. Useful for CI / scheduled drains.

### Code health

14. **Move `process_attachments.py` toward smaller modules.** The file is ~750 lines and houses extraction loop, supervisor, embed, sweep, and reembed — five distinct concerns. Splitting (e.g. `claim.py`, `supervisor.py`, `embed.py`) would make each easier to test in isolation.

15. **Investigation issue for residual surya hits at non-`.max()` sites.** PR #56 covered every `.max().item()` in surya. The 64 residual hits (and the multi-MPS yield collapse) imply other PyTorch ops at unpatched call sites have the same MPS bug. Worth filing upstream with our reproducer if not already known.

### Configuration

16. **Smoke-test `uv sync` against marker before any dependency change.** A 10-second sanity script that imports cv2 and runs a one-page PDF through marker would catch the kind of opencv break we hit. Could live as a justfile target `just smoke-marker`.

17. **Standardize log location.** `drain.log`, `drain-A.log`, `drain-B.log`, `retry-drain.log`, `retry-surya-drain.log`, `process_attachments.log`, `reembed.log` all accumulated in the project root over the drain. Pick a directory.

---

## Recommended next phase

Adopt the same execution pattern for the next full-corpus pass:

1. Apply items **#1 (Docling fallback)**, **#2 (NUL sanitize)**, and **#3 (hard-timeout retry pass)** as a single PR before starting.
2. Flip remaining `failed` rows to `pending` (one SQL `UPDATE`), preserving any rows we've decided are unrecoverable.
3. Run `maildb process_attachments run --workers 1 --extract-timeout 300` under a single supervisor.
4. Watch via `maildb jobs --watch 30` once a day; otherwise leave it alone.
5. After completion, run `process_attachments retry --hard-timeouts-only --extract-timeout 900` for one more pass at the slow-but-completable docs.
6. Final yield target: **>95%** of office formats (via Docling) and **>90%** of PDFs (Marker + the bumped-timeout retry).
