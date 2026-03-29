# Reliability Review: graph-calendar

**Date**: 2026-03-29 | **Files reviewed**: 2 (`graph/calendar.py`, `tests/test_calendar.py`) | **Reviewer**: reliability-reviewer

## Summary

The calendar module is well-structured from a reliability standpoint. All Graph API calls are wrapped in `try/except ODataError` with conversion to `RuntimeError` via a dedicated `_wrap_odata_error` helper. None/null responses are handled consistently throughout. The pagination loop in `list_events` has a proper termination bound. No shared mutable state, no resource lifecycle issues, no module-level growing collections. Two minor findings below.

---

## Findings

### [IMPORTANT] Pagination loop can accumulate unbounded results before truncation

**File**: `graph/calendar.py` (lines 141-148)

**Issue**: The pagination `while` loop checks `len(events) < limit` to decide whether to fetch the next page, but each page's results are appended in full via `events.extend(result.value)`. If a page returns a large batch, the `events` list can grow well past `limit` before the loop condition is re-evaluated. The final `events[:limit]` slice on line 149 truncates correctly, so the caller always gets the right count, but the in-memory list can temporarily hold significantly more items than requested.

**Impact**: For a typical calendar use case with default `limit=50` and Graph page sizes of 10-50, this is unlikely to cause problems. However, if `limit` is set very low (e.g., 5) but each page returns 50 events, the loop will still fetch one extra page of 50 items before stopping. This is a minor memory concern, not a crash risk.

**Recommendation**: No code change strictly required. If you want to tighten this, break out of the loop immediately after extend if `len(events) >= limit`.

**Rationale**: Low practical risk for a single-user calendar tool, but worth noting for completeness.

---

### [SUGGESTION] No logging on error recovery paths

**File**: `graph/calendar.py` (lines 71-75, and all `except ODataError` blocks)

**Issue**: When an `ODataError` is caught and converted to a `RuntimeError`, there is no `logger.error()` or `logger.warning()` call before re-raising. The `_wrap_odata_error` helper silently converts the exception. If the caller (MCP tool handler in `server.py`) also does not log the error, the Graph API failure details may not appear in the systemd journal.

**Impact**: Makes it harder to diagnose intermittent Graph API failures (rate limiting, transient auth issues, permission errors) in a long-running server. The error message is preserved in the `RuntimeError`, but only if something upstream logs it.

**Recommendation**: Add a `logger.warning` or `logger.error` call inside `_wrap_odata_error`:

```python
def _wrap_odata_error(exc: ODataError) -> RuntimeError:
    code = exc.error.code if exc.error else "unknown"
    msg = exc.error.message if exc.error else str(exc)
    logger.error("Graph API error %s: %s", code, msg)
    return RuntimeError(f"Graph API error {code}: {msg}")
```

**Rationale**: For a continuously-running systemd service, being able to `journalctl -u outlook-mcp | grep "Graph API error"` is essential for diagnosing problems without attaching a debugger.

---

### [SUGGESTION] No rate-limit (HTTP 429) detection or retry-after surfacing

**File**: `graph/calendar.py` (all `except ODataError` blocks)

**Issue**: Graph API 429 (Too Many Requests) responses are treated identically to all other errors. The ODataError does not distinguish rate limiting from permanent failures, so the caller cannot implement retry logic based on the error type.

**Impact**: Under sustained load, rate-limited requests will surface as generic errors. For a single-user personal tool, hitting Graph rate limits is unlikely but possible during bulk operations (e.g., creating many events).

**Recommendation**: If the ODataError exposes the HTTP status code (check `exc.response_status_code`), detect 429 and include "rate limited" and the Retry-After value in the error message. This is a hardening opportunity, not a blocker.

**Rationale**: Low probability for single-user deployment but good practice for a long-running service.

---

## What looks good

- **ODataError wrapping**: Every async Graph call has a consistent `try/except ODataError` pattern with conversion to `RuntimeError`. This prevents raw SDK exceptions from propagating to MCP clients.
- **None handling**: All response paths check for `None` results and `None` `.value` properties before iteration. `_event_to_dict` safely handles `None` sub-objects for start, end, location, body, organizer.
- **Pagination termination**: The `while` loop in `list_events` has a clear bound (`len(events) < limit` and `result.odata_next_link` being truthy). No risk of infinite pagination.
- **No shared mutable state**: All functions are pure async functions taking a `GraphClient` parameter. No module-level mutable state, no global variables modified at runtime. No concurrency concerns.
- **No resource leaks**: No `httpx.AsyncClient` created, no file handles opened, no async generators. The Graph SDK client lifecycle is managed elsewhere.
- **Test coverage**: Tests cover the main paths including None responses, empty responses, limit enforcement, PATCH-returning-None refetch, and RSVP actions.

---

## Verdict

**PASS**: No critical or important findings that would cause operational issues. The two suggestions (adding logging to the error wrapper and rate-limit detection) are hardening opportunities that would improve diagnosability but are not required for reliable operation.

---

**Review performed by**: Claude Code (reliability-reviewer)
**Review focus**: Concurrent async safety, Error recovery, Resource lifecycle, Startup/shutdown correctness, Logging hygiene
