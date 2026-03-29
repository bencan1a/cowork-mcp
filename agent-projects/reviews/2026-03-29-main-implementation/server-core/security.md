# Security Review: server-core

**Date**: 2026-03-29
**Files Reviewed**: 6 (server.py, config.py, auth/oauth_flow.py, auth/token_store.py, graph/client.py, run_auth.py)
**Reviewer**: security-reviewer
**Review Focus**: MCP Bearer Auth (timing attacks, auth bypass), Token Storage (Fernet encryption, chmod 600), MSAL OAuth (authority URL, secret handling), Secrets Management (env vars, logging), Input Validation, Host Binding

---

## Summary

The server core implements a sound security architecture for OAuth2 token management and MCP API authentication. Bearer auth is enforced at middleware level, tokens are encrypted at rest with Fernet and file permissions enforced to 600, and the MSAL authority is correctly set to the `consumers` endpoint for personal Microsoft accounts.

**Key Strengths:**
- BearerAuthMiddleware correctly enforces auth on all inbound requests with proper HTTP 401 responses
- Fernet encryption applied to token cache with chmod 600 enforcement after every write
- MSAL authority is correctly hardcoded to `https://login.microsoftonline.com/consumers` (not `common` or `organizations`)
- OAuth redirect callback binds only to localhost:8400 (not 0.0.0.0)
- Settings loads from `.env` only; no hardcoded secrets in source code
- Comprehensive test coverage for auth middleware including negative cases

**Critical Concern Identified:**
One timing-attack vulnerability in `BearerAuthMiddleware`: token comparison uses `==` instead of `hmac.compare_digest()`. This allows an attacker to measure response times to infer correct token characters.

---

## Findings

### 🔴 CRITICAL: Timing-Attack Vulnerability in Bearer Token Comparison

**File**: `/home/user/cowork-mcp/server.py` (lines 50-54)

**Issue**:
```python
async def dispatch(self, request: Request, call_next: Any) -> Response:
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {self._api_key}":  # ← Timing attack risk
        return Response("Unauthorized", status_code=401)
    return await call_next(request)
```

The `!=` operator (string equality comparison) exits early when a character mismatch is found. An attacker can measure response times to determine the correct token character by character in O(n) guesses instead of O(2^128). This is exploitable even on latency-constrained networks via repeated requests to a public endpoint.

**Recommendation**:
Use `hmac.compare_digest()` for constant-time comparison:

```python
import hmac

async def dispatch(self, request: Request, call_next: Any) -> Response:
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {self._api_key}"
    if not hmac.compare_digest(auth, expected):
        return Response("Unauthorized", status_code=401)
    return await call_next(request)
```

This ensures all incorrect tokens take the same time to reject, regardless of how many characters match the real token.

**Rationale**:
OWASP A02: Cryptographic Failures / A07: Authentication Failures. Timing attacks on authentication tokens are well-documented in security literature. The cowork-mcp security model explicitly requires timing-safe comparison (see CLAUDE.md: "Token comparison must use `hmac.compare_digest()` — never `==`").

**Test Coverage**:
The existing tests in `test_server.py` verify auth rejection/acceptance but do not measure response time. Consider adding a test that verifies constant-time behavior (though this is hard to test reliably; tool-level code review is the primary control).

---

### 🟡 IMPORTANT: Weak Default Secrets in config.py

**File**: `/home/user/cowork-mcp/config.py` (lines 18-28)

**Issue**:
```python
azure_client_id: str = Field(default="", description="Azure app client ID")
azure_client_secret: str = Field(default="", description="Azure app client secret")
token_encryption_key: str = Field(default="", description="Fernet encryption key for token cache")
mcp_api_key: str = Field(default="", description="Bearer token for MCP server auth")
```

All security-critical secrets default to empty strings. While empty strings will cause runtime failures when the server starts, they provide no early warning to a user who forgets to configure `.env`. More dangerously, if `.env` is misconfigured (e.g., commented lines or syntax errors), the app may silently fall back to these empty defaults without clear error logging.

Additionally, `token_encryption_key` defaults to `""`, which is not a valid Fernet key. If `Settings()` is instantiated without proper `.env` setup, the `TokenStore` constructor will fail with a cryptlib error instead of a clear configuration error.

**Recommendation**:
Remove defaults for security-sensitive fields and use `Field(..., description=...)` (no default) to force explicit configuration:

```python
from pydantic import Field

class Settings(BaseSettings):
    azure_client_id: str = Field(description="Azure app client ID")
    azure_client_secret: str = Field(description="Azure app client secret")
    token_encryption_key: str = Field(description="Fernet encryption key for token cache")
    mcp_api_key: str = Field(description="Bearer token for MCP server auth")
```

This will cause `pydantic_settings` to raise a clear `ValidationError` at startup if these vars are missing, rather than silently falling back to empty strings.

**Rationale**:
OWASP A05: Security Misconfiguration. Permissive defaults reduce the likelihood that misconfigurations are caught early, and can mask operator errors. Per CLAUDE.md, all secrets are env-only and must never have insecure defaults.

---

### 🔵 SUGGESTION: Host Binding Concerns in config.py

**File**: `/home/user/cowork-mcp/config.py` (lines 42-43)

**Issue**:
```python
host: str = Field(default="0.0.0.0")  # noqa: S104  # nosec B104 - intentional bind-all for server
```

The server binds to `0.0.0.0` by default, which exposes the MCP endpoint on all network interfaces. The nosec comment and docstring indicate this is intentional (for Cloudflare Tunnel deployment), but it remains a broad-surface bind.

In typical deployments, this is mitigated by:
1. Cloudflare Tunnel (user-facing TLS, authenticated)
2. Bearer auth on every request (MCP-level authentication)
3. Ubuntu server behind firewall

However, there are edge cases:
- If Cloudflare Tunnel is misconfigured or down, the endpoint is exposed to the network unencrypted.
- If `MCP_API_KEY` is weak, the Bearer auth can be brute-forced.
- If `.env` is missing, the server starts with auth token `""`, making it trivially bypassable.

**Recommendation** (low priority):
Document in `server.py` startup logs that the server is listening on `0.0.0.0:port`. Consider adding a warning log if `MCP_API_KEY` is short (< 32 bytes) or if deployed without Cloudflare Tunnel configured.

Example:
```python
if len(settings.mcp_api_key) < 32:
    logger.warning("MCP_API_KEY is short (%d chars); consider using a longer key", len(settings.mcp_api_key))

if settings.host == "0.0.0.0":
    logger.info("MCP server bound to 0.0.0.0:%d — ensure Cloudflare Tunnel is configured", settings.port)
```

**Rationale**:
OWASP A05: Security Misconfiguration / A04: Insecure Design. Defense in depth: even with Bearer auth, a broad network bind increases exposure if other controls fail.

---

### 🔵 SUGGESTION: Log Sensitive Data Safeguards

**File**: `/home/user/cowork-mcp/auth/oauth_flow.py` (lines 69-113)

**Issue**:
The `run_oauth_flow()` function and `TokenStore` are well-designed to avoid logging tokens. However, the OAuth flow logs:
- Authorization request URL (contains client_id, redirect_uri, scopes — safe)
- Token cache path (safe)

No sensitive data appears to be logged, but there is no explicit guard against accidental logging of `_auth_code` or token results.

**Recommendation** (low priority):
Add a comment or docstring to clarify that `_auth_code` and `result["access_token"]` must not be logged:

```python
def run_oauth_flow(settings: Settings) -> None:
    """Run the interactive OAuth2 authorization code flow.

    WARNING: Do NOT log _auth_code or access_token. They are sensitive credentials.
    """
    global _auth_code, _auth_event  # noqa: PLW0603
    # ... rest of function
```

And in `TokenStore.acquire_token_silent()`:
```python
def acquire_token_silent(self, scopes: list[str], client_id: str, client_secret: str) -> str:
    """Acquire an access token silently using the cached refresh token.

    NOTE: Do NOT log result["access_token"]. Return it safely without inspection.
    Raises RuntimeError if silent acquire fails (reauth required).
    """
```

**Rationale**:
OWASP A09: Security Logging and Monitoring Failures. Defensive coding against future developers accidentally logging credentials.

---

### 🔵 SUGGESTION: Empty String Validation for scope_*

**File**: `/home/user/cowork-mcp/config.py` (lines 31-39)

**Issue**:
The scope toggles are boolean fields with `default=True` or `default=False`. If a user accidentally sets `SCOPE_MAIL_READ=yes` or `SCOPE_MAIL_READ=maybe` in `.env`, pydantic will convert it to `True` (truthy). This is lenient and may mask typos.

Example: If `.env` has `SCOPE_MAIL_READ=yeS`, it will be silently parsed as `True`.

**Recommendation** (low priority):
No change needed if this lenient behavior is intentional. If stricter parsing is desired, add a validator:

```python
from pydantic import field_validator

@field_validator("scope_mail_read", ..., mode="before")
@classmethod
def validate_scope(cls, v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v.lower() not in ("true", "false"):
            raise ValueError(f"scope must be 'true' or 'false', got {v}")
        return v.lower() == "true"
    raise ValueError(f"scope must be bool or string, got {type(v)}")
```

But pydantic's default is reasonable for `.env` files.

**Rationale**:
Low severity. This is a convenience feature in pydantic, not a security gap.

---

## Verdict

⚠️ **PASS WITH CHANGES**

**Critical Finding (must fix):**
- Timing-attack vulnerability in `BearerAuthMiddleware` (line 52): Replace `!=` with `hmac.compare_digest()` for constant-time token comparison.

**Important Finding (should fix before merge):**
- Weak default secrets in `config.py`: Remove defaults for `azure_client_id`, `azure_client_secret`, `token_encryption_key`, and `mcp_api_key` to force explicit configuration and fail fast on misconfiguration.

**Suggestions (nice-to-have):**
- Add warning logs for weak `MCP_API_KEY` and broad host bind.
- Add code comments clarifying that tokens must never be logged.
- Consider stricter scope toggle validation (low priority).

The server architecture is fundamentally sound, but the timing-attack fix is non-negotiable for production deployment. The settings defaults should be fixed to catch misconfiguration errors at startup rather than runtime.

---

**Review performed by**: Claude Code (security-reviewer)
**Review scope**: OWASP Top 10 (A02, A05, A07, A09), cowork-mcp security model invariants, Bearer auth, Token storage, MSAL OAuth, Secrets hygiene
