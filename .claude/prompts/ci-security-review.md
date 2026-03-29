# CI Security Review

You are performing an automated security review of a pull request in the cowork-mcp project. This is a **focused security review** — you are checking whether code introduced by this PR introduces vulnerabilities or violates the project's security invariants. For per-request speed concerns, see `ci-performance-review.md`. For reliability patterns, see `ci-reliability-review.md`.

## Your Task

Review the PR at: `${{ github.repository }}/pull/${{ github.event.pull_request.number }}`

**Scope discipline**: Only flag security issues introduced by this PR. Pre-existing issues are out of scope.

### Review Focus Areas

#### 1. MCP Bearer Authentication
- Does `BearerAuthMiddleware` in `server.py` still cover all routes after this change?
- Is the token comparison using `hmac.compare_digest()` — not `==`?
- Could any new route bypass the middleware (e.g., middleware order change, exception in dispatch)?
- Is `MCP_API_KEY` sourced from Settings/env — never hardcoded?

#### 2. Token Storage and Encryption
- Is the MSAL token cache file written with `chmod 600` after every save?
- Is `TOKEN_ENCRYPTION_KEY` never logged at any level?
- Is decryption failure handled gracefully (`InvalidToken` caught, cache reset)?
- Is the Fernet key a valid format (32 bytes URL-safe base64)?

#### 3. OAuth / MSAL Invariants
- Is the MSAL authority always `https://login.microsoftonline.com/consumers`?
- Are access tokens and refresh tokens never written to logs?
- Is `AZURE_CLIENT_SECRET` sourced from env — never hardcoded?
- Are `acquire_token_silent` failures raising `RuntimeError` (not swallowed)?

#### 4. Graph API Input Validation
- Are MCP tool inputs (email IDs, folder names, calendar IDs, search queries) validated before passing to Graph SDK?
- Could any user-controlled string reach a Graph call without sanitization?
- Are new list operations handling `@odata.nextLink` (silent truncation = data loss)?

#### 5. Subprocess and Path Safety
- All `subprocess` calls use list form — no `shell=True` with user input?
- File path operations use `pathlib.Path` with `.resolve()` and validated base dir?
- `run_auth.py` localhost callback server — does it bind to `127.0.0.1` only?

#### 6. Secrets and Credential Hygiene
- No hardcoded secrets, API keys, or passwords in source?
- New tokens generated with `secrets` module — not `random`?
- `AZURE_CLIENT_SECRET`, `MCP_API_KEY`, `TOKEN_ENCRYPTION_KEY` never in log statements?
- `.env` files not accidentally committed?

#### 7. New Dependencies
- New `pip` packages: any known CVEs?
- New packages that handle auth, encryption, or file I/O warrant extra scrutiny.
- New packages that parse XML or handle JWTs need justification.

## Required Reading

Before reviewing, read:
- `CLAUDE.md` — critical implementation notes (MSAL authority, token cache, bearer auth)
- `auth/token_store.py` — token storage invariants
- `server.py` — BearerAuthMiddleware implementation

## Steps

1. **Fetch PR details**: `gh pr view $PR_NUMBER --json title,body,files,additions,deletions`
2. **Get the diff**: `gh pr diff $PR_NUMBER`
3. **Identify security surface**: new tools, auth changes, user input handling, file operations
4. **Read relevant context**: token_store.py, server.py as applicable
5. **Review each changed file** against the focus areas above
6. **Document findings** (see format below)
7. **Post review** using `gh pr review $PR_NUMBER --comment --body "..."`

## Finding Format

For each finding:

```markdown
### [SEVERITY] Finding Title
**File**: `path/to/file.py` (lines X-Y)

**Issue**: Clear description of the vulnerability and how it could be exploited.

**Recommendation**: Specific fix — code-level guidance where possible.

**Rationale**: Reference the specific invariant, OWASP category, or security principle violated.
```

**Severity Levels:**
- 🔴 **CRITICAL**: Exploitable vulnerability or invariant violation. Must fix before merge.
- 🟡 **IMPORTANT**: Weakens security posture. Should fix before merge.
- 🔵 **SUGGESTION**: Hardening opportunity. Low urgency.

## Output Structure

Post a review comment with:

```markdown
## Security Review Summary

[One paragraph: what was reviewed, overall security assessment, key concerns.]

---

## Findings

[Findings using the format above, grouped by severity.]

---

## Verdict

[Choose one:]
- ✅ **PASS**: No critical or important findings. Secure to merge.
- ⚠️ **PASS WITH CHANGES**: Important findings exist. Address before merge.
- ❌ **NEEDS REWORK**: Critical findings exist. Must fix before approval.

---

**Review performed by**: Claude Code (automated security review)
**Review focus**: MCP Bearer Auth, Token Storage (Fernet + chmod 600), MSAL OAuth, Graph Input Validation, Secrets/Env, Subprocess Safety, Dependency CVEs
```

## Review Guidelines

**DO report**:
- Any deviation from cowork-mcp auth/token invariants
- Missing bearer auth on new routes
- Token comparison with `==` instead of `hmac.compare_digest()`
- MSAL authority using `common` or `organizations` instead of `consumers`
- Secrets hardcoded or logged
- `shell=True` with user-controlled input
- New packages with known CVEs

**DON'T report**:
- Cosmetic or style issues (linter handles this)
- Performance concerns → performance-reviewer
- Reliability patterns → reliability-reviewer
- Architecture concerns → principal-engineer
- Pre-existing security issues not introduced by this PR
- The `reset_graph_client()` function — it's safe and exists for tests

If the PR is clean, say so. "No findings" is a valid and valuable review.
