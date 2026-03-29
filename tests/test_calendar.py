from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graph.calendar import (
    accept_event,
    create_event,
    decline_event,
    delete_event,
    get_event,
    get_free_busy,
    list_calendars,
    list_events,
    search_events,
    tentative_event,
    update_event,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_event(
    *,
    id: str = "evt-1",
    subject: str = "Test Event",
    start_dt: str = "2025-06-01T10:00:00Z",
    end_dt: str = "2025-06-01T11:00:00Z",
) -> MagicMock:
    """Return a mock Event object."""
    event = MagicMock()
    event.id = id
    event.subject = subject
    event.start = MagicMock(date_time=start_dt)
    event.end = MagicMock(date_time=end_dt)
    event.location = MagicMock(display_name="Conference Room A")
    event.is_online_meeting = False
    event.online_meeting_url = None
    event.body = MagicMock(content="Event body")
    event.organizer = MagicMock(email_address=MagicMock(address="organizer@example.com"))
    event.attendees = []
    event.is_cancelled = False
    event.is_all_day = False
    return event


@pytest.fixture
def gc() -> MagicMock:
    """Return a mock GraphClient with nested async mock structure."""
    mock_gc = MagicMock()
    return mock_gc


# ---------------------------------------------------------------------------
# list_events
# ---------------------------------------------------------------------------


class TestListEvents:
    async def test_list_events_returns_list(self, gc: MagicMock) -> None:
        event = _make_event()
        response = MagicMock()
        response.value = [event]
        response.odata_next_link = None

        gc.client.me.calendar_view.get = AsyncMock(return_value=response)

        result = await list_events(
            gc, start_datetime="2025-06-01T00:00:00Z", end_datetime="2025-06-30T23:59:59Z"
        )

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == "evt-1"
        assert result[0]["subject"] == "Test Event"

    async def test_list_events_empty_response(self, gc: MagicMock) -> None:
        response = MagicMock()
        response.value = []
        response.odata_next_link = None

        gc.client.me.calendar_view.get = AsyncMock(return_value=response)

        result = await list_events(
            gc, start_datetime="2025-06-01T00:00:00Z", end_datetime="2025-06-30T23:59:59Z"
        )

        assert result == []

    async def test_list_events_none_response(self, gc: MagicMock) -> None:
        gc.client.me.calendar_view.get = AsyncMock(return_value=None)

        result = await list_events(
            gc, start_datetime="2025-06-01T00:00:00Z", end_datetime="2025-06-30T23:59:59Z"
        )

        assert result == []

    async def test_list_events_respects_limit(self, gc: MagicMock) -> None:
        events = [_make_event(id=f"evt-{i}", subject=f"Event {i}") for i in range(5)]
        response = MagicMock()
        response.value = events
        response.odata_next_link = None

        gc.client.me.calendar_view.get = AsyncMock(return_value=response)

        result = await list_events(
            gc,
            start_datetime="2025-06-01T00:00:00Z",
            end_datetime="2025-06-30T23:59:59Z",
            limit=3,
        )

        assert len(result) == 3


# ---------------------------------------------------------------------------
# get_event
# ---------------------------------------------------------------------------


class TestGetEvent:
    async def test_get_event_returns_dict(self, gc: MagicMock) -> None:
        event = _make_event(id="evt-42", subject="Meeting")
        gc.client.me.events.by_event_id.return_value.get = AsyncMock(return_value=event)

        result = await get_event(gc, "evt-42")

        assert result["id"] == "evt-42"
        assert result["subject"] == "Meeting"
        gc.client.me.events.by_event_id.assert_called_once_with("evt-42")

    async def test_get_event_raises_on_none(self, gc: MagicMock) -> None:
        gc.client.me.events.by_event_id.return_value.get = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError, match="not found"):
            await get_event(gc, "missing-id")


# ---------------------------------------------------------------------------
# search_events
# ---------------------------------------------------------------------------


class TestSearchEvents:
    async def test_search_events_returns_list(self, gc: MagicMock) -> None:
        event = _make_event(subject="Budget Review")
        response = MagicMock()
        response.value = [event]
        response.odata_next_link = None

        gc.client.me.events.get = AsyncMock(return_value=response)

        result = await search_events(gc, "Budget")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["subject"] == "Budget Review"


# ---------------------------------------------------------------------------
# create_event
# ---------------------------------------------------------------------------


class TestCreateEvent:
    async def test_create_event_calls_post(self, gc: MagicMock) -> None:
        event = _make_event(id="new-evt", subject="New Meeting")
        gc.client.me.events.post = AsyncMock(return_value=event)

        result = await create_event(
            gc,
            subject="New Meeting",
            start="2025-06-10T14:00:00Z",
            end="2025-06-10T15:00:00Z",
        )

        gc.client.me.events.post.assert_called_once()
        assert result["id"] == "new-evt"
        assert result["subject"] == "New Meeting"

    async def test_create_event_with_attendees(self, gc: MagicMock) -> None:
        event = _make_event(id="evt-a")
        gc.client.me.events.post = AsyncMock(return_value=event)

        result = await create_event(
            gc,
            subject="Team Sync",
            start="2025-06-10T14:00:00Z",
            end="2025-06-10T15:00:00Z",
            attendees=["alice@example.com", "bob@example.com"],
        )

        assert result["id"] == "evt-a"
        call_args = gc.client.me.events.post.call_args
        created_event = call_args[0][0]
        assert len(created_event.attendees) == 2

    async def test_create_event_raises_on_none_response(self, gc: MagicMock) -> None:
        gc.client.me.events.post = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError, match="no result"):
            await create_event(
                gc,
                subject="Bad Event",
                start="2025-06-10T14:00:00Z",
                end="2025-06-10T15:00:00Z",
            )


# ---------------------------------------------------------------------------
# update_event
# ---------------------------------------------------------------------------


class TestUpdateEvent:
    async def test_update_event_calls_patch(self, gc: MagicMock) -> None:
        event = _make_event(id="evt-upd", subject="Updated Subject")
        gc.client.me.events.by_event_id.return_value.patch = AsyncMock(return_value=event)

        result = await update_event(gc, "evt-upd", subject="Updated Subject")

        gc.client.me.events.by_event_id.assert_called_once_with("evt-upd")
        gc.client.me.events.by_event_id.return_value.patch.assert_called_once()
        assert result["subject"] == "Updated Subject"

    async def test_update_event_refetches_on_none_patch(self, gc: MagicMock) -> None:
        # PATCH returns None (204), should refetch
        event = _make_event(id="evt-upd")
        gc.client.me.events.by_event_id.return_value.patch = AsyncMock(return_value=None)
        gc.client.me.events.by_event_id.return_value.get = AsyncMock(return_value=event)

        result = await update_event(gc, "evt-upd", subject="New Title")

        assert result["id"] == "evt-upd"


# ---------------------------------------------------------------------------
# delete_event
# ---------------------------------------------------------------------------


class TestDeleteEvent:
    async def test_delete_event_calls_delete(self, gc: MagicMock) -> None:
        gc.client.me.events.by_event_id.return_value.delete = AsyncMock(return_value=None)

        await delete_event(gc, "evt-del")

        gc.client.me.events.by_event_id.assert_called_once_with("evt-del")
        gc.client.me.events.by_event_id.return_value.delete.assert_called_once()


# ---------------------------------------------------------------------------
# RSVP actions
# ---------------------------------------------------------------------------


class TestAcceptEvent:
    async def test_accept_event(self, gc: MagicMock) -> None:
        gc.client.me.events.by_event_id.return_value.accept.post = AsyncMock(return_value=None)

        await accept_event(gc, "evt-1", comment="Looking forward to it!")

        gc.client.me.events.by_event_id.assert_called_once_with("evt-1")
        gc.client.me.events.by_event_id.return_value.accept.post.assert_called_once()
        body = gc.client.me.events.by_event_id.return_value.accept.post.call_args[0][0]
        assert body.comment == "Looking forward to it!"
        assert body.send_response is True


class TestDeclineEvent:
    async def test_decline_event(self, gc: MagicMock) -> None:
        gc.client.me.events.by_event_id.return_value.decline.post = AsyncMock(return_value=None)

        await decline_event(gc, "evt-1", comment="Can't make it")

        gc.client.me.events.by_event_id.return_value.decline.post.assert_called_once()
        body = gc.client.me.events.by_event_id.return_value.decline.post.call_args[0][0]
        assert body.comment == "Can't make it"


class TestTentativeEvent:
    async def test_tentative_event(self, gc: MagicMock) -> None:
        gc.client.me.events.by_event_id.return_value.tentatively_accept.post = AsyncMock(
            return_value=None
        )

        await tentative_event(gc, "evt-1")

        gc.client.me.events.by_event_id.return_value.tentatively_accept.post.assert_called_once()


# ---------------------------------------------------------------------------
# list_calendars
# ---------------------------------------------------------------------------


class TestListCalendars:
    async def test_list_calendars_returns_list(self, gc: MagicMock) -> None:
        cal = MagicMock()
        cal.id = "cal-1"
        cal.name = "My Calendar"
        cal.display_name = "My Calendar"
        cal.is_default_calendar = True
        cal.can_edit = True

        response = MagicMock()
        response.value = [cal]
        gc.client.me.calendars.get = AsyncMock(return_value=response)

        result = await list_calendars(gc)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == "cal-1"
        assert result[0]["is_default"] is True


# ---------------------------------------------------------------------------
# get_free_busy
# ---------------------------------------------------------------------------


class TestGetFreeBusy:
    async def test_get_free_busy_returns_dict(self, gc: MagicMock) -> None:
        schedule_item = MagicMock()
        schedule_item.schedule_id = "alice@example.com"
        schedule_item.availability_view = "0002"
        schedule_item.schedule_items = []

        response = MagicMock()
        response.value = [schedule_item]
        gc.client.me.calendar.get_schedule.post = AsyncMock(return_value=response)

        result = await get_free_busy(
            gc,
            start_datetime="2025-06-01T08:00:00Z",
            end_datetime="2025-06-01T18:00:00Z",
            schedules=["alice@example.com"],
        )

        assert isinstance(result, dict)
        assert "schedules" in result
        assert len(result["schedules"]) == 1
        assert result["schedules"][0]["schedule_id"] == "alice@example.com"
