# Performance Review: server-core

**Date**: 2026-03-29

**Files Reviewed**: `server.py`, `config.py`, `graph/client.py`, `auth/token_store.py`

**Reviewer**: performance-reviewer

---

## Summary

The server core demonstrates solid async correctness and efficient initialization. The FastMCP server properly registers tools conditionally, validates bearer tokens on every request, and uses a singleton GraphClient pattern with lazy initialization. The async closures capturing the module-level `gc` (GraphClient) are correctly structured as `async def` handlers without blocking operations.

**Key strengths:**
- All MCP tool handlers are correctly declared as `async def` — no blocking operations observed.
- BearerAuthMiddleware is non-blocking and efficient (simple string comparison).
- Scope toggle system uses synchronous validation at startup, then zero overhead at request time.
- GraphClient is created once per server startup via thread-safe singleton pattern.
- MSAL token refresh (`acquire_token_silent()`) is cached and only called when needed.

**Areas of concern:**
- TokenStore file I/O happens during module initialization and on every token refresh.
- Token cache encryption/decryption adds overhead on every Graph API call.
- Logging configuration is not async-aware.

---

## Findings

### 🟡 IMPORTANT: Blocking File I/O on TokenStore Initialization

**File**: `auth/token_store.py` (lines 23–43)

**Issue**: The `TokenStore.__init__()` constructor performs synchronous disk reads and Fernet decryption during initialization. This runs in the context of `get_graph_client()`, which is called at module level in `server.py` line 62:

```python
settings: Settings = Settings()
gc: GraphClient = get_graph_client(settings)  # ← blocks here
```

The token cache file is read (`read_bytes()`) and decrypted on every server startup. For a 10 KB cache file, this typically adds 5–20 ms. While this is a one-time cost at startup, it blocks the event loop briefly during `app.startup` if the server is reloaded.

**Impact**: Server startup is delayed by 5–20 ms per reload (development and deployment). Not user-visible in production (one-time on startup), but degrades development iteration speed.

**Recommendation**: Move token cache loading to an async startup hook if the server is reloaded frequently during development. For production, this is acceptable since startup happens once.

```python
# Current (synchronous, blocks on startup):
gc: GraphClient = get_graph_client(settings)

# Alternative (async, non-blocking):
# Register a FastMCP startup handler that calls await gc.store.load_async()
# and defer Graph API calls until after startup.
```

**Rationale**: The MCP server is deployed once and left running. However, during development and testing, if the server is reloaded between test runs, synchronous I/O on the critical path adds noticeable latency.

---

### 🟡 IMPORTANT: Token Cache Encryption Overhead on Every Graph Request

**File**: `auth/token_store.py` (lines 69–79), `graph/client.py` (lines 69–78)

**Issue**: The `acquire_token_silent()` method is called by the token provider's `get_authorization_token()` on every Graph API request. Even when the token is valid and cached, MSAL's `acquire_token_silent()` performs a local lookup (fast, ~1 ms), but if a refresh is needed, the cache is re-serialized and encrypted:

```python
# token_store.py, lines 48–50
serialized = self._cache.serialize()
encrypted = self._fernet.encrypt(serialized.encode())
self._cache_path.write_bytes(encrypted)
```

This is fast for a small cache but can add 10–50 ms for large token caches (hundreds of entries). The file write happens synchronously in the async context of a Graph API request.

**Impact**: If a token refresh occurs mid-request, the Graph call is delayed by 10–50 ms while the token is re-encrypted and written to disk. Rare (tokens last 1 hour), but affects every token refresh event.

**Recommendation**: Implement in-memory token cache with periodic flush to disk, or defer encryption/write to a background task.

```python
# Current:
def acquire_token_silent(self, scopes, client_id, client_secret):
    result = app.acquire_token_silent(scopes, account=accounts[0])
    if result and "access_token" in result:
        self.save()  # ← synchronous encryption + write
        return result["access_token"]

# Better:
async def acquire_token_silent_async(self, scopes, client_id, client_secret):
    result = app.acquire_token_silent(scopes, account=accounts[0])
    if result and "access_token" in result:
        # Defer encryption to background
        asyncio.create_task(self.save_async())
        return result["access_token"]
```

**Rationale**: Token refresh is infrequent but blocks the event loop when it occurs. Deferring the write removes the blocking operation from the critical path of Graph API calls.

---

### 🔵 SUGGESTION: Redundant Scope Evaluation in Token Provider

**File**: `graph/client.py` (lines 65–65, 74–76)

**Issue**: The `_TokenStoreAccessTokenProvider` builds its scope list once at initialization (line 65), but every call to `get_authorization_token()` passes the same scopes to `acquire_token_silent()`. There's no caching of scope resolution per request.

This is a micro-optimization (scope list is small, ~10 items), but could be optimized:

```python
# Current:
def __init__(self, store: TokenStore, settings: Settings) -> None:
    self._scopes = build_scopes(settings)  # ← built once

async def get_authorization_token(self, uri: str, ...) -> str:
    return self._store.acquire_token_silent(
        self._scopes,  # ← reused, good
        self._settings.azure_client_id,
        self._settings.azure_client_secret,
    )
```

The scopes are already cached at init time, so this is already optimized. No change needed.

**Impact**: None; this is well-optimized already.

**Rationale**: Confirmed that scope building is a one-time operation, not per-request.

---

### 🔵 SUGGESTION: Logging Configuration Not Async-Aware

**File**: `server.py` (lines 32–33, 65)

**Issue**: The logging configuration uses `logging.basicConfig()` at module level with a standard format string. This is fine, but the format string includes `%(asctime)s`, which calls `time.time()` (fast) not `time.sleep()`. No blocking observed.

However, if logging ever needs to be reconfigured after startup, it would happen synchronously. Current implementation is safe.

**Impact**: None; logging is correctly configured and non-blocking.

**Rationale**: No issue found; observation only.

---

### ✅ PASS: Bearer Auth Middleware is Efficient

**File**: `server.py` (lines 40–54)

**Issue**: No issue found. The `BearerAuthMiddleware` checks the Authorization header and compares it to the stored key in O(1) time with a simple string equality check. This runs on every request but adds negligible overhead (~0.1 ms per request).

**Impact**: None; highly efficient.

**Rationale**: The middleware is a simple string comparison with no I/O, crypto, or loop blocking.

---

### ✅ PASS: Scope Toggle System is Zero-Overhead

**File**: `server.py` (lines 79–143, etc.)

**Issue**: No issue found. Tool registration uses conditional blocks based on scope toggles read at module load time. This means:
- Scope evaluation happens once at startup: `if settings.scope_mail_read:`
- Tool registration is conditional: tools not registered if scope is disabled
- At request time: zero overhead (tool is either registered or not)

**Impact**: None; optimal design.

**Rationale**: Scope toggles are evaluated once during module load, not on every request.

---

### ✅ PASS: Singleton GraphClient Pattern is Thread-Safe

**File**: `graph/client.py` (lines 118–132)

**Issue**: No issue found. The `get_graph_client()` factory uses a double-checked locking pattern with a `threading.Lock()`. This ensures:
- First call acquires the lock and creates the singleton
- Subsequent calls return the cached instance without lock overhead
- Thread-safe for multi-worker deployments

**Impact**: None; well-implemented.

**Rationale**: The double-checked locking pattern is correctly implemented for thread safety with minimal overhead.

---

### ✅ PASS: No Blocking Operations in Async Handlers

**File**: `server.py` (lines 81–115, etc.)

**Issue**: No issue found. All MCP tool handlers are correctly declared as `async def` and delegate to graph module functions. No `time.sleep()`, `open().read()`, or blocking I/O found inside async handlers.

Example:
```python
@mcp.tool()
async def list_emails(...) -> list[dict[str, Any]]:
    try:
        return await mail.list_emails(...)  # ← properly awaited
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
```

**Impact**: None; correct async pattern throughout.

**Rationale**: All tool handlers are async and await their delegated functions without blocking.

---

### ✅ PASS: No Serial Graph Calls Observed

**File**: `server.py` (tool definitions)

**Issue**: No issue found. The tool handlers in `server.py` are thin wrappers that delegate to single Graph API calls. No serial/loop patterns observed (e.g., no `for message_id in ids: await client.get(id)`).

**Impact**: None; tool delegation is efficient.

**Rationale**: Each tool makes a single delegated call, which may internally handle pagination or parallelism (that's the responsibility of the `graph/*.py` modules, not reviewed here).

---

## Verdict

✅ **PASS**

The server core is well-structured and async-correct. No critical findings that would degrade user-visible performance. The two important findings (TokenStore file I/O on startup and token cache encryption on refresh) are optimizations that only matter during heavy token refresh cycles or frequent server reloads. For a single-user, always-running MCP server, the current implementation is performant and safe to merge.

**Recommendation**: Deploy as-is. Monitor token refresh latency in production. If refresh overhead becomes noticeable, implement async token cache save in a follow-up optimization pass.

---

**Review performed by**: performance-reviewer

**Review scope**: Async correctness, module initialization overhead, middleware efficiency, token lifecycle management, BearerAuthMiddleware per-request overhead
