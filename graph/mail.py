from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.automatic_replies_setting import AutomaticRepliesSetting
from msgraph.generated.models.automatic_replies_status import AutomaticRepliesStatus
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.date_time_time_zone import DateTimeTimeZone
from msgraph.generated.models.email_address import EmailAddress
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.models.mail_folder import MailFolder
from msgraph.generated.models.mailbox_settings import MailboxSettings
from msgraph.generated.models.message import Message
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.recipient import Recipient
from msgraph.generated.users.item.mail_folders.mail_folders_request_builder import (
    MailFoldersRequestBuilder,
)
from msgraph.generated.users.item.messages.item.forward.forward_post_request_body import (
    ForwardPostRequestBody,
)
from msgraph.generated.users.item.messages.item.move.move_post_request_body import (
    MovePostRequestBody,
)
from msgraph.generated.users.item.messages.item.reply.reply_post_request_body import (
    ReplyPostRequestBody,
)
from msgraph.generated.users.item.messages.item.reply_all.reply_all_post_request_body import (
    ReplyAllPostRequestBody,
)
from msgraph.generated.users.item.mail_folders.item.messages.messages_request_builder import (
    MessagesRequestBuilder as FolderMessagesRequestBuilder,
)
from msgraph.generated.users.item.messages.messages_request_builder import (
    MessagesRequestBuilder,
)
from msgraph.generated.users.item.send_mail.send_mail_post_request_body import (
    SendMailPostRequestBody,
)

if TYPE_CHECKING:
    from graph.client import GraphClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _message_to_dict(msg: Any) -> dict[str, Any]:
    """Convert a Graph SDK Message object to a plain dict."""
    from_address: str | None = None
    if msg.from_ and msg.from_.email_address:
        from_address = msg.from_.email_address.address

    to_addresses: list[str] = []
    for r in msg.to_recipients or []:
        if r.email_address and r.email_address.address:
            to_addresses.append(r.email_address.address)

    received: str | None = None
    if msg.received_date_time:
        received = msg.received_date_time.isoformat()

    return {
        "id": msg.id,
        "subject": msg.subject,
        "from": from_address,
        "to": to_addresses,
        "received": received,
        "is_read": msg.is_read,
        "body_preview": msg.body_preview,
        "body": msg.body.content if msg.body else None,
        "importance": str(msg.importance) if msg.importance else None,
        "has_attachments": msg.has_attachments,
    }


def _folder_to_dict(folder: Any) -> dict[str, Any]:
    """Convert a Graph SDK MailFolder object to a plain dict."""
    return {
        "id": folder.id,
        "display_name": folder.display_name,
        "parent_folder_id": folder.parent_folder_id,
        "child_folder_count": folder.child_folder_count,
        "total_item_count": folder.total_item_count,
        "unread_item_count": folder.unread_item_count,
        "is_hidden": folder.is_hidden,
    }


def _make_recipient(address: str) -> Recipient:
    ea = EmailAddress()
    ea.address = address
    r = Recipient()
    r.email_address = ea
    return r


def _wrap_odata_error(exc: ODataError) -> RuntimeError:
    code = exc.error.code if exc.error else "unknown"
    msg = exc.error.message if exc.error else str(exc)
    return RuntimeError(f"Graph API error {code}: {msg}")


# ---------------------------------------------------------------------------
# Mail listing / retrieval
# ---------------------------------------------------------------------------


async def list_emails(
    gc: GraphClient,
    *,
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
        gc: Authenticated GraphClient.
        folder: Well-known folder name (e.g. "inbox", "sentitems") or folder ID.
        sender: Filter by sender email address.
        subject: Filter by subject substring (OData contains).
        date_from: ISO 8601 lower bound for receivedDateTime.
        date_to: ISO 8601 upper bound for receivedDateTime.
        unread_only: If True, only return unread messages.
        limit: Maximum number of messages to return.

    Returns:
        List of message dicts.
    """
    filters: list[str] = []
    if sender:
        filters.append(f"from/emailAddress/address eq '{sender}'")
    if unread_only:
        filters.append("isRead eq false")
    if date_from:
        filters.append(f"receivedDateTime ge {date_from}")
    if date_to:
        filters.append(f"receivedDateTime le {date_to}")
    if subject:
        filters.append(f"contains(subject, '{subject}')")
    filter_str = " and ".join(filters) if filters else None

    page_size = min(limit, 50)

    try:
        # Resolve folder — use mailFolders/{folder}/messages for named folders
        messages_builder = gc.client.me.mail_folders.by_mail_folder_id(folder).messages

        query_params = FolderMessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
            top=page_size,
            filter=filter_str,
            orderby=["receivedDateTime DESC"],
            select=[
                "id",
                "subject",
                "from",
                "toRecipients",
                "receivedDateTime",
                "isRead",
                "bodyPreview",
                "body",
                "importance",
                "hasAttachments",
            ],
        )
        request_config = RequestConfiguration(query_parameters=query_params)

        results: list[dict[str, Any]] = []
        response = await messages_builder.get(request_configuration=request_config)
        while response and response.value:
            for msg in response.value:
                results.append(_message_to_dict(msg))
                if len(results) >= limit:
                    return results
            if response.odata_next_link:
                response = await messages_builder.with_url(response.odata_next_link).get()
            else:
                break
        return results
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


async def get_email(gc: GraphClient, message_id: str) -> dict[str, Any]:
    """Get full email by ID including body and attachment metadata.

    Args:
        gc: Authenticated GraphClient.
        message_id: The Graph message ID.

    Returns:
        Message dict with full body content.
    """
    try:
        msg = await gc.client.me.messages.by_message_id(message_id).get()
        if msg is None:
            raise RuntimeError(f"Message {message_id!r} not found")
        result = _message_to_dict(msg)
        # Include attachment list (metadata only, not content)
        if msg.has_attachments:
            att_response = await gc.client.me.messages.by_message_id(message_id).attachments.get()
            attachments: list[dict[str, Any]] = []
            for att in (att_response.value if att_response is not None else []) or []:
                attachments.append(
                    {
                        "id": att.id,
                        "name": att.name,
                        "content_type": att.content_type,
                        "size": att.size,
                    }
                )
            result["attachments"] = attachments
        return result
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


async def search_emails(gc: GraphClient, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Full-text search across mailbox using Graph $search.

    Args:
        gc: Authenticated GraphClient.
        query: KQL search string (e.g. "subject:meeting").
        limit: Maximum number of results to return.

    Returns:
        List of matching message dicts.
    """
    page_size = min(limit, 50)
    try:
        query_params = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
            search=f'"{query}"',
            top=page_size,
            select=[
                "id",
                "subject",
                "from",
                "toRecipients",
                "receivedDateTime",
                "isRead",
                "bodyPreview",
                "importance",
                "hasAttachments",
            ],
        )
        request_config = RequestConfiguration(query_parameters=query_params)
        results: list[dict[str, Any]] = []
        response = await gc.client.me.messages.get(request_configuration=request_config)
        while response and response.value:
            for msg in response.value:
                results.append(_message_to_dict(msg))
                if len(results) >= limit:
                    return results
            if response.odata_next_link:
                response = await gc.client.me.messages.with_url(response.odata_next_link).get()
            else:
                break
        return results
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


# ---------------------------------------------------------------------------
# Sending / replying / forwarding
# ---------------------------------------------------------------------------


async def send_email(
    gc: GraphClient,
    *,
    to: list[str],
    subject: str,
    body: str,
    body_type: str = "html",
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> None:
    """Send a new email.

    Args:
        gc: Authenticated GraphClient.
        to: List of recipient email addresses.
        subject: Email subject line.
        body: Email body content.
        body_type: "html" or "text" (default "html").
        cc: Optional CC recipient addresses.
        bcc: Optional BCC recipient addresses.
    """
    msg = Message()
    msg.subject = subject
    msg.to_recipients = [_make_recipient(addr) for addr in to]

    item_body = ItemBody()
    item_body.content = body
    item_body.content_type = BodyType.Html if body_type.lower() == "html" else BodyType.Text
    msg.body = item_body

    if cc:
        msg.cc_recipients = [_make_recipient(addr) for addr in cc]
    if bcc:
        msg.bcc_recipients = [_make_recipient(addr) for addr in bcc]

    req_body = SendMailPostRequestBody()
    req_body.message = msg
    req_body.save_to_sent_items = True

    try:
        await gc.client.me.send_mail.post(req_body)
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


async def reply_to_email(
    gc: GraphClient,
    message_id: str,
    *,
    body: str,
    reply_all: bool = False,
) -> None:
    """Reply or reply-all to an email.

    Args:
        gc: Authenticated GraphClient.
        message_id: The Graph message ID to reply to.
        body: Plain-text comment to include in the reply.
        reply_all: If True, reply to all recipients.
    """
    msg_builder = gc.client.me.messages.by_message_id(message_id)
    try:
        if reply_all:
            req_body = ReplyAllPostRequestBody()
            req_body.comment = body
            await msg_builder.reply_all.post(req_body)
        else:
            req_body_reply = ReplyPostRequestBody()
            req_body_reply.comment = body
            await msg_builder.reply.post(req_body_reply)
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


async def forward_email(
    gc: GraphClient,
    message_id: str,
    *,
    to: list[str],
    comment: str = "",
) -> None:
    """Forward email to new recipients.

    Args:
        gc: Authenticated GraphClient.
        message_id: The Graph message ID to forward.
        to: List of recipient email addresses.
        comment: Optional comment to prepend to the forwarded message.
    """
    req_body = ForwardPostRequestBody()
    req_body.to_recipients = [_make_recipient(addr) for addr in to]
    req_body.comment = comment
    try:
        await gc.client.me.messages.by_message_id(message_id).forward.post(req_body)
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


# ---------------------------------------------------------------------------
# Move / delete / mark
# ---------------------------------------------------------------------------


async def move_email(
    gc: GraphClient, message_id: str, destination_folder_id: str
) -> dict[str, Any]:
    """Move email to a folder.

    Args:
        gc: Authenticated GraphClient.
        message_id: The Graph message ID to move.
        destination_folder_id: Target folder ID or well-known name.

    Returns:
        Updated message dict reflecting new location.
    """
    req_body = MovePostRequestBody()
    req_body.destination_id = destination_folder_id
    try:
        moved = await gc.client.me.messages.by_message_id(message_id).move.post(req_body)
        if moved is None:
            raise RuntimeError(f"Move returned no message for {message_id!r}")
        return _message_to_dict(moved)
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


async def delete_email(gc: GraphClient, message_id: str) -> None:
    """Soft-delete email to Deleted Items.

    Args:
        gc: Authenticated GraphClient.
        message_id: The Graph message ID to delete.
    """
    try:
        await gc.client.me.messages.by_message_id(message_id).delete()
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


async def mark_email_read(
    gc: GraphClient, message_id: str, *, is_read: bool = True
) -> dict[str, Any]:
    """Mark email as read or unread.

    Args:
        gc: Authenticated GraphClient.
        message_id: The Graph message ID.
        is_read: True to mark read, False to mark unread.

    Returns:
        Updated message dict.
    """
    patch_msg = Message()
    patch_msg.is_read = is_read
    try:
        updated = await gc.client.me.messages.by_message_id(message_id).patch(patch_msg)
        if updated is None:
            raise RuntimeError(f"Patch returned no message for {message_id!r}")
        return _message_to_dict(updated)
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


async def list_mail_folders(gc: GraphClient) -> list[dict[str, Any]]:
    """List all top-level mail folders.

    Returns:
        List of folder dicts.
    """
    try:
        query_params = MailFoldersRequestBuilder.MailFoldersRequestBuilderGetQueryParameters(
            top=50,
        )
        request_config = MailFoldersRequestBuilder.MailFoldersRequestBuilderGetRequestConfiguration(
            query_parameters=query_params,
        )
        results: list[dict[str, Any]] = []
        response = await gc.client.me.mail_folders.get(request_configuration=request_config)
        while response and response.value:
            for folder in response.value:
                results.append(_folder_to_dict(folder))
            if response.odata_next_link:
                response = await gc.client.me.mail_folders.with_url(response.odata_next_link).get()
            else:
                break
        return results
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


async def create_mail_folder(
    gc: GraphClient,
    display_name: str,
    *,
    parent_folder_id: str | None = None,
) -> dict[str, Any]:
    """Create a new mail folder.

    Args:
        gc: Authenticated GraphClient.
        display_name: Name for the new folder.
        parent_folder_id: Parent folder ID; if None, creates at root.

    Returns:
        Created folder dict.
    """
    new_folder = MailFolder()
    new_folder.display_name = display_name
    try:
        if parent_folder_id:
            created = await gc.client.me.mail_folders.by_mail_folder_id(
                parent_folder_id
            ).child_folders.post(new_folder)
        else:
            created = await gc.client.me.mail_folders.post(new_folder)
        if created is None:
            raise RuntimeError(f"Create folder returned None for {display_name!r}")
        return _folder_to_dict(created)
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


# ---------------------------------------------------------------------------
# Mailbox settings / auto-reply
# ---------------------------------------------------------------------------


async def get_mailbox_settings(gc: GraphClient) -> dict[str, Any]:
    """Get mailbox settings including timezone and auto-reply status.

    Returns:
        Dict of mailbox settings.
    """
    try:
        settings = await gc.client.me.mailbox_settings.get()
        if settings is None:
            raise RuntimeError("Mailbox settings returned None")
        result: dict[str, Any] = {
            "time_zone": settings.time_zone,
            "date_format": settings.date_format,
            "time_format": settings.time_format,
            "language": None,
            "automatic_replies": None,
        }
        if settings.language:
            result["language"] = {
                "locale": settings.language.locale,
                "display_name": settings.language.display_name,
            }
        if settings.automatic_replies_setting:
            ars = settings.automatic_replies_setting
            result["automatic_replies"] = {
                "status": str(ars.status) if ars.status else None,
                "external_audience": str(ars.external_audience) if ars.external_audience else None,
                "internal_reply_message": ars.internal_reply_message,
                "external_reply_message": ars.external_reply_message,
                "scheduled_start_date_time": (
                    ars.scheduled_start_date_time.date_time
                    if ars.scheduled_start_date_time
                    else None
                ),
                "scheduled_end_date_time": (
                    ars.scheduled_end_date_time.date_time if ars.scheduled_end_date_time else None
                ),
            }
        return result
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc


async def set_auto_reply(
    gc: GraphClient,
    *,
    enabled: bool,
    message: str = "",
    start_datetime: str | None = None,
    end_datetime: str | None = None,
) -> dict[str, Any]:
    """Enable or disable out-of-office auto-reply.

    Args:
        gc: Authenticated GraphClient.
        enabled: True to enable auto-reply, False to disable.
        message: Auto-reply message text (used for both internal and external).
        start_datetime: ISO 8601 datetime for scheduled start (UTC).
        end_datetime: ISO 8601 datetime for scheduled end (UTC).

    Returns:
        Updated mailbox settings dict.
    """
    ars = AutomaticRepliesSetting()
    if not enabled:
        ars.status = AutomaticRepliesStatus.Disabled
    elif start_datetime and end_datetime:
        ars.status = AutomaticRepliesStatus.Scheduled
        start_dt = DateTimeTimeZone()
        start_dt.date_time = start_datetime
        start_dt.time_zone = "UTC"
        ars.scheduled_start_date_time = start_dt

        end_dt = DateTimeTimeZone()
        end_dt.date_time = end_datetime
        end_dt.time_zone = "UTC"
        ars.scheduled_end_date_time = end_dt
    else:
        ars.status = AutomaticRepliesStatus.AlwaysEnabled

    if message:
        ars.internal_reply_message = message
        ars.external_reply_message = message

    patch_settings = MailboxSettings()
    patch_settings.automatic_replies_setting = ars

    try:
        updated = await gc.client.me.mailbox_settings.patch(patch_settings)
        if updated is None:
            raise RuntimeError("Mailbox settings patch returned None")
        result: dict[str, Any] = {
            "time_zone": updated.time_zone,
            "automatic_replies": None,
        }
        if updated.automatic_replies_setting:
            u_ars = updated.automatic_replies_setting
            result["automatic_replies"] = {
                "status": str(u_ars.status) if u_ars.status else None,
                "internal_reply_message": u_ars.internal_reply_message,
                "external_reply_message": u_ars.external_reply_message,
            }
        return result
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc
