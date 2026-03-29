# CI Performance Review

You are performing an automated performance review of a pull request in the cowork-mcp project. This is a **focused per-request performance review** — you are checking whether code introduced by this PR will be slow for individual MCP tool calls. For cloud scaling concerns, see `ci-reliability-review.md`. For security vulnerabilities, see `ci-security-review.md`.

## Your Task

Review the PR at: `${{ github.repository }}/pull/${{ github.event.pull_request.number }}`

**Scope discipline**: Only flag performance regressions introduced by this PR. Pre-existing issues are out of scope.

### Review Focus Areas

#### 1. Backend Async Correctness
- New `async def` handlers calling `time.sleep()` instead of `asyncio.sleep()`?
- Synchronous file I/O (reading/writing large files) in async handlers without `asyncio.to_thread()`?
- CPU-bound work (Fernet decrypt/encrypt on large data) in async handlers without `asyncio.to_thread()`?
- `requests` library used instead of `httpx` async client?
- `subprocess.run()` in async context instead of `asyncio.create_subprocess_exec()`?

#### 2. Graph API Call Efficiency
- New tools making serial `await` Graph calls that could use `asyncio.gather()` for parallelism?
- Graph calls inside loops (N+1 pattern — one round-trip per item)?
- Missing `$select` parameter on Graph calls that fetch all fields when only a few are needed?
- New calls that fetch the full resource when only the ID or a few fields are needed?

#### 3. Pagination Completeness
- New list operations that stop after first page without following `@odata.nextLink`?
- List operations that fetch more pages than needed when a `limit` parameter is provided?
- Empty `@odata.nextLink` check missing (will loop forever if Graph returns a circular link)?

#### 4. Token Refresh Overhead
- `acquire_token_silent()` called on every MCP tool call instead of relying on MSAL cache?
- Token expiry checked manually when MSAL handles it internally via the cache?
- Token refresh triggered unnecessarily when cached token is still valid?

#### 5. HTTPX Client Lifecycle
- `httpx.AsyncClient()` created inside a request handler without reuse (new TCP handshake per call)?
- Graph SDK HTTP client not properly managed through the GraphClient singleton?

## Required Reading

Before reviewing, read:
- `CLAUDE.md` — project rules
- `graph/client.py` — GraphClient singleton and how the HTTP client is managed

## Steps

1. **Fetch PR details**: `gh pr view $PR_NUMBER --json title,body,files,additions,deletions`
2. **Get the diff**: `gh pr diff $PR_NUMBER`
3. **Identify performance-sensitive changes**: new async functions, new Graph calls, new list operations
4. **Review each changed file** against the focus areas above
5. **Estimate impact** — quantify where possible ("blocks event loop for ~100ms" vs "one-time cold path")
6. **Document findings** (see format below)
7. **Post review** using `gh pr review $PR_NUMBER --comment --body "..."`

## Finding Format

For each finding:

```markdown
### [SEVERITY] Location
**File**: `path/to/file.py` (lines X-Y)

**Issue**: Clear description of the performance problem and when it triggers.

**Impact**: Estimated user-visible effect (e.g., "blocks event loop on every mail fetch", "N+1: one extra Graph round-trip per email in the list").

**Recommendation**: Specific fix — code pattern to apply.

**Rationale**: Why this matters for an interactive MCP server.
```

**Severity Levels**:
- 🔴 **CRITICAL**: Will cause visible slowness or timeouts for typical usage. Must fix before merge.
- 🟡 **IMPORTANT**: Will degrade as data grows. Should fix before merge.
- 🔵 **SUGGESTION**: Optimization opportunity with limited immediate impact.

## Output Structure

Post a review comment with:

```markdown
## Performance Review Summary

[One paragraph overall performance assessment]

---

## Findings

[List all findings using the format above]

---

## Verdict

[Choose one:]
- ✅ **PASS**: No critical or important findings. Performant to merge.
- ⚠️ **PASS WITH CHANGES**: Important findings exist. Address before merge.
- ❌ **NEEDS REWORK**: Critical findings exist. Will cause visible degradation.

---

**Review performed by**: Claude Code (automated performance review)
**Review focus**: Async correctness, Graph API efficiency, Pagination completeness, Token refresh overhead, HTTPX client lifecycle
```

## Review Guidelines

**DO report**:
- `time.sleep()` in async handlers
- `requests` library in async context
- Serial `await` Graph calls that could be `asyncio.gather()`
- New list operations without `@odata.nextLink` pagination
- `httpx.AsyncClient()` created per-request
- Graph calls without `$select` when fetching large resource types

**DON'T report**:
- Micro-optimizations with no user-perceptible impact
- Horizontal scaling or multi-instance concerns → reliability-reviewer
- Security issues → security-reviewer
- Architecture concerns → principal-engineer
- Pre-existing performance issues not introduced by this PR

If the PR is clean, say so. "No findings" is a valid and valuable review.
