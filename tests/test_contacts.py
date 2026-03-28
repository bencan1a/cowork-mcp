from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graph.contacts import create_contact, get_contact, list_contacts, update_contact

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_contact(
    *,
    id: str = "contact-1",
    display_name: str = "Alice Smith",
    given_name: str = "Alice",
    surname: str = "Smith",
    email: str = "alice@example.com",
) -> MagicMock:
    """Return a mock Contact object."""
    contact = MagicMock()
    contact.id = id
    contact.display_name = display_name
    contact.given_name = given_name
    contact.surname = surname
    contact.email_addresses = [MagicMock(address=email)]
    contact.mobile_phone = None
    contact.business_phones = []
    contact.company_name = "ACME Corp"
    contact.job_title = "Engineer"
    return contact


@pytest.fixture
def gc() -> MagicMock:
    """Return a mock GraphClient."""
    return MagicMock()


# ---------------------------------------------------------------------------
# list_contacts
# ---------------------------------------------------------------------------


class TestListContacts:
    async def test_list_contacts_returns_list(self, gc: MagicMock) -> None:
        contact = _make_contact()
        response = MagicMock()
        response.value = [contact]
        response.odata_next_link = None

        gc.client.me.contacts.get = AsyncMock(return_value=response)

        result = await list_contacts(gc)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == "contact-1"
        assert result[0]["display_name"] == "Alice Smith"
        assert result[0]["email"] == "alice@example.com"

    async def test_list_contacts_empty_response(self, gc: MagicMock) -> None:
        response = MagicMock()
        response.value = []
        response.odata_next_link = None

        gc.client.me.contacts.get = AsyncMock(return_value=response)

        result = await list_contacts(gc)

        assert result == []

    async def test_list_contacts_none_response(self, gc: MagicMock) -> None:
        gc.client.me.contacts.get = AsyncMock(return_value=None)

        result = await list_contacts(gc)

        assert result == []

    async def test_list_contacts_with_search(self, gc: MagicMock) -> None:
        response = MagicMock()
        response.value = []
        response.odata_next_link = None

        gc.client.me.contacts.get = AsyncMock(return_value=response)

        await list_contacts(gc, search="Alice")

        # Verify get was called (query params are embedded in the request config)
        gc.client.me.contacts.get.assert_called_once()

    async def test_list_contacts_respects_limit(self, gc: MagicMock) -> None:
        contacts = [_make_contact(id=f"c-{i}", display_name=f"Person {i}") for i in range(5)]
        response = MagicMock()
        response.value = contacts
        response.odata_next_link = None

        gc.client.me.contacts.get = AsyncMock(return_value=response)

        result = await list_contacts(gc, limit=3)

        assert len(result) == 3


# ---------------------------------------------------------------------------
# get_contact
# ---------------------------------------------------------------------------


class TestGetContact:
    async def test_get_contact_returns_dict(self, gc: MagicMock) -> None:
        contact = _make_contact(id="contact-42", display_name="Bob Jones")
        gc.client.me.contacts.by_contact_id.return_value.get = AsyncMock(return_value=contact)

        result = await get_contact(gc, "contact-42")

        assert result["id"] == "contact-42"
        assert result["display_name"] == "Bob Jones"
        gc.client.me.contacts.by_contact_id.assert_called_once_with("contact-42")

    async def test_get_contact_raises_on_none(self, gc: MagicMock) -> None:
        gc.client.me.contacts.by_contact_id.return_value.get = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError, match="not found"):
            await get_contact(gc, "missing-id")


# ---------------------------------------------------------------------------
# create_contact
# ---------------------------------------------------------------------------


class TestCreateContact:
    async def test_create_contact_calls_post(self, gc: MagicMock) -> None:
        contact = _make_contact(id="new-contact", display_name="Carol White")
        gc.client.me.contacts.post = AsyncMock(return_value=contact)

        result = await create_contact(
            gc,
            given_name="Carol",
            surname="White",
            display_name="Carol White",
            email_addresses=["carol@example.com"],
        )

        gc.client.me.contacts.post.assert_called_once()
        assert result["id"] == "new-contact"
        assert result["display_name"] == "Carol White"

    async def test_create_contact_raises_on_none_response(self, gc: MagicMock) -> None:
        gc.client.me.contacts.post = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError, match="no result"):
            await create_contact(gc, given_name="Test")

    async def test_create_contact_with_string_email(self, gc: MagicMock) -> None:
        """Email addresses passed as plain strings should be wrapped in EmailAddress objects."""
        contact = _make_contact()
        gc.client.me.contacts.post = AsyncMock(return_value=contact)

        await create_contact(gc, given_name="Dave", email_addresses=["dave@example.com"])

        call_args = gc.client.me.contacts.post.call_args
        created_contact = call_args[0][0]
        assert len(created_contact.email_addresses) == 1
        # The email address object should have the address set
        assert created_contact.email_addresses[0].address == "dave@example.com"


# ---------------------------------------------------------------------------
# update_contact
# ---------------------------------------------------------------------------


class TestUpdateContact:
    async def test_update_contact_calls_patch(self, gc: MagicMock) -> None:
        contact = _make_contact(id="contact-upd", display_name="Updated Name")
        gc.client.me.contacts.by_contact_id.return_value.patch = AsyncMock(return_value=contact)

        result = await update_contact(gc, "contact-upd", display_name="Updated Name")

        gc.client.me.contacts.by_contact_id.assert_called_once_with("contact-upd")
        gc.client.me.contacts.by_contact_id.return_value.patch.assert_called_once()
        assert result["display_name"] == "Updated Name"

    async def test_update_contact_refetches_on_none_patch(self, gc: MagicMock) -> None:
        contact = _make_contact(id="contact-upd")
        gc.client.me.contacts.by_contact_id.return_value.patch = AsyncMock(return_value=None)
        gc.client.me.contacts.by_contact_id.return_value.get = AsyncMock(return_value=contact)

        result = await update_contact(gc, "contact-upd", job_title="Director")

        assert result["id"] == "contact-upd"
