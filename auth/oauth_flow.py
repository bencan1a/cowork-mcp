from __future__ import annotations

import logging
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

import msal

from auth.token_store import TokenStore

if TYPE_CHECKING:
    from config import Settings

logger = logging.getLogger(__name__)

# Microsoft Graph scopes required for all features
GRAPH_SCOPES = [
    "Mail.ReadWrite",
    "Mail.Send",
    "MailboxSettings.ReadWrite",
    "Calendars.ReadWrite",
    "Contacts.ReadWrite",
    "Tasks.ReadWrite",
    "User.Read",
]

_auth_code: str | None = None
_expected_state: str | None = None
_auth_event = threading.Event()


class _CallbackHandler(BaseHTTPRequestHandler):
    """Temporary HTTP handler to capture the OAuth redirect."""

    def do_GET(self) -> None:
        global _auth_code  # noqa: PLW0603
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        received_state = params.get("state", [None])[0]
        if received_state != _expected_state:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(
                b"<h1>Authentication failed</h1><p>State mismatch (possible CSRF).</p>"
            )
        elif "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authentication successful!</h1><p>You may close this tab.</p>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h1>Authentication failed</h1>")
        _auth_event.set()

    def log_message(
        self, format: str, *args: Any
    ) -> None:  # shadows builtin `format`; required by BaseHTTPRequestHandler signature
        logger.debug("OAuth callback: " + format, *args)


def run_oauth_flow(settings: Settings) -> None:
    """Run the interactive OAuth2 authorization code flow.

    Opens a browser for the user to log in, captures the redirect on
    localhost:8400, exchanges the code for tokens, and saves to TokenStore.
    """
    global _auth_code, _expected_state, _auth_event  # noqa: PLW0603
    _auth_code = None
    _expected_state = secrets.token_urlsafe(32)
    _auth_event = threading.Event()

    store = TokenStore(settings.token_cache_path, settings.token_encryption_key)

    app = msal.ConfidentialClientApplication(
        settings.azure_client_id,
        client_credential=settings.azure_client_secret,
        authority="https://login.microsoftonline.com/consumers",
        token_cache=store._cache,  # accessing private MSAL cache to share state
    )

    redirect_uri = "http://127.0.0.1:8400/callback"
    auth_url = app.get_authorization_request_url(
        scopes=GRAPH_SCOPES,
        redirect_uri=redirect_uri,
        state=_expected_state,
    )

    # Start local redirect capture server in a daemon thread
    server = HTTPServer(("127.0.0.1", 8400), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(
        f"\nOpening browser for Microsoft login...\nIf browser does not open, visit:\n{auth_url}\n"
    )
    webbrowser.open(auth_url)

    _auth_event.wait(timeout=300)
    server.server_close()

    if not _auth_code:
        raise RuntimeError("OAuth flow timed out or was cancelled")

    result = app.acquire_token_by_authorization_code(
        _auth_code,
        scopes=GRAPH_SCOPES,
        redirect_uri=redirect_uri,
    )

    if "access_token" not in result:
        error = result.get("error_description", result.get("error", "unknown"))
        raise RuntimeError(f"Token exchange failed: {error}")

    store.save()
    print("Authentication successful! Token saved.")
    logger.info("OAuth flow completed; token cached at %s", settings.token_cache_path)
