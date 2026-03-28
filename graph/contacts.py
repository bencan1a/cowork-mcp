from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.contact import Contact
from msgraph.generated.models.email_address import EmailAddress
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.users.item.contacts.contacts_request_builder import (
    ContactsRequestBuilder,
)

if TYPE_CHECKING:
    from graph.client import GraphClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _contact_to_dict(c: Any) -> dict[str, Any]:
    """Convert a Graph SDK Contact object to a plain dict."""
    return {
        "id": c.id,
        "display_name": c.display_name,
        "given_name": c.given_name,
        "surname": c.surname,
        "email": c.email_addresses[0].address if c.email_addresses else None,
        "phone": c.mobile_phone or (c.business_phones[0] if c.business_phones else None),
        "company": c.company_name,
        "job_title": c.job_title,
    }


def _wrap_odata_error(exc: ODataError) -> RuntimeError:
    """Convert an ODataError into a RuntimeError with a readable message."""
    code = exc.error.code if exc.error else "unknown"
    msg = exc.error.message if exc.error else str(exc)
    return RuntimeError(f"Graph API error {code}: {msg}")


# ---------------------------------------------------------------------------
# List contacts
# ---------------------------------------------------------------------------


async def list_contacts(
    gc: GraphClient,
    search: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return contacts, optionally filtered by a search query."""
    query_params = ContactsRequestBuilder.ContactsRequestBuilderGetQueryParameters(
        top=limit,
        search=search,
    )
    request_config = RequestConfiguration(query_parameters=query_params)

    try:
        result = await gc.client.me.contacts.get(request_configuration=request_config)
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc

    if result is None or result.value is None:
        return []

    contacts = list(result.value)

    # Handle pagination
    while result is not None and result.odata_next_link and len(contacts) < limit:
        try:
            result = await gc.client.me.contacts.with_url(result.odata_next_link).get()
        except ODataError as exc:
            raise _wrap_odata_error(exc) from exc
        if result and result.value:
            contacts.extend(result.value)

    return [_contact_to_dict(c) for c in contacts[:limit]]


# ---------------------------------------------------------------------------
# Get single contact
# ---------------------------------------------------------------------------


async def get_contact(gc: GraphClient, contact_id: str) -> dict[str, Any]:
    """Fetch a single contact by ID."""
    try:
        contact = await gc.client.me.contacts.by_contact_id(contact_id).get()
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc

    if contact is None:
        raise RuntimeError(f"Contact {contact_id!r} not found")
    return _contact_to_dict(contact)


# ---------------------------------------------------------------------------
# Create contact
# ---------------------------------------------------------------------------


async def create_contact(gc: GraphClient, **fields: Any) -> dict[str, Any]:
    """Create a new contact.

    Common fields: given_name, surname, display_name, email_addresses,
    mobile_phone, business_phones, company_name, job_title.
    """
    contact = Contact()

    # Handle email_addresses specially — convert plain strings to EmailAddress objects
    email_input = fields.pop("email_addresses", None)
    if email_input:
        email_list: list[EmailAddress] = []
        for item in email_input if isinstance(email_input, list) else [email_input]:
            if isinstance(item, str):
                ea = EmailAddress()
                ea.address = item
                email_list.append(ea)
            else:
                email_list.append(item)
        contact.email_addresses = email_list

    for key, value in fields.items():
        if hasattr(contact, key):
            setattr(contact, key, value)
        else:
            logger.warning("Unknown Contact field %r — skipping", key)

    try:
        created = await gc.client.me.contacts.post(contact)
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc

    if created is None:
        raise RuntimeError("Contact creation returned no result")
    return _contact_to_dict(created)


# ---------------------------------------------------------------------------
# Update contact
# ---------------------------------------------------------------------------


async def update_contact(gc: GraphClient, contact_id: str, **fields: Any) -> dict[str, Any]:
    """Update an existing contact. Pass keyword args matching Graph Contact fields."""
    contact = Contact()

    for key, value in fields.items():
        if hasattr(contact, key):
            setattr(contact, key, value)
        else:
            logger.warning("Unknown Contact field %r — skipping", key)

    try:
        updated = await gc.client.me.contacts.by_contact_id(contact_id).patch(contact)
    except ODataError as exc:
        raise _wrap_odata_error(exc) from exc

    if updated is None:
        # PATCH may return 204 No Content — re-fetch to return the updated state
        return await get_contact(gc, contact_id)
    return _contact_to_dict(updated)
