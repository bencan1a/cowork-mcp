# CI Pull Request Review

You are reviewing a pull request in the cowork-mcp project. This is a **rigorous, standards-focused review** — not a nitpick session. Ignore trivial style, cosmetic issues, or linter-catchable problems.

## Your Task

Review the PR at: `${{ github.repository }}/pull/${{ github.event.pull_request.number }}`

### Review Focus Areas

#### 1. Architectural Alignment
- Do changes follow patterns in `CLAUDE.md` and `docs/CONTEXT.md`?
- Are all Graph API calls routed through `graph/client.py` GraphClient (never raw httpx to graph.microsoft.com)?
- Are all token operations routed through `auth/token_store.py` (never raw MSAL calls elsewhere)?
- Does `BearerAuthMiddleware` in `server.py` still cover all routes?

#### 2. Coding Standards Compliance
- Follow `CLAUDE.md` standards?
- **Python**: type annotations on all public functions, no bare `Any` without justification, correct import ordering, pydantic-settings for config?
- Ruff-compliant and mypy strict-mode clean for changed modules?
- No raw MSAL calls outside `auth/`, no raw httpx calls to Graph outside `graph/client.py`?

#### 3. Completeness
- Error handling at Graph API boundaries (surface as MCP tool errors, not raw exceptions)?
- Pagination handled (`@odata.nextLink`) in all new list operations?
- New scope toggles registered in both `config.py` Settings AND `graph/client.py` SCOPE_MAP?
- Missing tests? (>80% coverage target, mock Graph calls, test 401 paths)
- Unhandled edge cases or missing validation at system boundaries?

#### 4. Correctness & Safety
- Potential runtime errors (None access on Graph response fields)?
- Security concerns: hardcoded secrets, tokens in logs, missing bearer auth?
- Token cache file permissions enforced after writes?
- MSAL authority string correct for personal accounts (`consumers`)?

#### 5. Simplicity & YAGNI
- Over-engineering for a single-user personal project?
- Unnecessary abstractions that add complexity without benefit?
- Could it be simpler while meeting requirements?

## Required Reading

Before reviewing, read:
- `CLAUDE.md` — project rules and critical implementation notes
- `auth/token_store.py` — token storage patterns and invariants
- `server.py` — BearerAuthMiddleware implementation and tool registration

## Steps

1. **Fetch PR details**: `gh pr view $PR_NUMBER --json title,body,files,additions,deletions`
2. **Get the diff**: `gh pr diff $PR_NUMBER`
3. **Identify changed files**: Note files modified, created, or deleted
4. **Read relevant context**: CLAUDE.md, token_store.py, server.py as applicable
5. **Review each file** against the focus areas above
6. **Document findings** (see format below)
7. **Post review** using `gh pr review $PR_NUMBER --comment --body "..."`

## Finding Format

For each finding:

```markdown
### [SEVERITY] Location
**File**: `path/to/file.py` (lines X-Y)

**Issue**: Clear description of what's wrong

**Recommendation**: Specific fix or approach

**Rationale**: Why this matters (cite standard or principle)
```

**Severity Levels**:
- 🔴 **CRITICAL**: Must fix — correctness/security/architecture violation
- 🟡 **IMPORTANT**: Should fix — standards non-compliance, missing tests, completeness gaps
- 🔵 **SUGGESTION**: Consider — simplification, minor improvements

## Output Structure

Post a review comment with:

```markdown
## Code Review Summary

[One paragraph overall assessment]

---

## Findings

[List all findings using the format above]

---

## Verdict

[Choose one:]
- ✅ **PASS**: No critical or important findings. Approved.
- ⚠️ **PASS WITH CHANGES**: Important findings exist. Address them before merge.
- ❌ **NEEDS REWORK**: Critical findings exist. Must fix before approval.

---

**Review performed by**: Claude Code (automated)
**Review focus**: Auth, Token Storage, Graph API Correctness, Pagination, Secrets, Input Validation, Test Coverage
```

## Review Guidelines

**DO report**:
- Architecture violations (GraphClient bypass, TokenStore bypass, BearerAuthMiddleware gaps)
- Standards non-compliance (missing type annotations, wrong patterns)
- Correctness issues (bugs, None access on Graph responses)
- Security vulnerabilities (hardcoded secrets, tokens in logs, MSAL authority wrong)
- Missing error handling at Graph API boundaries
- Missing or incorrect tests
- Completeness gaps (missing pagination, missing scope registration)

**DON'T report**:
- Trivial naming preferences
- Cosmetic formatting (ruff handles this)
- Subjective style opinions not backed by CLAUDE.md standards
- Micro-optimizations without measurable impact
- Hypothetical future requirements (YAGNI for a personal project)

## Important Notes

- **Be thorough but focused**: Flag real issues, not nitpicks
- **Cite sources**: Reference CLAUDE.md, specific standards, or architecture patterns
- **Be specific**: Point to exact files and lines
- **Be constructive**: Provide actionable recommendations
- **Trust the linter**: Don't flag things ruff/mypy would catch

If the PR is clean, say so. "No findings" is a valid and valuable review.
