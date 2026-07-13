---
name: chronicle-build
description: Resume and drive the Life Chronicle UI build (analyst workstation over the maildb archive). Use when the user says "continue the Life Chronicle build", "work on the chronicle app", "/chronicle-build", or asks to resume/advance the UI application project. Claude plans, coordinates, and reviews; the cheap coder (grok) implements.
---

# Chronicle Build Driver

You are the planner, coordinator, and reviewer for the Life Chronicle application. Grok
(via the cheap-coder skill) is the implementer. The user has standing intent for this
project to be driven to completion phase by phase.

## Resume procedure

1. Read `docs/superpowers/plans/2026-07-13-life-chronicle-plan.md` — architecture
   decisions, phase/task breakdown, execution protocol, and the **STATE** section
   pointing at the next task.
2. Consult the normative spec `docs/superpowers/specs/2026-07-13-life-chronicle-spec.md`
   for the sections the next task touches (it is 1.5k lines — read selectively by
   section number from the plan's task table).
3. Check the GitHub epic issue "Life Chronicle — analyst workstation UI" and any open
   `cheap-coder/chronicle-phase-*` PR for in-flight state.
4. Continue per the plan's **Execution protocol** (Section 4): elaborate the next task
   spec, invoke the cheap-coder workflow with the grok profile at max effort, gate,
   review, commit to the phase PR.
5. After each task: update the plan's STATE table and the epic checklist.
6. At phase boundaries: elaborate the next phase's task table in the plan (progressive
   elaboration) and present it to the user before starting; phases 3–5 are scoped but
   not yet task-detailed.

## Invariants (from the spec — never trade away)

- Root route is Chronicle; secondary lenses share one working set.
- Source tables are immutable; the app writes only `app_*` tables.
- Every generated claim traceable to source evidence; origin always visible.
- Email HTML sanitized server-side; remote content blocked by default.
- Model routing visible, configurable, audited; no silent external transmission.
- Server-side aggregation + cursor pagination; never load the archive into the browser.
- Both gates green before any commit: `uv run just check` and `just check-app`.

## Merge policy

The user decides merges unless they have explicitly delegated merging for the current
phase. Reviews are recorded per task in the worktree's `.cheap-coder/review-NN.md` and
summarized on the phase PR.
