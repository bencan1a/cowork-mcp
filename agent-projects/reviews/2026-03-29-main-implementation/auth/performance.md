# Performance Review: auth

**Date**: 2026-03-29 | **Files reviewed**: 3 | **Reviewer**: performance-reviewer

## Summary

The auth module consists of three files: an empty `__init__.py`, the one-time interactive `oauth_flow.py`, and the hot-path `token_store.py`. The OAuth flow is a setup-only script (never called during MCP tool serving) and has no performance concerns worth flagging. The `TokenStore` class, however, is invoked on every Graph API request via the Kiota `_TokenStoreAccessTokenProvider.get_authorization_token()` path and contains two findings related to blocking synchronous operations inside an async call chain and repeated MSAL application construction.

---

## Findings

### [IMPORTANT] MSAL app rebuilt on every token acquisition

**File**: `auth/token_store.py` (lines 61-79, 89-105)

**Issue**: `acquire_token_silent()` calls `self._build_app()` which constructs a new `msal.ConfidentialClientApplication` instance on every invocation. MSAL app construction includes parsing authority metadata and setting up internal caches. This method is called on every Graph API request because `_TokenStoreAccessTokenProvider.get_authorization_token()` (in `graph/client.py`, line 74) delegates directly to it.

**Impact**: Adds unnecessary CPU work and object allocation on every single MCP tool call. The overhead is modest (likely 1-5ms per call) but is pure waste that compounds when a single tool makes multiple Graph requests.

**Recommendation**: Build the `ConfidentialClientApplication` once during `TokenStore.__init__()` (or lazily on first use) and reuse it. The `client_id` and `client_secret` are invariant for the lifetime of the process. Store it as `self._app` and reuse in `acquire_token_silent()` and `get_account()`.

```python
# In __init__, or lazily:
self._app = msal.ConfidentialClientApplication(
    client_id, client_credential=client_secret,
    authority="https://login.microsoftonline.com/consumers",
    token_cache=self._cache,
)

# In acquire_token_silent:
result = self._app.acquire_token_silent(scopes, account=accounts[0])
```

**Rationale**: Eliminating per-request object construction is a straightforward win on every MCP tool call.

---

### [IMPORTANT] Synchronous file I/O and Fernet crypto in async call chain

**File**: `auth/token_store.py` (lines 29-42 for `_load`, lines 44-53 for `save`)

**Issue**: `TokenStore.save()` performs synchronous file write (`self._cache_path.write_bytes()`) and Fernet encryption, and `_load()` performs synchronous file read and Fernet decryption. These are called from `acquire_token_silent()` (line 74 of `save()`), which is called from the `async def get_authorization_token()` method in `graph/client.py` line 74 -- without `await` or `asyncio.to_thread()`. This means the synchronous `acquire_token_silent` call blocks the asyncio event loop.

Looking at `graph/client.py` line 69-78, `get_authorization_token` is declared `async def` but calls `self._store.acquire_token_silent()` synchronously. The entire MSAL token acquisition plus file I/O plus Fernet encrypt/decrypt runs blocking on the event loop.

**Impact**: For a typical invocation where the token is already cached and valid, the blocking time is small (file read of a few KB + Fernet decrypt + MSAL in-memory lookup + conditional Fernet encrypt + file write). Likely 1-10ms. However, when `acquire_token_silent` triggers a network refresh of an expired token, the MSAL HTTP call to Microsoft's token endpoint is also synchronous and could block for 100-500ms. During this time, all other concurrent MCP requests are stalled.

**Recommendation**: Wrap the synchronous `acquire_token_silent` call in `asyncio.to_thread()` inside `get_authorization_token`:

```python
async def get_authorization_token(self, uri: str, ...) -> str:
    import asyncio
    return await asyncio.to_thread(
        self._store.acquire_token_silent,
        self._scopes,
        self._settings.azure_client_id,
        self._settings.azure_client_secret,
    )
```

This offloads the entire synchronous chain (MSAL refresh + Fernet + file I/O) to a thread, keeping the event loop free.

**Rationale**: The project spec states this is a single-user server, so concurrent requests may be infrequent. However, a single MCP tool call can trigger multiple Graph API requests (each calling `get_authorization_token`), and blocking the event loop during a token refresh prevents those requests from running in parallel via `asyncio.gather`. This directly undermines the parallelization pattern recommended for Graph API call efficiency.

---

### [SUGGESTION] Token save on every successful silent acquisition

**File**: `auth/token_store.py` (lines 73-74)

**Issue**: `acquire_token_silent()` calls `self.save()` after every successful token acquisition. The `save()` method does check `has_state_changed` (line 46) and short-circuits if nothing changed, which is correct. However, when MSAL does refresh the token, `save()` runs Fernet encryption and a synchronous file write on every call that triggered a refresh. This is necessary for durability but worth noting that it happens in the hot path.

**Impact**: Minimal. The `has_state_changed` guard means this only runs on actual refreshes (roughly once per hour when the access token expires). No action needed.

**Rationale**: Documenting for completeness. The current design is correct -- persisting after refresh is the right trade-off between durability and performance.

---

### No findings for `auth/oauth_flow.py`

This file implements the one-time interactive OAuth setup flow (`run_oauth_flow`). It uses `threading`, `HTTPServer`, and `webbrowser` -- all appropriate for a synchronous CLI script that runs exactly once during initial setup. It is never called during MCP server operation. No performance concerns.

---

## Verdict

**PASS WITH CHANGES** -- Two important findings exist. The synchronous blocking of the event loop during token acquisition (especially during refresh) and the per-request MSAL app reconstruction should be addressed before merge. Neither will cause timeouts under normal single-user load, but they undermine the async architecture and will cause visible stalls when token refresh coincides with parallel Graph API calls.

---

**Review performed by**: Claude Code (performance-reviewer)
**Review focus**: Async correctness, Graph API efficiency, Pagination completeness, Token refresh overhead, HTTPX client lifecycle
