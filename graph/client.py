from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from kiota_abstractions.authentication import (
    AccessTokenProvider,
    AllowedHostsValidator,
    BaseBearerTokenAuthenticationProvider,
)
from msgraph.graph_request_adapter import GraphRequestAdapter
from msgraph.graph_service_client import GraphServiceClient

from auth.token_store import TokenStore
from config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scope mapping
# ---------------------------------------------------------------------------

#: Map each Settings scope toggle to its Graph OAuth scopes
SCOPE_MAP: dict[str, list[str]] = {
    "scope_mail_read": ["Mail.ReadWrite"],
    "scope_mail_write": ["Mail.ReadWrite"],
    "scope_mail_send": ["Mail.Send"],
    "scope_calendar_read": ["Calendars.ReadWrite"],
    "scope_calendar_write": ["Calendars.ReadWrite"],
    "scope_contacts_read": ["Contacts.ReadWrite"],
    "scope_contacts_write": ["Contacts.ReadWrite"],
    "scope_tasks_read": ["Tasks.ReadWrite"],
    "scope_tasks_write": ["Tasks.ReadWrite"],
}

#: Always-required scopes
BASE_SCOPES: list[str] = ["User.Read", "MailboxSettings.ReadWrite", "offline_access"]


def build_scopes(settings: Settings) -> list[str]:
    """Return the deduplicated list of OAuth scopes needed for enabled toggles."""
    scopes: set[str] = set(BASE_SCOPES)
    for attr, graph_scopes in SCOPE_MAP.items():
        if getattr(settings, attr, False):
            scopes.update(graph_scopes)
    return sorted(scopes)


# ---------------------------------------------------------------------------
# Access token provider
# ---------------------------------------------------------------------------


class _TokenStoreAccessTokenProvider(AccessTokenProvider):
    """Kiota AccessTokenProvider backed by TokenStore.

    get_authorization_token() is called by BaseBearerTokenAuthenticationProvider
    on every request to supply a fresh (or cached) access token.
    """

    def __init__(self, store: TokenStore, settings: Settings) -> None:
        self._store = store
        self._settings = settings
        self._scopes = build_scopes(settings)
        self._validator = AllowedHostsValidator(["graph.microsoft.com"])

    async def get_authorization_token(
        self,
        uri: str,  # noqa: ARG002 — required by AccessTokenProvider protocol
        additional_authentication_context: dict[str, Any] = {},  # noqa: ARG002,B006 — required by protocol
    ) -> str:
        return await asyncio.to_thread(
            self._store.acquire_token_silent,
            self._scopes,
            self._settings.azure_client_id,
            self._settings.azure_client_secret,
        )

    def get_allowed_hosts_validator(self) -> AllowedHostsValidator:
        return self._validator


# ---------------------------------------------------------------------------
# GraphClient wrapper
# ---------------------------------------------------------------------------


class GraphClient:
    """Authenticated Microsoft Graph client.

    Wraps GraphServiceClient with automatic token refresh via TokenStore.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._store = TokenStore(settings.token_cache_path, settings.token_encryption_key)
        token_provider = _TokenStoreAccessTokenProvider(self._store, settings)
        auth_provider = BaseBearerTokenAuthenticationProvider(token_provider)
        # Build the adapter directly to avoid the credentials→AzureIdentity path,
        # which only accepts Azure SDK TokenCredential objects.
        request_adapter = GraphRequestAdapter(auth_provider)
        self._client = GraphServiceClient(request_adapter=request_adapter)

    @property
    def client(self) -> GraphServiceClient:
        return self._client

    @property
    def store(self) -> TokenStore:
        return self._store


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_instance: GraphClient | None = None
_lock = threading.Lock()


def get_graph_client(settings: Settings | None = None) -> GraphClient:
    """Return the singleton GraphClient, creating it if necessary."""
    global _instance  # noqa: PLW0603
    if _instance is None:
        with _lock:
            if _instance is None:
                if settings is None:
                    settings = Settings()  # type: ignore[call-arg]  # pydantic-settings loads from .env
                _instance = GraphClient(settings)
                logger.info("GraphClient initialised")
    return _instance


def reset_graph_client() -> None:
    """Reset the singleton (for testing)."""
    global _instance  # noqa: PLW0603
    with _lock:
        _instance = None
