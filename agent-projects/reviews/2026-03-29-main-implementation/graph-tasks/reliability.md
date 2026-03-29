# Reliability Review: graph-tasks

**Date**: 2026-03-29
**Files**: `graph/tasks.py`, `graph/client.py` (singleton/token context)
**Reviewer**: reliability-reviewer

## Summary

`graph/tasks.py` implements four CRUD operations for Microsoft To Do tasks via the Graph SDK. The module is stateless -- it holds no module-level mutable state and delegates all shared-state concerns (token cache, HTTP client lifecycle) to `GraphClient` and `TokenStore`. Error handling is consistently structured: every Graph API call is wrapped in `try/except ODataError` and converted to `RuntimeError` via `_wrap_odata_error`, and the caller in `server.py` catches `RuntimeError` and re-raises as `ValueError` for MCP error surfacing. The pagination loop in `list_tasks` is bounded by the `limit` parameter. The `complete_task` re-fetch fallback for HTTP 204 responses is correctly implemented with its own error handling.

Overall this is a well-structured module with no critical reliability issues. There are two notable concerns: the pagination loop lacks a safety bound independent of the `limit` parameter, and there is no rate-limit (HTTP 429) detection.

---

## Findings

### [SUGGESTION] Pagination loop has no iteration cap independent of `limit`
**File**: `graph/tasks.py` (lines 113-123)

**Issue**: The `while` loop at line 113 continues as long as `result.odata_next_link` is present and `len(tasks) < limit`. If the Graph API returns pages where each page contains zero items but still includes a `nextLink` (a documented edge case in some Graph API endpoints), this loop would spin indefinitely making network requests without ever incrementing `len(tasks)`.

**Impact**: Under a pathological Graph API response, a single `list_tasks` call could block the asyncio event loop indefinitely (in terms of network I/O iterations), preventing other MCP tool calls from being serviced.

**Recommendation**: Add a maximum iteration guard:

```python
MAX_PAGES = 20  # safety cap

pages_fetched = 0
while result is not None and result.odata_next_link and len(tasks) < limit:
    if pages_fetched >= MAX_PAGES:
        logger.warning("Pagination safety cap reached (%d pages) for list %s", MAX_PAGES, resolved_list_id)
        break
    pages_fetched += 1
    # ... existing fetch logic ...
```

**Rationale**: A long-running server should never have a loop whose termination depends entirely on external API behavior. A safety cap prevents a degraded Graph API from causing an effectively infinite loop.

---

### [SUGGESTION] No HTTP 429 (rate limit) detection or retry-after surfacing
**File**: `graph/tasks.py` (lines 100-105, 159-164, 185-189, 221-226)

**Issue**: All Graph API calls catch `ODataError` and convert it to a generic `RuntimeError`. Graph API 429 (Too Many Requests) responses include a `Retry-After` header that is not detected or surfaced. The error message will be a generic "Graph API error" with no indication to the caller that the request is retryable or how long to wait.

**Impact**: Under sustained load, the MCP client (Claude) has no signal that it should back off and retry. It may repeatedly invoke the tool, worsening the rate limit situation.

**Recommendation**: Detect 429 status in `_wrap_odata_error` and include retry guidance in the error message:

```python
def _wrap_odata_error(exc: ODataError) -> RuntimeError:
    code = exc.error.code if exc.error else "unknown"
    msg = exc.error.message if exc.error else str(exc)
    if exc.response_status_code == 429:
        return RuntimeError(f"Graph API rate limited (429). Retry after a short delay. Details: {msg}")
    return RuntimeError(f"Graph API error {code}: {msg}")
```

**Rationale**: Rate limiting is the most common transient failure mode for Graph API in a long-running server. Surfacing it distinctly allows the MCP client to make better retry decisions.

---

### [SUGGESTION] `_resolve_list_id` makes a Graph API call on every operation when `list_id` is None
**File**: `graph/tasks.py` (lines 54-70)

**Issue**: When `list_id` is not provided, `_resolve_list_id` fetches all todo lists from Graph to find the default list ID. This happens on every single task operation (list, create, complete, delete). The default list ID is stable for a given user and does not change between requests.

**Impact**: For a server handling multiple task operations over days, this doubles the Graph API call count unnecessarily. It also doubles the exposure to transient Graph API failures and rate limiting for every task tool call.

**Recommendation**: Cache the resolved default list ID in-memory with a TTL (e.g., 1 hour):

```python
_default_list_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 3600  # seconds

async def _resolve_list_id(gc: GraphClient, list_id: str | None) -> str:
    if list_id is not None:
        return list_id

    import time
    cache_key = "default"
    if cache_key in _default_list_cache:
        cached_id, cached_at = _default_list_cache[cache_key]
        if time.monotonic() - cached_at < _CACHE_TTL:
            return cached_id

    # ... existing fetch logic ...
    _default_list_cache[cache_key] = (str(first.id), time.monotonic())
    return str(first.id)
```

Note: this introduces module-level mutable state, but since uvicorn runs a single-process event loop, concurrent writes to this dict are safe (dict assignment is atomic in CPython and there is no read-modify-write race -- each coroutine writes the same value independently).

**Rationale**: Reducing unnecessary API calls improves reliability by lowering the surface area for transient failures and rate limiting.

---

### [SUGGESTION] `_task_to_dict` does not guard against unexpected None attributes
**File**: `graph/tasks.py` (lines 28-44)

**Issue**: The helper accesses `task.id` and `task.title` without None checks. While the Graph SDK should always populate these for valid tasks, a partially constructed or corrupted SDK object could cause an `AttributeError` that would propagate as an unhandled exception through the MCP tool handler.

**Impact**: Low probability, but if it occurs, the raw `AttributeError` would surface as an unstructured 500 error rather than a meaningful MCP tool error.

**Recommendation**: This is a minor hardening opportunity. The current code is acceptable given that `id` and `title` are required fields in the Graph API schema. No immediate action needed, but if defensive coding is desired:

```python
"id": task.id if task.id else "unknown",
"title": task.title or "(untitled)",
```

**Rationale**: Defense in depth against unexpected SDK behavior in a long-running server.

---

## Verdict

- **PASS**: No critical or important findings. The module is well-structured with consistent error handling, bounded pagination, and no shared mutable state. The suggestions above are hardening opportunities that would improve resilience under edge conditions but are not required for safe operation.

---

**Review performed by**: Claude Code (reliability-reviewer)
**Review focus**: Concurrent async safety, Error recovery, Resource lifecycle, Startup/shutdown correctness, Logging hygiene
