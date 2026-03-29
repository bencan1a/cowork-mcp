# CI Reliability Review

You are performing an automated reliability review of a pull request in the cowork-mcp project. This is a **focused long-running server health review** — you are checking whether code introduced by this PR will cause the server to crash, leak resources, or degrade silently over days or weeks of continuous operation as a single-instance systemd service. For per-request speed, see `ci-performance-review.md`. For security vulnerabilities, see `ci-security-review.md`.

## Your Task

Review the PR at: `${{ github.repository }}/pull/${{ github.event.pull_request.number }}`

**Scope discipline**: Only flag reliability regressions introduced by this PR. Pre-existing issues are out of scope.

### Production Context

- **Deployment**: Single Ubuntu server, single uvicorn process (`--workers 1`), managed by systemd
- **Concurrency**: Multiple MCP tool calls can be in-flight simultaneously via asyncio (not threads)
- **The key failure mode**: code that works in testing silently crashes or leaks resources over time in production

### Review Focus Areas

#### 1. Concurrent Request Safety
- New module-level mutable state written by async handlers without `asyncio.Lock`?
- `TOKEN_CACHE_PATH` written concurrently by multiple requests without serialization?
- GraphClient singleton initialization that could race on first concurrent request?
- `global` variables used as request-scoped state (they're process-scoped — shared across requests)?

*Note*: Use `asyncio.Lock()`, not `threading.Lock()`, for shared state in async code.

#### 2. Error Recovery Patterns
- New `except Exception: pass` — silently swallowing errors?
- Graph API errors (`ODataError`, `APIError`) not caught and converted to structured MCP tool errors?
- MSAL `acquire_token_silent()` failures not caught — will they propagate as unhandled 500 errors?
- Graph API 429 (rate limit) not detected — will it crash or silently fail instead of surfacing a retryable error?
- Bare `raise` in async context without logging the exception first?

#### 3. Resource Lifecycle
- `httpx.AsyncClient()` created without `async with` or explicit `.aclose()` (connection pool leak)?
- File handles opened without `with` statements (file descriptor leak)?
- Module-level growing collections (e.g., list at module scope, appended on every request)?
- `asyncio.Task` created but never awaited or stored (fire-and-forget = can't be cleaned up)?

#### 4. Startup/Shutdown Correctness
- Configuration validated lazily (first request fails) instead of at startup?
- `TOKEN_CACHE_PATH` not checked for readability/writeability at startup?
- Shutdown hooks that don't await pending token cache writes?
- Startup code that catches all exceptions and logs "warning: failed to load X" instead of stopping?

#### 5. Logging Hygiene
- New `logging.debug(f"Response: {large_response}")` that could fill disk on verbose logging?
- New `print()` statements (not captured by systemd journal)?
- Auth events (token refresh, auth failure) not logged at WARNING/ERROR?
- `TOKEN_ENCRYPTION_KEY`, `MCP_API_KEY`, or `AZURE_CLIENT_SECRET` appearing in any log statement?

## Required Reading

Before reviewing, read:
- `CLAUDE.md` — project rules and critical implementation notes
- `auth/token_store.py` — token storage and file locking patterns
- `graph/client.py` — GraphClient singleton lifecycle

## Steps

1. **Fetch PR details**: `gh pr view $PR_NUMBER --json title,body,files,additions,deletions`
2. **Get the diff**: `gh pr diff $PR_NUMBER`
3. **Identify reliability surface**: shared state, error handling paths, resource usage, startup/shutdown
4. **Review each changed file** against the focus areas above
5. **Document findings** (see format below)
6. **Post review** using `gh pr review $PR_NUMBER --comment --body "..."`

## Finding Format

For each finding:

```markdown
### [SEVERITY] Finding Title
**File**: `path/to/file.py` (lines X-Y)

**Issue**: Description of the reliability problem and how it manifests over time.

**Impact**: How this affects a long-running server (e.g., "file descriptor leak — exhausts FDs after ~1000 requests").

**Recommendation**: Specific fix — code pattern to apply.

**Rationale**: Why this matters for a continuously-running systemd service.
```

**Severity Levels:**
- 🔴 **CRITICAL**: Will cause server crash, data corruption, or resource exhaustion. Must fix before merge.
- 🟡 **IMPORTANT**: Will degrade reliability over time. Should fix before merge.
- 🔵 **SUGGESTION**: Hardening opportunity with low immediate risk.

## Output Structure

Post a review comment with:

```markdown
## Reliability Review Summary

[One paragraph: what was reviewed, overall reliability assessment, key concerns.]

---

## Findings

[Findings using the format above, grouped by severity.]

---

## Verdict

[Choose one:]
- ✅ **PASS**: No critical or important findings. Reliable to merge.
- ⚠️ **PASS WITH CHANGES**: Important findings exist. Address before merge.
- ❌ **NEEDS REWORK**: Critical findings exist. Will cause operational issues.

---

**Review performed by**: Claude Code (automated reliability review)
**Review focus**: Concurrent async safety, Error recovery, Resource lifecycle, Startup/shutdown correctness, Logging hygiene
```

## Review Guidelines

**DO report**:
- Shared mutable state modified without `asyncio.Lock`
- `httpx.AsyncClient` not properly closed
- `except Exception: pass` (silent swallowing)
- Graph/MSAL errors not converted to structured MCP tool errors
- `print()` in production code
- Module-level growing collections
- Unbounded log strings at INFO or above
- Startup code that doesn't fail fast on bad config

**DON'T report**:
- The existing GraphClient singleton pattern — it's intentional
- Per-request speed concerns → performance-reviewer
- Security vulnerabilities → security-reviewer
- Pre-existing reliability issues not introduced by this PR
- The in-process nature of this server — it's a single-user personal tool, not a distributed system

If the PR is clean, say so. "No findings" is a valid and valuable review.
