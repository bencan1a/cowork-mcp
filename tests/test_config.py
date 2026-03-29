from __future__ import annotations

import pytest

from config import Settings

# Required secrets for all Settings instantiations in tests
_REQUIRED = {
    "azure_client_id": "test-id",
    "azure_client_secret": "test-secret",
    "token_encryption_key": "test-key",
    "mcp_api_key": "test-api-key",
}


def test_settings_defaults() -> None:
    s = Settings(**_REQUIRED)
    assert s.port == 8000
    assert s.log_level == "INFO"
    assert s.scope_mail_read is True


def test_settings_scope_toggles() -> None:
    s = Settings(**_REQUIRED, scope_mail_read=False, scope_calendar_write=False)
    assert s.scope_mail_read is False
    assert s.scope_calendar_write is False


def test_settings_log_level_uppercase() -> None:
    s = Settings(**_REQUIRED, log_level="debug")
    assert s.log_level == "DEBUG"


def test_settings_invalid_log_level() -> None:
    with pytest.raises(Exception):
        Settings(**_REQUIRED, log_level="VERBOSE")


def test_settings_fixture(settings: Settings) -> None:
    assert settings.azure_client_id == "test-client-id"
    assert settings.mcp_api_key == "test-api-key"
