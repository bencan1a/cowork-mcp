from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from msgraph.generated.models.automatic_replies_status import AutomaticRepliesStatus
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.o_data_errors.main_error import MainError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from graph.client import GraphClient
from graph.mail import (
    create_mail_folder,
    delete_email,
    forward_email,
    get_email,
    get_mailbox_settings,
    list_emails,
    list_mail_folders,
    mark_email_read,
    move_email,
    reply_to_email,
    search_emails,
    send_email,
    set_auto_reply,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_message(
    msg_id: str = "msg1",
    subject: str = "Test Subject",
    is_read: bool = False,
) -> MagicMock:
    msg = MagicMock()
    msg.id = msg_id
    msg.subject = subject
    msg.is_read = is_read
    msg.body_preview = "preview text"
    msg.has_attachments = False
    msg.received_date_time = None
    msg.from_ = None
    msg.to_recipients = []
    msg.body = None
    msg.importance = None
    return msg


def _make_page(messages: list[Any], next_link: str | None = None) -> MagicMock:
    page = MagicMock()
    page.value = messages
    page.odata_next_link = next_link
    return page


def _make_mock_folder(
    folder_id: str = "folder1",
    display_name: str = "Test Folder",
) -> MagicMock:
    folder = MagicMock()
    folder.id = folder_id
    folder.display_name = display_name
    folder.parent_folder_id = None
    folder.child_folder_count = 0
    folder.total_item_count = 5
    folder.unread_item_count = 2
    folder.is_hidden = False
    return folder


@pytest.fixture
def mock_gc() -> MagicMock:
    gc = MagicMock(spec=GraphClient)
    gc.client = MagicMock()
    return gc


# ---------------------------------------------------------------------------
# list_emails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_emails_returns_messages(mock_gc: MagicMock) -> None:
    msg = _make_mock_message()
    page = _make_page([msg])
    # list_emails routes through mail_folders
    mock_gc.client.me.mail_folders.by_mail_folder_id.return_value.messages.get = AsyncMock(
        return_value=page
    )
    result = await list_emails(mock_gc, folder="inbox", limit=10)
    assert len(result) == 1
    assert result[0]["id"] == "msg1"
    assert result[0]["subject"] == "Test Subject"
    assert result[0]["is_read"] is False


@pytest.mark.asyncio
async def test_list_emails_respects_limit(mock_gc: MagicMock) -> None:
    messages = [_make_mock_message(msg_id=f"msg{i}") for i in range(5)]
    page = _make_page(messages)
    mock_gc.client.me.mail_folders.by_mail_folder_id.return_value.messages.get = AsyncMock(
        return_value=page
    )
    result = await list_emails(mock_gc, folder="inbox", limit=3)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_list_emails_follows_pagination(mock_gc: MagicMock) -> None:
    msg1 = _make_mock_message(msg_id="msg1")
    msg2 = _make_mock_message(msg_id="msg2")
    page1 = _make_page([msg1], next_link="https://graph.microsoft.com/v1.0/nextpage")
    page2 = _make_page([msg2])

    messages_builder = MagicMock()
    messages_builder.get = AsyncMock(return_value=page1)
    messages_builder.with_url.return_value.get = AsyncMock(return_value=page2)
    mock_gc.client.me.mail_folders.by_mail_folder_id.return_value.messages = messages_builder

    result = await list_emails(mock_gc, folder="inbox", limit=10)
    assert len(result) == 2
    assert result[0]["id"] == "msg1"
    assert result[1]["id"] == "msg2"


@pytest.mark.asyncio
async def test_list_emails_empty_folder(mock_gc: MagicMock) -> None:
    page = _make_page([])
    mock_gc.client.me.mail_folders.by_mail_folder_id.return_value.messages.get = AsyncMock(
        return_value=page
    )
    result = await list_emails(mock_gc, folder="inbox")
    assert result == []


@pytest.mark.asyncio
async def test_list_emails_odata_error(mock_gc: MagicMock) -> None:
    err = ODataError()
    main = MainError()
    main.code = "Forbidden"
    main.message = "Access denied"
    err.error = main

    mock_gc.client.me.mail_folders.by_mail_folder_id.return_value.messages.get = AsyncMock(
        side_effect=err
    )
    with pytest.raises(RuntimeError, match="Graph API error Forbidden"):
        await list_emails(mock_gc, folder="inbox")


# ---------------------------------------------------------------------------
# get_email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_email_returns_message(mock_gc: MagicMock) -> None:
    msg = _make_mock_message(msg_id="abc123", subject="Hello")
    mock_gc.client.me.messages.by_message_id.return_value.get = AsyncMock(return_value=msg)
    result = await get_email(mock_gc, "abc123")
    assert result["id"] == "abc123"
    assert result["subject"] == "Hello"
    mock_gc.client.me.messages.by_message_id.assert_called_with("abc123")


@pytest.mark.asyncio
async def test_get_email_with_attachments(mock_gc: MagicMock) -> None:
    msg = _make_mock_message(msg_id="att_msg")
    msg.has_attachments = True

    att = MagicMock()
    att.id = "att1"
    att.name = "file.pdf"
    att.content_type = "application/pdf"
    att.size = 12345
    att_page = MagicMock()
    att_page.value = [att]

    msg_item_builder = MagicMock()
    msg_item_builder.get = AsyncMock(return_value=msg)
    msg_item_builder.attachments.get = AsyncMock(return_value=att_page)
    mock_gc.client.me.messages.by_message_id.return_value = msg_item_builder

    result = await get_email(mock_gc, "att_msg")
    assert result["has_attachments"] is True
    assert len(result["attachments"]) == 1
    assert result["attachments"][0]["name"] == "file.pdf"


# ---------------------------------------------------------------------------
# search_emails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_emails_returns_results(mock_gc: MagicMock) -> None:
    msg = _make_mock_message(msg_id="srch1", subject="Meeting Notes")
    page = _make_page([msg])
    mock_gc.client.me.messages.get = AsyncMock(return_value=page)
    result = await search_emails(mock_gc, "meeting", limit=10)
    assert len(result) == 1
    assert result[0]["subject"] == "Meeting Notes"


@pytest.mark.asyncio
async def test_search_emails_empty_results(mock_gc: MagicMock) -> None:
    page = _make_page([])
    mock_gc.client.me.messages.get = AsyncMock(return_value=page)
    result = await search_emails(mock_gc, "nonexistent query")
    assert result == []


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_calls_post(mock_gc: MagicMock) -> None:
    mock_gc.client.me.send_mail.post = AsyncMock(return_value=None)
    await send_email(mock_gc, to=["a@b.com"], subject="Hi", body="<p>Hello</p>")
    mock_gc.client.me.send_mail.post.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_with_cc_bcc(mock_gc: MagicMock) -> None:
    mock_gc.client.me.send_mail.post = AsyncMock(return_value=None)
    await send_email(
        mock_gc,
        to=["to@example.com"],
        subject="CC Test",
        body="body",
        cc=["cc@example.com"],
        bcc=["bcc@example.com"],
    )
    call_args = mock_gc.client.me.send_mail.post.call_args
    req_body = call_args[0][0]
    assert req_body.message.cc_recipients is not None
    assert req_body.message.bcc_recipients is not None


@pytest.mark.asyncio
async def test_send_email_plain_text(mock_gc: MagicMock) -> None:
    mock_gc.client.me.send_mail.post = AsyncMock(return_value=None)
    await send_email(mock_gc, to=["r@x.com"], subject="s", body="plain", body_type="text")
    req_body = mock_gc.client.me.send_mail.post.call_args[0][0]
    assert req_body.message.body.content_type == BodyType.Text


# ---------------------------------------------------------------------------
# reply_to_email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_to_email(mock_gc: MagicMock) -> None:
    mock_gc.client.me.messages.by_message_id.return_value.reply.post = AsyncMock(return_value=None)
    await reply_to_email(mock_gc, "msg1", body="Thanks!")
    mock_gc.client.me.messages.by_message_id.return_value.reply.post.assert_called_once()


@pytest.mark.asyncio
async def test_reply_all_to_email(mock_gc: MagicMock) -> None:
    mock_gc.client.me.messages.by_message_id.return_value.reply_all.post = AsyncMock(
        return_value=None
    )
    await reply_to_email(mock_gc, "msg1", body="Reply all!", reply_all=True)
    mock_gc.client.me.messages.by_message_id.return_value.reply_all.post.assert_called_once()


# ---------------------------------------------------------------------------
# forward_email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_email(mock_gc: MagicMock) -> None:
    mock_gc.client.me.messages.by_message_id.return_value.forward.post = AsyncMock(
        return_value=None
    )
    await forward_email(mock_gc, "msg1", to=["fwd@example.com"], comment="FYI")
    call_args = mock_gc.client.me.messages.by_message_id.return_value.forward.post.call_args
    req_body = call_args[0][0]
    assert req_body.comment == "FYI"
    assert len(req_body.to_recipients) == 1


# ---------------------------------------------------------------------------
# move_email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_email(mock_gc: MagicMock) -> None:
    moved_msg = _make_mock_message(msg_id="msg1")
    mock_gc.client.me.messages.by_message_id.return_value.move.post = AsyncMock(
        return_value=moved_msg
    )
    result = await move_email(mock_gc, "msg1", "archiveFolder")
    assert result["id"] == "msg1"
    call_args = mock_gc.client.me.messages.by_message_id.return_value.move.post.call_args
    req_body = call_args[0][0]
    assert req_body.destination_id == "archiveFolder"


# ---------------------------------------------------------------------------
# delete_email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_email(mock_gc: MagicMock) -> None:
    mock_gc.client.me.messages.by_message_id.return_value.delete = AsyncMock(return_value=None)
    await delete_email(mock_gc, "msg1")
    mock_gc.client.me.messages.by_message_id.return_value.delete.assert_called_once()


# ---------------------------------------------------------------------------
# mark_email_read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_email_read(mock_gc: MagicMock) -> None:
    updated = _make_mock_message(is_read=True)
    mock_gc.client.me.messages.by_message_id.return_value.patch = AsyncMock(return_value=updated)
    result = await mark_email_read(mock_gc, "msg1", is_read=True)
    assert result["is_read"] is True


@pytest.mark.asyncio
async def test_mark_email_unread(mock_gc: MagicMock) -> None:
    updated = _make_mock_message(is_read=False)
    mock_gc.client.me.messages.by_message_id.return_value.patch = AsyncMock(return_value=updated)
    result = await mark_email_read(mock_gc, "msg1", is_read=False)
    assert result["is_read"] is False
    patch_call = mock_gc.client.me.messages.by_message_id.return_value.patch.call_args[0][0]
    assert patch_call.is_read is False


# ---------------------------------------------------------------------------
# list_mail_folders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_mail_folders(mock_gc: MagicMock) -> None:
    folder = _make_mock_folder(folder_id="f1", display_name="Inbox")
    page = MagicMock()
    page.value = [folder]
    page.odata_next_link = None
    mock_gc.client.me.mail_folders.get = AsyncMock(return_value=page)
    result = await list_mail_folders(mock_gc)
    assert len(result) == 1
    assert result[0]["id"] == "f1"
    assert result[0]["display_name"] == "Inbox"


@pytest.mark.asyncio
async def test_list_mail_folders_empty(mock_gc: MagicMock) -> None:
    page = MagicMock()
    page.value = []
    page.odata_next_link = None
    mock_gc.client.me.mail_folders.get = AsyncMock(return_value=page)
    result = await list_mail_folders(mock_gc)
    assert result == []


# ---------------------------------------------------------------------------
# create_mail_folder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_mail_folder_at_root(mock_gc: MagicMock) -> None:
    new_folder = _make_mock_folder(folder_id="new1", display_name="My Folder")
    mock_gc.client.me.mail_folders.post = AsyncMock(return_value=new_folder)
    result = await create_mail_folder(mock_gc, "My Folder")
    assert result["id"] == "new1"
    assert result["display_name"] == "My Folder"


@pytest.mark.asyncio
async def test_create_mail_folder_as_subfolder(mock_gc: MagicMock) -> None:
    new_folder = _make_mock_folder(folder_id="sub1", display_name="Sub Folder")
    mock_gc.client.me.mail_folders.by_mail_folder_id.return_value.child_folders.post = AsyncMock(
        return_value=new_folder
    )
    result = await create_mail_folder(mock_gc, "Sub Folder", parent_folder_id="parent1")
    assert result["id"] == "sub1"
    mock_gc.client.me.mail_folders.by_mail_folder_id.assert_called_with("parent1")


# ---------------------------------------------------------------------------
# get_mailbox_settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mailbox_settings(mock_gc: MagicMock) -> None:
    settings_obj = MagicMock()
    settings_obj.time_zone = "UTC"
    settings_obj.date_format = "M/d/yyyy"
    settings_obj.time_format = "h:mm tt"
    settings_obj.language = None
    settings_obj.automatic_replies_setting = None
    mock_gc.client.me.mailbox_settings.get = AsyncMock(return_value=settings_obj)
    result = await get_mailbox_settings(mock_gc)
    assert result["time_zone"] == "UTC"
    assert result["automatic_replies"] is None


@pytest.mark.asyncio
async def test_get_mailbox_settings_with_auto_reply(mock_gc: MagicMock) -> None:
    ars = MagicMock()
    ars.status = "Disabled"
    ars.external_audience = None
    ars.internal_reply_message = "On vacation"
    ars.external_reply_message = "On vacation"
    ars.scheduled_start_date_time = None
    ars.scheduled_end_date_time = None

    settings_obj = MagicMock()
    settings_obj.time_zone = "Eastern Standard Time"
    settings_obj.date_format = None
    settings_obj.time_format = None
    settings_obj.language = None
    settings_obj.automatic_replies_setting = ars
    mock_gc.client.me.mailbox_settings.get = AsyncMock(return_value=settings_obj)
    result = await get_mailbox_settings(mock_gc)
    assert result["automatic_replies"] is not None
    assert result["automatic_replies"]["internal_reply_message"] == "On vacation"


# ---------------------------------------------------------------------------
# set_auto_reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_auto_reply_disabled(mock_gc: MagicMock) -> None:
    updated = MagicMock()
    updated.time_zone = "UTC"
    updated.automatic_replies_setting = None
    mock_gc.client.me.mailbox_settings.patch = AsyncMock(return_value=updated)
    result = await set_auto_reply(mock_gc, enabled=False)
    assert result["time_zone"] == "UTC"
    patch_call = mock_gc.client.me.mailbox_settings.patch.call_args[0][0]
    assert patch_call.automatic_replies_setting.status == AutomaticRepliesStatus.Disabled


@pytest.mark.asyncio
async def test_set_auto_reply_always_enabled(mock_gc: MagicMock) -> None:
    updated = MagicMock()
    updated.time_zone = "UTC"
    updated.automatic_replies_setting = None
    mock_gc.client.me.mailbox_settings.patch = AsyncMock(return_value=updated)
    await set_auto_reply(mock_gc, enabled=True, message="Out of office")
    patch_call = mock_gc.client.me.mailbox_settings.patch.call_args[0][0]
    assert patch_call.automatic_replies_setting.status == AutomaticRepliesStatus.AlwaysEnabled
    assert patch_call.automatic_replies_setting.internal_reply_message == "Out of office"


@pytest.mark.asyncio
async def test_set_auto_reply_scheduled(mock_gc: MagicMock) -> None:
    updated = MagicMock()
    updated.time_zone = "UTC"
    updated.automatic_replies_setting = None
    mock_gc.client.me.mailbox_settings.patch = AsyncMock(return_value=updated)
    await set_auto_reply(
        mock_gc,
        enabled=True,
        message="Away",
        start_datetime="2026-04-01T00:00:00",
        end_datetime="2026-04-08T00:00:00",
    )
    patch_call = mock_gc.client.me.mailbox_settings.patch.call_args[0][0]
    assert patch_call.automatic_replies_setting.status == AutomaticRepliesStatus.Scheduled
    assert patch_call.automatic_replies_setting.scheduled_start_date_time.date_time == (
        "2026-04-01T00:00:00"
    )
