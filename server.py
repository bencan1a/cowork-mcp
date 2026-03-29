"""Outlook MCP Server.

Exposes Microsoft Graph API tools via FastMCP's Streamable HTTP transport.
Bearer auth is enforced on every request via BearerAuthMiddleware.
Tool groups are registered conditionally based on scope toggles in Settings.

Run with:
    uvicorn server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from config import Settings
from graph import calendar, contacts, mail, tasks
from graph.client import GraphClient, get_graph_client

if TYPE_CHECKING:
    from starlette.requests import Request

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bearer auth middleware
# ---------------------------------------------------------------------------


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Enforce Authorization: Bearer <api_key> on every request.

    Returns HTTP 401 for missing or invalid tokens.
    """

    def __init__(self, app: Any, api_key: str) -> None:
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        auth = request.headers.get("Authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {self._api_key}".encode()):
            logger.warning("Unauthorized request from %s", request.client.host)
            return Response("Unauthorized", status_code=401)
        return await call_next(request)


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

settings: Settings = Settings()
if not settings.mcp_api_key:
    raise RuntimeError("MCP_API_KEY must be set to a non-empty value in .env")
gc: GraphClient = get_graph_client(settings)

# Set log level from settings
logging.getLogger().setLevel(settings.log_level)

mcp: FastMCP = FastMCP(
    name="outlook-mcp",
    instructions="Provides read/write access to a personal Outlook account via Microsoft Graph API.",
)

registered_groups: list[str] = []
skipped_groups: list[str] = []

# ---------------------------------------------------------------------------
# Mail — read
# ---------------------------------------------------------------------------

if settings.scope_mail_read:

    @mcp.tool()
    async def list_emails(
        folder: str = "inbox",
        sender: str | None = None,
        subject: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        unread_only: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List emails with optional filters. Handles pagination transparently.

        Args:
            folder: Well-known folder name (e.g. "inbox", "sentitems") or folder ID.
            sender: Filter by sender email address.
            subject: Filter by subject substring.
            date_from: ISO 8601 lower bound for receivedDateTime.
            date_to: ISO 8601 upper bound for receivedDateTime.
            unread_only: If True, only return unread messages.
            limit: Maximum number of messages to return (default 20).
        """
        try:
            return await mail.list_emails(
                gc,
                folder=folder,
                sender=sender,
                subject=subject,
                date_from=date_from,
                date_to=date_to,
                unread_only=unread_only,
                limit=limit,
            )
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def get_email(message_id: str) -> dict[str, Any]:
        """Get full email by ID including body and attachment metadata.

        Args:
            message_id: The Graph message ID.
        """
        try:
            return await mail.get_email(gc, message_id)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def search_emails(query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Full-text search across mailbox using Graph $search.

        Args:
            query: KQL search string (e.g. "subject:meeting").
            limit: Maximum number of results to return (default 20).
        """
        try:
            return await mail.search_emails(gc, query, limit=limit)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    registered_groups.append("mail_read")
else:
    skipped_groups.append("mail_read")

# ---------------------------------------------------------------------------
# Mail — write
# ---------------------------------------------------------------------------

if settings.scope_mail_write:

    @mcp.tool()
    async def move_email(message_id: str, destination_folder_id: str) -> dict[str, Any]:
        """Move an email to a folder.

        Args:
            message_id: The Graph message ID to move.
            destination_folder_id: Target folder ID or well-known name.
        """
        try:
            return await mail.move_email(gc, message_id, destination_folder_id)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def delete_email(message_id: str) -> None:
        """Soft-delete an email to Deleted Items.

        Args:
            message_id: The Graph message ID to delete.
        """
        try:
            await mail.delete_email(gc, message_id)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def mark_email_read(message_id: str, is_read: bool = True) -> dict[str, Any]:
        """Mark an email as read or unread.

        Args:
            message_id: The Graph message ID.
            is_read: True to mark read, False to mark unread (default True).
        """
        try:
            return await mail.mark_email_read(gc, message_id, is_read=is_read)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def list_mail_folders() -> list[dict[str, Any]]:
        """List all top-level mail folders."""
        try:
            return await mail.list_mail_folders(gc)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def create_mail_folder(
        display_name: str, parent_folder_id: str | None = None
    ) -> dict[str, Any]:
        """Create a new mail folder.

        Args:
            display_name: Name for the new folder.
            parent_folder_id: Parent folder ID; if None, creates at root.
        """
        try:
            return await mail.create_mail_folder(
                gc, display_name, parent_folder_id=parent_folder_id
            )
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def get_mailbox_settings() -> dict[str, Any]:
        """Get mailbox settings including timezone and auto-reply status."""
        try:
            return await mail.get_mailbox_settings(gc)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def set_auto_reply(
        enabled: bool,
        message: str = "",
        start_datetime: str | None = None,
        end_datetime: str | None = None,
    ) -> dict[str, Any]:
        """Enable or disable out-of-office auto-reply.

        Args:
            enabled: True to enable auto-reply, False to disable.
            message: Auto-reply message text.
            start_datetime: ISO 8601 datetime for scheduled start (UTC).
            end_datetime: ISO 8601 datetime for scheduled end (UTC).
        """
        try:
            return await mail.set_auto_reply(
                gc,
                enabled=enabled,
                message=message,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    registered_groups.append("mail_write")
else:
    skipped_groups.append("mail_write")

# ---------------------------------------------------------------------------
# Mail — send
# ---------------------------------------------------------------------------

if settings.scope_mail_send:

    @mcp.tool()
    async def send_email(
        to: list[str],
        subject: str,
        body: str,
        body_type: str = "html",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> None:
        """Send a new email.

        Args:
            to: List of recipient email addresses.
            subject: Email subject line.
            body: Email body content.
            body_type: "html" or "text" (default "html").
            cc: Optional CC recipient addresses.
            bcc: Optional BCC recipient addresses.
        """
        try:
            await mail.send_email(
                gc,
                to=to,
                subject=subject,
                body=body,
                body_type=body_type,
                cc=cc,
                bcc=bcc,
            )
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def reply_to_email(message_id: str, body: str, reply_all: bool = False) -> None:
        """Reply or reply-all to an email.

        Args:
            message_id: The Graph message ID to reply to.
            body: Plain-text comment to include in the reply.
            reply_all: If True, reply to all recipients (default False).
        """
        try:
            await mail.reply_to_email(gc, message_id, body=body, reply_all=reply_all)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def forward_email(message_id: str, to: list[str], comment: str = "") -> None:
        """Forward an email to new recipients.

        Args:
            message_id: The Graph message ID to forward.
            to: List of recipient email addresses.
            comment: Optional comment to prepend to the forwarded message.
        """
        try:
            await mail.forward_email(gc, message_id, to=to, comment=comment)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    registered_groups.append("mail_send")
else:
    skipped_groups.append("mail_send")

# ---------------------------------------------------------------------------
# Calendar — read
# ---------------------------------------------------------------------------

if settings.scope_calendar_read:

    @mcp.tool()
    async def list_events(
        start_datetime: str,
        end_datetime: str,
        calendar_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return events within a date range (expands recurring events).

        Args:
            start_datetime: ISO 8601 start of range (UTC).
            end_datetime: ISO 8601 end of range (UTC).
            calendar_id: Calendar ID (reserved for future use; defaults to primary).
            limit: Maximum number of events to return (default 50).
        """
        try:
            return await calendar.list_events(
                gc,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                calendar_id=calendar_id,
                limit=limit,
            )
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def get_event(event_id: str) -> dict[str, Any]:
        """Fetch a single calendar event by ID.

        Args:
            event_id: The Graph event ID.
        """
        try:
            return await calendar.get_event(gc, event_id)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def search_events(query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search calendar events by subject/body using $search.

        Args:
            query: Search string.
            limit: Maximum number of results to return (default 20).
        """
        try:
            return await calendar.search_events(gc, query, limit=limit)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def list_calendars() -> list[dict[str, Any]]:
        """Return all calendars for the authenticated user."""
        try:
            return await calendar.list_calendars(gc)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def get_free_busy(
        start_datetime: str, end_datetime: str, schedules: list[str]
    ) -> dict[str, Any]:
        """Query free/busy availability for a list of email addresses.

        Args:
            start_datetime: ISO 8601 start of query window (UTC).
            end_datetime: ISO 8601 end of query window (UTC).
            schedules: List of email addresses to query.
        """
        try:
            return await calendar.get_free_busy(
                gc,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                schedules=schedules,
            )
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    registered_groups.append("calendar_read")
else:
    skipped_groups.append("calendar_read")

# ---------------------------------------------------------------------------
# Calendar — write
# ---------------------------------------------------------------------------

if settings.scope_calendar_write:

    @mcp.tool()
    async def create_event(
        subject: str,
        start: str,
        end: str,
        location: str | None = None,
        body: str | None = None,
        attendees: list[str] | None = None,
        is_online_meeting: bool = False,
        reminder_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Create a new calendar event.

        Args:
            subject: Event title.
            start: ISO 8601 start datetime (UTC).
            end: ISO 8601 end datetime (UTC).
            location: Optional location string.
            body: Optional event description.
            attendees: Optional list of attendee email addresses.
            is_online_meeting: If True, create an online meeting (default False).
            reminder_minutes: Minutes before event to trigger reminder.
        """
        try:
            return await calendar.create_event(
                gc,
                subject=subject,
                start=start,
                end=end,
                location=location,
                body=body,
                attendees=attendees,
                is_online_meeting=is_online_meeting,
                reminder_minutes=reminder_minutes,
            )
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def update_event(
        event_id: str,
        subject: str | None = None,
        start: str | None = None,
        end: str | None = None,
        location: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing calendar event.

        Args:
            event_id: The Graph event ID to update.
            subject: New event title (optional).
            start: New ISO 8601 start datetime (optional).
            end: New ISO 8601 end datetime (optional).
            location: New location string (optional).
            body: New event description (optional).
        """
        fields: dict[str, Any] = {}
        if subject is not None:
            fields["subject"] = subject
        if start is not None:
            fields["start"] = start
        if end is not None:
            fields["end"] = end
        if location is not None:
            fields["location"] = location
        if body is not None:
            fields["body"] = body
        try:
            return await calendar.update_event(gc, event_id, **fields)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def delete_event(event_id: str) -> None:
        """Delete a calendar event by ID.

        Args:
            event_id: The Graph event ID to delete.
        """
        try:
            await calendar.delete_event(gc, event_id)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def accept_event(event_id: str, comment: str = "") -> None:
        """Accept a meeting invitation.

        Args:
            event_id: The Graph event ID.
            comment: Optional response comment.
        """
        try:
            await calendar.accept_event(gc, event_id, comment=comment)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def decline_event(event_id: str, comment: str = "") -> None:
        """Decline a meeting invitation.

        Args:
            event_id: The Graph event ID.
            comment: Optional response comment.
        """
        try:
            await calendar.decline_event(gc, event_id, comment=comment)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def tentative_event(event_id: str, comment: str = "") -> None:
        """Tentatively accept a meeting invitation.

        Args:
            event_id: The Graph event ID.
            comment: Optional response comment.
        """
        try:
            await calendar.tentative_event(gc, event_id, comment=comment)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    registered_groups.append("calendar_write")
else:
    skipped_groups.append("calendar_write")

# ---------------------------------------------------------------------------
# Contacts — read
# ---------------------------------------------------------------------------

if settings.scope_contacts_read:

    @mcp.tool()
    async def list_contacts(search: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Return contacts, optionally filtered by a search query.

        Args:
            search: Optional search string to filter contacts.
            limit: Maximum number of contacts to return (default 50).
        """
        try:
            return await contacts.list_contacts(gc, search=search, limit=limit)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def get_contact(contact_id: str) -> dict[str, Any]:
        """Fetch a single contact by ID.

        Args:
            contact_id: The Graph contact ID.
        """
        try:
            return await contacts.get_contact(gc, contact_id)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    registered_groups.append("contacts_read")
else:
    skipped_groups.append("contacts_read")

# ---------------------------------------------------------------------------
# Contacts — write
# ---------------------------------------------------------------------------

if settings.scope_contacts_write:

    @mcp.tool()
    async def create_contact(**fields: Any) -> dict[str, Any]:
        """Create a new contact.

        Common fields: given_name, surname, display_name, email_addresses,
        mobile_phone, business_phones, company_name, job_title.
        """
        try:
            return await contacts.create_contact(gc, **fields)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def update_contact(contact_id: str, **fields: Any) -> dict[str, Any]:
        """Update an existing contact.

        Args:
            contact_id: The Graph contact ID to update.
            **fields: Contact fields to update.
        """
        try:
            return await contacts.update_contact(gc, contact_id, **fields)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    registered_groups.append("contacts_write")
else:
    skipped_groups.append("contacts_write")

# ---------------------------------------------------------------------------
# Tasks — read
# ---------------------------------------------------------------------------

if settings.scope_tasks_read:

    @mcp.tool()
    async def list_tasks(
        list_id: str | None = None, completed: bool | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return tasks from a todo list, optionally filtered by completion status.

        Args:
            list_id: Todo list ID; if None, uses the default list.
            completed: True for completed tasks only, False for incomplete only,
                       None for all tasks (default).
            limit: Maximum number of tasks to return (default 50).
        """
        try:
            return await tasks.list_tasks(gc, list_id=list_id, completed=completed, limit=limit)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    registered_groups.append("tasks_read")
else:
    skipped_groups.append("tasks_read")

# ---------------------------------------------------------------------------
# Tasks — write
# ---------------------------------------------------------------------------

if settings.scope_tasks_write:

    @mcp.tool()
    async def create_task(
        title: str,
        list_id: str | None = None,
        due_date: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Create a new task in the specified (or default) todo list.

        Args:
            title: Task title.
            list_id: Todo list ID; if None, uses the default list.
            due_date: ISO 8601 due date (UTC).
            notes: Optional task notes.
        """
        try:
            return await tasks.create_task(
                gc, title=title, list_id=list_id, due_date=due_date, notes=notes
            )
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def complete_task(task_id: str, list_id: str | None = None) -> dict[str, Any]:
        """Mark a task as completed.

        Args:
            task_id: The Graph task ID.
            list_id: Todo list ID; if None, uses the default list.
        """
        try:
            return await tasks.complete_task(gc, task_id, list_id=list_id)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    async def delete_task(task_id: str, list_id: str | None = None) -> None:
        """Delete a task by ID.

        Args:
            task_id: The Graph task ID.
            list_id: Todo list ID; if None, uses the default list.
        """
        try:
            await tasks.delete_task(gc, task_id, list_id=list_id)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    registered_groups.append("tasks_write")
else:
    skipped_groups.append("tasks_write")

# ---------------------------------------------------------------------------
# Startup logging
# ---------------------------------------------------------------------------

logger.info("Registered tool groups: %s", registered_groups)
if skipped_groups:
    logger.info("Skipped tool groups (scope disabled): %s", skipped_groups)

# ---------------------------------------------------------------------------
# ASGI app assembly — export for uvicorn server:app
# ---------------------------------------------------------------------------

app = mcp.http_app(
    transport="streamable-http",
    middleware=[Middleware(BearerAuthMiddleware, api_key=settings.mcp_api_key)],
)
