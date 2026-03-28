from __future__ import annotations

import pytest

from config import Settings


@pytest.fixture
def settings() -> Settings:
    """Return test settings with safe defaults."""
    return Settings(
        azure_client_id="test-client-id",
        azure_client_secret="test-client-secret",
        token_cache_path=".test_token_cache.json",
        token_encryption_key="test-key-placeholder",
        mcp_api_key="test-api-key",
    )
