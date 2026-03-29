# General Review: graph-mail

**Date**: 2026-03-29 | **Files reviewed**: 2 | **Reviewer**: principal-engineer

## Summary

`graph/mail.py` is a well-structured, comprehensive module covering all mail operations (list, get, search, send, reply, forward, move, delete, mark, folders, mailbox settings, auto-reply). The code follows project conventions consistently: GraphClient dependency injection, OData error wrapping, pagination handling, and clean dict serialization. Tests cover the happy paths and a few error cases but have meaningful gaps in edge-case and error-path coverage.

## Findings

### RED -- Critical

**F1. OData filter injection via unsanitized string interpolation**
`list_emails` (lines 143-150) builds OData `$filter` strings by interpolating user-supplied `sender` and `subject` values directly into query fragments:

```python
filters.append(f"from/emailAddress/address eq '{sender}'")
filters.append(f"contains(subject, '{subject}')")
```

A `sender` value like `test@x.com' or 1 eq 1 or '' eq '` or a `subject` containing a single quote will break the filter or return unintended results. While the Graph API is unlikely to allow arbitrary data exfiltration, malformed filters will produce confusing 400 errors or silently return wrong results. At minimum, single quotes in OData string literals must be escaped by doubling them (`''`).

**Recommendation**: Create a helper `_odata_escape(value: str) -> str` that replaces `'` with `''`, and apply it to all interpolated string values.

**F2. `search_emails` omits `body` from `$select` but `_message_to_dict` reads `msg.body.content`**
`search_emails` (line 246) does not include `"body"` in the `$select` list, unlike `list_emails` which does. The resulting dict will have `"body": None` for every search result even if the message has a body. This is an inconsistency that could confuse MCP tool consumers expecting uniform message dicts.

**Recommendation**: Either add `"body"` to the `search_emails` select list, or document this as intentional (search results return preview only). If intentional, consider excluding the `body` key from the returned dict entirely to avoid ambiguity.

### YELLOW -- Important

**F3. `_wrap_odata_error` is duplicated across four modules**
Identical copies exist in `graph/mail.py`, `graph/calendar.py`, `graph/contacts.py`, and `graph/tasks.py`. This violates DRY and means a bug fix or enhancement (e.g., logging the error code, including the request ID) must be applied in four places.

**Recommendation**: Extract to a shared `graph/errors.py` module. This is a small, safe refactor.

**F4. `_message_to_dict` accepts `Any` instead of `Message`**
The converter function types its parameter as `Any` (line 54), losing all type safety. The same applies to `_folder_to_dict`. The msgraph SDK provides `Message` and `MailFolder` types that could be used here.

**Recommendation**: Type the parameters as `Message` and `MailFolder` respectively. If there are edge cases where the SDK returns a supertype, use `Message | None` with an early guard.

**F5. Pagination loop duplicated three times**
The pattern of `while response and response.value: ... if response.odata_next_link: ...` appears in `list_emails`, `search_emails`, and `list_mail_folders` with minor variations. This is not egregious at three occurrences but will grow as more list operations are added.

**Recommendation**: Consider a generic `async def _collect_pages(first_response, builder, limit)` helper. Low urgency -- the current repetition is manageable.

**F6. `list_emails` empty page with `value=[]` exits the pagination loop but a `None` response does not**
The `while response and response.value` condition means an empty `value` list (`[]`) is falsy and exits the loop, which is correct. However, a response where `value is None` also exits. The Graph API typically returns an empty list rather than `None`, but this is an implicit assumption worth documenting.

**F7. No test coverage for OData error paths on most operations**
Only `list_emails` has an `ODataError` test (`test_list_emails_odata_error`). The remaining 12 public functions lack error-path tests. Since `_wrap_odata_error` is the shared error mechanism, one test per function would catch regressions if someone accidentally removes the `try/except` block.

**Recommendation**: Add at least one `ODataError` test for `send_email`, `get_email`, `move_email`, and `search_emails` as representative samples.

**F8. No tests for `get_email` when message is `None`**
`get_email` (line 208) raises `RuntimeError` when the SDK returns `None`, but no test covers this branch. Similarly, `move_email` and `mark_email_read` have `None`-check branches without test coverage.

### GREEN -- Suggestions

**F9. `@pytest.mark.asyncio` decorators are redundant**
`CLAUDE.md` documents that `asyncio_mode = "auto"` is configured in `pyproject.toml`, meaning async test functions run automatically. All 26 async tests in `test_mail.py` have explicit `@pytest.mark.asyncio` decorators that are unnecessary. Removing them would reduce noise, though this is purely cosmetic.

**F10. `list_emails` defaults to `folder="inbox"` but does not validate the folder name**
Passing an invalid well-known folder name (e.g., `"inboxx"`) will produce an opaque Graph API 404 error. A small allowlist of well-known names with a helpful error message would improve the developer experience, though the Graph API error is ultimately caught by `_wrap_odata_error`.

**F11. `set_auto_reply` with `enabled=True, start_datetime` set but `end_datetime` missing falls through to `AlwaysEnabled`**
The logic on line 578 checks `start_datetime and end_datetime` together. If a caller provides only `start_datetime` without `end_datetime`, the function silently sets `AlwaysEnabled` instead of raising a validation error about the incomplete schedule. This is a minor edge case but could surprise callers.

**F12. Test helper `_make_mock_message` could set `from_` and `received_date_time` to realistic values**
Currently both default to `None`, which means `_message_to_dict` serialization of these fields is not exercised in most tests. A test with realistic `from_` and `received_date_time` mock values would cover more of the serialization logic.

## Verdict

**Assessment: YELLOW -- Important issues to address**

The module is well-written and functionally complete. The OData injection issue (F1) is the most important finding to fix -- it is a straightforward change. The `$select` inconsistency (F2) and `_wrap_odata_error` duplication (F3) are worthwhile quality improvements. Test coverage is good for happy paths but has clear gaps in error handling and edge cases.

**Priority remediation order:**
1. F1 -- OData string escaping (security/correctness)
2. F2 -- `search_emails` select consistency (correctness)
3. F3 -- Extract shared `_wrap_odata_error` (DRY)
4. F7/F8 -- Add error-path and None-guard tests (coverage)
5. F4 -- Tighten `_message_to_dict` type signature (type safety)
6. F11 -- Validate `set_auto_reply` schedule completeness (edge case)
