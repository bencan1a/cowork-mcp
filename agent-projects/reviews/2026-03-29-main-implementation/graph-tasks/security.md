# Security Review: graph-tasks
**Date**: 2026-03-29 | **Files**: 2 | **Reviewer**: security-reviewer

## Summary

Reviewed `graph/tasks.py` (228 lines) and the corresponding tool wrappers in `server.py` (lines 620-698). The module covers list, create, complete, and delete operations against Microsoft To Do via the Graph SDK. All Graph calls correctly go through the `GraphClient` wrapper. OData errors are caught and wrapped into `RuntimeError` with code and message — no raw exception details leak to callers. The `$filter` expression in `list_tasks` is constructed entirely from hardcoded string literals (never user-supplied values), so there is no OData filter injection in that path.

The primary security concerns are: no format validation on `list_id` and `task_id` before they are embedded in Graph API URL path segments; no upper bound on the `limit` parameter enabling a resource exhaustion loop; and no ISO 8601 format validation on `due_date` before it is passed to the Graph API.

No critical invariant violations were found. Bearer auth is enforced by `BearerAuthMiddleware` upstream of all tool calls. No secrets or tokens are logged. All Graph operations go through `GraphClient`.

---

## Findings

### [IMPORTANT] No Validation on `list_id` and `task_id` Path Parameters
**File**: `graph/tasks.py` (lines 54-70, 101, 116, 160, 187-199, 221-226)

**Issue**: The `list_id` and `task_id` parameters are user-controlled strings that are embedded directly into Graph API URL path segments via `by_todo_task_list_id(resolved_list_id)` and `by_todo_task_id(task_id)` without any format validation. The Graph SDK constructs a URL of the form:

```
/me/todo/lists/{list_id}/tasks/{task_id}
```

While the Graph SDK performs URL encoding (preventing raw path traversal with `/` or `..`), there is no local check that these values conform to the expected opaque ID format. An attacker with valid bearer credentials could supply crafted IDs — for example, extremely long strings or strings with unusual Unicode — to probe Graph API error behaviour or trigger unexpected SDK paths. More concretely, if the SDK ever interpolates these into an OData URL without encoding (a latent risk as SDK versions change), unvalidated IDs become an injection vector.

The project invariant states: "All MCP tool inputs are user-controlled. Validate types and ranges before passing to Graph API."

**Recommendation**: Add a lightweight validation helper that checks `list_id` and `task_id` against the character set that Graph API IDs are known to use (alphanumeric, hyphens, underscores, and base64url characters):

```python
import re

_GRAPH_ID_RE = re.compile(r'^[A-Za-z0-9_\-=]{1,512}$')

def _validate_graph_id(value: str, label: str) -> None:
    if not _GRAPH_ID_RE.match(value):
        raise RuntimeError(f"Invalid {label} format")
```

Call this in `_resolve_list_id` (after confirming `list_id is not None`) and at the top of `complete_task` and `delete_task` for `task_id`.

**Rationale**: OWASP A03 (Injection). Project invariant: validate all user-controlled inputs before passing to Graph API.

---

### [IMPORTANT] No Upper Bound on `limit` Parameter
**File**: `graph/tasks.py` (lines 78-125)

**Issue**: `list_tasks` accepts a `limit` parameter (default 50) with no maximum enforced. The pagination loop at line 113 continues fetching `@odata.nextLink` pages until `len(tasks) >= limit`. A caller passing `limit=1000000` causes the server to issue thousands of sequential Graph API requests before returning, exhausting server memory (accumulating all tasks in memory) and triggering Graph API throttling (HTTP 429), which in turn stalls the server process for the throttle-back-off duration.

The `server.py` tool wrapper at line 623-637 passes `limit` through to the module function without any bounds check.

**Recommendation**: Enforce a hard cap at the top of `list_tasks`:

```python
MAX_LIMIT = 500
limit = max(1, min(limit, MAX_LIMIT))
```

**Rationale**: OWASP A04 (Insecure Design) — resource exhaustion via unbounded pagination. Mirrors the same finding in `graph/mail.py`.

---

### [IMPORTANT] No Format Validation on `due_date` Before Graph API Submission
**File**: `graph/tasks.py` (lines 147-151)

**Issue**: The `due_date` parameter is assigned directly to `due_dt.date_time` without validating that it is a well-formed ISO 8601 datetime string:

```python
due_dt.date_time = due_date
```

If a user supplies a malformed value (e.g., an empty string, a string with newline characters, or an arbitrarily long string), this is sent to the Graph API as-is. The Graph API will return an OData error, which is caught and re-raised as `RuntimeError` — so there is no silent failure. However, the project invariant requires local validation before reaching the API, and malformed values make unnecessary API round-trips. A string containing a newline could also cause log injection if `due_date` is ever included in a log statement.

**Recommendation**: Parse and validate the date string locally before use:

```python
from datetime import datetime

if due_date:
    try:
        datetime.fromisoformat(due_date.replace("Z", "+00:00"))
    except ValueError:
        raise RuntimeError(f"Invalid due_date format; expected ISO 8601: {due_date!r}")
    due_dt = DateTimeTimeZone()
    due_dt.date_time = due_date
    due_dt.time_zone = "UTC"
    task.due_date_time = due_dt
```

**Rationale**: OWASP A03 (Injection — log injection via newline characters). Project invariant: validate types and ranges before passing to Graph API.

---

### [SUGGESTION] `title` and `notes` Have No Length Bounds
**File**: `graph/tasks.py` (lines 144-157)

**Issue**: `title` and `notes` are assigned directly to Graph SDK objects with no length validation. While the Graph API enforces its own limits (task titles are capped at 255 characters per the Graph API spec; notes at 100,000 characters for the body), there is no local rejection of oversized inputs. Extremely large `notes` values (e.g., several megabytes) will be accepted, serialised, and transmitted before the API rejects them, wasting bandwidth and potentially causing memory pressure for large payloads.

**Recommendation**: Apply lightweight local caps consistent with Graph API limits:

```python
MAX_TITLE_LEN = 255
MAX_NOTES_LEN = 100_000

if len(title) > MAX_TITLE_LEN:
    raise RuntimeError(f"title exceeds maximum length of {MAX_TITLE_LEN} characters")
if notes and len(notes) > MAX_NOTES_LEN:
    raise RuntimeError(f"notes exceeds maximum length of {MAX_NOTES_LEN} characters")
```

**Rationale**: Defense in depth — input validation per project invariants. Prevents unnecessary large API payloads.

---

### [SUGGESTION] `_resolve_list_id` Does Not Paginate the Lists Endpoint
**File**: `graph/tasks.py` (lines 54-70)

**Issue**: `_resolve_list_id` calls `gc.client.me.todo.lists.get()` and returns the ID of the first item in `result.value` without handling `@odata.nextLink`. For a user with more todo lists than the Graph API default page size (currently 100), the "first" list in the returned page may not be a stable or predictable choice. This is primarily a correctness issue, but if pagination were unexpectedly truncated, an attacker with many lists could influence which list is selected as the default.

This is low severity given the single-user architecture, but it is inconsistent with the project invariant that all list operations must handle `@odata.nextLink` pagination transparently.

**Recommendation**: For the default-list resolution use case, simply select the first result from the first page (the current behaviour is acceptable for "get the default list"). Document this explicitly with a comment explaining that pagination is intentionally skipped here because the first result from the Graph API is the user's default list.

**Rationale**: Project invariant — `@odata.nextLink` pagination must be handled. If intentional, document the exception clearly.

---

### [SUGGESTION] Error Message in `complete_task` Includes User-Supplied `task_id`
**File**: `graph/tasks.py` (line 205)

**Issue**: The fallback error after a 204 PATCH includes the raw user-supplied `task_id` in the error message:

```python
raise RuntimeError(f"Task {task_id!r} not found after completion")
```

With the `!r` repr the value is safely quoted, so there is no log injection risk for typical inputs. However, if `task_id` validation (see first finding) is not added, an extremely long or Unicode-heavy ID will propagate into the error string and potentially into logs. Once `task_id` is validated at entry, this becomes a non-issue.

**Recommendation**: Resolve by implementing the `task_id` validation from the first finding. No independent change needed.

**Rationale**: OWASP A09 (Security Logging) — defence in depth against log injection from unvalidated user input.

---

## Verdict

**PASS WITH CHANGES** — No critical invariant violations were found. All Graph calls route through `GraphClient`, `BearerAuthMiddleware` protects all tool entry points, OData errors are properly wrapped and never expose raw stack traces, and no secrets or tokens appear in log statements. The `$filter` expression is safely constructed from hardcoded literals only. The three important findings — unvalidated path segment IDs, unbounded `limit`, and unvalidated `due_date` — should be addressed before merge. They represent violations of the project's own input validation invariants and create unnecessary API exposure surface and resource exhaustion risk. All three fixes are small and straightforward.

---

**Review performed by**: Claude Code (security-reviewer)
**Review focus**: MCP Bearer Auth, Token Storage (Fernet + chmod 600), MSAL OAuth, Graph Input Validation, Secrets/Env, Subprocess Safety, Dependency CVEs
