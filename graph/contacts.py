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

from graph.errors import MAX_PAGES, clamp_limit, validate_graph_id, wrap_odata_error

if TYPE_CHECKING:
    from graph.client import GraphClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowlist of fields safe to set on a Contact object
# ---------------------------------------------------------------------------

_ALLOWED_CONTACT_FIELDS: set[str] = {
    "given_name",
    "surname",
    "display_name",
    "mobile_phone",
    "business_phones",
    "company_name",
    "job_title",
    "personal_notes",
}


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


def _normalize_email_addresses(
    email_input: Any,
) -> list[EmailAddress]:
    """Convert plain strings or EmailAddress objects into a list of EmailAddress."""
    email_list: list[EmailAddress] = []
    for item in email_input if isinstance(email_input, list) else [email_input]:
        if isinstance(item, str):
            ea = EmailAddress()
            ea.address = item
            email_list.append(ea)
        else:
            email_list.append(item)
    return email_list


# ---------------------------------------------------------------------------
# List contacts
# ---------------------------------------------------------------------------


async def list_contacts(
    gc: GraphClient,
    search: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return contacts, optionally filtered by a search query."""
    limit = clamp_limit(limit)

    query_params = ContactsRequestBuilder.ContactsRequestBuilderGetQueryParameters(
        top=limit,
        search=search,
    )
    request_config = RequestConfiguration(query_parameters=query_params)

    try:
        result = await gc.client.me.contacts.get(request_configuration=request_config)
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc

    if result is None or result.value is None:
        return []

    contacts = list(result.value)

    # Handle pagination
    pages = 1
    while result is not None and result.odata_next_link and len(contacts) < limit:
        if pages >= MAX_PAGES:
            logger.warning("Pagination safety cap reached (%d pages)", MAX_PAGES)
            break
        try:
            result = await gc.client.me.contacts.with_url(result.odata_next_link).get()
        except ODataError as exc:
            raise wrap_odata_error(exc) from exc
        if result and result.value:
            contacts.extend(result.value)
        pages += 1

    return [_contact_to_dict(c) for c in contacts[:limit]]


# ---------------------------------------------------------------------------
# Get single contact
# ---------------------------------------------------------------------------


async def get_contact(gc: GraphClient, contact_id: str) -> dict[str, Any]:
    """Fetch a single contact by ID."""
    validate_graph_id(contact_id, "contact_id")
    try:
        contact = await gc.client.me.contacts.by_contact_id(contact_id).get()
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc

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
        contact.email_addresses = _normalize_email_addresses(email_input)

    for key, value in fields.items():
        if key in _ALLOWED_CONTACT_FIELDS:
            setattr(contact, key, value)
        else:
            logger.warning("Unknown or disallowed Contact field %r — skipping", key)

    try:
        created = await gc.client.me.contacts.post(contact)
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc

    if created is None:
        raise RuntimeError("Contact creation returned no result")
    return _contact_to_dict(created)


# ---------------------------------------------------------------------------
# Update contact
# ---------------------------------------------------------------------------


async def update_contact(gc: GraphClient, contact_id: str, **fields: Any) -> dict[str, Any]:
    """Update an existing contact. Pass keyword args matching Graph Contact fields."""
    validate_graph_id(contact_id, "contact_id")
    contact = Contact()

    # Handle email_addresses specially — convert plain strings to EmailAddress objects
    email_input = fields.pop("email_addresses", None)
    if email_input:
        contact.email_addresses = _normalize_email_addresses(email_input)

    for key, value in fields.items():
        if key in _ALLOWED_CONTACT_FIELDS:
            setattr(contact, key, value)
        else:
            logger.warning("Unknown or disallowed Contact field %r — skipping", key)

    try:
        updated = await gc.client.me.contacts.by_contact_id(contact_id).patch(contact)
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc

    if updated is None:
        # PATCH may return 204 No Content — re-fetch to return the updated state
        return await get_contact(gc, contact_id)
    return _contact_to_dict(updated)


# ---------------------------------------------------------------------------
# Delete contact
# ---------------------------------------------------------------------------


async def delete_contact(gc: GraphClient, contact_id: str) -> None:
    """Delete a contact by ID."""
    validate_graph_id(contact_id, "contact_id")
    try:
        await gc.client.me.contacts.by_contact_id(contact_id).delete()
    except ODataError as exc:
        raise wrap_odata_error(exc) from exc
