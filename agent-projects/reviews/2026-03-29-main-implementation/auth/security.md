# Security Review: auth

**Date**: 2026-03-29
**Branch**: main (full implementation)
**Files reviewed**: 3 (`auth/__init__.py`, `auth/oauth_flow.py`, `auth/token_store.py`)
**Reviewer**: security-reviewer

## Summary

The auth module is well-structured overall: Fernet encryption is properly applied to the token cache, file permissions are enforced to 600 after every write, the MSAL authority is correctly set to `https://login.microsoftonline.com/consumers` in both `oauth_flow.py` and `token_store.py`, secrets are loaded from `.env` (which is gitignored), and `run_auth.py` validates that required credentials exist before proceeding. However, there is one critical finding in the bearer auth middleware in `server.py` (timing-safe comparison), one important finding regarding the OAuth callback server bind address, and one important finding regarding missing empty-key guards on startup.

---

## Findings

### [CRITICAL] Bearer token comparison uses `==` instead of `hmac.compare_digest()`

**File**: `/home/user/cowork-mcp/server.py` (line 52)

**Issue**: The `BearerAuthMiddleware.dispatch` method compares the incoming bearer token with `!=` (string equality):

```python
if auth != f"Bearer {self._api_key}":
```

String equality comparison in Python short-circuits on the first differing byte, making it vulnerable to timing side-channel attacks. An attacker can iteratively guess the API key one character at a time by measuring response times. While this requires many requests and precise timing, it is a well-documented attack vector against authentication tokens, and the fix is trivial.

**Recommendation**: Use `hmac.compare_digest()` for the comparison:

```python
import hmac

expected = f"Bearer {self._api_key}"
if not hmac.compare_digest(auth, expected):
    return Response("Unauthorized", status_code=401)
```

**Rationale**: Violates the project's own security invariant ("Token comparison must use `hmac.compare_digest()` -- never `==`"). OWASP A02: Cryptographic Failures.

---

### [IMPORTANT] OAuth callback server binds to `localhost` instead of `127.0.0.1`

**File**: `/home/user/cowork-mcp/auth/oauth_flow.py` (line 85)

**Issue**: The temporary OAuth callback HTTP server is created with:

```python
server = HTTPServer(("localhost", 8400), _CallbackHandler)
```

The `CLAUDE.md` spec explicitly states this must bind to `127.0.0.1`. While `localhost` typically resolves to `127.0.0.1`, on systems with misconfigured `/etc/hosts` or dual-stack IPv6 configurations, it could resolve to `::1` (IPv6 loopback) or, in adversarial configurations, to a non-loopback address. On some Linux systems, `localhost` may also resolve to `0.0.0.0` in edge cases. Using the explicit IP address `127.0.0.1` removes this ambiguity.

Additionally, the MSAL redirect URI is `http://localhost:8400/callback` which is unencrypted HTTP. This is acceptable only because it is a loopback address -- but if `localhost` resolves unexpectedly, the auth code could be intercepted on the network.

**Recommendation**: Bind to the explicit loopback address:

```python
server = HTTPServer(("127.0.0.1", 8400), _CallbackHandler)
```

**Rationale**: CLAUDE.md invariant: "run_auth.py localhost callback server binds to 127.0.0.1 only -- never 0.0.0.0". OWASP A04: Insecure Design.

---

### [IMPORTANT] No startup guard against empty `MCP_API_KEY`

**File**: `/home/user/cowork-mcp/server.py` (lines 61-62), `/home/user/cowork-mcp/config.py` (line 28)

**Issue**: `mcp_api_key` defaults to an empty string `""` in `Settings`. If the `.env` file is missing or `MCP_API_KEY` is not set, the server starts with `self._api_key = ""`. The bearer auth middleware would then accept any request with the header `Authorization: Bearer ` (with an empty token value). This effectively disables authentication entirely.

The same concern applies to `azure_client_id`, `azure_client_secret`, and `token_encryption_key` -- all default to empty strings. While the server may fail later at token acquisition time, the MCP API key is the front-door authentication and must never be empty in production.

**Recommendation**: Add a `field_validator` or `model_validator` in `Settings` that raises a `ValueError` if `mcp_api_key` is empty at startup. Alternatively, add a startup check in `server.py` before constructing the ASGI app:

```python
if not settings.mcp_api_key:
    raise RuntimeError("MCP_API_KEY must be set in .env -- server cannot start without authentication")
```

**Rationale**: OWASP A07: Authentication Failures. An unconfigured deployment would be fully open.

---

### [IMPORTANT] OAuth flow does not use PKCE or validate the `state` parameter

**File**: `/home/user/cowork-mcp/auth/oauth_flow.py` (lines 79-82, 100-104)

**Issue**: The `get_authorization_request_url()` call does not explicitly request a PKCE challenge (`code_challenge` / `code_challenge_method`), and there is no `state` parameter generated or validated on the callback. While MSAL for Python may handle PKCE internally for confidential clients, the absence of a `state` parameter means the OAuth flow is vulnerable to CSRF attacks: an attacker could craft a malicious redirect to `http://localhost:8400/callback?code=ATTACKER_CODE` and associate their account with the victim's token cache.

In practice, this is mitigated by the fact that the callback server only runs briefly during interactive setup on localhost, reducing the attack window. However, the `state` parameter is a standard OAuth 2.0 defense-in-depth mechanism.

**Recommendation**: MSAL's `get_authorization_request_url()` returns a `state` value that should be captured and validated in the callback handler:

```python
flow = app.initiate_auth_code_flow(scopes=GRAPH_SCOPES, redirect_uri=redirect_uri)
auth_url = flow["auth_uri"]
# In callback handler, validate state matches flow["state"]
```

Alternatively, use `initiate_auth_code_flow` + `acquire_token_by_auth_code_flow` which handles state validation automatically.

**Rationale**: OWASP A04: Insecure Design. OAuth 2.0 RFC 6749 Section 10.12 (CSRF protection).

---

### [SUGGESTION] Token cache file created before `chmod 600` -- race condition window

**File**: `/home/user/cowork-mcp/auth/token_store.py` (lines 49-52)

**Issue**: The `save()` method calls `write_bytes()` first, then `chmod()`. Between the write and the chmod, the file exists with the default umask permissions (typically 644), creating a brief window where other users on the system could read the encrypted token cache. While the tokens are Fernet-encrypted, defense-in-depth says the file should never be world-readable even momentarily.

**Recommendation**: Set the umask before writing, or use `os.open()` with explicit mode flags to create the file with 600 permissions atomically:

```python
import os
fd = os.open(str(self._cache_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'wb') as f:
    f.write(encrypted)
```

**Rationale**: Defense-in-depth for token storage invariant. Low exploitability on a single-user server.

---

### [SUGGESTION] OAuth callback handler does not validate the `error` parameter

**File**: `/home/user/cowork-mcp/auth/oauth_flow.py` (lines 37-51)

**Issue**: The `_CallbackHandler.do_GET` method checks for `code` in the query parameters but does not inspect the `error` or `error_description` parameters that Microsoft returns when the user denies consent or an error occurs. The handler falls through to the generic 400 response, and the flow eventually times out after 300 seconds rather than failing immediately with a descriptive error.

**Recommendation**: Check for `error` in `params` and surface the error message immediately, then set `_auth_event` to unblock the main thread without waiting for the timeout.

**Rationale**: Improved error handling. Low security impact but affects operational reliability during auth setup.

---

## Verdict

- **NEEDS REWORK**: One critical finding (timing-unsafe token comparison in bearer auth middleware) must be fixed before merge. The three important findings (localhost bind address, empty API key guard, missing PKCE/state) should also be addressed.

---

**Review performed by**: Claude Code (security-reviewer)
**Review focus**: MCP Bearer Auth, Token Storage (Fernet + chmod 600), MSAL OAuth, Graph Input Validation, Secrets/Env, Subprocess Safety, Dependency CVEs
