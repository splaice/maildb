# Attachment Extraction — Single-MPS-Worker Discipline

**Rule:** on Apple Silicon, run **one** attachment-extraction supervisor at a time. Do not start a second one in parallel.

## Why

PR #60 made parallel supervisors **safe** — each tags its claims with a
unique `claimed_by` UUID and only kills rows it owns, so two supervisors
no longer race on shared stuck-row detection. But the Apr 2026 dual-MPS
experiment showed yield collapses from **~73% (one worker)** to **~3%
(two workers)** because two processes hammering Apple Silicon MPS
simultaneously trigger surya kernel bugs at PyTorch ops the
`scripts/patches/surya-mps-fix.patch` doesn't cover (`argmax`, `gather`,
`topk`, `take_along_dim`, etc.). Net useful throughput is **strictly
worse** with two workers.

Bottom line: parallel-supervisor isolation lets you run two if you want
to, but you shouldn't — until upstream MPS reduce kernels stabilize.

See: `docs/retrospectives/attachment-drain-retrospective.md` §4 ("MPS
contention destroys yield with two workers").

## Confirm a single supervisor

Before starting a drain:

```bash
maildb jobs
```

Under **Active processes**, expect at most one row whose command starts
with `maildb process_attachments run` (or `… retry`). If you see two,
stop — kill the older one before continuing.

Once started, recheck periodically:

```bash
watch -n 60 maildb jobs
```

## Safely killing a supervisor

The worker subprocess is a session leader (it calls `os.setsid()` so the
supervisor can SIGKILL the whole process group). That means
**SIGTERM/SIGKILL on the supervisor parent does *not* propagate to the
worker** — the worker becomes an orphan of init and keeps claiming rows
silently.

To stop cleanly:

```bash
# Find the worker pid (under "In flight" → look at Active processes for
# the supervisor's child). Then kill the worker's process group:
kill -TERM -- -<worker_pid>     # graceful
kill -KILL -- -<worker_pid>     # if it doesn't exit in a few seconds
```

The leading `-` in `-<pid>` makes `kill` target the process group named
by `pid`. After that, killing the supervisor parent is fine.

## Adding CPU-mode workers (future)

Multi-worker supervised runs are tracked in #68 but **not yet
implemented**. The supervised path currently dispatches to
`_run_supervised_single_worker` regardless of `--workers`. When #68
ships, CPU-mode (non-MPS) workers can run in parallel safely; MPS-mode
workers still cannot.

## Related

- Issue #59 — per-supervisor `claimed_by` isolation (fixed in PR #60)
- Issue #64 — this discipline doc
- Issue #68 — multi-worker supervised path (future, not for MPS)
- Issue #75 — file upstream surya issue for residual MPS bugs
