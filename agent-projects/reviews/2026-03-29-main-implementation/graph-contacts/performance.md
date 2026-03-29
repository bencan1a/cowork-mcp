# Performance Review: graph-contacts

**Date**: 2026-03-29 | **Files reviewed**: 2 (`graph/contacts.py`, `tests/test_contacts.py`) | **Reviewer**: performance-reviewer

## Summary

The contacts module is well-structured with correct async usage throughout. All Graph API calls use `await` properly, pagination is implemented in `list_contacts`, and the msgraph-sdk handles HTTP client lifecycle internally. Two optimization opportunities exist: missing `$select` projections on list and get operations, and missing `$select` on the pagination follow-up requests. Neither is critical for the contacts resource type (which has fewer fields than messages), but both are worth addressing.

---

## Findings

### [SUGGESTION] Missing `$select` projection on `list_contacts`

**File**: `graph/contacts.py` (lines 57-64)

**Issue**: The `list_contacts` query does not specify a `$select` parameter, so Graph API returns all fields on every Contact object. The `_contact_to_dict` helper only uses 8 fields (`id`, `display_name`, `given_name`, `surname`, `email_addresses`, `mobile_phone`, `business_phones`, `company_name`, `job_title`), but the Contact resource has roughly 30+ fields including `photo`, `extensions`, `personal_notes`, and multi-value collections.

**Impact**: Modest increase in response payload size per call. Contact objects are smaller than Message objects, so the overhead is limited -- likely 20-50% extra data transferred per contact. For a typical 50-contact list, this adds a small amount of unnecessary network transfer and deserialization time.

**Recommendation**: Add a `select` parameter to the query configuration:

```python
query_params = ContactsRequestBuilder.ContactsRequestBuilderGetQueryParameters(
    top=limit,
    search=search,
    select=[
        "id", "displayName", "givenName", "surname",
        "emailAddresses", "mobilePhone", "businessPhones",
        "companyName", "jobTitle",
    ],
)
```

**Rationale**: Reduces payload size and Graph API response time. Standard optimization for any list endpoint.

---

### [SUGGESTION] Missing `$select` projection on `get_contact`

**File**: `graph/contacts.py` (lines 90-99)

**Issue**: `get_contact` fetches a single contact by ID without specifying `$select`. Same set of unused fields are transferred as in the list case.

**Impact**: Minimal for a single-object fetch. The extra data is a few KB at most.

**Recommendation**: Add a request configuration with the same `$select` fields used in `_contact_to_dict`.

**Rationale**: Consistency with best practices and marginal latency improvement.

---

## What Was Checked (No Issues Found)

- **Async correctness**: All functions are `async def` and use `await` for Graph SDK calls. No blocking I/O, no `time.sleep()`, no synchronous HTTP calls. Clean.
- **Pagination**: `list_contacts` correctly follows `odata_next_link` in a while loop and respects the `limit` parameter with `contacts[:limit]` truncation. The implementation handles `None` responses from pagination calls.
- **HTTPX client lifecycle**: The module uses the msgraph-sdk, which manages its own HTTP client internally. No raw `httpx.AsyncClient` usage. Not applicable.
- **Token refresh**: Handled at the `GraphClient` layer, not in this module. Not applicable.
- **N+1 queries**: No loops issuing individual Graph calls. Clean.
- **Serial calls that could be parallel**: Each function makes a single logical Graph call (or a sequential pagination chain, which must be serial). No opportunity for `asyncio.gather()`.

---

## Verdict

**PASS**: No critical or important findings. The two suggestions are minor `$select` optimizations on a resource type with relatively small payloads. The module is performant to merge as-is.

---

**Review performed by**: Claude Code (performance-reviewer)
**Review focus**: Async correctness, Graph API efficiency, Pagination completeness, Token refresh overhead, HTTPX client lifecycle
