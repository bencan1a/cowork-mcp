from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config import Settings


def pytest_configure(config: pytest.Config) -> None:
    """Pre-import server.py under mocked credentials before test collection.

    ``server.py`` calls ``Settings()`` and ``get_graph_client()`` at module
    level.  Without real credentials those calls raise ValueError (bad Fernet
    key).  We import it once here under mocks and immediately stop the patches
    so that other tests (test_config, test_graph_client, ...) see the real
    ``Settings`` and ``GraphClient`` constructors.
    """
    if "server" in sys.modules:
        return  # Already imported — nothing to do.

    mock_settings = MagicMock(spec=Settings)
    mock_settings.scope_mail_read = False
    mock_settings.scope_mail_write = False
    mock_settings.scope_mail_send = False
    mock_settings.scope_calendar_read = False
    mock_settings.scope_calendar_write = False
    mock_settings.scope_contacts_read = False
    mock_settings.scope_contacts_write = False
    mock_settings.scope_tasks_read = False
    mock_settings.scope_tasks_write = False
    mock_settings.mcp_api_key = "test-api-key"
    mock_settings.log_level = "INFO"

    _settings_patcher = patch("config.Settings", return_value=mock_settings)
    _gc_patcher = patch("graph.client.get_graph_client", return_value=MagicMock())

    _settings_patcher.start()
    _gc_patcher.start()
    try:
        import server  # noqa: F401, PLC0415  # side-effect: caches module in sys.modules
    finally:
        # Stop patches immediately so other tests see the real constructors.
        _settings_patcher.stop()
        _gc_patcher.stop()


@pytest.fixture
def settings() -> Settings:
    """Return test settings with safe defaults."""
    return Settings(
        azure_client_id="test-client-id",
        azure_client_secret="test-client-secret",
        token_cache_path=Path(".test_token_cache.json"),
        token_encryption_key="test-key-placeholder",
        mcp_api_key="test-api-key",
    )
