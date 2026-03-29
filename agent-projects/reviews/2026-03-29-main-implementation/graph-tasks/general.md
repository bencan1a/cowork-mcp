# General Review: graph/tasks

**Date**: 2026-03-29

**Files**: 2
- `graph/tasks.py` (228 lines)
- `tests/test_tasks.py` (256 lines)

**Reviewer**: principal-engineer

---

## Summary

The tasks module implements a clean, focused CRUD interface for Microsoft Graph Todo tasks. Code is well-structured with appropriate error handling, pagination support, and reasonable test coverage. However, there is a critical gap: **no `update_task` function**, which breaks feature parity with sibling modules (`calendar.py` has `update_event`, `contacts.py` has `update_contact`). Additionally, test coverage could be more robust in edge cases and pagination scenarios.

---

## Findings

### 🔴 CRITICAL

**Missing `update_task` implementation**

The module implements create/read/delete but not update. Both `calendar.py` (line 270) and `contacts.py` (line 149) provide `update_*` functions using a flexible `**fields` pattern. Tasks needs this for:
- Modifying task title, notes, status progressively
- Achieving feature parity with other domains
- Supporting incremental updates (don't require re-sending entire task state)

**Impact**: Users can only complete or delete tasks, not edit them after creation. This is a significant usability gap.

**Recommendation**: Implement `update_task(gc, task_id, list_id=None, **fields)` following the calendar/contacts pattern:
```python
async def update_task(gc: GraphClient, task_id: str, list_id: str | None = None, **fields: Any) -> dict[str, Any]:
    """Update a task. Pass keyword args matching Graph TodoTask fields (e.g., title=..., status=..., importance=...)."""
    resolved_list_id = await _resolve_list_id(gc, list_id)
    task = TodoTask()

    for key, value in fields.items():
        if hasattr(task, key):
            setattr(task, key, value)
        else:
            logger.warning("Unknown TodoTask field %r — skipping", key)

    try:
        updated = await gc.client.me.todo.lists.by_todo_task_list_id(resolved_list_id).tasks.by_todo_task_id(task_id).patch(task)
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc

    if updated is None:
        try:
            fetched = await gc.client.me.todo.lists.by_todo_task_list_id(resolved_list_id).tasks.by_todo_task_id(task_id).get()
        except ODataError as exc:
            raise _wrap_odata_error(exc) from exc
        if fetched is None:
            raise RuntimeError(f"Task {task_id!r} not found after update")
        return _task_to_dict(fetched)
    return _task_to_dict(updated)
```

**Jira/GitHub Issue**: Should be created to track this implementation gap.

---

### 🟡 IMPORTANT

**Incomplete pagination test coverage**

`test_list_tasks_filter_completed` (line 109) verifies that a filter is passed but does **not** actually assert on the filter value sent to the API. The test calls the function but never checks the `request_configuration` passed to `.get()`.

**Current test:**
```python
async def test_list_tasks_filter_completed(self, gc: MagicMock) -> None:
    tasks_response = MagicMock()
    tasks_response.value = []
    tasks_response.odata_next_link = None
    gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.get = AsyncMock(
        return_value=tasks_response
    )

    await list_tasks(gc, list_id="list-1", completed=True)
    # Only verifies get was called, not that filter was correct
```

**Recommendation**: Enhance to verify the filter expression:
```python
call_args = gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.get.call_args
request_config = call_args.kwargs["request_configuration"]
assert request_config.query_parameters.filter == "status eq 'completed'"
```

**Why**: Tests should verify behavior, not just that methods were called. Filters are the core contract here.

---

**No test for pagination loop termination**

`list_tasks` implements pagination (lines 113–123) but tests don't verify:
1. That the loop correctly terminates when `odata_next_link` is `None`
2. That the `limit` parameter is respected during pagination (tasks from multiple pages are aggregated but truncated)
3. Behavior when pagination exceeds the limit

**Recommendation**: Add test:
```python
async def test_list_tasks_pagination_respects_limit(self, gc: MagicMock) -> None:
    """Verify pagination stops at limit even with multiple pages."""
    task1 = _make_task(id="t1")
    task2 = _make_task(id="t2")
    task3 = _make_task(id="t3")

    # First page returns 2 tasks with nextLink
    page1 = MagicMock()
    page1.value = [task1, task2]
    page1.odata_next_link = "https://graph.microsoft.com/v1.0/me/todo/lists/list-1/tasks?$skip=2"

    # Second page returns 1 task (we'll fetch it but shouldn't return it)
    page2 = MagicMock()
    page2.value = [task3]
    page2.odata_next_link = None

    get_mock = gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.get
    get_mock.side_effect = [page1, page2]

    result = await list_tasks(gc, list_id="list-1", limit=2)

    assert len(result) == 2
    assert result[0]["id"] == "t1"
    assert result[1]["id"] == "t2"
```

---

**Type annotation inconsistency: `Any` in `_task_to_dict`**

Line 28 declares `task: Any` instead of `task: TodoTask`. While this works (the function is internal), it weakens IDE support and type checking. The function explicitly accesses `.id`, `.title`, etc., which are TodoTask attributes.

**Recommendation**: Change to `task: TodoTask` to improve type safety and IDE hints.

---

### 🟢 SUGGESTIONS

**Minor: Unused import**

`TasksRequestBuilder` is imported (line 14) and used only once (line 94) to construct query parameters. This is fine, but consider whether it's clearer to inline:

```python
# Current (verbose but explicit):
query_params = TasksRequestBuilder.TasksRequestBuilderGetQueryParameters(top=limit, filter=filter_expr)

# Alternative (shorter):
from msgraph.generated.users.item.todo.lists.item.tasks.tasks_request_builder import (
    TasksRequestBuilder,
)
query_params = TasksRequestBuilder.TasksRequestBuilderGetQueryParameters(...)
```

This is a matter of preference. The current approach is reasonable.

---

**Test fixture naming: `_make_task` could be more explicit**

The fixture factory `_make_task` (line 15) accepts optional parameters (`id`, `title`, `status_value`) with defaults. Most tests use defaults but some override. Consider whether the defaults match common test scenarios, or document the "typical task" being created.

**Note**: This is a style suggestion; current naming is clear enough.

---

**Documentation: `_resolve_list_id` behavior deserves emphasis**

The function (line 54) silently fetches the default list if `list_id` is `None`. While this is documented in docstrings, the convenience is powerful — every public function can work with or without an explicit list. Consider a brief inline comment explaining the rationale:

```python
async def _resolve_list_id(gc: GraphClient, list_id: str | None) -> str:
    """Return *list_id* if provided, otherwise the ID of the first todo list.

    This convenience allows all public functions to work with the default list
    without requiring callers to manage list discovery.
    """
```

---

## Edge Cases & Risks

| Edge Case | Risk | Mitigation |
|-----------|------|-----------|
| Task with no ID returned from Graph | RuntimeError "default todo list has no ID" | Tested implicitly in `_resolve_list_id`; could be explicit |
| PATCH returns 204 No Content (no response body) | `complete_task` handles via refetch | ✓ Implemented; tested in `test_complete_task_refetches_on_none_patch` |
| `@odata.nextLink` pagination with large datasets | Loop may aggregate many tasks before truncating to limit | Tested only by presence of next link; not tested for correctness of limit enforcement |
| Empty list response (None or empty array) | Handled in `list_tasks` (lines 107–108) | ✓ Test covers empty `.value` |
| Graph API error with no error code | `_wrap_odata_error` defaults to "unknown" | ✓ Safe; produces meaningful message |
| Task without title | Graph would reject (title is required) but module doesn't validate | This is Graph API's job; module is permissive (acceptable) |

---

## Technical Debt

No technical debt incurred in the implementation itself. However:

1. **Missing feature (update_task)**: Not debt per se, but a gap that should be tracked as an issue. Users can work around it by deleting and recreating, but that's not ideal.

2. **Test coverage for pagination edge cases**: The pagination logic is correct but under-tested. Recommend adding tests for boundary conditions (limit reached mid-page, multiple pages, etc.).

---

## Code Quality Assessment

| Aspect | Status | Notes |
|--------|--------|-------|
| **Naming** | ✓ Excellent | Functions, parameters, and variables are clear and intent-revealing |
| **Error Handling** | ✓ Good | ODataError wrapping is consistent; RuntimeErrors are descriptive |
| **Type Safety** | ✓ Mostly Good | Minor issue: `task: Any` in `_task_to_dict` should be `task: TodoTask` |
| **Async/Await** | ✓ Good | Proper use of async; no blocking calls |
| **DRY** | ✓ Good | Helper functions (`_task_to_dict`, `_wrap_odata_error`, `_resolve_list_id`) reduce duplication |
| **Comments** | ✓ Adequate | Comments focus on "why" (e.g., line 195: PATCH may return 204) rather than "what" |
| **Testing** | ⚠️ Acceptable | Good coverage of happy paths; weak on pagination and filter verification |
| **Consistency** | ✓ Good | Matches patterns in `calendar.py` and `contacts.py` (except missing `update_*`) |

---

## Verdict

**⚠️ CONDITIONAL PASS** — The module is well-crafted and production-ready for current use cases (create, read, complete, delete). However, **the absence of `update_task` is a critical gap** that should be addressed before the feature set is considered complete. Test coverage is good for implemented functions but weak for pagination edge cases.

### Before shipping as "complete":

1. **Implement `update_task`** to match `calendar.py` and `contacts.py` patterns
2. **Enhance test coverage** for pagination limit enforcement and filter expressions
3. **Fix type annotation** in `_task_to_dict` (`task: Any` → `task: TodoTask`)

### After those fixes:

The module will be ready for production with full feature parity and comprehensive test coverage (estimated ~80%).

---

## Recommended Action Items

- [ ] Create GitHub Issue: "Implement update_task in graph/tasks.py for feature parity"
- [ ] Add test: `test_list_tasks_pagination_respects_limit`
- [ ] Add test: `test_list_tasks_filter_completed` should verify filter expression in call args
- [ ] Update type annotation: `task: Any` → `task: TodoTask` in `_task_to_dict`
- [ ] Register `update_task` in `server.py` under `scope_tasks_write` block (once implemented)

