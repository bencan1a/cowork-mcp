# Security Review: graph-client

**Date**: 2026-03-29 | **Files reviewed**: 2 (`graph/__init__.py`, `graph/client.py`) + `auth/token_store.py` for context | **Reviewer**: security-reviewer

## Summary

The graph client module implements a singleton `GraphClient` backed by MSAL token refresh via `TokenStore`, exposed to the Kiota/msgraph SDK through a custom `_TokenStoreAccessTokenProvider`. The MSAL authority is correctly hardcoded to `consumers`. Token storage uses Fernet encryption with chmod 600 enforcement. The singleton uses proper double-checked locking. Two findings were identified: one important (AllowedHostsValidator bypass) and one critical (timing-safe comparison missing in `server.py` bearer auth, noted here since it was encountered during contextual review).

---

## Findings

### [IMPORTANT] AllowedHostsValidator initialized with empty list permits requests to any host
**File**: `graph/client.py` (line 67)

**Issue**: `_TokenStoreAccessTokenProvider` initializes `AllowedHostsValidator([])` with an empty allowed-hosts list. Per the Kiota SDK behavior, an empty list means "allow all hosts" -- the provider will supply an access token to any URL the SDK is asked to call. If any code path (current or future) constructs a request to a non-Microsoft endpoint using this Graph client's request adapter, the access token would be sent to that endpoint. Today all calls go through `GraphServiceClient` which targets `graph.microsoft.com`, so the practical risk is low. However, this violates the principle of least privilege and creates a latent SSRF-adjacent credential leak vector if the adapter is ever reused.

**Recommendation**: Restrict the validator to the known Microsoft Graph host:
```python
self._validator = AllowedHostsValidator(["graph.microsoft.com"])
```

**Rationale**: A10 (SSRF) / Defense in depth. Access tokens should only be sent to explicitly allowed hosts.

---

### [CRITICAL] Bearer auth token comparison uses string equality instead of hmac.compare_digest
**File**: `server.py` (line 52)

**Issue**: While reviewing `graph/client.py` in context, the bearer auth middleware in `server.py` was observed using `auth != f"Bearer {self._api_key}"` -- a standard Python string comparison. This is vulnerable to timing side-channel attacks where an attacker can iteratively guess the API key by measuring response time differences. Over a network (especially through Cloudflare Tunnel), the timing signal is noisy but not negligible for a persistent attacker.

**Recommendation**: Replace with:
```python
import hmac

expected = f"Bearer {self._api_key}"
if not hmac.compare_digest(auth.encode(), expected.encode()):
    return Response("Unauthorized", status_code=401)
```

**Rationale**: A02 (Cryptographic Failures). The project's own security invariants explicitly require `hmac.compare_digest()` for token comparison -- never `==`.

---

### [SUGGESTION] acquire_token_silent error message may leak MSAL error details to callers
**File**: `auth/token_store.py` (lines 76-78)

**Issue**: When `acquire_token_silent` fails, the `error_description` from the MSAL result is included in the `RuntimeError` message. This error string originates from Microsoft's token endpoint and could contain internal details (tenant info, token hints, correlation IDs). This `RuntimeError` propagates up through `_TokenStoreAccessTokenProvider.get_authorization_token()` and may surface in MCP tool error responses depending on how `server.py` handles exceptions.

**Recommendation**: Log the full error at `logger.warning` level for diagnostics, but raise a generic message to callers:
```python
logger.warning("Silent token acquisition failed: %s", error)
raise RuntimeError("Authentication expired -- run `python run_auth.py` to re-authenticate")
```

**Rationale**: A05 (Security Misconfiguration) / A09 (Logging). Error details from upstream services should not be forwarded to external callers.

---

### [SUGGESTION] Mutable default argument in get_authorization_token signature
**File**: `graph/client.py` (line 72)

**Issue**: The `additional_authentication_context` parameter defaults to `{}` (a mutable default). While this parameter is unused (as noted by the `noqa` comment) and the method never mutates it, mutable defaults are a known Python footgun. The `# noqa: B006` suppression acknowledges this. No security impact today, but if future code were to mutate this dict, state would leak across calls.

**Recommendation**: Change to `None` with a docstring noting protocol conformance, or leave as-is with the existing suppression. This is cosmetic and acknowledged.

**Rationale**: Defensive coding. No exploitable impact.

---

## What Looks Good

- **MSAL authority**: Correctly hardcoded to `https://login.microsoftonline.com/consumers` in `token_store.py` line 93. No `common` or `organizations` variant.
- **Singleton thread safety**: `get_graph_client()` uses proper double-checked locking with `threading.Lock()`. The outer `None` check avoids lock contention on the hot path; the inner check prevents race conditions.
- **Token encryption**: Fernet encryption with chmod 600 enforcement on every write. `InvalidToken` exceptions (corrupted cache) are caught gracefully and logged without exposing key material.
- **No credential logging**: Neither `graph/client.py` nor `auth/token_store.py` log access tokens, refresh tokens, client secrets, or encryption keys. Logger calls reference only file paths and generic status messages.
- **Token acquisition centralized**: All token acquisition goes through `TokenStore.acquire_token_silent()` -- no scattered raw MSAL calls.
- **`reset_graph_client()`**: Properly guarded by the lock; exists for tests only as documented.

---

## Verdict

**NEEDS REWORK** -- The `hmac.compare_digest` violation in `server.py` line 52 is a CRITICAL finding against an explicit project invariant. The `AllowedHostsValidator` empty-list issue is IMPORTANT. Both should be addressed before merge.

---

**Review performed by**: Claude Code (security-reviewer)
**Review focus**: MCP Bearer Auth, Token Storage (Fernet + chmod 600), MSAL OAuth, Graph Input Validation, Secrets/Env, Subprocess Safety, Dependency CVEs
