# General Review: graph-calendar

**Date**: 2026-03-29
**Files**: `graph/calendar.py`, `tests/test_calendar.py`
**Reviewer**: principal-engineer

## Summary

The calendar module is well-structured and follows the established patterns across all graph modules consistently. Code is readable, error handling is uniform, and the test suite covers every public function. Two areas warrant attention: the duplicated `_wrap_odata_error` helper across all four graph modules, and several gaps in test coverage around edge cases and the `update_event` function's `setattr`-based field handling.

## Findings

### Strengths

- **Consistent module structure.** The file follows the same layout as `mail.py`, `contacts.py`, and `tasks.py`: internal helpers, then CRUD operations, each with ODataError wrapping. This consistency reduces cognitive load when moving between modules.
- **Pagination handled correctly.** `list_events` properly follows `@odata.nextLink` with a `limit` cap, matching the project requirement for transparent pagination.
- **Clear `_event_to_dict` conversion.** Defensive null checks on nested objects (`event.start`, `event.body`, `event.organizer.email_address`) prevent AttributeError surprises from partial Graph API responses.
- **RSVP actions are clean and focused.** `accept_event`, `decline_event`, and `tentative_event` each do exactly one thing with minimal ceremony.
- **Test suite covers all 11 public functions.** Every exported function has at least one test, and tests verify both return values and call-site arguments (e.g., checking `send_response` on RSVP bodies).

---

### Recommendations

#### RED -- Critical

**R1. `update_event` accepts arbitrary `**fields` with `setattr` -- no validation or transformation.**
(`graph/calendar.py`, line 270-288)

The `update_event` function accepts any keyword arguments and blindly calls `setattr(event, key, value)` for recognized attributes. Unlike `create_event`, which constructs `DateTimeTimeZone`, `Location`, `Attendee`, and `ItemBody` objects from plain values, `update_event` requires callers to pass pre-constructed SDK model objects. This creates an inconsistent API surface: `create_event(start="2025-06-01T10:00:00Z")` works, but `update_event(event_id, start="2025-06-01T10:00:00Z")` would set `event.start` to a raw string, which the Graph SDK would reject at serialization time.

This is the same pattern used in `contacts.py` `update_contact` -- neither transforms inputs. The `server.py` tool for `update_calendar_event` does construct the correct SDK objects before calling `update_event`, so the bug would not manifest today through the MCP tool path. However, the function's public API is misleading and would break if called directly.

**Recommendation:** Either (a) add the same field-to-model transformations that `create_event` has, or (b) restrict the signature to explicit keyword arguments (`subject`, `start`, `end`, `location`, `body`) and transform them internally, matching `create_event`'s approach. Option (b) is preferred because it makes the contract explicit and catches invalid fields at the function boundary.

#### YELLOW -- Important

**R2. `_wrap_odata_error` is duplicated identically across all four graph modules.**
(`graph/calendar.py:71`, `graph/mail.py:104`, `graph/contacts.py:39`, `graph/tasks.py:47`)

All four modules define byte-for-byte identical implementations:

```python
def _wrap_odata_error(exc: ODataError) -> RuntimeError:
    code = exc.error.code if exc.error else "unknown"
    msg = exc.error.message if exc.error else str(exc)
    return RuntimeError(f"Graph API error {code}: {msg}")
```

This is a textbook DRY violation. While the current duplication is harmless, if the error format needs to change (e.g., adding request IDs, structured error codes for MCP), four files must be updated in lockstep.

**Recommendation:** Extract to `graph/errors.py` or add it to `graph/client.py` and import from there. All modules already import from the `graph` package.

**R3. `_event_to_dict` uses `Any` parameter type -- loses static analysis value.**
(`graph/calendar.py:43`)

The function signature is `def _event_to_dict(event: Any)`. Since the parameter is always a `msgraph.generated.models.event.Event`, typing it as `Event` (already imported at line 13) would enable mypy to catch attribute access errors. The same pattern appears in `_message_to_dict(msg: Any)` in mail.py, `_contact_to_dict(c: Any)` in contacts.py, and `_task_to_dict(task: Any)` in tasks.py.

**Recommendation:** Change to `def _event_to_dict(event: Event) -> dict[str, Any]:`. If the SDK's type stubs are incomplete and cause false positives, use `Event` with a targeted `# type: ignore` comment rather than abandoning type safety entirely.

**R4. `list_calendars` uses both `hasattr` check and `getattr` fallback for the same concept.**
(`graph/calendar.py:96-97`)

```python
"name": cal.name if hasattr(cal, "name") else None,
"display_name": getattr(cal, "display_name", None),
```

These two lines use different defensive patterns for the same concern (optional attributes on the calendar object). The `hasattr`/`if` pattern on line 96 will still raise if the attribute exists but accessing it throws. The `getattr(..., None)` pattern on line 97 is more concise and equally safe. Pick one approach and use it consistently.

**Recommendation:** Use `getattr(cal, "name", None)` for both, or better, just access `cal.name` directly if the Calendar SDK model guarantees the attribute exists (which it does -- it may just be `None`).

**R5. Test coverage gaps -- no ODataError tests for any function.**
(`tests/test_calendar.py`)

None of the 14 test cases verify error handling behavior. Every public function wraps `ODataError` via `_wrap_odata_error`, but no test confirms that:
- An `ODataError` from the Graph SDK is re-raised as a `RuntimeError`.
- The error message includes the OData error code and message.

This is the most important behavioral contract of the module (per CLAUDE.md: "surface Graph API errors as meaningful MCP tool errors, never raw stack traces"), yet it is completely untested.

**Recommendation:** Add at least one parametrized test that verifies the ODataError-to-RuntimeError transformation for a representative function (e.g., `list_events`). A single test covers the shared `_wrap_odata_error` helper used by all functions.

**R6. `search_events` does not handle pagination.**
(`graph/calendar.py:174-194`)

Unlike `list_events`, `list_contacts`, and `list_tasks`, the `search_events` function does not follow `@odata.nextLink`. It fetches a single page and slices to `limit`. If Graph returns fewer results than `limit` in the first page but has more available, the caller gets an incomplete result set with no indication that more exist.

The same gap exists in `mail.py`'s `search_emails`, which does handle pagination. This inconsistency suggests `search_events` was overlooked.

**Recommendation:** Add the standard pagination loop, consistent with `list_events` and `mail.search_emails`.

#### GREEN -- Suggestion

**R7. `list_calendars` return dict includes both `name` and `display_name` for the same field.**
(`graph/calendar.py:93-102`)

The Graph Calendar model uses `name` as the property for the calendar's display name. Including both `name` and `display_name` (via `getattr`) in the output dict creates ambiguity for consumers -- which field should they use? In practice, `display_name` will likely always be `None` since the Calendar model doesn't have that attribute.

**Recommendation:** Use a single `"name"` key, or if the intent is to align with other modules that use `display_name`, rename the key to `display_name` and source it from `cal.name`.

**R8. `create_event` hardcodes timezone to "UTC" without caller override.**
(`graph/calendar.py:221-222, 226-227`)

The `start` and `end` parameters accept ISO 8601 strings, but the timezone is always forced to "UTC". CLAUDE.md states "All inputs/outputs use ISO 8601 with timezone", which implies the timezone should be parsed from the input string or accepted as a separate parameter. Users in non-UTC timezones may be surprised that their timezone-aware ISO strings are reinterpreted as UTC.

**Recommendation:** Either accept an optional `timezone` parameter (defaulting to "UTC"), or parse the timezone from the ISO 8601 input string. Document the current UTC-only behavior explicitly in the docstring as a temporary limitation.

**R9. Test helper `_make_event` uses `id` as a parameter name, shadowing the builtin.**
(`tests/test_calendar.py:31`)

Minor style issue. Using `event_id` instead of `id` would avoid shadowing Python's builtin `id()` function and aligns with the naming convention used in the production code (`event_id`).

## Verdict

**Solid implementation that follows established project patterns well.** The module is production-ready for its current use through the MCP server layer, which compensates for some of the function-level API gaps (R1, R8) by doing its own input transformation.

The most impactful improvements to prioritize:

1. **R5 (test ODataError handling)** -- low effort, high value. This is the module's core error contract and should be verified.
2. **R2 (extract `_wrap_odata_error`)** -- affects all four graph modules, straightforward refactor.
3. **R6 (search pagination)** -- functional gap that could silently truncate results.
4. **R1 (update_event API)** -- latent bug waiting for a direct caller outside `server.py`.

Items R3, R4, R7, R8, R9 are incremental quality improvements that can be addressed opportunistically.
