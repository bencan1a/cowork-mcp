# Security Review: graph-calendar
**Date**: 2026-03-29 | **Files reviewed**: 2 (`graph/calendar.py`, `tests/test_calendar.py`) | **Reviewer**: security-reviewer

## Summary

The calendar module is a well-structured Graph API wrapper that delegates all HTTP calls through the SDK's `GraphServiceClient` (via `GraphClient`), properly handles `ODataError`, and implements pagination on `list_events`. All calendar tools are gated behind `BearerAuthMiddleware` via the MCP server. No secrets are logged and no raw `httpx` calls bypass the `GraphClient`.

There are no critical findings. I identified two important issues (missing input validation on attendee emails and unbounded `limit` parameter) and two hardening suggestions.

---

## Findings

### [IMPORTANT] No email validation on attendee addresses
**File**: `graph/calendar.py` (lines 240-249), `server.py` (lines 425, 441-452)

**Issue**: The `create_event` function accepts a `list[str]` for `attendees` and passes each string directly to `EmailAddress.address` without any validation. The `get_free_busy` function similarly accepts a `list[str]` for `schedules` with no validation. While the Graph API will reject obviously malformed addresses, there is no pre-flight check. A malicious or malformed input (e.g., extremely long strings, strings with special characters, or non-email values) is sent directly to the Microsoft Graph API. This is a defense-in-depth concern -- the server should validate inputs before making external API calls.

**Recommendation**: Add a basic email format check (regex or a lightweight validator) before constructing `Attendee` objects and before passing `schedules` to `GetSchedulePostRequestBody`. A simple regex like `^[^@\s]+@[^@\s]+\.[^@\s]+$` with a length cap (e.g., 254 characters per RFC 5321) is sufficient:

```python
import re
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LEN = 254

def _validate_email(addr: str) -> None:
    if len(addr) > MAX_EMAIL_LEN or not _EMAIL_RE.match(addr):
        raise ValueError(f"Invalid email address: {addr!r}")
```

**Rationale**: A03 (Injection) -- all MCP tool inputs are user-controlled and should be validated before use in external API calls. Matches the project invariant: "All MCP tool inputs are user-controlled. Validate types and ranges before passing to Graph API. Email addresses: validate structure before use."

---

### [IMPORTANT] No upper bound on `limit` parameter
**File**: `graph/calendar.py` (lines 110-149, 174-194), `server.py` (lines 329-352, 367-377)

**Issue**: The `limit` parameter on `list_events` and `search_events` accepts any `int` value from the MCP client with no upper bound. A caller could pass `limit=999999`, causing the pagination loop in `list_events` (lines 141-148) to follow potentially thousands of `@odata.nextLink` pages, consuming server memory and time. While Graph API pages are typically small (10-50 items), the cumulative effect of unbounded pagination is a resource exhaustion vector. Additionally, negative values are not rejected.

**Recommendation**: Clamp `limit` to a reasonable maximum (e.g., 500) and enforce a minimum of 1:

```python
limit = max(1, min(limit, 500))
```

Apply this at the entry point in `server.py` tool definitions or at the top of each function in `graph/calendar.py`.

**Rationale**: A03 (Injection) / Input Validation -- project invariant states "Validate types and ranges before passing to Graph API."

---

### [SUGGESTION] `update_event` accepts arbitrary field names via `**fields`
**File**: `graph/calendar.py` (lines 270-288), `server.py` (lines 456-489)

**Issue**: The `update_event` function uses `setattr(event, key, value)` for each key in `**fields`, guarded only by `hasattr(event, key)`. The `server.py` tool wrapper currently restricts the fields to `subject`, `start`, `end`, `location`, `body` -- so the actual exposure is limited by the MCP tool signature. However, if the tool signature is later expanded or if `update_event` is called from another context, `setattr` on an SDK model object with attacker-controlled keys could set unintended properties. The current `server.py` gating makes this low risk today.

**Recommendation**: Add an allowlist of permitted field names inside `update_event`:

```python
ALLOWED_UPDATE_FIELDS = {"subject", "start", "end", "location", "body", "is_online_meeting"}
for key, value in fields.items():
    if key not in ALLOWED_UPDATE_FIELDS:
        logger.warning("Disallowed Event field %r -- skipping", key)
        continue
    setattr(event, key, value)
```

**Rationale**: Defense-in-depth against future changes widening the attack surface.

---

### [SUGGESTION] `search_events` passes user query string directly to `$search`
**File**: `graph/calendar.py` (lines 174-194)

**Issue**: The `query` parameter from the MCP tool is passed directly to the Graph SDK's `search` query parameter, which maps to the OData `$search` operator. Microsoft Graph's `$search` on events uses KQL (Keyword Query Language) syntax. While the Graph SDK handles URL encoding and the server-side Graph API enforces its own parsing, a user could craft KQL expressions to manipulate search behavior (e.g., `subject:secret OR body:confidential`). This is low risk because: (1) the query runs in the context of the authenticated user's own mailbox, so no privilege escalation is possible; (2) the Graph API's KQL parser is restrictive.

**Recommendation**: For defense-in-depth, consider stripping or escaping KQL operators (`AND`, `OR`, `NOT`, `:`, `"`) from the query string if the intent is plain-text search only. Alternatively, document that KQL syntax is supported and intentional.

**Rationale**: A03 (Injection) -- user-controlled strings in query parameters. Low severity because the scope is limited to the user's own data.

---

## Verdict

**PASS WITH CHANGES** -- No critical findings. Two important findings (missing email validation and unbounded `limit`) should be addressed before merge per project invariants requiring input validation on all user-controlled MCP tool inputs. The two suggestions are hardening opportunities with low urgency.

---

**Review performed by**: Claude Code (security-reviewer)
**Review focus**: MCP Bearer Auth, Token Storage (Fernet + chmod 600), MSAL OAuth, Graph Input Validation, Secrets/Env, Subprocess Safety, Dependency CVEs
