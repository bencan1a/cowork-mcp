"""Tests for server.py — BearerAuthMiddleware and app assembly."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import server
from server import BearerAuthMiddleware

if TYPE_CHECKING:
    from starlette.requests import Request


def _homepage(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def _make_test_app(api_key: str) -> Starlette:
    """Create a minimal Starlette app with BearerAuthMiddleware."""
    starlette_app = Starlette(routes=[Route("/", _homepage)])
    starlette_app.add_middleware(BearerAuthMiddleware, api_key=api_key)
    return starlette_app


class TestBearerAuthMiddleware:
    """Tests for BearerAuthMiddleware."""

    def test_no_auth_header_returns_401(self) -> None:
        client = TestClient(_make_test_app("secret"), raise_server_exceptions=False)
        response = client.get("/")
        assert response.status_code == 401

    def test_wrong_token_returns_401(self) -> None:
        client = TestClient(_make_test_app("secret"), raise_server_exceptions=False)
        response = client.get("/", headers={"Authorization": "Bearer wrong-token"})
        assert response.status_code == 401

    def test_correct_token_passes(self) -> None:
        client = TestClient(_make_test_app("secret"), raise_server_exceptions=False)
        response = client.get("/", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 200
        assert response.text == "ok"

    def test_missing_bearer_prefix_returns_401(self) -> None:
        """Bare token without 'Bearer ' prefix should be rejected."""
        client = TestClient(_make_test_app("secret"), raise_server_exceptions=False)
        response = client.get("/", headers={"Authorization": "secret"})
        assert response.status_code == 401

    def test_empty_authorization_header_returns_401(self) -> None:
        client = TestClient(_make_test_app("secret"), raise_server_exceptions=False)
        response = client.get("/", headers={"Authorization": ""})
        assert response.status_code == 401

    def test_basic_auth_scheme_returns_401(self) -> None:
        """Basic auth scheme should be rejected (only Bearer is accepted)."""
        client = TestClient(_make_test_app("secret"), raise_server_exceptions=False)
        response = client.get("/", headers={"Authorization": "Basic c2VjcmV0"})
        assert response.status_code == 401

    def test_correct_token_returns_body(self) -> None:
        """Verify the downstream response body is forwarded when auth passes."""
        client = TestClient(_make_test_app("my-api-key"), raise_server_exceptions=False)
        response = client.get("/", headers={"Authorization": "Bearer my-api-key"})
        assert response.status_code == 200
        assert response.text == "ok"

    def test_different_api_keys_are_independent(self) -> None:
        """Two apps with different keys should each only accept their own key."""
        client_a = TestClient(_make_test_app("key-a"), raise_server_exceptions=False)
        client_b = TestClient(_make_test_app("key-b"), raise_server_exceptions=False)

        assert client_a.get("/", headers={"Authorization": "Bearer key-a"}).status_code == 200
        assert client_a.get("/", headers={"Authorization": "Bearer key-b"}).status_code == 401
        assert client_b.get("/", headers={"Authorization": "Bearer key-b"}).status_code == 200
        assert client_b.get("/", headers={"Authorization": "Bearer key-a"}).status_code == 401

    def test_post_request_without_auth_returns_401(self) -> None:
        """Auth check applies to all HTTP methods."""

        def _post_handler(request: Request) -> PlainTextResponse:
            return PlainTextResponse("posted")

        starlette_app: Any = Starlette(routes=[Route("/submit", _post_handler, methods=["POST"])])
        starlette_app.add_middleware(BearerAuthMiddleware, api_key="secret")
        client = TestClient(starlette_app, raise_server_exceptions=False)
        response = client.post("/submit")
        assert response.status_code == 401

    def test_post_request_with_valid_auth_passes(self) -> None:
        """Valid auth on POST should pass through to the handler."""

        def _post_handler(request: Request) -> PlainTextResponse:
            return PlainTextResponse("posted")

        starlette_app: Any = Starlette(routes=[Route("/submit", _post_handler, methods=["POST"])])
        starlette_app.add_middleware(BearerAuthMiddleware, api_key="secret")
        client = TestClient(starlette_app, raise_server_exceptions=False)
        response = client.post("/submit", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 200


class TestServerAppExport:
    """Verify the module-level 'app' is importable and is an ASGI app."""

    def test_app_is_importable(self) -> None:
        """server.app must exist and be importable (for uvicorn server:app)."""
        assert hasattr(server, "app")

    def test_app_has_asgi_interface(self) -> None:
        """server.app must be callable (ASGI interface)."""
        assert callable(server.app)

    def test_bearer_auth_middleware_is_exported(self) -> None:
        """BearerAuthMiddleware must be importable from server module."""
        assert BearerAuthMiddleware is not None

    def test_mcp_instance_is_exported(self) -> None:
        """The FastMCP instance should be accessible on the module."""
        assert hasattr(server, "mcp")

    def test_app_rejects_unauthenticated_mcp_requests(self) -> None:
        """The assembled app should return 401 for requests without auth."""
        client = TestClient(server.app, raise_server_exceptions=False)
        response = client.post("/mcp")
        assert response.status_code == 401

    def test_app_rejects_wrong_token(self) -> None:
        """The assembled app should return 401 for wrong bearer token."""
        client = TestClient(server.app, raise_server_exceptions=False)
        response = client.post("/mcp", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401
