from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config import Settings
from graph.client import GraphClient, build_scopes, get_graph_client, reset_graph_client


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure singleton is reset between tests."""
    reset_graph_client()
    yield
    reset_graph_client()


class TestBuildScopes:
    def test_includes_base_scopes(self) -> None:
        s = Settings()
        scopes = build_scopes(s)
        assert "User.Read" in scopes
        assert "offline_access" in scopes

    def test_mail_scopes_when_enabled(self) -> None:
        s = Settings(scope_mail_read=True, scope_mail_send=True)
        scopes = build_scopes(s)
        assert "Mail.ReadWrite" in scopes
        assert "Mail.Send" in scopes

    def test_no_duplicates(self) -> None:
        s = Settings(scope_mail_read=True, scope_mail_write=True)
        scopes = build_scopes(s)
        assert len(scopes) == len(set(scopes))

    def test_disabled_scope_excluded(self) -> None:
        # If ALL contacts scopes are off, Contacts.ReadWrite must not appear
        s2 = Settings(scope_contacts_read=False, scope_contacts_write=False)
        scopes2 = build_scopes(s2)
        assert "Contacts.ReadWrite" not in scopes2

    def test_disabled_tasks_scope_excluded(self) -> None:
        s = Settings(scope_tasks_read=False, scope_tasks_write=False)
        scopes = build_scopes(s)
        assert "Tasks.ReadWrite" not in scopes

    def test_returns_sorted_list(self) -> None:
        s = Settings()
        scopes = build_scopes(s)
        assert scopes == sorted(scopes)

    def test_base_scopes_always_present(self) -> None:
        # Even with every toggle disabled, base scopes are there
        s = Settings(
            scope_mail_read=False,
            scope_mail_write=False,
            scope_mail_send=False,
            scope_calendar_read=False,
            scope_calendar_write=False,
            scope_contacts_read=False,
            scope_contacts_write=False,
            scope_tasks_read=False,
            scope_tasks_write=False,
        )
        scopes = build_scopes(s)
        assert "User.Read" in scopes
        assert "MailboxSettings.ReadWrite" in scopes
        assert "offline_access" in scopes
        # No domain scopes
        assert "Mail.ReadWrite" not in scopes
        assert "Calendars.ReadWrite" not in scopes


class TestGraphClientSingleton:
    def _make_settings(self) -> Settings:
        return Settings(
            azure_client_id="test-client-id",
            azure_client_secret="test-client-secret",
            token_cache_path=Path(".test_cache.json"),
            token_encryption_key="dGVzdC1rZXktZm9yLXRlc3Rpbmctb25seS1kb250LXVzZQ==",
            mcp_api_key="test-api-key",
        )

    def test_singleton_returns_same_instance(self) -> None:
        settings = self._make_settings()
        with (
            patch("graph.client.GraphClient.__init__", return_value=None),
            patch.object(GraphClient, "_store", create=True, new_callable=MagicMock),
            patch.object(GraphClient, "_client", create=True, new_callable=MagicMock),
        ):
            c1 = get_graph_client(settings)
            c2 = get_graph_client(settings)
            assert c1 is c2

    def test_singleton_init_called_once(self) -> None:
        settings = self._make_settings()
        with (
            patch("graph.client.GraphClient.__init__", return_value=None) as mock_init,
            patch.object(GraphClient, "_store", create=True, new_callable=MagicMock),
            patch.object(GraphClient, "_client", create=True, new_callable=MagicMock),
        ):
            get_graph_client(settings)
            get_graph_client(settings)
            mock_init.assert_called_once()

    def test_reset_allows_new_instance(self) -> None:
        settings = self._make_settings()
        with (
            patch("graph.client.GraphClient.__init__", return_value=None),
            patch.object(GraphClient, "_store", create=True, new_callable=MagicMock),
            patch.object(GraphClient, "_client", create=True, new_callable=MagicMock),
        ):
            c1 = get_graph_client(settings)
            reset_graph_client()
            c2 = get_graph_client(settings)
            assert c1 is not c2
