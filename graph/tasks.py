from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.date_time_time_zone import DateTimeTimeZone
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.task_status import TaskStatus
from msgraph.generated.models.todo_task import TodoTask
from msgraph.generated.users.item.todo.lists.item.tasks.tasks_request_builder import (
    TasksRequestBuilder,
)

if TYPE_CHECKING:
    from graph.client import GraphClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _task_to_dict(task: Any) -> dict[str, Any]:
    """Convert a Graph SDK TodoTask object to a plain dict."""
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status.value if task.status else None,
        "due_date": task.due_date_time.date_time if task.due_date_time else None,
        "completed_date": (
            task.completed_date_time.date_time if task.completed_date_time else None
        ),
        "body": task.body.content if task.body else None,
        "importance": str(task.importance) if task.importance else None,
        "created": str(task.created_date_time) if task.created_date_time else None,
        "last_modified": (
            str(task.last_modified_date_time) if task.last_modified_date_time else None
        ),
    }


def _wrap_odata_error(exc: ODataError) -> RuntimeError:
    """Convert an ODataError into a RuntimeError with a readable message."""
    code = exc.error.code if exc.error else "unknown"
    msg = exc.error.message if exc.error else str(exc)
    return RuntimeError(f"Graph API error {code}: {msg}")


async def _resolve_list_id(gc: GraphClient, list_id: str | None) -> str:
    """Return *list_id* if provided, otherwise the ID of the first todo list."""
    if list_id is not None:
        return list_id

    try:
        result = await gc.client.me.todo.lists.get()
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc

    if result is None or not result.value:
        raise RuntimeError("No todo lists found for the authenticated user")

    first = result.value[0]
    if first.id is None:
        raise RuntimeError("Default todo list has no ID")
    return str(first.id)


# ---------------------------------------------------------------------------
# List tasks
# ---------------------------------------------------------------------------


async def list_tasks(
    gc: GraphClient,
    *,
    list_id: str | None = None,
    completed: bool | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return tasks from a todo list, optionally filtered by completion status."""
    resolved_list_id = await _resolve_list_id(gc, list_id)

    filter_expr: str | None = None
    if completed is True:
        filter_expr = "status eq 'completed'"
    elif completed is False:
        filter_expr = "status ne 'completed'"

    query_params = TasksRequestBuilder.TasksRequestBuilderGetQueryParameters(
        top=limit,
        filter=filter_expr,
    )
    request_config = RequestConfiguration(query_parameters=query_params)

    try:
        result = await gc.client.me.todo.lists.by_todo_task_list_id(resolved_list_id).tasks.get(
            request_configuration=request_config
        )
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc

    if result is None or result.value is None:
        return []

    tasks = list(result.value)

    # Handle pagination
    while result is not None and result.odata_next_link and len(tasks) < limit:
        try:
            result = (
                await gc.client.me.todo.lists.by_todo_task_list_id(resolved_list_id)
                .tasks.with_url(result.odata_next_link)
                .get()
            )
        except ODataError as exc:
            raise _wrap_odata_error(exc) from exc
        if result and result.value:
            tasks.extend(result.value)

    return [_task_to_dict(t) for t in tasks[:limit]]


# ---------------------------------------------------------------------------
# Create task
# ---------------------------------------------------------------------------


async def create_task(
    gc: GraphClient,
    *,
    title: str,
    list_id: str | None = None,
    due_date: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create a new task in the specified (or default) todo list."""
    resolved_list_id = await _resolve_list_id(gc, list_id)

    task = TodoTask()
    task.title = title

    if due_date:
        due_dt = DateTimeTimeZone()
        due_dt.date_time = due_date
        due_dt.time_zone = "UTC"
        task.due_date_time = due_dt

    if notes:
        body = ItemBody()
        body.content = notes
        body.content_type = BodyType.Text
        task.body = body

    try:
        created = await gc.client.me.todo.lists.by_todo_task_list_id(resolved_list_id).tasks.post(
            task
        )
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc

    if created is None:
        raise RuntimeError("Task creation returned no result")
    return _task_to_dict(created)


# ---------------------------------------------------------------------------
# Complete task
# ---------------------------------------------------------------------------


async def complete_task(
    gc: GraphClient, task_id: str, list_id: str | None = None
) -> dict[str, Any]:
    """Mark a task as completed."""
    resolved_list_id = await _resolve_list_id(gc, list_id)

    patch_task = TodoTask()
    patch_task.status = TaskStatus.Completed

    try:
        updated = (
            await gc.client.me.todo.lists.by_todo_task_list_id(resolved_list_id)
            .tasks.by_todo_task_id(task_id)
            .patch(patch_task)
        )
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc

    if updated is None:
        # PATCH may return 204 — re-fetch
        try:
            fetched = (
                await gc.client.me.todo.lists.by_todo_task_list_id(resolved_list_id)
                .tasks.by_todo_task_id(task_id)
                .get()
            )
        except ODataError as exc:
            raise _wrap_odata_error(exc) from exc
        if fetched is None:
            raise RuntimeError(f"Task {task_id!r} not found after completion")
        return _task_to_dict(fetched)

    return _task_to_dict(updated)


# ---------------------------------------------------------------------------
# Delete task
# ---------------------------------------------------------------------------


async def delete_task(gc: GraphClient, task_id: str, list_id: str | None = None) -> None:
    """Delete a task by ID."""
    resolved_list_id = await _resolve_list_id(gc, list_id)

    try:
        await (
            gc.client.me.todo.lists.by_todo_task_list_id(resolved_list_id)
            .tasks.by_todo_task_id(task_id)
            .delete()
        )
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc
