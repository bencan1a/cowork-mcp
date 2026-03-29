# Reliability Review: auth

**Date**: 2026-03-29 | **Files reviewed**: 3 (`auth/__init__.py`, `auth/oauth_flow.py`, `auth/token_store.py`) | **Reviewer**: reliability-reviewer

## Summary

The auth module provides encrypted token persistence (`TokenStore`) and a one-time interactive OAuth flow (`oauth_flow.py`). The `TokenStore` class is the most reliability-critical component: it is instantiated once inside the `GraphClient` singleton and its `acquire_token_silent()` and `save()` methods are called on every Graph API request from concurrent async handlers. The OAuth flow module (`oauth_flow.py`) runs only during manual setup and poses no runtime risk.

The primary concern is that `TokenStore.save()` writes the token cache file without any locking, and it is called from concurrent async request paths. Because the write itself is synchronous (`Path.write_bytes`), individual writes are unlikely to interleave within a single asyncio event loop tick, but MSAL `acquire_token_silent()` is also synchronous and blocking, meaning multiple concurrent requests can each trigger a save with stale state. Additionally, the synchronous blocking nature of MSAL calls in an async context is itself a concern.

---

## Findings

### [IMPORTANT] Token cache file writes are not protected by a lock

**File**: `auth/token_store.py` (lines 44-53)

**Issue**: `TokenStore.save()` writes to `TOKEN_CACHE_PATH` without any locking mechanism. The `save()` method is called from `acquire_token_silent()` (line 74) which is invoked on every Graph API request via `_TokenStoreAccessTokenProvider.get_authorization_token()` in `graph/client.py`. When multiple MCP tool calls are in-flight, two concurrent requests could both call `acquire_token_silent()`, both get `has_state_changed == True`, and both write the file. While Python's GIL and asyncio's single-threaded nature mean the individual `write_bytes()` calls won't literally interleave, the following race is possible:

1. Request A calls `acquire_token_silent()` -- MSAL refreshes the token, cache state changes
2. Event loop yields (e.g., during the `await` in the Kiota auth provider)
3. Request B calls `acquire_token_silent()` with the same cache object -- MSAL may refresh again
4. Both call `save()`, and one write may overwrite the other's state

More critically, if `has_state_changed` is checked by two concurrent callers before either writes, both will write, which is wasteful but not corrupting. However, if the MSAL cache object itself is modified concurrently (it is not thread-safe by MSAL's own documentation), the serialized state could be inconsistent.

**Impact**: Potential token cache corruption after a token refresh, requiring manual re-authentication via `run_auth.py`. Low probability on any single occurrence but accumulates over days of operation.

**Recommendation**: Add an `asyncio.Lock` to serialize all `acquire_token_silent` + `save` sequences:

```python
import asyncio

class TokenStore:
    def __init__(self, cache_path, encryption_key):
        ...
        self._lock = asyncio.Lock()

    async def acquire_token_silent_async(self, scopes, client_id, client_secret):
        async with self._lock:
            # run blocking MSAL call in executor (see next finding)
            token = await asyncio.get_event_loop().run_in_executor(
                None, self._acquire_token_silent_sync, scopes, client_id, client_secret
            )
            self.save()
            return token
```

**Rationale**: The token cache is the only persistent state this server manages. Corruption means the server silently stops being able to authenticate until a human intervenes.

---

### [IMPORTANT] Synchronous blocking MSAL calls in async request path

**File**: `auth/token_store.py` (lines 61-78), `graph/client.py` (lines 69-78)

**Issue**: `_TokenStoreAccessTokenProvider.get_authorization_token()` is an `async` method, but it calls `self._store.acquire_token_silent()` which is entirely synchronous. MSAL's `acquire_token_silent()` may perform an HTTP round-trip to Microsoft's token endpoint when the access token is expired and needs refresh (typically every 60 minutes). This HTTP call blocks the asyncio event loop for the duration of the network request (potentially seconds).

During that blocking period, all other concurrent MCP requests are stalled -- no request processing, no responses sent, no heartbeats.

**Impact**: Every ~60 minutes when the token refreshes, all in-flight MCP requests stall for 1-5 seconds. Under normal load this is barely noticeable, but combined with network issues to Microsoft's auth servers, it could cause cascading timeouts.

**Recommendation**: Wrap the blocking MSAL call in `asyncio.get_event_loop().run_in_executor()`:

```python
async def get_authorization_token(self, uri, additional_authentication_context={}):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        self._store.acquire_token_silent,
        self._scopes,
        self._settings.azure_client_id,
        self._settings.azure_client_secret,
    )
```

**Rationale**: A single-worker async server must never block the event loop on network I/O. Token refresh happens infrequently but blocks for a non-trivial duration.

---

### [SUGGESTION] TokenStore._load() silently ignores corrupted cache

**File**: `auth/token_store.py` (lines 29-42)

**Issue**: When the token cache file exists but cannot be decrypted (`InvalidToken`) or fails to load for any other reason, the code logs a warning and continues with an empty cache. This is correct behavior for startup resilience, but the generic `except Exception` on line 41 could mask serious issues (e.g., `PermissionError` meaning the file permissions are wrong, or `OSError` meaning disk is full).

**Impact**: Low -- the server starts but cannot authenticate, which will surface as a `RuntimeError` on the first Graph API call. The root cause (e.g., wrong permissions) will be harder to diagnose from logs alone.

**Recommendation**: Log at ERROR level for `PermissionError` and `OSError`, and include the exception type in the log message. Consider re-raising `PermissionError` since it indicates a deployment configuration problem that should fail fast.

```python
except InvalidToken:
    logger.warning("Token cache at %s could not be decrypted -- ignoring", self._cache_path)
except PermissionError:
    logger.error("Cannot read token cache at %s -- check file permissions", self._cache_path)
    raise
except Exception as exc:
    logger.warning("Failed to load token cache (%s): %s", type(exc).__name__, exc)
```

**Rationale**: Failing fast on permission errors prevents a confusing failure mode where the server starts but every request fails with "no cached accounts."

---

### [SUGGESTION] oauth_flow.py uses print() for user-facing output

**File**: `auth/oauth_flow.py` (lines 89-91, 111)

**Issue**: `run_oauth_flow()` uses `print()` for user-facing messages. This is acceptable because `oauth_flow.py` is only run interactively via `run_auth.py` and is never invoked by the long-running server process. No reliability impact.

**Impact**: None for the long-running server. Noted only for completeness.

**Recommendation**: No change needed. The `print()` usage is appropriate for an interactive CLI script.

---

### [SUGGESTION] GraphClient singleton uses threading.Lock instead of asyncio.Lock

**File**: `graph/client.py` (lines 118-132)

**Issue**: The `get_graph_client()` singleton factory uses `threading.Lock()`. In a single-worker asyncio server, `threading.Lock` works correctly (it won't deadlock because there's only one thread), but it is unnecessary overhead compared to the check-and-set pattern that asyncio's cooperative scheduling already guarantees for synchronous code paths. The real concern would be if `get_graph_client()` were called from an `async` context where the lock could block the event loop -- but since `GraphClient.__init__` is synchronous and fast, the blocking duration is negligible.

**Impact**: Negligible. The lock is acquired only on first initialization (once per server lifetime).

**Recommendation**: No change required. The current pattern is safe for the deployment model.

---

## Verdict

**PASS WITH CHANGES**

The two IMPORTANT findings should be addressed before long-term production operation:

1. **Token cache write locking** -- concurrent requests can race on `save()`, risking cache corruption that requires manual re-authentication.
2. **Blocking MSAL calls in async path** -- token refresh blocks the entire event loop, stalling all concurrent requests for the duration of the network round-trip.

Both issues manifest infrequently (roughly once per token refresh cycle, ~60 minutes) and may not cause visible problems during initial testing, but they will surface under sustained multi-request load over days of uptime.

---

**Review performed by**: Claude Code (reliability-reviewer)
**Review focus**: Concurrent async safety, Error recovery, Resource lifecycle, File locking, Token cache corruption scenarios
