# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

# Project Standards

Python 3.12+ project — PostgreSQL, psycopg3, pgvector, Ollama, Pydantic v2, managed with uv.

## Quick Reference

Use the just targets to run test, run linter, etc.

## Design Document

`docs/DESIGN.md` is a high-level design overview — load-bearing decisions, architecture map, and intent. For current implementation details (schema columns, indexes, method signatures, DSL operators, pipeline code paths), read the code. Update DESIGN.md only when a decision changes, not when an implementation detail moves.

## Critical Rules

- ALWAYS use `uv` for dependency management. Never use pip directly.
- ALWAYS use `uv run` to execute commands (ensures correct virtualenv).
- ALL config lives in `pyproject.toml` — no setup.cfg, setup.py, or tool-specific config files.
- NEVER commit without running `uv run just check`.
- Use `pydantic-settings` for all environment/configuration management.
- Write a test for every new MailDB method, MCP tool, and ingestion/parsing function.
- Work in feature branches instead of worktrees
