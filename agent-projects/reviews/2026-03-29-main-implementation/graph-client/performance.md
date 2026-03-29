# Performance Review: graph-client

**Date**: 2026-03-29 | **Files reviewed**: 2 (`graph/__init__.py`, `graph/client.py`) | **Reviewer**: performance-reviewer

---

## Summary

The graph client module provides a singleton `GraphClient` backed by the Microsoft Graph SDK, with token acquisition delegated to `TokenStore` via a Kiota `AccessTokenProvider`. The singleton pattern and SDK-managed HTTP client lifecycle are sound. However, there is one critical issue: the `get_authorization_token` async method calls synchronous MSAL code that can perform blocking network I/O (token refresh) and synchronous disk I/O (cache save) directly on the event loop. A second important finding concerns the repeated construction of MSAL application objects on every token acquisition call.

---

## Findings

### [CRITICAL] Synchronous blocking inside async `get_authorization_token`

**File**: `graph/client.py` (lines 69-78), with blocking work in `auth/token_store.py` (lines 61-78)

**Issue**: `_TokenStoreAccessTokenProvider.get_authorization_token()` is an `async def` method (required by the Kiota `AccessTokenProvider` protocol) but calls `self._store.acquire_token_silent()` synchronously. That method does three potentially blocking things on the event loop:

1. `msal.ConfidentialClientApplication()` is constructed fresh each call (line 66 of `token_store.py`), which involves metadata discovery (potentially a network call on first use or cache miss).
2. `app.acquire_token_silent()` (line 72 of `token_store.py`) will make a synchronous HTTPS request to Azure AD when the access token has expired and a refresh is needed. MSAL uses the `requests` library internally for these calls. This blocks the uvicorn event loop for the full duration of the HTTP round-trip (typically 200-800ms).
3. `self.save()` (line 74 of `token_store.py`) performs synchronous file write, Fernet encryption, and `chmod` on disk.

When the access token is still cached and valid, `acquire_token_silent` returns quickly from the in-memory cache and the impact is minimal. But once per token lifetime (typically every 60 minutes), the refresh path blocks the event loop for hundreds of milliseconds, stalling all concurrent MCP requests.

**Impact**: Every 60 minutes (on token refresh), all concurrent MCP tool calls are blocked for 200-800ms while the synchronous MSAL refresh completes. During normal cached-token hits, impact is low (sub-millisecond).

**Recommendation**: Wrap the synchronous `acquire_token_silent` call in `asyncio.to_thread()` so the blocking MSAL and file I/O runs on a thread pool worker:

```python
async def get_authorization_token(self, uri: str, additional_authentication_context: dict[str, Any] = {}) -> str:
    return await asyncio.to_thread(
        self._store.acquire_token_silent,
        self._scopes,
        self._settings.azure_client_id,
        self._settings.azure_client_secret,
    )
```

This keeps the event loop free even during token refreshes.

**Rationale**: An MCP server handling multiple tool calls concurrently cannot afford to block the event loop on network I/O. Even a single blocked refresh stalls every in-flight request.

---

### [IMPORTANT] MSAL application object rebuilt on every token acquisition

**File**: `auth/token_store.py` (lines 61-66, 89-105)

**Issue**: Every call to `acquire_token_silent()` calls `self._build_app()`, which constructs a new `msal.ConfidentialClientApplication` instance. While MSAL caches authority metadata internally after the first discovery, the object construction itself is unnecessary overhead repeated on every single Graph API request (since the Kiota auth provider calls `get_authorization_token` for each request).

**Impact**: Minor CPU overhead per request (object allocation, authority URL parsing). The MSAL authority metadata cache mitigates the worst case (network discovery), but the repeated construction is wasteful given the parameters never change.

**Recommendation**: Cache the MSAL app instance on `TokenStore` after first construction (keyed by `client_id`), or build it once in `TokenStore.__init__` if the client ID and secret are known at construction time. Since `GraphClient.__init__` already has access to settings, the MSAL app could be pre-built and reused.

**Rationale**: Eliminating per-request object construction reduces GC pressure and makes the token-cached fast path faster.

---

### [SUGGESTION] Singleton uses `threading.Lock` -- adequate but worth noting

**File**: `graph/client.py` (lines 118-132)

**Issue**: The double-checked locking singleton uses `threading.Lock`. This is correct for thread safety during initialization. Since `get_graph_client()` is typically called once at module level in `server.py`, the lock is not in any hot path. No change needed.

**Impact**: None in practice -- the lock is only contended during startup.

**Recommendation**: No action required. Documented here for completeness.

**Rationale**: N/A.

---

### [SUGGESTION] HTTPX client lifecycle managed by msgraph SDK -- no issue

**File**: `graph/client.py` (lines 100-103)

**Issue**: The code uses `GraphRequestAdapter` and `GraphServiceClient` from the official `msgraph` SDK, which manages its own HTTP client lifecycle internally. There are no raw `httpx.AsyncClient` instantiations in this module.

**Impact**: None -- this is the correct pattern.

**Recommendation**: No action required.

**Rationale**: The SDK handles connection pooling and reuse internally.

---

## Verdict

**WARNING** **PASS WITH CHANGES**: One critical finding (synchronous blocking in async token provider) should be addressed before merge to prevent event loop stalls during token refresh. The MSAL app reconstruction is a secondary concern that should also be fixed for cleaner per-request performance.

| Severity | Count |
|----------|-------|
| CRITICAL | 1     |
| IMPORTANT | 1    |
| SUGGESTION | 2   |

---

**Review performed by**: Claude Code (performance-reviewer)
**Review focus**: Async correctness, Graph API efficiency, Pagination completeness, Token refresh overhead, HTTPX client lifecycle
