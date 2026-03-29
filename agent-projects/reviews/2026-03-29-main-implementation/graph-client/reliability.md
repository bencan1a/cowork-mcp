# Reliability Review: graph-client

**Date**: 2026-03-29 | **Files reviewed**: `graph/__init__.py`, `graph/client.py`, `auth/token_store.py` | **Reviewer**: reliability-reviewer

## Summary

The graph-client module implements a singleton `GraphClient` backed by MSAL token refresh via `TokenStore`. The singleton factory uses double-checked locking with `threading.Lock`, which is correct for the single-worker uvicorn deployment. However, there are meaningful reliability concerns around concurrent token cache writes lacking async-safe locking, synchronous blocking I/O called from async context, and the absence of any shutdown/cleanup path for the singleton.

---

## Findings

### [IMPORTANT] Token cache file writes are not protected against concurrent async interleaving
**File**: `auth/token_store.py` (lines 44-53, called from line 74)

**Issue**: `TokenStore.save()` is called from `acquire_token_silent()`, which is called from `_TokenStoreAccessTokenProvider.get_authorization_token()` on every Graph API request. When multiple MCP tool calls are in-flight simultaneously, multiple coroutines can call `save()` concurrently. The `has_state_changed` check, serialize, encrypt, and write sequence is not atomic -- interleaved writes could produce a corrupted or truncated cache file.

**Impact**: If two concurrent requests both trigger a token refresh (e.g., after cache expiry), interleaved `write_bytes()` calls could corrupt `TOKEN_CACHE_PATH`. On the next restart, `_load()` would fail to decrypt and the user would need to re-authenticate via `run_auth.py`.

**Recommendation**: Add an `asyncio.Lock` at the `GraphClient` or `TokenStore` level to serialize cache writes. Since `save()` is called from a sync method, the lock would need to be applied at the async boundary in `get_authorization_token()`:

```python
class _TokenStoreAccessTokenProvider(AccessTokenProvider):
    def __init__(self, store: TokenStore, settings: Settings) -> None:
        ...
        self._save_lock = asyncio.Lock()

    async def get_authorization_token(self, uri, ...) -> str:
        token = self._store.acquire_token_silent(...)
        async with self._save_lock:
            self._store.save()
        return token
```

This requires separating `save()` out of `acquire_token_silent()` so the async layer controls serialization.

**Rationale**: Over days of operation with concurrent MCP requests, cache corruption probability increases. Corruption forces manual re-authentication, which is disruptive for a headless systemd service.

---

### [IMPORTANT] Synchronous blocking I/O in async token provider
**File**: `graph/client.py` (lines 69-78)

**Issue**: `get_authorization_token()` is an `async` method but calls `self._store.acquire_token_silent()` synchronously. That method performs: (1) MSAL `acquire_token_silent()` which may make an HTTP call to Azure AD to refresh the token, and (2) file I/O via `save()`. Both of these block the asyncio event loop.

**Impact**: When a token refresh is needed (roughly every hour), the blocking MSAL HTTP call to Azure AD stalls the entire event loop for the duration of the network round-trip (typically 200-1000ms). All other in-flight MCP requests are frozen during this time. Under normal operation this is a brief stall; under network issues it could be multiple seconds.

**Recommendation**: Wrap the synchronous call in `asyncio.to_thread()`:

```python
async def get_authorization_token(self, uri, ...) -> str:
    return await asyncio.to_thread(
        self._store.acquire_token_silent,
        self._scopes,
        self._settings.azure_client_id,
        self._settings.azure_client_secret,
    )
```

Note: if using `asyncio.to_thread`, the thread-safety of `msal.SerializableTokenCache` must also be considered. MSAL's token cache is thread-safe for reads, but concurrent writes may need a `threading.Lock` around the `acquire_token_silent` + `save` sequence.

**Rationale**: Hourly token refreshes blocking the event loop cause periodic latency spikes for all concurrent requests. For a personal single-user tool this is tolerable but will manifest as mysterious timeouts during token refresh windows.

---

### [SUGGESTION] No shutdown cleanup for GraphClient singleton
**File**: `graph/client.py` (lines 118-132)

**Issue**: The `GraphClient` singleton is created at module import time (via `server.py` line 62) but is never cleaned up on shutdown. There is no lifespan hook, no `close()` method, and no signal handler. If `TokenStore.save()` is in-flight when systemd sends SIGTERM, the write could be interrupted, leaving a truncated cache file.

**Impact**: Low probability but non-zero: a token cache write interrupted by SIGTERM produces a corrupted file, requiring manual re-authentication.

**Recommendation**: Add a `close()` method to `GraphClient` that calls `self._store.save()`, and wire it into a FastMCP/Starlette lifespan or shutdown event. This ensures any pending cache state is flushed cleanly.

**Rationale**: systemd sends SIGTERM with a default 90-second grace period. A shutdown hook that flushes token state takes milliseconds and eliminates a class of corruption bugs.

---

### [SUGGESTION] MSAL app rebuilt on every token acquisition
**File**: `auth/token_store.py` (lines 61-78, 89-105)

**Issue**: `acquire_token_silent()` calls `_build_app()` which constructs a new `msal.ConfidentialClientApplication` on every invocation. MSAL app construction is not free -- it parses authority metadata and sets up internal state. Since this is called on every Graph API request, it adds unnecessary overhead.

**Impact**: No correctness issue, but unnecessary CPU and memory churn on every request. Over days of operation this is wasted work.

**Recommendation**: Cache the MSAL app instance in `TokenStore.__init__()` (keyed by client_id + client_secret) and reuse it across calls. The token cache is already shared via `self._cache`.

**Rationale**: Minor efficiency improvement that also simplifies the code. MSAL apps are designed to be long-lived singletons.

---

### [SUGGESTION] Mutable default argument in protocol method
**File**: `graph/client.py` (line 72)

**Issue**: `additional_authentication_context: dict[str, Any] = {}` uses a mutable default argument. While marked with `# noqa: B006` acknowledging it, in a long-running server this shared default dict could theoretically be mutated by Kiota internals, affecting subsequent calls.

**Impact**: Very low -- Kiota likely never mutates this argument. But the noqa suppression means linters won't catch it if Kiota's behavior changes.

**Recommendation**: No action required given the noqa comment. If ever revisited, use `None` with a conditional `or {}` inside the method body.

**Rationale**: Defensive coding for a long-running process.

---

## Verdict

**PASS WITH CHANGES**: The concurrent token cache write issue (first finding) is the most important to address before this runs unattended for weeks. The sync-in-async blocking is a secondary concern that causes periodic latency spikes but not data corruption. The remaining findings are hardening suggestions.

---

**Review performed by**: Claude Code (reliability-reviewer)
**Review focus**: Singleton lifecycle, concurrent async safety, token refresh failure recovery, resource cleanup, sync-in-async mixing
