# General Review: graph-contacts

**Date**: 2026-03-29
**Files**: 2 (`graph/contacts.py`, `tests/test_contacts.py`)
**Reviewer**: principal-engineer

---

## Summary

`graph/contacts.py` is well-structured, consistent with the project's architectural
patterns, and handles the most important runtime concerns (pagination, 204 No Content,
OData error translation). The test suite covers the four implemented operations with
meaningful scenarios. The primary gap is the complete absence of `delete_contact`,
which is a standard CRUD operation implied by the overall design, and a secondary
weakness is that `update_contact` does not handle `email_addresses` the way
`create_contact` does, creating an inconsistency that will surface as a silent bug.

---

## Findings

### 🔴 Missing `delete_contact` function

The module exposes `list_contacts`, `get_contact`, `create_contact`, and
`update_contact` but has no `delete_contact`. This is a functional gap, not a style
issue. Any server-side MCP tool that wants to offer contact deletion has no backend
to call, and the absence is not acknowledged anywhere (no TODO, no comment). Graph
API supports `DELETE /me/contacts/{id}` — the implementation is straightforward and
consistent with the existing pattern.

Recommended implementation:

```python
async def delete_contact(gc: GraphClient, contact_id: str) -> None:
    """Delete a contact by ID. Raises RuntimeError if the contact does not exist."""
    try:
        await gc.client.me.contacts.by_contact_id(contact_id).delete()
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc
```

No return value is needed (Graph returns 204). The corresponding test class should
cover the success path and an ODataError propagation case.

---

### 🔴 `update_contact` silently drops `email_addresses` string input

`create_contact` has explicit logic to convert plain strings (and lists of strings)
into `EmailAddress` objects before assigning them to `contact.email_addresses`. The
`update_contact` function uses a generic `setattr` loop and has no equivalent
conversion. If a caller passes `email_addresses=["new@example.com"]` to
`update_contact`, the raw Python string lands on the SDK object. The SDK will either
raise a type error at serialization time, send malformed JSON, or silently discard the
field — none of these outcomes are acceptable and none produce a clear error message.

The email-normalization logic in `create_contact` should be extracted into a private
helper and called from both functions:

```python
def _normalize_email_addresses(email_input: Any) -> list[EmailAddress]:
    result: list[EmailAddress] = []
    for item in email_input if isinstance(email_input, list) else [email_input]:
        if isinstance(item, str):
            ea = EmailAddress()
            ea.address = item
            result.append(ea)
        else:
            result.append(item)
    return result
```

Both `create_contact` and `update_contact` would call this before the `setattr` loop.
This eliminates the duplication and closes the gap.

---

### 🟡 `_contact_to_dict` uses `Any` annotation instead of the concrete type

The parameter is annotated `c: Any` rather than `c: Contact`. This was presumably
done to avoid an import at runtime, but `Contact` is already imported unconditionally
at the top of the module (line 7). Tightening the annotation gives mypy full
visibility into attribute access on `c` and makes the intent clearer to readers.

Change:

```python
def _contact_to_dict(c: Any) -> dict[str, Any]:
```

to:

```python
def _contact_to_dict(c: Contact) -> dict[str, Any]:
```

---

### 🟡 Pagination loop condition is subtly wrong under high-volume responses

The pagination loop guard is:

```python
while result is not None and result.odata_next_link and len(contacts) < limit:
```

If the first page already returns more items than `limit` (Graph's `$top` is a hint,
not a guarantee), the loop is skipped and `contacts[:limit]` truncates correctly.
However, if the first page returns fewer items than `limit` and a next page exists,
the loop fetches the next page but appends *all* items from it without checking
whether the running total has crossed `limit`. The final `contacts[:limit]` slice
corrects the count, but unnecessary network round-trips are made when mid-pagination
the target count is already reached.

The fix is to check `len(contacts) < limit` *after* each extend, which the current
loop guard does at the top but only for whether to enter the next iteration — not
after the extend inside the loop body. This is a minor efficiency issue, not a
correctness bug, because the slice at the end is correct.

More importantly: the `$top` parameter on the initial request is set to `limit`, so
Graph will already cap the first page. The pagination path is only exercised when a
next link is present *and* the first page was exactly `limit` items (which means the
slice at line 82 discards nothing from subsequent pages anyway). The logic is
effectively correct but worth documenting with a comment explaining the invariant.

---

### 🟡 `create_contact` email branch does not handle empty list input

```python
if email_input:
```

This guard is falsy for both `None` and `[]`. An empty list `[]` is a legitimate
explicit value meaning "clear all email addresses" in an update scenario, but for
create it is arguably a no-op. The behaviour is acceptable for `create_contact` but
would be wrong if this guard were reused verbatim for `update_contact`. This is
another reason to extract the normalization helper and separate the guard logic per
call site.

---

### 🔵 Test for `list_contacts` with search does not assert query parameter value

`test_list_contacts_with_search` calls `assert_called_once()` but does not verify
that the `RequestConfiguration` passed to `get()` carries the expected search term.
Because `RequestConfiguration` is an opaque object from the SDK, the assertion as
written would pass even if the search parameter were silently dropped. This is a
test that gives confidence it does not actually have.

A stronger assertion would inspect `call_args` and check that the query parameters
object has `search == "Alice"`, similar to how `test_create_contact_with_string_email`
inspects the posted `Contact` object.

---

### 🔵 `_make_contact` factory does not expose `company_name` / `job_title` as parameters

The helper hardcodes `company_name = "ACME Corp"` and `job_title = "Engineer"`, which
means tests cannot exercise paths that depend on those fields being absent (`None`).
`_contact_to_dict` maps both fields directly, so their absence is already tested
implicitly when the mock returns `None` for them. Expanding `_make_contact` to accept
optional overrides for all contact fields would make future tests easier to write and
would mirror the completeness of `_contact_to_dict`.

---

### 🔵 No test covers ODataError propagation for any operation

Every public function wraps `ODataError` via `_wrap_odata_error`. There are zero tests
verifying that an `ODataError` raised by the SDK is re-raised as a `RuntimeError`
with the expected message format. This is a tested contract in other graph modules and
should be consistent here.

Pattern to follow:

```python
async def test_list_contacts_raises_on_graph_error(self, gc: MagicMock) -> None:
    err = MagicMock(spec=ODataError)
    err.error = MagicMock(code="ErrorItemNotFound", message="Not found")
    gc.client.me.contacts.get = AsyncMock(side_effect=err)

    with pytest.raises(RuntimeError, match="ErrorItemNotFound"):
        await list_contacts(gc)
```

The same pattern applies to `get_contact`, `create_contact`, and `update_contact`.

---

## Verdict: ⚠️

The implementation is solid for the four operations it covers, and the code is clean,
readable, and consistent with project conventions. Two items block an unconditional
pass:

1. `delete_contact` is absent with no acknowledgement — this is a functional gap that
   will require server.py to route around it or leave users without a delete capability.

2. The `email_addresses` normalization asymmetry between `create_contact` and
   `update_contact` is a latent bug that will produce confusing failures when a caller
   attempts to update email addresses on an existing contact.

Both are straightforward to fix. Address those two and the module is in good shape.
