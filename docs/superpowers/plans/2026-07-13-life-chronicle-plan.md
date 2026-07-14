# Life Chronicle — Master Implementation Plan

**Spec:** `docs/superpowers/specs/2026-07-13-life-chronicle-spec.md` (normative; wireframes in `life-chronicle-wireframes/`)
**Execution model:** Claude = planner / coordinator / reviewer. Grok Build CLI (`grok-4.5` @ max) = implementer via the cheap-coder workflow.
**Driver:** `.claude/skills/chronicle-build` — re-entrant; any session can say "continue the Life Chronicle build" and resume from the STATE section below.
**Tracking epic:** see GitHub epic issue "Life Chronicle — analyst workstation UI".

## 1. Spec review verdict (2026-07-13)

The spec is implementable as written and fits this repo unusually well:

- **Existing leverage.** The archive layer the spec assumes (Section 1.5) already exists: `emails`/threads (union-find repaired), attachments + extracted markdown, pgvector embeddings + HNSW, hybrid search with RRF (`search_all`), and — critically — the contacts subsystem (Phases 1+2) is a working implementation of Section 7's identity resolution: entity address book, human-probability classifier, manual curation with no-clobber, snapshot-audited merge/unmerge. People & Organizations is mostly a UI over what we just shipped.
- **Genuine gaps** (all additive, per the spec's adapter rule): no HTTP API (only MCP/CLI), no web frontend, no auth, no topics subsystem (hierarchy/clustering), no events subsystem, no passage-level citation store, no workspaces, no export/redaction pipeline.
- **Non-negotiables honored by design:** sources stay immutable (app writes only to new `app_*` tables); derived objects versioned; citations traceable; HTML sanitized server-side; model routing visible/audited; scale via server-side aggregation + cursor pagination (the `include_total` work from PR #102 aligns exactly).

## 2. Architecture decisions (within Section 20.2 decision boundaries)

| Decision | Choice | Rationale |
| --- | --- | --- |
| Repo layout | Monorepo: `apps/chronicle/server` + `apps/chronicle/web` in this repo | The adapter imports the `maildb` library directly; no duplicated query logic. |
| Backend | Python 3.12, FastAPI, pydantic v2, uv workspace member | Reuses pool/config/query code; SSE for `/api/ask`; same toolchain and gates. |
| Frontend | React + TypeScript + Vite + Tailwind (tokens from Section 13), pnpm | Mainstream, testable; Tailwind config carries the graphite/steel token table verbatim. |
| Timeline rendering | Custom Canvas 2D renderer with DOM/table accessible alternative | Spec 17.5 permits Canvas for density; no chart library fits the lane/LOD model. |
| Client state | URL-first (QueryScope serializer) + zustand (transient) + TanStack Query (fetch/abort) | Spec G-003/PERF-003: URL restorable state, cancellable requests. |
| App data | New `app_`-prefixed tables (`app_users`, `app_saved_views`, `app_workspaces`, `app_workspace_blocks`, `app_events`, `app_event_versions`, `app_citations`, `app_topics`, `app_audit`, …) | Additive-only; source tables untouched; versioned derived objects per Section 2.4. |
| Auth | Session cookie + argon2, single user, authz boundary designed for multi-user; passkeys in Phase 5 | Spec 15.1. |
| Model gateway | Server-side only; Ollama local route first, external providers behind per-action policy + audit | Spec 12.5/15.3; MCP server stays independent. |
| Jobs | Reuse maildb job/claim patterns for generation jobs (topics, events, previews, exports) | Battle-hardened SKIP LOCKED idioms already in repo. |
| Gates | `uv run just check` (unchanged) + new `just check-app` (server pytest + web lint/tsc/vitest/build); CI job added in Phase 0 | Every task commit must pass both. |

## 3. Phase and task breakdown

Tasks are cheap-coder-sized (≈ one focused PR or less). One PR per phase, one commit per task, mirroring the contacts Phase 2 execution. Phases 3–5 are elaborated in detail when they start (progressive elaboration); their task lists below are scoping placeholders.

### Phase 0 — Archive adapter & foundation (PR: `cheap-coder/chronicle-phase-0`)

| Task | Scope | Exit criterion |
| --- | --- | --- |
| 0.1 | Server scaffold: uv workspace member `apps/chronicle/server`; FastAPI app; session auth (argon2, env-configured single user); `GET /api/archive/summary` from maildb (accounts, date range, counts, extraction/embedding coverage); `app_users` + `app_audit` tables; pytest wiring; `just check-app` target | Authenticated request returns real archive coverage JSON; gates green |
| 0.2 | Web scaffold: `apps/chronicle/web` Vite+React+TS+Tailwind with Section 13 tokens; login flow; workstation shell skeleton (command bar, scope bar, nav, canvas, inspector zones, status strip); archive summary rendered | Shell renders at 1440px with real data; keyboard focus order correct; gates green |
| 0.3 | Source contracts: stable ID scheme; `GET /api/sources/:id`, `/api/sources/:id/context`, `/api/threads/:id`; opaque cursor pagination util; server-side HTML sanitization (allowlist, remote content stripped) | Message/attachment/thread retrievable by stable ID with sanitized body; unit tests incl. sanitizer XSS corpus |
| 0.4 | Data Health basics: `/api/health/archive` (threading, extraction, embedding, jobs from existing tables); `/data-health` route with tables | Extraction failures and embedding coverage visible in UI |

### Phase 1 — Chronicle foundation (PR: `cheap-coder/chronicle-phase-1`)

| Task | Scope |
| --- | --- |
| 1.1 | `POST /api/chronicle/buckets`: server-side time bucketing (year→hour) chosen from viewport+pixel width; lanes: messages, attachments, people-active; scope filtering via QueryScope |
| 1.2 | Timeline canvas MVP: axis, wheel/pinch zoom with LOD switch, pan, brush, density navigator; messages + attachments lanes; accessible table alternative |
| 1.3 | Working set: QueryScope serializer, scope bar chips (date/person/mailbox/exclusion), URL replace-state/history contract, back/forward restoration |
| 1.4 | Evidence inspector + Message/Thread reader: sanitized rendering, quoted-text collapse, envelope, source modes, open-from-mark and return-to-viewport guarantee |
| 1.5 | People lane from contacts subsystem; lane configuration (hide/reorder/resize/collapse, saved lens) |
| 1.6 | Focus mode + Chronicle acceptance tests (Section 4.10 criteria 1–8, events excluded until Phase 3) |

### Phase 2 — Research & evidence (PR: `cheap-coder/chronicle-phase-2`)

| Task | Scope |
| --- | --- |
| 2.1 | `POST /api/search`: hybrid (reuse `search_all`/RRF), Exact and Semantic modes, structured syntax parser (Section 5.3), facets, cursor, why-matched |
| 2.2 | `POST /api/query/interpret`: NL → QueryScope proposal via model gateway; editable chips |
| 2.3 | Research Desk UI: modes, result cards per type, grouping, duplicate suppression |
| 2.4 | `POST /api/ask` SSE: retrieval → streamed grounded answer; citation contract (`app_citations`, char offsets + excerpt hash); model gateway v1 (Ollama route, prompt-injection boundaries, audit) |
| 2.5 | Files & Attachments: browser views, sandboxed preview (PDF/image/text), extraction status, duplicate groups (hash exists in schema) |
| 2.6 | Workspaces v1: CRUD, pins, notes, notebook layout; Markdown/CSV/JSON manifest export |

### Phase 3 — Chronicle intelligence (elaborate at start)

Event schema (`app_events` + versions + claims + citations), generation job over scope fingerprints, event lanes, reconstruction view (claim-to-evidence matrix), confirm/edit/dismiss with no-clobber, focus narrative, compare mode.

### Phase 4 — Secondary exploration (elaborate at start)

Topics subsystem (clustering job over embeddings, hierarchy, curation with manual precedence), Topic Atlas (hierarchy default, projection with LOD, river, matrix), person/organization profiles + ego graph (contacts synergy), version families.

### Phase 5 — Hardening (elaborate at start)

WCAG 2.2 AA audit, security review (CSP, sanitizer corpus, IDOR/enumeration), performance/scale tests against the 1.28M-email archive, redaction pipeline, passkeys, audit completeness, release gates (Section 19.6).

## 4. Execution protocol (per task)

1. Elaborate the task into a self-contained `.cheap-coder/task-NN.md` spec (signatures, files, conventions, acceptance criteria, gate command) in a phase worktree.
2. Run grok (`grok-4.5 @ max`, 10-min cap via `perl -e 'alarm 600; exec @ARGV'` — macOS has no `timeout`).
3. Run gates: `uv run just check` and `just check-app`. Review the diff (correctness/security/performance/quality); record verdict in `.cheap-coder/review-NN.md`; max 1 revision round, then takeover.
4. Commit per task; push to the phase PR; CI must be green.
5. Update the STATE section below and the epic issue checklist in the same commit or immediately after merge.
6. Merge decision belongs to the user unless they have delegated it for the phase.

## 5. STATE — live progress (update after every task)

**Next up:** Phase 1, Task 1.2 (timeline canvas MVP). Goal mode active (2026-07-13): user delegated review+merge of all phases to Claude via /goal.

| Date | Task | PR | Outcome |
| --- | --- | --- | --- |
| 2026-07-13 | Plan + spec committed; harness created | — | This document |
| 2026-07-13 | 0.1 server scaffold: FastAPI, session auth, archive summary, app tables, check-app gate | chronicle-phase-0 | Approved; both gates green (app 20 tests, root 674) |
| 2026-07-13 | 0.2 web scaffold: §13 tokens, login flow, workstation shell, summary panel | chronicle-phase-0 | Approved (1 reviewer fix: unused asset removed); gates green (web 15 tests + build) |
| 2026-07-13 | 0.3 source contracts: stable IDs, sources/threads endpoints, nh3 sanitizer, cursor util | chronicle-phase-0 | Approved (1 reviewer fix: native nh3 link_rel over regex post-processing); 72 server tests |
| 2026-07-13 | 0.4 Data Health: /api/health/archive + /data-health page | chronicle-phase-0 | Approved; Phase 0 complete (75 server + 17 web tests) |
| 2026-07-13 | Phase 0 PR #106 merged (2e48ae9) | #106 | CI green |
| 2026-07-13 | 1.1 buckets endpoint: QueryScope v1, auto-aggregation, lanes, density, fingerprint | chronicle-phase-1 | Approved; 93 server tests |
