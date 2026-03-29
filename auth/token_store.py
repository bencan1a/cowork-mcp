from __future__ import annotations

import logging
import stat
import threading
from typing import TYPE_CHECKING, Any, cast

import msal
from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class TokenStore:
    """Encrypted MSAL token cache backed by a local file.

    The cache file is encrypted with a Fernet symmetric key.
    File permissions are enforced to 600 after every write.
    """

    def __init__(self, cache_path: Path, encryption_key: str) -> None:
        self._cache_path = cache_path
        self._fernet = Fernet(encryption_key.encode())
        self._cache = msal.SerializableTokenCache()
        self._lock = threading.Lock()
        self._app: msal.ConfidentialClientApplication | None = None
        self._app_client_id: str = ""
        self._load()

    def _load(self) -> None:
        """Load and decrypt token cache from disk if it exists."""
        if not self._cache_path.exists():
            logger.debug("No token cache found at %s", self._cache_path)
            return
        try:
            encrypted = self._cache_path.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            self._cache.deserialize(decrypted.decode())
            logger.debug("Token cache loaded from %s", self._cache_path)
        except InvalidToken:
            logger.warning("Token cache at %s could not be decrypted — ignoring", self._cache_path)
        except Exception as exc:
            logger.warning("Failed to load token cache: %s", exc)

    def save(self) -> None:
        """Encrypt and persist token cache to disk with 600 permissions."""
        if not self._cache.has_state_changed:
            return
        serialized = self._cache.serialize()
        encrypted = self._fernet.encrypt(serialized.encode())
        self._cache_path.write_bytes(encrypted)
        # Enforce owner-read/write only
        self._cache_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        logger.debug("Token cache saved to %s", self._cache_path)

    def get_account(self) -> dict[str, Any] | None:
        """Return the first cached account, or None if not authenticated."""
        app = self._build_app("")
        accounts = app.get_accounts()
        return cast("dict[str, Any]", accounts[0]) if accounts else None

    def acquire_token_silent(self, scopes: list[str], client_id: str, client_secret: str) -> str:
        """Acquire an access token silently using the cached refresh token.

        Raises RuntimeError if silent acquire fails (reauth required).
        Thread-safe: serializes token acquisition + save via a lock.
        """
        with self._lock:
            if self._app is None or self._app_client_id != client_id:
                self._app = self._build_app(client_id, client_secret)
                self._app_client_id = client_id
            app = self._app
            accounts = app.get_accounts()
            if not accounts:
                raise RuntimeError(
                    "No cached accounts found — run `python run_auth.py` to authenticate"
                )
            result = app.acquire_token_silent(scopes, account=accounts[0])
            if result and "access_token" in result:
                self.save()
                return cast("str", result["access_token"])
            error = result.get("error_description", "unknown") if result else "no result"
            raise RuntimeError(
                f"Silent token acquisition failed: {error} — run `python run_auth.py` to re-authenticate"
            )

    def needs_reauth(self, scopes: list[str], client_id: str, client_secret: str) -> bool:
        """Return True if interactive re-authentication is needed."""
        try:
            self.acquire_token_silent(scopes, client_id, client_secret)
            return False
        except RuntimeError:
            return True

    def _build_app(
        self, client_id: str, client_secret: str | None = None
    ) -> msal.ConfidentialClientApplication | msal.PublicClientApplication:
        """Build an MSAL app with the shared token cache."""
        authority = "https://login.microsoftonline.com/consumers"
        if client_secret:
            return msal.ConfidentialClientApplication(
                client_id,
                client_credential=client_secret,
                authority=authority,
                token_cache=self._cache,
            )
        return msal.PublicClientApplication(
            client_id or "placeholder",
            authority=authority,
            token_cache=self._cache,
        )
