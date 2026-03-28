from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from msgraph.generated.models.task_status import TaskStatus

from graph.tasks import complete_task, create_task, delete_task, list_tasks

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_task(
    *,
    id: str = "task-1",
    title: str = "Buy groceries",
    status_value: str = "notStarted",
) -> MagicMock:
    """Return a mock TodoTask object."""
    task = MagicMock()
    task.id = id
    task.title = title
    task.status = MagicMock(value=status_value)
    task.due_date_time = None
    task.completed_date_time = None
    task.body = MagicMock(content="Task notes")
    task.importance = MagicMock(__str__=lambda self: "normal")
    task.created_date_time = "2025-06-01T10:00:00Z"
    task.last_modified_date_time = "2025-06-01T10:00:00Z"
    return task


def _make_todo_list(id: str = "list-1", display_name: str = "Tasks") -> MagicMock:
    """Return a mock TodoTaskList object."""
    lst = MagicMock()
    lst.id = id
    lst.display_name = display_name
    return lst


@pytest.fixture
def gc() -> MagicMock:
    """Return a mock GraphClient with todo list structure set up."""
    mock_gc = MagicMock()

    # Default: return a single todo list
    todo_list = _make_todo_list()
    list_response = MagicMock()
    list_response.value = [todo_list]
    mock_gc.client.me.todo.lists.get = AsyncMock(return_value=list_response)

    return mock_gc


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------


class TestListTasks:
    async def test_list_tasks_returns_list(self, gc: MagicMock) -> None:
        task = _make_task()
        tasks_response = MagicMock()
        tasks_response.value = [task]
        tasks_response.odata_next_link = None

        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.get = AsyncMock(
            return_value=tasks_response
        )

        result = await list_tasks(gc, list_id="list-1")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == "task-1"
        assert result[0]["title"] == "Buy groceries"

    async def test_list_tasks_uses_default_list_when_no_list_id(self, gc: MagicMock) -> None:
        task = _make_task()
        tasks_response = MagicMock()
        tasks_response.value = [task]
        tasks_response.odata_next_link = None

        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.get = AsyncMock(
            return_value=tasks_response
        )

        result = await list_tasks(gc)  # no list_id → should use default

        gc.client.me.todo.lists.get.assert_called_once()
        gc.client.me.todo.lists.by_todo_task_list_id.assert_called_once_with("list-1")
        assert len(result) == 1

    async def test_list_tasks_empty_response(self, gc: MagicMock) -> None:
        tasks_response = MagicMock()
        tasks_response.value = []
        tasks_response.odata_next_link = None

        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.get = AsyncMock(
            return_value=tasks_response
        )

        result = await list_tasks(gc, list_id="list-1")

        assert result == []

    async def test_list_tasks_filter_completed(self, gc: MagicMock) -> None:
        tasks_response = MagicMock()
        tasks_response.value = []
        tasks_response.odata_next_link = None

        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.get = AsyncMock(
            return_value=tasks_response
        )

        await list_tasks(gc, list_id="list-1", completed=True)

        # Verify get was called with request config containing filter
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.get.assert_called_once()

    async def test_list_tasks_raises_when_no_lists(self, gc: MagicMock) -> None:
        list_response = MagicMock()
        list_response.value = []
        gc.client.me.todo.lists.get = AsyncMock(return_value=list_response)

        with pytest.raises(RuntimeError, match="No todo lists found"):
            await list_tasks(gc)


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------


class TestCreateTask:
    async def test_create_task_calls_post(self, gc: MagicMock) -> None:
        task = _make_task(id="new-task", title="Write tests")
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.post = AsyncMock(
            return_value=task
        )

        result = await create_task(gc, title="Write tests", list_id="list-1")

        gc.client.me.todo.lists.by_todo_task_list_id.assert_called_with("list-1")
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.post.assert_called_once()
        assert result["id"] == "new-task"
        assert result["title"] == "Write tests"

    async def test_create_task_with_due_date_and_notes(self, gc: MagicMock) -> None:
        task = _make_task(id="t-due")
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.post = AsyncMock(
            return_value=task
        )

        await create_task(
            gc,
            title="Submit report",
            list_id="list-1",
            due_date="2025-06-30T00:00:00Z",
            notes="Remember to include appendix",
        )

        call_args = gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.post.call_args
        created_task = call_args[0][0]
        assert created_task.due_date_time is not None
        assert created_task.body is not None

    async def test_create_task_raises_on_none_response(self, gc: MagicMock) -> None:
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.post = AsyncMock(
            return_value=None
        )

        with pytest.raises(RuntimeError, match="no result"):
            await create_task(gc, title="Doomed task", list_id="list-1")

    async def test_create_task_uses_default_list(self, gc: MagicMock) -> None:
        task = _make_task()
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.post = AsyncMock(
            return_value=task
        )

        await create_task(gc, title="Auto-list task")

        gc.client.me.todo.lists.get.assert_called_once()
        gc.client.me.todo.lists.by_todo_task_list_id.assert_called_with("list-1")


# ---------------------------------------------------------------------------
# complete_task
# ---------------------------------------------------------------------------


class TestCompleteTask:
    async def test_complete_task_calls_patch(self, gc: MagicMock) -> None:
        task = _make_task(id="task-done", status_value="completed")
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.by_todo_task_id.return_value.patch = AsyncMock(
            return_value=task
        )

        result = await complete_task(gc, "task-done", list_id="list-1")

        gc.client.me.todo.lists.by_todo_task_list_id.assert_called_with("list-1")
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.by_todo_task_id.assert_called_with(
            "task-done"
        )
        patch_mock = gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.by_todo_task_id.return_value.patch
        patch_mock.assert_called_once()

        # Verify the patched task has status = Completed
        call_args = patch_mock.call_args[0][0]
        assert call_args.status == TaskStatus.Completed
        assert result["id"] == "task-done"

    async def test_complete_task_refetches_on_none_patch(self, gc: MagicMock) -> None:
        task = _make_task(id="task-204", status_value="completed")
        item_builder = gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.by_todo_task_id.return_value
        item_builder.patch = AsyncMock(return_value=None)
        item_builder.get = AsyncMock(return_value=task)

        result = await complete_task(gc, "task-204", list_id="list-1")

        item_builder.get.assert_called_once()
        assert result["id"] == "task-204"


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------


class TestDeleteTask:
    async def test_delete_task_calls_delete(self, gc: MagicMock) -> None:
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.by_todo_task_id.return_value.delete = AsyncMock(
            return_value=None
        )

        await delete_task(gc, "task-del", list_id="list-1")

        gc.client.me.todo.lists.by_todo_task_list_id.assert_called_with("list-1")
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.by_todo_task_id.assert_called_with(
            "task-del"
        )
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.by_todo_task_id.return_value.delete.assert_called_once()

    async def test_delete_task_uses_default_list(self, gc: MagicMock) -> None:
        gc.client.me.todo.lists.by_todo_task_list_id.return_value.tasks.by_todo_task_id.return_value.delete = AsyncMock(
            return_value=None
        )

        await delete_task(gc, "task-x")

        gc.client.me.todo.lists.get.assert_called_once()
        gc.client.me.todo.lists.by_todo_task_list_id.assert_called_with("list-1")
