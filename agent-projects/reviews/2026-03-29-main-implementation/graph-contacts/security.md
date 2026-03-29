# Security Review: graph-contacts
**Date**: 2026-03-29 | **Files reviewed**: 2 (`graph/contacts.py`, `server.py` contacts sections) | **Reviewer**: security-reviewer

## Summary

The contacts module is a straightforward Graph API wrapper that delegates all HTTP calls through the SDK's `GraphServiceClient` (via `GraphClient`), properly converts `ODataError` to `RuntimeError`, and implements `@odata.nextLink` pagination. All contacts tools are gated behind `BearerAuthMiddleware` in `server.py`. No secrets are logged and no raw `httpx` calls bypass the `GraphClient`.

There are no critical findings. I identified three important issues: unrestricted `setattr` on SDK model objects in `create_contact` and `update_contact`, no upper bound on the `limit` parameter, and unvalidated `contact_id` input. I also identified two hardening suggestions regarding `$search` query injection and `odata_next_link` URL validation in the pagination loop.

---

## Findings

### [IMPORTANT] `setattr` with user-controlled keys lacks an allowlist
**File**: `graph/contacts.py` (lines 128-131, 153-156)

**Issue**: Both `create_contact` and `update_contact` accept `**fields` (arbitrary keyword arguments) and call `setattr(contact, key, value)` for any key where `hasattr(contact, key)` returns True. The `Contact` SDK model object has many attributes beyond the intended contact fields -- including internal SDK properties like `odata_type`, `additional_data`, `backing_store`, and potentially other properties that control serialization or request behavior. A caller who passes `odata_type="malicious.value"` or `additional_data={"@odata.type": "..."}` could manipulate the PATCH/POST request body in unintended ways.

The `server.py` tool wrappers for `create_contact` (line 588) and `update_contact` (line 600) both use `**fields: Any`, passing all MCP client-provided keyword arguments directly through without any filtering. Unlike the `update_event` tool in `server.py` (lines 456-489) which explicitly enumerates allowed fields before passing them to the graph module, the contacts tools impose no such restriction at any layer.

**Recommendation**: Add an allowlist of permitted field names in `create_contact` and `update_contact`:

```python
_ALLOWED_CONTACT_FIELDS = {
    "given_name", "surname", "display_name", "middle_name",
    "mobile_phone", "business_phones", "home_phones",
    "company_name", "job_title", "department",
    "personal_notes", "title", "nickname",
}

for key, value in fields.items():
    if key not in _ALLOWED_CONTACT_FIELDS:
        logger.warning("Disallowed Contact field %r -- skipping", key)
        continue
    setattr(contact, key, value)
```

Alternatively, restrict the fields at the `server.py` tool signature level (as `update_event` does) so that only known fields can be passed through.

**Rationale**: A03 (Injection) -- all MCP tool inputs are user-controlled. The `hasattr` guard is insufficient because SDK model objects expose internal properties that should not be user-settable. This is the same pattern flagged as a suggestion in the calendar review, but the contacts module has higher exposure because `server.py` does not filter fields before forwarding them.

---

### [IMPORTANT] No upper bound on `limit` parameter
**File**: `graph/contacts.py` (lines 51-82), `server.py` (lines 553, 560)

**Issue**: The `limit` parameter on `list_contacts` accepts any `int` value from the MCP client with no upper bound or lower bound validation. A caller could pass `limit=999999`, causing the pagination loop (lines 74-80) to follow potentially thousands of `@odata.nextLink` pages, consuming server memory and time. Negative values or zero are not rejected either -- `limit=0` would return an empty list harmlessly, but `limit=-1` would cause the `contacts[:limit]` slice to silently truncate results.

**Recommendation**: Clamp `limit` to a reasonable range at the entry point:

```python
limit = max(1, min(limit, 500))
```

Apply this either in the `server.py` tool definition or at the top of `list_contacts` in `graph/contacts.py`.

**Rationale**: A03 (Injection) / Input Validation -- project invariant states "All MCP tool inputs are user-controlled. Validate types and ranges before passing to Graph API."

---

### [IMPORTANT] No validation on `contact_id` parameter
**File**: `graph/contacts.py` (lines 90-99, 149-167)

**Issue**: The `contact_id` parameter in `get_contact` and `update_contact` is passed directly to `by_contact_id(contact_id)` without any format validation. Graph contact IDs are opaque strings, but accepting arbitrary user input without a basic sanity check (non-empty, reasonable length, no path-separator characters) means malformed or malicious values are forwarded to the Graph API. While the Graph SDK handles URL construction and the API server will reject invalid IDs, defense-in-depth requires pre-flight validation on all user-controlled identifiers.

**Recommendation**: Add a basic ID validation helper:

```python
def _validate_id(value: str, name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must not be empty")
    if len(value) > 1024:
        raise ValueError(f"{name} exceeds maximum length")
```

**Rationale**: A03 (Injection) -- project invariant: "All MCP tool inputs are user-controlled. Validate types and ranges before passing to Graph API."

---

### [SUGGESTION] `$search` query passed without sanitization
**File**: `graph/contacts.py` (lines 57-59)

**Issue**: The `search` parameter from the MCP tool is passed directly to the Graph SDK's `search` query parameter, which maps to the OData `$search` operator. Microsoft Graph's `$search` on contacts uses a simple keyword matching approach (not full KQL), but user-controlled strings are still forwarded without any sanitization. This is low risk because: (1) the query runs in the context of the authenticated user's own contacts, so no privilege escalation is possible; (2) the Graph API's search parser on the `/me/contacts` endpoint is restrictive and does not support complex query operators.

**Recommendation**: For defense-in-depth, consider imposing a maximum length on the search string (e.g., 256 characters) to prevent oversized query parameters. Document that the search parameter is passed through to Graph's `$search` if KQL-like syntax is intentionally supported.

**Rationale**: A03 (Injection) -- user-controlled strings in query parameters. Low severity because the scope is limited to the user's own data and the contacts `$search` endpoint has limited query syntax.

---

### [SUGGESTION] Pagination `with_url` follows server-provided URL without validation
**File**: `graph/contacts.py` (lines 76)

**Issue**: The pagination loop calls `gc.client.me.contacts.with_url(result.odata_next_link).get()`, where `odata_next_link` is a URL returned by the Microsoft Graph API. While Microsoft Graph should always return legitimate `graph.microsoft.com` URLs, there is no validation that the `odata_next_link` actually points to the expected host. If the Graph API response were tampered with (e.g., via a compromised intermediate or a future API behavior change), the client would follow an arbitrary URL with the user's access token in the Authorization header.

This is the same pattern used across all graph modules (mail, calendar, tasks) and is a standard SDK usage pattern. The risk is very low because: (1) the Graph SDK constructs these requests through its own request adapter; (2) TLS protects the response in transit.

**Recommendation**: For defense-in-depth, validate that `odata_next_link` starts with `https://graph.microsoft.com/` before following it:

```python
if result.odata_next_link and not result.odata_next_link.startswith(
    "https://graph.microsoft.com/"
):
    logger.warning("Unexpected nextLink domain -- stopping pagination")
    break
```

**Rationale**: A10 (SSRF) -- following a server-provided URL without scheme/host validation. Very low likelihood given the trusted source, but worth hardening.

---

## Verdict

**PASS WITH CHANGES** -- No critical findings. Three important findings should be addressed before merge: (1) the unrestricted `setattr` pattern in `create_contact`/`update_contact` combined with the unfiltered `**fields` passthrough from `server.py` creates a wider attack surface than the equivalent calendar code; (2) unbounded `limit` enables resource exhaustion; (3) `contact_id` lacks basic input validation. The two suggestions are low-urgency hardening opportunities.

---

**Review performed by**: Claude Code (security-reviewer)
**Review focus**: MCP Bearer Auth, Token Storage (Fernet + chmod 600), MSAL OAuth, Graph Input Validation, Secrets/Env, Subprocess Safety, Dependency CVEs
