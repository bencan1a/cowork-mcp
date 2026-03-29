# Performance Review: graph-mail
**Date**: 2026-03-29 | **Files reviewed**: 2 | **Reviewer**: performance-reviewer

## Summary

The `graph/mail.py` module is well-structured with proper async patterns, good use of `$select` projections on list operations, and correct pagination handling in `list_emails`, `search_emails`, and `list_mail_folders`. The code uses the `msgraph-sdk` throughout (no raw `httpx` calls), so HTTPX client lifecycle is not a concern. There are two notable findings: a missed parallelization opportunity in `get_email` and a missing `$select` projection on the single-message fetch. No blocking I/O or event-loop-blocking patterns were found.

---

## Findings

### [YELLOW] Serial Graph API calls in `get_email` could be parallelized
**File**: `graph/mail.py` (lines 205-226)

**Issue**: `get_email` first awaits the message fetch, then conditionally awaits the attachments fetch. These two calls are independent and could run concurrently. However, the attachment fetch is conditional on `msg.has_attachments`, which is only known after the first call completes. This means true parallelization would require always fetching attachments (and discarding the empty result when there are none) or using `$expand=attachments` to fetch both in a single request.

**Impact**: For messages with attachments, this adds one full Graph API round-trip (~100-300ms) sequentially. For messages without attachments, no impact.

**Recommendation**: Use the `$expand=attachments($select=id,name,contentType,size)` OData parameter on the initial message GET request. This fetches message and attachment metadata in a single round-trip, eliminating the conditional second call entirely.

```python
# Instead of two sequential calls, use $expand on the single call:
query_params = MessageItemRequestBuilder.MessageItemRequestBuilderGetQueryParameters(
    expand=["attachments($select=id,name,contentType,size)"],
)
```

**Rationale**: For an interactive MCP tool call, saving 100-300ms on every email-with-attachments fetch is user-perceptible. The `$expand` approach is both simpler and faster.

---

### [BLUE] Missing `$select` projection on `get_email` single-message fetch
**File**: `graph/mail.py` (lines 206)

**Issue**: The `get_email` call at line 206 fetches a single message without a `$select` parameter. This returns all 50+ fields from the Graph API, even though `_message_to_dict` only uses about 10 of them. While `list_emails` and `search_emails` correctly use `$select`, the single-message fetch does not.

**Impact**: Minor for a single message -- the extra payload is small (a few KB). This is a consistency and hygiene issue rather than a user-perceptible latency problem.

**Recommendation**: Add a `$select` parameter matching the fields used in `_message_to_dict`:

```python
query_params = MessageItemRequestBuilder.MessageItemRequestBuilderGetQueryParameters(
    select=["id", "subject", "from", "toRecipients", "receivedDateTime",
            "isRead", "bodyPreview", "body", "importance", "hasAttachments"],
)
request_config = RequestConfiguration(query_parameters=query_params)
msg = await gc.client.me.messages.by_message_id(message_id).get(
    request_configuration=request_config
)
```

**Rationale**: Consistency with the list operations and slightly reduced response payload.

---

### [BLUE] `search_emails` excludes `body` from `$select` but `list_emails` includes it
**File**: `graph/mail.py` (lines 245-251 vs 164-173)

**Issue**: `list_emails` includes `"body"` in its `$select` list (line 172), while `search_emails` omits it (lines 245-251). Including `body` in list operations fetches the full HTML body for every message in the result set, which can be substantial (tens of KB per message, multiplied by the page size).

**Impact**: For `list_emails` with 20-50 results, fetching full bodies adds significant payload -- potentially 500KB-1MB+ of HTML that may not be needed when browsing an inbox. The `bodyPreview` field (already included) is usually sufficient for list views.

**Recommendation**: Remove `"body"` from the `$select` in `list_emails`. Users who need the full body can call `get_email` on specific messages. This matches the pattern already used by `search_emails`.

**Rationale**: Reduces response payload and Graph API response time for the most common mail operation (listing inbox).

---

## What looks good

- **Pagination**: All three list operations (`list_emails`, `search_emails`, `list_mail_folders`) correctly follow `@odata.nextLink` and respect the `limit` parameter with early termination. The `page_size = min(limit, 50)` pattern avoids over-fetching on the first page.
- **`$select` usage**: `list_emails` and `search_emails` both use `$select` to limit returned fields.
- **`$top` usage**: Both list operations set `$top` to `min(limit, 50)`, correctly capping the page size.
- **Async correctness**: No blocking I/O, no `time.sleep()`, no synchronous HTTP calls. All Graph API calls use `await` on SDK async methods.
- **No N+1 patterns**: No loops with individual Graph API calls inside.
- **SDK-managed HTTP client**: Uses `msgraph-sdk` throughout, so connection pooling and TCP reuse are handled by the SDK.
- **Test coverage**: Tests verify pagination, limit enforcement, empty results, and error handling.

---

## Verdict

- **PASS**: No critical findings. Two suggestions for optimization (parallelizing attachment fetch via `$expand`, adding `$select` to single-message fetch) and one suggestion to remove `body` from `list_emails` `$select` to reduce payload. None of these are blockers -- the code is performant for typical single-user MCP tool calls.

---

**Review performed by**: Claude Code (performance-reviewer)
**Review focus**: Async correctness, Graph API efficiency, Pagination completeness, Token refresh overhead, HTTPX client lifecycle
