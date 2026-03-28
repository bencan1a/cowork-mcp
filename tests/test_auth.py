from __future__ import annotations

import json
import stat
import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from auth.token_store import TokenStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def tmp_cache(tmp_path: Path, fernet_key: str) -> TokenStore:
    return TokenStore(tmp_path / "cache.json", fernet_key)


class TestTokenStore:
    def test_load_missing_cache(self, tmp_path: Path, fernet_key: str) -> None:
        """No error when cache file does not exist."""
        store = TokenStore(tmp_path / "nonexistent.json", fernet_key)
        assert store.get_account() is None

    def test_save_creates_file(self, tmp_cache: TokenStore, tmp_path: Path) -> None:
        """save() creates an encrypted file after state change."""
        # Force state change: deserialize then manually mark changed (MSAL resets flag on deserialize)
        fake_cache_data = '{"Account": {}, "AccessToken": {}, "RefreshToken": {}, "IdToken": {}, "AppMetadata": {}}'
        tmp_cache._cache.deserialize(fake_cache_data)
        tmp_cache._cache.has_state_changed = True
        tmp_cache.save()
        cache_file = tmp_path / "cache.json"
        assert cache_file.exists()

    def test_save_sets_chmod_600(self, tmp_cache: TokenStore, tmp_path: Path) -> None:
        """File permissions are 600 after save."""
        fake_cache_data = '{"Account": {}, "AccessToken": {}, "RefreshToken": {}, "IdToken": {}, "AppMetadata": {}}'
        tmp_cache._cache.deserialize(fake_cache_data)
        tmp_cache._cache.has_state_changed = True
        tmp_cache.save()
        cache_file = tmp_path / "cache.json"
        mode = stat.S_IMODE(cache_file.stat().st_mode)
        # On Windows, chmod has limited effect; skip on Windows
        if sys.platform != "win32":
            assert mode == 0o600

    def test_roundtrip_encrypt_decrypt(self, tmp_path: Path, fernet_key: str) -> None:
        """Saving and reloading preserves cache content."""
        store1 = TokenStore(tmp_path / "cache.json", fernet_key)
        # Use a proper MSAL account structure so get_accounts() can locate it
        fake_data = json.dumps(
            {
                "Account": {
                    "uid.utid-login.windows.net-consumers-test@example.com": {
                        "home_account_id": "uid.utid",
                        "environment": "login.windows.net",
                        "realm": "consumers",
                        "local_account_id": "uid",
                        "username": "test@example.com",
                        "authority_type": "MSSTS",
                        "client_info": "",
                    }
                },
                "AccessToken": {},
                "RefreshToken": {},
                "IdToken": {},
                "AppMetadata": {},
            }
        )
        store1._cache.deserialize(fake_data)
        store1._cache.has_state_changed = True
        store1.save()
        # Reload
        store2 = TokenStore(tmp_path / "cache.json", fernet_key)
        account = store2.get_account()
        assert account is not None
        assert account.get("username") == "test@example.com"

    def test_acquire_token_silent_no_accounts(self, tmp_cache: TokenStore) -> None:
        """Raises RuntimeError when no cached accounts exist."""
        with pytest.raises(RuntimeError, match="No cached accounts"):
            tmp_cache.acquire_token_silent(["Mail.Read"], "client-id", "client-secret")

    def test_needs_reauth_returns_true_when_empty(self, tmp_cache: TokenStore) -> None:
        """needs_reauth() returns True when cache is empty."""
        assert tmp_cache.needs_reauth(["Mail.Read"], "client-id", "client-secret") is True

    def test_acquire_token_silent_calls_msal(self, tmp_path: Path, fernet_key: str) -> None:
        """acquire_token_silent delegates to MSAL."""
        store = TokenStore(tmp_path / "cache.json", fernet_key)
        mock_app = MagicMock()
        mock_app.get_accounts.return_value = [{"username": "test@example.com"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "fake-token"}
        with patch.object(store, "_build_app", return_value=mock_app):
            token = store.acquire_token_silent(["Mail.Read"], "client-id", "client-secret")
        assert token == "fake-token"  # noqa: S105  # not a real token; test assertion
        mock_app.acquire_token_silent.assert_called_once()
