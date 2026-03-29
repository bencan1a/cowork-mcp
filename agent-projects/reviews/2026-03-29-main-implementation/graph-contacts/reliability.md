# Reliability Review: graph-contacts

**Date**: 2026-03-29 | **Files reviewed**: `graph/contacts.py`, `tests/test_contacts.py` | **Reviewer**: reliability-reviewer

## Summary

The contacts module is well-structured with consistent ODataError handling via `_wrap_odata_error`, proper None checks on API responses, and a pagination loop that is bounded by the `limit` parameter. No shared mutable state exists -- all functions are stateless and receive the GraphClient as an argument. The main concerns are a potential `AttributeError` in `_contact_to_dict` when contact sub-fields have unexpected shapes, and the pagination loop accumulating beyond the requested limit before truncation.

---

## Findings

### [IMPORTANT] `_contact_to_dict` can raise AttributeError on malformed email entries

**File**: `graph/contacts.py` (line 33)

**Issue**: Line 33 accesses `c.email_addresses[0].address`. If the Graph API returns an `email_addresses` list where the first element has a `None` or missing `.address` attribute, this will raise an `AttributeError` that is not caught anywhere in the call chain. Similarly, `c.business_phones[0]` on line 34 could raise `IndexError` if `business_phones` is truthy but empty (e.g., an object that evaluates as truthy but has length zero, though unlikely with standard lists).

**Impact**: An unhandled `AttributeError` in a tool handler propagates as a raw 500 error to the MCP client instead of a structured error. The server itself stays up, but the user gets no actionable error message, and the failure is not logged with context.

**Recommendation**: Wrap the email/phone extraction with defensive access:

```python
def _contact_to_dict(c: Any) -> dict[str, Any]:
    email = None
    if c.email_addresses:
        first = c.email_addresses[0]
        email = getattr(first, "address", None)

    phone = c.mobile_phone
    if not phone and c.business_phones:
        phone = c.business_phones[0]

    return {
        "id": c.id,
        "display_name": c.display_name,
        "given_name": c.given_name,
        "surname": c.surname,
        "email": email,
        "phone": phone,
        "company": c.company_name,
        "job_title": c.job_title,
    }
```

**Rationale**: Over days of operation processing diverse contacts, encountering a malformed contact record is likely. Defensive extraction prevents a single bad record from failing an entire list operation.

---

### [SUGGESTION] Pagination loop fetches a full page beyond the limit before truncating

**File**: `graph/contacts.py` (lines 74-81)

**Issue**: The `while` loop condition checks `len(contacts) < limit`, but after each page fetch it extends the list with the full page of results. If `limit=50` and 45 contacts are already fetched, the next page could add another 50, bringing the total to 95 before truncation at line 82. This is not a correctness bug (the final slice at line 82 enforces the limit), but it means the server issues an unnecessary Graph API call and temporarily holds more data in memory than needed.

**Impact**: Low. Extra API call costs latency and a small amount of memory. Not a resource leak since it is bounded.

**Recommendation**: No change required, but if optimizing later, pass `top=min(page_size, limit - len(contacts))` as a query parameter on paginated requests to avoid over-fetching.

**Rationale**: Minor inefficiency; the truncation at line 82 prevents any downstream impact.

---

### [SUGGESTION] `update_contact` does not handle email_addresses the same way as `create_contact`

**File**: `graph/contacts.py` (lines 149-167 vs 107-141)

**Issue**: `create_contact` has special handling to convert plain email strings into `EmailAddress` objects (lines 116-126), but `update_contact` uses a generic `setattr` loop without this conversion. If a caller passes `email_addresses=["new@example.com"]` to `update_contact`, the Graph SDK will receive raw strings instead of `EmailAddress` objects, which will likely cause an API error or silent data loss.

**Impact**: Functional bug rather than a reliability/resource issue, but the resulting ODataError or SDK exception would surface as an opaque error to the MCP client. Over time this creates confusing error patterns in logs.

**Recommendation**: Extract the email conversion logic into a shared helper and call it from both `create_contact` and `update_contact`.

**Rationale**: Consistent input handling reduces the surface area for unexpected runtime errors in a long-running service.

---

### [SUGGESTION] No test coverage for ODataError paths

**File**: `tests/test_contacts.py`

**Issue**: No tests simulate an `ODataError` being raised by the Graph SDK. The `_wrap_odata_error` helper and the `except ODataError` blocks in all four public functions are untested.

**Impact**: Regressions in error handling would not be caught until production. For a long-running service, error paths are exercised frequently and must be reliable.

**Recommendation**: Add tests that mock the Graph SDK to raise `ODataError` and verify that `RuntimeError` is raised with the expected message format.

**Rationale**: Error paths are the most critical code in a long-running service -- they determine whether failures are recoverable or opaque.

---

## Verdict

**PASS**: No critical findings. The module has no shared mutable state, no resource lifecycle issues, and proper error wrapping. The important finding around `_contact_to_dict` defensiveness should be addressed before merge to prevent malformed contact data from producing unstructured errors, but it does not threaten server stability.

---

**Review performed by**: Claude Code (reliability-reviewer)
**Review focus**: Concurrent async safety, Error recovery, Resource lifecycle, Startup/shutdown correctness, Logging hygiene
