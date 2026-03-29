# Performance Review: graph/tasks.py

**Date**: 2026-03-29
**Reviewer**: performance-reviewer
**Files Reviewed**: `graph/tasks.py`, `graph/client.py`, `tests/test_tasks.py`
**Focus**: Async correctness, redundant API calls, pagination efficiency, token refresh overhead

---

## Summary

The `graph/tasks.py` module implements four primary operations: `list_tasks()`, `create_task()`, `complete_task()`, and `delete_task()`. All functions are correctly async and free of blocking I/O. However, there is **one critical performance issue**: every task operation calls `_resolve_list_id()` unconditionally, which makes a Graph API call to fetch the default list whenever `list_id` is `None`. For the common case where users work with their default todo list, this adds 100–300ms of latency to every operation unnecessarily.

Pagination in `list_tasks()` is correctly implemented, pagination limits are respected, and token refresh is handled by the MSAL layer without duplicate calls. Async patterns are sound.

---

## Findings

### 🔴 CRITICAL: Redundant `_resolve_list_id()` API Call on Every Operation

**File**: `/home/user/cowork-mcp/graph/tasks.py` (lines 54–70)

**Issue**:
The `_resolve_list_id(gc, list_id)` helper is called unconditionally at the start of all four task operations: `list_tasks()` (line 86), `create_task()` (line 142), `complete_task()` (line 180), and `delete_task()` (line 218). When `list_id` is `None`, the helper fetches the user's todo lists via `gc.client.me.todo.lists.get()` (line 60) to return the ID of the first list.

For users who consistently work with their default todo list (the dominant use case), this means:
- **Every** `list_tasks()` call = 2 Graph requests (1 to resolve list, 1 to fetch tasks)
- **Every** `create_task()` call = 2 Graph requests (1 to resolve list, 1 to create task)
- **Every** `complete_task()` call = 2 Graph requests (1 to resolve list, 1 to update task)
- **Every** `delete_task()` call = 2 Graph requests (1 to resolve list, 1 to delete task)

The first list in the user's account rarely changes, making this a wasteful round-trip on nearly every operation.

**Impact**:
Adds 100–300ms of latency per operation in the default-list case. User creates a task: they wait for two sequential HTTP round-trips instead of one. A power user creating, checking, and completing multiple tasks in succession will experience cumulative slowness.

**Recommendation**:
Implement a **cached default list ID** in the `GraphClient` singleton. Compute it once during server initialization or on first use, then reuse it for the lifetime of the service. Provide a mechanism to invalidate the cache if the user's todo list configuration changes (e.g., if they delete and recreate their default list). Example pattern:

```python
class GraphClient:
    def __init__(self, settings: Settings) -> None:
        # ... existing init ...
        self._default_todo_list_id: str | None = None

    async def get_default_todo_list_id(self) -> str:
        """Return the cached default todo list ID, fetching once if needed."""
        if self._default_todo_list_id is None:
            result = await self._client.me.todo.lists.get()
            if result and result.value:
                self._default_todo_list_id = str(result.value[0].id)
            else:
                raise RuntimeError("No todo lists found")
        return self._default_todo_list_id

# In graph/tasks.py:
async def _resolve_list_id(gc: GraphClient, list_id: str | None) -> str:
    """Return *list_id* if provided, otherwise the cached default list ID."""
    if list_id is not None:
        return list_id
    return await gc.get_default_todo_list_id()
```

This reduces the four-request sequence (resolve + operate × 4) down to just one resolve on first operation, then operate-only for all subsequent calls. For a user creating 5 tasks, this saves 4 round-trips (400–1200ms).

**Rationale**:
Interactive MCP tools benefit from low-latency responses. An extra 100–300ms per call is noticeable to the user and compounds across multiple operations in a session. Caching the default list ID is safe because:
1. Users almost never change their default list
2. If they do, the app can be restarted (or a cache invalidation mechanism added)
3. The fallback is still correct: if the cache misses, the old behavior applies

---

### 🟡 IMPORTANT: No `$select` Projection in `list_tasks()` Graph Query

**File**: `/home/user/cowork-mcp/graph/tasks.py` (lines 94–98)

**Issue**:
The `list_tasks()` function constructs a query with `top` and `filter` parameters but does not include a `$select` projection. This causes Graph API to return all fields of every `TodoTask` object, even if only a subset (e.g., `id`, `title`, `status`) are needed and exposed to the user.

The Graph SDK's `TasksRequestBuilder.TasksRequestBuilderGetQueryParameters` does not expose a `$select` parameter directly in the documented API, but it may be available through the underlying OData layer or custom request configuration.

**Impact**:
Fetches more data than necessary, increasing payload size and response time. For a user with hundreds of tasks, this could add 10–50ms per call and consume extra bandwidth.

**Recommendation**:
Investigate if the Graph SDK's `TasksRequestBuilder` supports `$select` via custom headers or alternative configuration. If supported, add a parameter to limit returned fields:

```python
# Hypothetical approach if TasksRequestBuilder supports $select
query_params = TasksRequestBuilder.TasksRequestBuilderGetQueryParameters(
    top=limit,
    filter=filter_expr,
    # select="id,title,status,dueDatetime,completedDateTime,body,importance,createdDateTime,lastModifiedDateTime"
)
```

If the SDK does not support it, document this as a known limitation and consider filing an issue with Microsoft Graph SDK maintainers.

**Rationale**:
Unnecessary data transfer is wasteful. Even a small reduction in payload (100–200 bytes per task) adds up over time, especially for users with large todo lists.

---

### 🔵 SUGGESTION: Pagination Loop Could Cache Result Count

**File**: `/home/user/cowork-mcp/graph/tasks.py` (lines 113–124)

**Issue**:
The pagination loop in `list_tasks()` checks `len(tasks) < limit` on every iteration. As the list grows, this becomes a more expensive operation (though still O(1) for a Python list). More importantly, the loop could exit early once it has fetched enough items to reach the limit, reducing unnecessary Graph calls.

Current code:
```python
while result is not None and result.odata_next_link and len(tasks) < limit:
    # Fetch next page...
```

This correctly stops fetching once `len(tasks) >= limit`, but the check happens *after* the fetch. If the first page has 100 items and the limit is 50, an extra fetch is avoided—good. However, if the first page has 40 items and the limit is 50, one extra fetch happens to get just 10 more items.

**Impact**:
Negligible for typical use cases (limit defaults to 50, first page is usually 10–50 items). This is a micro-optimization with limited user-visible benefit.

**Recommendation**:
Keep the current logic as-is. The behavior is correct and the performance cost is minimal. If profile data in the future shows this is a bottleneck, consider early-exit logic, but it is not urgent.

**Rationale**:
Premature optimization. The current code is correct and clear.

---

### ✅ PASS: Async Correctness

**Files**: `/home/user/cowork-mcp/graph/tasks.py`

**Analysis**:
All functions are correctly declared `async def`. All Graph API calls use `await`, never blocking calls like `requests.get()` or `time.sleep()`. The msgraph SDK's async methods are properly awaited. Token refresh via `TokenStore.acquire_token_silent()` is synchronous but runs inside Kiota's async HTTP adapter, so it does not block the event loop in practice (MSAL's in-memory operations are fast).

No findings.

---

### ✅ PASS: Pagination Completeness

**Files**: `/home/user/cowork-mcp/graph/tasks.py` (lines 113–124)

**Analysis**:
The `list_tasks()` function correctly handles `@odata.nextLink` pagination:
1. Initializes `tasks = list(result.value)` to capture the first page
2. Enters a `while` loop that checks for `result.odata_next_link`
3. Fetches subsequent pages and appends them to `tasks`
4. Stops when there are no more pages or the limit is reached
5. Returns `tasks[:limit]` to enforce the limit

No silent truncation. No over-fetching. Correct.

---

### ✅ PASS: Error Handling

**Files**: `/home/user/cowork-mcp/graph/tasks.py` (lines 47–51, throughout)

**Analysis**:
All Graph API calls are wrapped in `try`/`except ODataError` blocks. Errors are converted to `RuntimeError` with readable messages via `_wrap_odata_error()`. This prevents raw stack traces from leaking to MCP clients and matches the error surfacing pattern defined in CLAUDE.md.

No findings.

---

## Verdict

⚠️ **PASS WITH CHANGES**

The module is largely performant and correct, but the **critical redundant `_resolve_list_id()` API call** must be addressed before merge. This single issue could add 100–300ms to every task operation in the default-list case, which is the dominant use pattern.

**Required fixes:**
1. Implement caching of the default todo list ID in `GraphClient` to avoid repeated list-fetch calls.

**Recommended improvements:**
1. Investigate `$select` support in the Graph SDK to reduce payload size (lower priority).

Once the caching fix is applied, the code is performant and ready to merge.

---

**Reviewed by**: Claude Code (performance-reviewer)
**Review completed**: 2026-03-29
**Status**: Awaiting caching implementation for `_resolve_list_id()`
