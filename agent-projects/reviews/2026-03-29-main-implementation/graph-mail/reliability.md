# Reliability Review: graph-mail

**Date**: 2026-03-29 | **Files reviewed**: 2 | **Reviewer**: reliability-reviewer

## Summary

The `graph/mail.py` module is well-structured with consistent ODataError wrapping across all public functions, proper None-checking on Graph API return values, and bounded pagination loops. The code has no module-level mutable state, no resource lifecycle issues, and no concurrency concerns -- all functions are pure async operations on an injected `GraphClient` with no shared state. The test file provides good coverage of the happy path, pagination, and ODataError propagation.

Two important findings relate to pagination safety (unbounded loop when `limit` is not set sensibly relative to mailbox size) and a potential `AttributeError` in the ODataError wrapper when `exc.error` is `None`. One suggestion addresses the lack of logging on error paths.

---

## Findings

### [YELLOW] Pagination loops lack a hard upper bound

**File**: `graph/mail.py` (lines 181-189, 260-268, 463-469)

**Issue**: The pagination loops in `list_emails`, `search_emails`, and `list_mail_folders` terminate only when `response.value` is empty/falsy, the `limit` is reached, or `odata_next_link` is absent. If the caller passes a very large `limit` (or the `list_mail_folders` function which has no `limit` parameter at all), and the Graph API keeps returning next links, the loop will continue fetching pages indefinitely. For `list_mail_folders` there is no cap at all -- a mailbox with hundreds of folders would be fine, but the loop has no defensive maximum.

**Impact**: In normal operation this is low risk since mailboxes are finite. However, a Graph API bug returning a cyclic `nextLink` (has happened historically) would cause an infinite loop that blocks the asyncio event loop, making the server unresponsive to all other requests until the task is cancelled or the process is killed.

**Recommendation**: Add a maximum page count as a safety valve (e.g., 100 pages). Log a warning if the limit is hit.

```python
MAX_PAGES = 100
page_count = 0
while response and response.value:
    page_count += 1
    if page_count > MAX_PAGES:
        logger.warning("Pagination safety limit reached (%d pages)", MAX_PAGES)
        break
    # ... existing logic
```

**Rationale**: A single runaway pagination loop in a `--workers 1` asyncio server blocks the entire event loop. A hard cap prevents this from becoming a server-wide outage.

---

### [YELLOW] `_wrap_odata_error` can raise `AttributeError` when `exc.error` is `None`

**File**: `graph/mail.py` (lines 104-107)

**Issue**: The helper accesses `exc.error.code` and `exc.error.message` with a ternary guard, but if `exc.error` is `None`, the fallback for `msg` is `str(exc)`, which is correct. However, the ODataError class from the msgraph SDK can also have `exc.error` set to an object where `.code` or `.message` is `None`. In that case, `code` would be `None` (not the string `"unknown"`), and the formatted error message would read `"Graph API error None: None"`. This is cosmetic but makes log-based debugging harder.

More critically, if the SDK ever raises an `ODataError` subclass or variant where `.error` is not the expected `MainError` type, attribute access could raise `AttributeError`, which would escape the `except ODataError` block and propagate as an unhandled exception.

**Impact**: Degraded error messages in logs; potential for an unhandled `AttributeError` to propagate instead of the intended `RuntimeError`.

**Recommendation**: Add defensive access:

```python
def _wrap_odata_error(exc: ODataError) -> RuntimeError:
    try:
        code = exc.error.code if exc.error else "unknown"
        msg = exc.error.message if exc.error else str(exc)
    except AttributeError:
        code = "unknown"
        msg = str(exc)
    return RuntimeError(f"Graph API error {code}: {msg}")
```

**Rationale**: This is the single error-wrapping choke point for all Graph API calls in the mail module. If it fails, every caller loses its structured error handling.

---

### [BLUE] No logging on error recovery paths

**File**: `graph/mail.py` (all `except ODataError` blocks)

**Issue**: The module defines a `logger` at line 46 but never uses it. When an `ODataError` is caught and re-raised as `RuntimeError`, there is no log entry. The caller (MCP tool handler in `server.py`) may or may not log the error before converting it to an MCP error response. If it does not, the Graph API error details are lost from the server's systemd journal.

**Impact**: When diagnosing intermittent Graph API failures on a long-running server, the only record would be whatever the MCP framework logs, which may not include the original OData error code and message.

**Recommendation**: Add `logger.warning(...)` or `logger.error(...)` before re-raising in `_wrap_odata_error`:

```python
def _wrap_odata_error(exc: ODataError) -> RuntimeError:
    code = exc.error.code if exc.error else "unknown"
    msg = exc.error.message if exc.error else str(exc)
    logger.error("Graph API error %s: %s", code, msg)
    return RuntimeError(f"Graph API error {code}: {msg}")
```

**Rationale**: For a systemd-managed server, the journal is the primary diagnostic tool. Errors that are only surfaced via MCP responses to the client are invisible to the operator.

---

### [BLUE] OData filter values not sanitized against injection

**File**: `graph/mail.py` (lines 143-150)

**Issue**: The `sender` and `subject` filter parameters are interpolated directly into OData filter strings using f-strings. A `sender` value containing a single quote (e.g., `O'Brien@example.com`) would produce a malformed OData filter, causing a 400 error from the Graph API. A `subject` containing a single quote would similarly break the `contains()` call.

**Impact**: Not a security issue (Graph API is the user's own mailbox), but malformed filters cause opaque 400 errors that are hard to diagnose. Legitimate email addresses with apostrophes would fail silently.

**Recommendation**: Escape single quotes in OData string literals by doubling them:

```python
def _odata_escape(value: str) -> str:
    return value.replace("'", "''")

# Then:
filters.append(f"from/emailAddress/address eq '{_odata_escape(sender)}'")
filters.append(f"contains(subject, '{_odata_escape(subject)}')")
```

**Rationale**: Prevents opaque 400 errors from the Graph API that would be difficult to diagnose in production logs.

---

## Verdict

**PASS WITH CHANGES**

The module is generally solid. The pagination safety valve (first finding) is the most operationally important fix -- a runaway pagination loop would block the entire single-worker asyncio server. The ODataError wrapper hardening and logging additions are straightforward improvements that reduce diagnostic difficulty for a long-running service.

---

**Review performed by**: Claude Code (reliability-reviewer)
**Review focus**: Concurrent async safety, Error recovery, Resource lifecycle, Startup/shutdown correctness, Logging hygiene
