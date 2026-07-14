# Life Chronicle — Verification Report

**Date:** 2026-07-14  
**Scope:** Phase 5 final — acceptance workflows, §19.6 release gates, §18 requirements register, performance, security, limitations  
**Spec:** `docs/superpowers/specs/2026-07-13-life-chronicle-spec.md`  
**Perf source:** `apps/chronicle/server/perf/results-2026-07-14.json`

---

## 1. Release gates (§19.6 / Table 43)

| Area | Status | Evidence |
| --- | --- | --- |
| **Product** | **Pass** | Root route is Chronicle (`App.tsx` index → `ChroniclePage`). Secondary lenses inherit working-set scope via `useWorkingSetStore` + ScopeBar. Acceptance: `chronicle.acceptance.test.tsx`, workflows A–E. |
| **Evidence** | **Pass** | Generated claims/events expose citations and open full sources. `EventCard` / `ReconstructionView`; Ask citations in `AnswerBlock`; workflows A & E. Server: `test_events.py`, `test_generate.py`, `test_ask.py`. |
| **Safety** | **Pass** | Sanitizer corpus + remote block (`test_sanitize.py`); preview CSP/sandbox (`test_files.py::test_preview_allowlist_and_headers`); client CSP meta (`api/cspMeta.test.ts`); containment (`test_files.py::test_containment_guard`). |
| **Scale** | **Pass with note** | ETag + `app_cache` (`test_etag.py`, `test_cache.py`); cursor pagination (`test_search.py::test_cursor_window_walk`, `test_files.py::test_list_shape_and_keyset`); cancellation leaves server healthy (cache/search suites). **Live search soft-miss** — see §3. Hard floor (target × 2) not breached. |
| **Accessibility** | **Pass** | WCAG token contrast (`a11y/contrast.test.ts`); landmark/ARIA (`a11y/ariaCoverage.test.tsx`); focus order (`a11y/focusOrder.test.tsx`); keyboard registry (`keyboard/shortcutRegistry.test.ts`); table alternatives for canvas (`TimelineTable.test.tsx`, `CompareView.test.tsx`). |
| **Privacy** | **Pass** | Model route/policy on Ask UI (`AnswerBlock.test.tsx`); settings whitelist + audit (`test_settings.py`); export redaction never mutates originals (`test_redact.py::test_scan_and_redact_workspace_do_not_mutate_originals`, `test_workspaces.py` export suite); fresh-auth export (`test_auth.py::test_stale_auth_at_requires_reauth`). |
| **Data quality** | **Pass** | Data Health coverage/extraction/embeddings/jobs (`test_health.py`, `DataHealthPage.test.tsx`); failed attachment discoverability (workflow C; `FilesPage.test.tsx` failed-status rows). |
| **State** | **Pass** | URL serialize/restore (`workingset/urlState.test.ts`, `useUrlSync.test.tsx`); focus return contract (workflow A; `FocusMode.test.tsx`); source Back restores viewport (workflow A; `SourcePage.test.tsx`). |

---

## 2. Requirements register audit (§18 / Table 42)

Statuses: **met** | **met-with-note** | **deferred**. Evidence is one line; only real suites/files are cited.

| ID | Status | Evidence |
| --- | --- | --- |
| G-001 | met | Root `/` → Chronicle; `chronicle.acceptance.test.tsx`, `App.tsx`. |
| G-002 | met | Shared `useWorkingSetStore` + ScopeBar across lenses; workflow B. |
| G-003 | met | `urlState.test.ts`, `useUrlSync.test.tsx`, workspace scope restore. |
| G-004 | met | `CommandBar.test.tsx`, `CommandPalette.test.tsx`, Research Ask. |
| G-005 | met | `Inspector` shell always mounted; workflow A selection + recon. |
| G-006 | met | `PinToWorkspace` + answer pin; workflows A & E. |
| G-007 | met | Origin badges on events/topics; `EventCard.test.tsx`, `originBadge.tsx`. |
| G-008 | met-with-note | Density/theme/lanes persist via settings + local appearance (`SettingsPage.test.tsx`, `appearance.test.ts`); panel widths not fully device-persisted. |
| LC-001 | met | Timeline aggregation + zoom; `timeScale.test.ts`, `useChronicleBuckets.test.ts`. |
| LC-002 | met | Density navigator; `ChroniclePage.timeline.test.tsx`. |
| LC-003 | met | Default lanes messages/attachments/people/topics/events; `laneModel.test.ts`. |
| LC-004 | met | `LaneConfigPanel.test.tsx`, settings default_lanes. |
| LC-005 | met | Mark → inspector; workflow A/D, `InspectorPanel.test.tsx`. |
| LC-006 | met | Event origin/derivation/status/correction; workflow A, `test_events.py`. |
| LC-007 | met | Bucket Open as list → `SourceList`; focus source sequence. |
| LC-008 | met | Workflow A double Back; `FocusMode.test.tsx`. |
| LC-009 | met | Compare mode; `CompareView.test.tsx`, `test_chronicle.py` compare paths. |
| LC-010 | met | Chronicle without model calls; `chronicle.acceptance.test.tsx` asserts no `/api/ask`. |
| RD-001 | met | Hybrid/Exact/Semantic; `ResearchDeskPage.test.tsx`, `test_search.py`. |
| RD-002 | met | Message + attachment results; workflow C, `test_search.py`. |
| RD-003 | met | NL interpret → editable chips; workflow C, `test_interpret.py`. |
| RD-004 | met | Ask keeps ranked sources; `ResearchDeskPage.test.tsx` ask+search. |
| RD-005 | met | Citations / unmatched markers; `AnswerBlock.test.tsx`, `test_ask.py`. |
| RD-006 | met | Conflicting claims in reconstruction; workflow A. |
| RD-007 | met-with-note | Client grouping: thread/year/mailbox (`grouping.ts` / `grouping.test.ts`). **Absent:** org and version-family grouping in Research results. |
| TA-001 | met | Hierarchy default; `TopicsPage.test.tsx`. |
| TA-002 | met | Origin badge; `originBadge.tsx`, topic card. |
| TA-003 | met | Projection LOD; `ProjectionView` + `test_topics.py`. |
| TA-004 | met | Topic member list; workflow B, `TopicCard` sources. |
| TA-005 | met | Manual topic corrections preserved; `test_topics.py` curation paths. |
| PE-001 | met | Merge/unmerge versioned; workflow D, `test_people.py`. |
| PE-002 | met | Address classes / role signals on card; `PersonProfilePage.test.tsx`. |
| PE-003 | met | Ego edges carry thread evidence; workflow D. |
| PE-004 | met | Copy audit forbids quality-from-volume; `people/copyAudit.test.ts`. |
| FI-001 | met | Attachment → source_message_id provenance; workflow C, `test_files.py`, `test_search.py`. |
| FI-002 | met | Failed extraction still listed; workflow C, `FilesPage.test.tsx`. |
| FI-003 | met | Sandboxed preview, no active content; `test_files.py::test_preview_allowlist_and_headers`, PreviewPanel. |
| FI-004 | met | Duplicates + probable version families; workflow C, `test_files.py` family/compare suite. |
| TR-001 | met | `test_sanitize.py` (incl. adversarial corpus); remote blocked. |
| TR-002 | met | Quoted collapse/expand; `quotedText.test.ts`, `SourcePage.test.tsx`. |
| TR-003 | **deferred** | Thread merge/split corrections not implemented. |
| WS-001 | met | Workspace stores scope + pins; workflow B/E, `test_workspaces.py`. |
| WS-002 | met | Notebook layout + export; workflow E, `WorkspacePage.test.tsx`. |
| WS-003 | met | Provenance manifest + fingerprint; workflow E, `test_workspaces.py::test_export_markdown_json_csv_manifest_and_fingerprint`. |
| WS-004 | **deferred** | Reproducible snapshot source sets (SHOULD) not implemented. |
| AI-001 | met | Ask retrieval status + scope; `AnswerBlock.test.tsx`. |
| AI-002 | met | Model route display + settings gates; `AnswerBlock`, `test_settings.py`. |
| AI-003 | met | Derivation / process_version on events; workflow A, `test_generate.py`. |
| AI-004 | met | Injection stays in sources block; `test_gateway.py::test_injection_text_stays_in_sources_block_never_alters_roles`. |
| AI-005 | met | Corrections not silently overwritten (version conflict banners); `WorkspacePage.test.tsx` 409, event version checks. |
| DH-001 | met | `test_health.py`, `DataHealthPage.test.tsx`. |
| DH-002 | met | Failed records openable; Data Health + Files failed rows. |
| SEC-001 | met | Auth session, rate limit, export audit; `test_auth.py`, `test_db.py::test_audit_insert`. |
| SEC-002 | met | Sanitizer + CSP middleware + preview sandbox; `test_sanitize.py`, `test_auth.py::test_security_headers_present`, `cspMeta.test.ts`. |
| SEC-003 | met | Redacted export is a copy; `test_redact.py`, workspace export tests. |
| PERF-001 | met | Cursor pagination server-side; list UIs; `test_search.py`, `test_files.py`. |
| PERF-002 | met | Server aggregation for buckets/compare/topics; `test_chronicle.py`, `test_cache.py`. |
| PERF-003 | met | AbortController on search/buckets; client ignore-stale patterns in research/chronicle hooks. |
| A11Y-001 | met | Keyboard shortcuts + focus order tests; `a11y/*`, `keyboard/*`. |
| A11Y-002 | met | Table/list equivalents for canvas/compare/graph; `TimelineTable`, `CompareView`, ego evidence list. |
| A11Y-003 | met | Text-prefixed diff kinds / status labels (not color-only); `VersionCompareView`, extraction status text. |

### Known deferrals (honest)

| Item | Disposition |
| --- | --- |
| **TR-003** | Thread merge/split corrections — not implemented. |
| **RD-007 partial** | Research grouping is thread/year/mailbox only; org + version-family grouping absent from Research Desk (families live under Files). |
| **§15.1 passkeys** | Password + argon2 + login rate limit shipped instead of passkeys (`test_auth.py` rate-limit suite). |
| **Research-lens topic scoping** | Topic membership is authoritative in Topic Atlas member lists (workflow B), not as a Research Desk scope chip. |
| **WS-004** | Workspace snapshot source sets (SHOULD) — not implemented; live query + pins only. |
| **PDF/ZIP export formats** | Markdown / JSON / CSV shipped; PDF and ZIP not implemented (`WorkspacePage` export menu: markdown/json/csv). |

---

## 3. Performance results

**Source:** `apps/chronicle/server/perf/results-2026-07-14.json`  
**Environment:** 1,279,362 messages · 943,604 threads · 48,467 attachments · 63,061 contacts  
**Date range:** 1998-04-27 → 2100-09-08 · `n_runs=5` · hard floor = target × 2  
**Gate rule:** nonzero exit only if warm p50 exceeds target × 2 (hard floor). Soft miss = warm p50 over target but under hard floor.

### Live harness table (warm p50 vs §16.2 targets)

| Scenario | Target (ms) | Warm p50 (ms) | Warm p95 (ms) | Cold (ms) | Pass |
| --- | --- | --- | --- | --- | --- |
| archive_summary | 1000 | **170.08** | 178.78 | 168.31 | ✓ |
| buckets_full_extent | 1500 | **173.02** | 178.65 | 5424.66 | ✓ |
| buckets_1y_month | 1500 | **173.25** | 188.14 | 1340.23 | ✓ |
| search_exact | 2000 | **3225.37** | 3236.63 | 3240.81 | ✗ soft |
| search_hybrid | 3000 | **3381.39** | 3394.19 | 3389.70 | ✗ soft |
| sources_list | 1000 | **2.16** | 2.53 | 13.65 | ✓ |
| topics_list | 1000 | **166.57** | 177.63 | 209.25 | ✓ |

**Hard floor failures:** none (`hard_floor_failures: []`).  
**Soft failures:** `search_exact`, `search_hybrid`.

### Search common-term analysis

Common-term substring search (e.g. `%meeting%`) defeats trigram GIN selectivity on the live archive: EXPLAIN-level cost stays high (~1M buffers class), so warm p50 sits ~3.2–3.4s regardless of cold/warm. Selective terms remain fast. Hybrid adds embedding ranking but does not fix the exact/common-term bottleneck (hybrid warm p50 ≈ 3381 ms vs 3000 target).

### tsvector remediation recommendation

Add an **additive** maildb schema migration: `tsvector` full-text (GIN) on email body/subject (and attachment extracted text where appropriate), with ranked FTS queries for common terms. This is outside the chronicle app boundary (depends on maildb backlog). Until then, document the soft miss; do not treat hard-floor green as “search is done.”

---

## 4. Security controls

| Control | Status | Tests / pointers |
| --- | --- | --- |
| Sanitizer corpus | Pass | `apps/chronicle/server/tests/test_sanitize.py` — scripts, onerror, javascript:, remote img, SVG, mXSS/math, srcset, formaction, data: URIs, etc. |
| CSP | Pass | Server middleware `default-src 'none'` + preview sandbox CSP (`test_files.py::test_preview_allowlist_and_headers`, `test_auth.py::test_security_headers_present`); client meta (`web/src/api/cspMeta.test.ts`). |
| Containment | Pass | Attachment path containment (`test_files.py::test_containment_guard`). |
| Rate limit | Pass | Login fixed-window (`test_auth.py::test_login_rate_limit_429_and_reset_on_success`, window math tests). |
| Reauth | Pass | Fresh `auth_at` for export (`test_auth.py::test_stale_auth_at_requires_reauth`, `WorkspacePage.test.tsx` reauth panel). |
| Redaction | Pass | PII detect/apply (`test_redact.py`); review-then-confirm export (workflow E, `WorkspacePage.test.tsx`, `test_workspaces.py::test_export_redaction_review_then_confirm`). |
| Audit coverage | Pass | Login/logout (`test_auth.py`); ask/interpret/generate/export/download audits (`test_gateway.py`, `test_interpret.py`, `test_generate.py`, `test_workspaces.py`, `test_files.py::test_download_disposition_and_audit`); health audit tail (`test_health.py`). |

---

## 5. Acceptance workflows

| Workflow | Spec | Test file | Status |
| --- | --- | --- | --- |
| A — reconstruct a decision | §19.1 | `web/src/acceptance/workflowA.acceptance.test.tsx` | Pass |
| B — explore an unknown period | §19.2 | `web/src/acceptance/workflowB.acceptance.test.tsx` | Pass |
| C — vaguely remembered file | §19.3 | `web/src/acceptance/workflowC.acceptance.test.tsx` | Pass |
| D — investigate a person | §19.4 | `web/src/acceptance/workflowD.acceptance.test.tsx` | Pass |
| E — defensible case file | §19.5 | `web/src/acceptance/workflowE.acceptance.test.tsx` | Pass |
| Chronicle baseline | shell/state | `web/src/acceptance/chronicle.acceptance.test.tsx` | Pass |

---

## 6. Known limitations & recommended next steps

1. **Search soft-miss (common terms)** — implement maildb `tsvector` FTS GIN (see §3). Out of app scope this phase.
2. **Incremental sync** — depends on maildb backlog **#41 / #42** (live archive refresh without full reimport). Chronicle assumes a current DB snapshot.
3. **Deferred product items** — TR-003 thread merge/split; WS-004 snapshot source sets; RD-007 org/version-family Research grouping; PDF/ZIP export; passkeys.
4. **Cold bucket full-extent** — first uncached full-extent buckets still cold ~5.4s; warm path is fine via `app_cache` + ETags. Optional: warm critical extents on deploy.
5. **Topics generation** — intentionally excluded from the perf harness (too slow); list path is in budget.
6. **Embedding service dependency** — hybrid degrades cleanly when embeddings are down (`test_search.py` degraded/503 paths); document operational coupling to Ollama.

---

## 7. Sign-off

| Gate class | Result |
| --- | --- |
| `just check-app` | Required green for release (server ruff/mypy/pytest + web tsc/vitest/build) |
| `just check` (root) | Required green for monorepo library gate |
| §19.6 product/evidence/safety/a11y/privacy/data/state | **Pass** |
| §19.6 scale | **Pass with documented search soft-miss** |
| §18 register | Dispositioned; deferrals listed above |

**Release recommendation:** Ship with documented search soft-miss and listed deferrals; prioritize tsvector FTS and maildb incremental sync (#41/#42) as follow-on work.
