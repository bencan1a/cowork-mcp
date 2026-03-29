# General Review: claude-md

**Date**: 2026-03-29
**Branch**: claude/comp-review-all-OrwvK vs main
**Files reviewed**: 1
**Reviewer**: principal-engineer

## Summary

This diff updates `CLAUDE.md` to accurately reflect the current codebase state: adding documentation for `config.py`, `graph/contacts.py`, `graph/tasks.py`, the `BearerAuthMiddleware`, the tool registration pattern, corrected `--cov` paths (from `src/` to `.`), corrected `mypy`/`bandit` target paths, accurate `check-all` description, and a new Testing Notes section. All factual claims in the diff were verified against the actual codebase and are correct. The changes improve onboarding accuracy and reduce the risk of agents or developers following stale instructions.

## Findings

### [SUGGESTION] Stale docs/CONTEXT.md contradicts updated CLAUDE.md
**File**: `docs/CONTEXT.md` (lines 26-30, folder_structure section)

**Issue**: `docs/CONTEXT.md` still references `"src/": "Source code"` in its `folder_structure` and refers to the project as "python-template". The CLAUDE.md diff correctly adds the note "Important: no `src/` directory", but `CONTEXT.md` now contradicts this. While the `CONTEXT.md` file is not part of this diff, the inconsistency is worth flagging because the CLAUDE.md changes make it more visible.

**Recommendation**: Regenerate or manually update `docs/CONTEXT.md` to reflect the actual project structure and name. If it is auto-generated (the header says "Generated"), ensure the generation script picks up the correct metadata.

**Rationale**: Having two authoritative context files disagree about project structure (`src/` vs root-level modules) will confuse both human developers and AI agents. CLAUDE.md is now correct; CONTEXT.md should follow.

### [SUGGESTION] Coverage target dropped without replacement
**File**: `CLAUDE.md` (line 106 in current file)

**Issue**: The old text specified ">80% coverage" as a quality standard (item 5 under Code Quality Standards). The new text replaces this with "all pass; `pytest-asyncio` with `asyncio_mode = \"auto\"`", removing the coverage target entirely. This is factually accurate (the Makefile `check-all` target runs `pytest` without `--cov`), but it means there is no longer any stated coverage expectation in the project's guidance document.

**Recommendation**: If a coverage target is still desired, add it back explicitly (e.g., "target >80% coverage on changed code"). If the team intentionally dropped it, no action needed -- but be aware that without a stated target, coverage may drift downward over time.

**Rationale**: Coverage targets, even soft ones, provide a useful signal for maintaining test discipline. Removing one without discussion is worth a conscious decision.

## Verdict
- PASS -- no critical or important findings

The CLAUDE.md updates are accurate, well-structured, and improve the documentation significantly. Every factual claim (file existence, Makefile targets, conftest hook behavior, middleware presence, pydantic-settings usage) was verified against the codebase and confirmed correct. The two suggestions above are minor consistency and process improvements that do not block merge.
