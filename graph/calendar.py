from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.attendee import Attendee
from msgraph.generated.models.attendee_type import AttendeeType
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.date_time_time_zone import DateTimeTimeZone
from msgraph.generated.models.email_address import EmailAddress
from msgraph.generated.models.event import Event
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.models.location import Location
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.users.item.calendar.get_schedule.get_schedule_post_request_body import (
    GetSchedulePostRequestBody,
)
from msgraph.generated.users.item.calendar_view.calendar_view_request_builder import (
    CalendarViewRequestBuilder,
)
from msgraph.generated.users.item.events.item.accept.accept_post_request_body import (
    AcceptPostRequestBody,
)
from msgraph.generated.users.item.events.item.decline.decline_post_request_body import (
    DeclinePostRequestBody,
)
from msgraph.generated.users.item.events.item.tentatively_accept.tentatively_accept_post_request_body import (
    TentativelyAcceptPostRequestBody,
)

from graph.errors import MAX_PAGES, clamp_limit, validate_graph_id, wrap_odata_error

if TYPE_CHECKING:
    from graph.client import GraphClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Convert a Graph SDK Event object to a plain dict."""
    return {
        "id": event.id,
        "subject": event.subject,
        "start": event.start.date_time if event.start else None,
        "end": event.end.date_time if event.end else None,
        "location": event.location.display_name if event.location else None,
        "is_online_meeting": event.is_online_meeting,
        "online_meeting_url": event.online_meeting_url,
        "body": event.body.content if event.body else None,
        "organizer": (
            event.organizer.email_address.address
            if event.organizer and event.organizer.email_address
            else None
        ),
        "attendees": [
            {
                "email": a.email_address.address if a.email_address else None,
                "status": str(a.status.response) if a.status else None,
            }
            for a in (event.attendees or [])
        ],
        "is_cancelled": event.is_cancelled,
        "is_all_day": event.is_all_day,
    }


# ---------------------------------------------------------------------------
# Calendar listing
# ---------------------------------------------------------------------------


async def list_calendars(gc: GraphClient) -> list[dict[str, Any]]:
    """Return all calendars for the authenticated user."""
    try:
        result = await gc.client.me.calendars.get()
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc

    if result is None or result.value is None:
        return []

    return [
        {
            "id": cal.id,
            "name": cal.name if hasattr(cal, "name") else None,
            "display_name": getattr(cal, "display_name", None),
            "is_default": cal.is_default_calendar,
            "can_edit": cal.can_edit,
        }
        for cal in result.value
    ]


# ---------------------------------------------------------------------------
# Event listing (calendar view)
# ---------------------------------------------------------------------------


async def list_events(
    gc: GraphClient,
    *,
    start_datetime: str,
    end_datetime: str,
    calendar_id: str | None = None,  # noqa: ARG001 — reserved for future calendar-specific filtering
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return events within a date range using the calendarView endpoint.

    Uses ``/me/calendarView`` which expands recurring events — preferred over
    ``/me/events`` for time-range queries.
    """
    limit = clamp_limit(limit)
    query_params = CalendarViewRequestBuilder.CalendarViewRequestBuilderGetQueryParameters(
        start_date_time=start_datetime,
        end_date_time=end_datetime,
        top=limit,
    )
    request_config = RequestConfiguration(query_parameters=query_params)

    try:
        result = await gc.client.me.calendar_view.get(request_configuration=request_config)
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc

    if result is None or result.value is None:
        return []

    events = list(result.value)

    # Handle pagination via @odata.nextLink
    pages = 1
    while result is not None and result.odata_next_link and len(events) < limit:
        if pages >= MAX_PAGES:
            logger.warning("list_events: hit MAX_PAGES (%d) safety cap", MAX_PAGES)
            break
        try:
            result = await gc.client.me.calendar_view.with_url(result.odata_next_link).get()
        except ODataError as exc:
            raise wrap_odata_error(exc) from exc
        if result and result.value:
            events.extend(result.value)
        pages += 1

    return [_event_to_dict(e) for e in events[:limit]]


# ---------------------------------------------------------------------------
# Single event
# ---------------------------------------------------------------------------


async def get_event(gc: GraphClient, event_id: str) -> dict[str, Any]:
    """Fetch a single event by ID."""
    validate_graph_id(event_id, "event_id")
    try:
        event = await gc.client.me.events.by_event_id(event_id).get()
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc

    if event is None:
        raise RuntimeError(f"Event {event_id!r} not found")
    return _event_to_dict(event)


# ---------------------------------------------------------------------------
# Search events
# ---------------------------------------------------------------------------


async def search_events(gc: GraphClient, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search events by subject/body using ``$search``."""
    limit = clamp_limit(limit)

    from msgraph.generated.users.item.events.events_request_builder import (  # noqa: PLC0415
        EventsRequestBuilder,
    )

    query_params = EventsRequestBuilder.EventsRequestBuilderGetQueryParameters(
        search=query,
        top=limit,
    )
    request_config = RequestConfiguration(query_parameters=query_params)

    try:
        result = await gc.client.me.events.get(request_configuration=request_config)
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc

    if result is None or result.value is None:
        return []

    events = list(result.value)

    # Handle pagination via @odata.nextLink
    pages = 1
    while result is not None and result.odata_next_link and len(events) < limit:
        if pages >= MAX_PAGES:
            logger.warning("search_events: hit MAX_PAGES (%d) safety cap", MAX_PAGES)
            break
        try:
            result = await gc.client.me.events.with_url(result.odata_next_link).get()
        except ODataError as exc:
            raise wrap_odata_error(exc) from exc
        if result and result.value:
            events.extend(result.value)
        pages += 1

    return [_event_to_dict(e) for e in events[:limit]]


# ---------------------------------------------------------------------------
# Create event
# ---------------------------------------------------------------------------


async def create_event(
    gc: GraphClient,
    *,
    subject: str,
    start: str,
    end: str,
    location: str | None = None,
    body: str | None = None,
    attendees: list[str] | None = None,
    is_online_meeting: bool = False,
    reminder_minutes: int | None = None,
) -> dict[str, Any]:
    """Create a new calendar event."""
    event = Event()
    event.subject = subject
    event.is_online_meeting = is_online_meeting

    start_dt = DateTimeTimeZone()
    start_dt.date_time = start
    start_dt.time_zone = "UTC"
    event.start = start_dt

    end_dt = DateTimeTimeZone()
    end_dt.date_time = end
    end_dt.time_zone = "UTC"
    event.end = end_dt

    if location:
        loc = Location()
        loc.display_name = location
        event.location = loc

    if body:
        item_body = ItemBody()
        item_body.content = body
        item_body.content_type = BodyType.Text
        event.body = item_body

    if attendees:
        attendee_list: list[Attendee] = []
        for email_addr in attendees:
            att = Attendee()
            att.type = AttendeeType.Required
            ea = EmailAddress()
            ea.address = email_addr
            att.email_address = ea
            attendee_list.append(att)
        event.attendees = attendee_list

    if reminder_minutes is not None:
        event.is_reminder_on = True
        event.reminder_minutes_before_start = reminder_minutes

    try:
        created = await gc.client.me.events.post(event)
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc

    if created is None:
        raise RuntimeError("Event creation returned no result")
    return _event_to_dict(created)


# ---------------------------------------------------------------------------
# Update event
# ---------------------------------------------------------------------------


async def update_event(gc: GraphClient, event_id: str, **fields: Any) -> dict[str, Any]:
    """Update an existing event. Pass keyword args matching Graph Event fields."""
    validate_graph_id(event_id, "event_id")
    event = Event()

    for key, value in fields.items():
        if hasattr(event, key):
            setattr(event, key, value)
        else:
            logger.warning("Unknown Event field %r — skipping", key)

    try:
        updated = await gc.client.me.events.by_event_id(event_id).patch(event)
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc

    if updated is None:
        # PATCH may return 204 No Content — re-fetch to return the updated state
        return await get_event(gc, event_id)
    return _event_to_dict(updated)


# ---------------------------------------------------------------------------
# Delete event
# ---------------------------------------------------------------------------


async def delete_event(gc: GraphClient, event_id: str) -> None:
    """Delete an event by ID."""
    validate_graph_id(event_id, "event_id")
    try:
        await gc.client.me.events.by_event_id(event_id).delete()
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc


# ---------------------------------------------------------------------------
# RSVP actions
# ---------------------------------------------------------------------------


async def accept_event(gc: GraphClient, event_id: str, comment: str = "") -> None:
    """Accept a meeting invitation."""
    validate_graph_id(event_id, "event_id")
    body = AcceptPostRequestBody()
    body.send_response = True
    body.comment = comment

    try:
        await gc.client.me.events.by_event_id(event_id).accept.post(body)
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc


async def decline_event(gc: GraphClient, event_id: str, comment: str = "") -> None:
    """Decline a meeting invitation."""
    validate_graph_id(event_id, "event_id")
    body = DeclinePostRequestBody()
    body.send_response = True
    body.comment = comment

    try:
        await gc.client.me.events.by_event_id(event_id).decline.post(body)
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc


async def tentative_event(gc: GraphClient, event_id: str, comment: str = "") -> None:
    """Tentatively accept a meeting invitation."""
    validate_graph_id(event_id, "event_id")
    body = TentativelyAcceptPostRequestBody()
    body.send_response = True
    body.comment = comment

    try:
        await gc.client.me.events.by_event_id(event_id).tentatively_accept.post(body)
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc


# ---------------------------------------------------------------------------
# Free/busy schedule
# ---------------------------------------------------------------------------


async def get_free_busy(
    gc: GraphClient,
    *,
    start_datetime: str,
    end_datetime: str,
    schedules: list[str],
) -> dict[str, Any]:
    """Query free/busy availability for a list of email addresses."""
    request_body = GetSchedulePostRequestBody()
    request_body.schedules = schedules

    start_dt = DateTimeTimeZone()
    start_dt.date_time = start_datetime
    start_dt.time_zone = "UTC"
    request_body.start_time = start_dt

    end_dt = DateTimeTimeZone()
    end_dt.date_time = end_datetime
    end_dt.time_zone = "UTC"
    request_body.end_time = end_dt

    try:
        result = await gc.client.me.calendar.get_schedule.post(request_body)
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc

    if result is None or result.value is None:
        return {"schedules": []}

    return {
        "schedules": [
            {
                "schedule_id": item.schedule_id,
                "availability_view": item.availability_view,
                "schedule_items": [
                    {
                        "status": str(si.status) if si.status else None,
                        "start": si.start.date_time if si.start else None,
                        "end": si.end.date_time if si.end else None,
                    }
                    for si in (item.schedule_items or [])
                ],
            }
            for item in result.value
        ]
    }
