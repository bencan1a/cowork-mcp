# Reliability Review: server-core

**Date**: 2026-03-29
**Files Reviewed**: 4
**Reviewer**: reliability-reviewer

## Summary

The server core implements a single-process async FastMCP server with scope-based tool registration, Bearer auth enforcement, and a singleton Graph client. The codebase demonstrates good foundational patterns: error conversion at tool boundaries (RuntimeError → ValueError), explicit scope toggle logging, and secure token cache handling.

However, **three important reliability gaps** have been identified that will cause operational issues in production:

1. **Module-level initialization without error handling** — Settings() and get_graph_client() are called at import time with no graceful degradation if .env is missing or invalid
2. **No lifespan hooks** — The server has no startup validation, graceful shutdown, or token cache persistence sync on termination
3. **Concurrent token cache writes without locking** — Multiple async requests can race on TokenStore.save(), corrupting the encrypted cache file

The first issue is critical for deployment (systemd boot failure). The second is important for data safety (token refresh state lost on hard shutdown). The third will surface under moderate concurrent load (multiple tool calls in-flight).

---

## Findings

### CRITICAL

#### Finding 1.1: Unguarded Module-Level Settings Initialization

**File**: `/home/user/cowork-mcp/server.py` (lines 61-62)

```python
settings: Settings = Settings()
gc: GraphClient = get_graph_client(settings)
```

**Issue**: `Settings()` is called at module import time with no error handling. If `.env` is missing or contains invalid values (e.g., invalid `log_level`), the server fails to import entirely, preventing `uvicorn server:app` from starting. Similarly, `get_graph_client()` may fail if `token_encryption_key` is empty or malformed.

**Impact**: On systemd startup, if `.env` is misconfigured:
- Server fails to import → uvicorn crashes with no ASGI app
- systemd sees exit code 1 and respects `Restart=always`, but logs become difficult to diagnose
- No clear feedback about which setting is wrong
- The server cannot start until `.env` is manually corrected

**Recommendation**: Add a startup lifespan hook to FastMCP that validates configuration before the ASGI app is callable. Move Settings() and get_graph_client() into the lifespan hook, fail fast with a clear error message if validation fails, and log which settings are missing.

**Rationale**: A single-instance systemd service must fail fast and clearly during boot, not silently retry. Validation at startup also makes deployment automation easier (can check logs for "ready" vs. "failed").

---

#### Finding 1.2: Unguarded Module-Level GraphClient Initialization

**File**: `/home/user/cowork-mcp/graph/client.py` (lines 122-132, exported to server.py line 62)

**Issue**: `get_graph_client(settings)` is called at module level in server.py. If the token cache file is corrupted (invalid Fernet key, malformed JSON after decryption), `TokenStore.__init__()` will catch the exception and log a warning (line 40), but the GraphClient is still created successfully. This masks the underlying issue: the token cache is unrecoverable, and the next call to `acquire_token_silent()` will fail with "No cached accounts found" and require re-authentication.

At import time, this is not immediately obvious. The first request that needs a token will fail with a cryptic "No cached accounts" message instead of "Token cache corrupted — please re-authenticate."

**Impact**: Deployment bugs hidden until first request:
- Token cache corruption goes undetected at startup
- First client request fails with misleading error message
- Operator sees "No cached accounts" but token cache file exists (confusion)
- Recovery requires manually deleting .token_cache.json and running run_auth.py again

**Recommendation**: In the startup lifespan hook, explicitly verify the token cache is readable by calling `self._store.get_account()`. If it returns None on a deployment where accounts should exist, log a clear error.

**Rationale**: A server that silently masks configuration corruption will cause operational confusion. Better to fail loudly at startup.

---

### IMPORTANT

#### Finding 2.1: No Startup Validation / Lifespan Hook

**File**: `/home/user/cowork-mcp/server.py` (lines 1-716)

**Issue**: The server has no FastMCP `@mcp.on_startup()` or similar hook. Configuration and token cache validation happen at module import time (lines 61-62), before the ASGI app is even created. There is no graceful shutdown hook either.

Specifically:
- Azure credentials (client_id, client_secret) are never validated before the first request
- Token cache readability is assumed, not verified
- No pre-flight check that the token cache directory exists and is writable
- No log message confirming the server is ready to accept requests

**Impact**: Silent failures in production:
- Empty `AZURE_CLIENT_ID` or `AZURE_CLIENT_SECRET` is not detected until a tool is invoked
- Token cache directory does not exist → first auth attempt fails
- No clear "server ready" log line for deployment health checks
- Systemd sees the process is running but has no indication the server is functional

**Recommendation**: Implement a lifespan hook in FastMCP:

```python
@mcp.on_startup()
async def startup():
    # Validate all required settings
    if not settings.azure_client_id or not settings.azure_client_secret:
        raise RuntimeError("Azure credentials not configured (set AZURE_CLIENT_ID and AZURE_CLIENT_SECRET)")
    if not settings.token_encryption_key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY not set in .env")
    if not settings.mcp_api_key:
        raise RuntimeError("MCP_API_KEY not set in .env")

    # Verify token cache directory exists and is writable
    cache_dir = settings.token_cache_path.parent
    if not cache_dir.exists():
        raise RuntimeError(f"Token cache directory does not exist: {cache_dir}")
    if not cache_dir.is_dir():
        raise RuntimeError(f"Token cache path is not a directory: {cache_dir}")
    if not os.access(cache_dir, os.W_OK):
        raise RuntimeError(f"Token cache directory is not writable: {cache_dir}")

    logger.info("Configuration validated; registered tools: %s", registered_groups)
    logger.info("Server ready to accept MCP requests")
```

**Rationale**: Systemd unit file uses `Restart=always`, which means failed startup is hidden unless the operator checks logs. A clear "ready" message makes it obvious the server is functional.

---

#### Finding 2.2: No Graceful Shutdown Hook

**File**: `/home/user/cowork-mcp/server.py` and `/home/user/cowork-mcp/auth/token_store.py`

**Issue**: The server has no shutdown hook. If a token refresh is in-flight when systemd sends SIGTERM, the refreshed token is not persisted to disk (TokenStore.save() is called in acquire_token_silent at line 74 of token_store.py, but there's no guarantee it completes before the process exits).

The systemd unit file has `Type=simple` and `Restart=always`, which means:
1. systemd sends SIGTERM to the process
2. The process has ~90 seconds to shut down before SIGKILL is sent
3. If uvicorn is handling concurrent requests, in-flight token refreshes may be interrupted

**Impact**: Token cache corruption or data loss:
- A token refresh in-flight during shutdown may write partially-encrypted data to disk
- Next startup, the corrupted cache file cannot be decrypted
- Requires manual deletion of .token_cache.json and re-authentication

**Recommendation**: Implement a FastMCP shutdown hook that:
1. Stops accepting new requests
2. Waits for in-flight requests to complete (with a timeout)
3. Calls `gc.store.save()` to persist any pending token state
4. Logs shutdown completion

```python
@mcp.on_shutdown()
async def shutdown():
    logger.info("Shutdown signal received; saving token cache...")
    gc.store.save()
    logger.info("Token cache saved; server shutting down")
```

Also, use a semaphore or flag to prevent new requests from starting during shutdown.

**Rationale**: A long-running systemd service will receive SIGTERM periodically (e.g., during OS updates). Graceful shutdown prevents token cache corruption.

---

#### Finding 2.3: Concurrent TokenStore.save() Writes Not Protected by Lock

**File**: `/home/user/cowork-mcp/auth/token_store.py` (lines 44-53)

```python
def save(self) -> None:
    """Encrypt and persist token cache to disk with 600 permissions."""
    if not self._cache.has_state_changed:
        return
    serialized = self._cache.serialize()
    encrypted = self._fernet.encrypt(serialized.encode())
    self._cache_path.write_bytes(encrypted)  # <-- NOT LOCKED
    self._cache_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
```

**Issue**: `TokenStore.save()` is called from `acquire_token_silent()` (line 74), which is called by multiple concurrent async MCP tool handlers. With uvicorn's `--workers 1` but multiple in-flight async requests, two concurrent calls to `save()` can race:

1. Request A acquires token, starts writing encrypted data
2. Request B acquires token (different scopes), starts writing encrypted data
3. Both write to the same file → file is partially overwritten/corrupted

The file write (`Path.write_bytes()`) is not atomic across multiple Python processes (or coroutines in the same process).

**Impact**: Under concurrent load (e.g., Claude making multiple simultaneous tool calls):
- Token cache file becomes corrupted
- Next startup, decryption fails with InvalidToken
- Server cannot re-acquire token
- Requires manual re-authentication

**Recommendation**: Add an `asyncio.Lock` to TokenStore to serialize all save() calls:

```python
class TokenStore:
    def __init__(self, cache_path: Path, encryption_key: str) -> None:
        self._cache_path = cache_path
        self._fernet = Fernet(encryption_key.encode())
        self._cache = msal.SerializableTokenCache()
        self._save_lock = asyncio.Lock()  # <-- ADD THIS
        self._load()

    async def save(self) -> None:
        """Encrypt and persist token cache to disk with 600 permissions."""
        async with self._save_lock:
            if not self._cache.has_state_changed:
                return
            serialized = self._cache.serialize()
            encrypted = self._fernet.encrypt(serialized.encode())
            self._cache_path.write_bytes(encrypted)
            self._cache_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            logger.debug("Token cache saved to %s", self._cache_path)

    async def acquire_token_silent(self, scopes: list[str], client_id: str, client_secret: str) -> str:
        """Acquire an access token silently using the cached refresh token."""
        app = self._build_app(client_id, client_secret)
        accounts = app.get_accounts()
        if not accounts:
            raise RuntimeError("No cached accounts found — run `python run_auth.py` to authenticate")
        result = app.acquire_token_silent(scopes, account=accounts[0])
        if result and "access_token" in result:
            await self.save()  # <-- NOW AWAITED
            return cast("str", result["access_token"])
        error = result.get("error_description", "unknown") if result else "no result"
        raise RuntimeError(f"Silent token acquisition failed: {error} — run `python run_auth.py` to re-authenticate")
```

Note: This also requires changing `save()` from sync to async, and updating all call sites to `await gc.store.save()`.

**Rationale**: Token cache is the persistent state for the server. Corrupting it breaks the deployment until manual recovery.

---

### SUGGESTION

#### Finding 3.1: Missing Startup Validation Tests

**File**: `/home/user/cowork-mcp/tests/test_server.py`

**Issue**: The test suite covers the BearerAuthMiddleware thoroughly but has no tests for startup behavior. There are no tests that verify:
- Settings validation fails fast (e.g., invalid log_level, empty azure_client_id)
- Token cache corruption is detected
- Configuration completeness is checked

**Recommendation**: Add a test module `tests/test_server_startup.py` that:
1. Patches `Settings()` to return invalid configuration
2. Verifies that importing/initializing the app raises with a clear message
3. Tests token cache corruption recovery

```python
def test_startup_fails_on_missing_azure_credentials():
    """Startup should fail fast if Azure credentials are not configured."""
    # This would require moving Settings() to a startup hook first
    # (see Finding 2.1)
    pass

def test_startup_fails_on_invalid_log_level():
    """Settings validator should reject invalid log levels."""
    with pytest.raises(ValueError, match="log_level"):
        Settings(log_level="INVALID")

def test_token_cache_corruption_detected_at_startup():
    """If token cache is corrupted, startup should log a warning."""
    # This requires explicit validation at startup
    pass
```

**Rationale**: Startup behavior is critical for a systemd service. Tests ensure regressions don't silently mask configuration errors.

---

#### Finding 3.2: No Logging of Long-Running Request State

**File**: `/home/user/cowork-mcp/server.py` (tool implementations)

**Issue**: Each tool is registered as a closure that calls a domain function (e.g., `mail.list_emails()`), catches RuntimeError, and raises ValueError. There is no intermediate logging:

```python
@mcp.tool()
async def list_emails(...) -> list[dict[str, Any]]:
    try:
        return await mail.list_emails(...)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
```

If a request hangs or takes an unexpectedly long time, there is no log evidence of what tool was invoked or how long it took. This makes debugging slow requests difficult.

**Recommendation**: Add request-scoped timing and logging:

```python
@mcp.tool()
async def list_emails(...) -> list[dict[str, Any]]:
    start = time.monotonic()
    try:
        logger.debug("list_emails: sender=%s, folder=%s, limit=%s", sender, folder, limit)
        result = await mail.list_emails(...)
        elapsed = time.monotonic() - start
        logger.info("list_emails completed in %.2fs; returned %d messages", elapsed, len(result))
        return result
    except RuntimeError as exc:
        elapsed = time.monotonic() - start
        logger.warning("list_emails failed after %.2fs: %s", elapsed, str(exc))
        raise ValueError(str(exc)) from exc
```

This is optional if log aggregation/tracing is already in place, but improves observability for a single-instance server.

**Rationale**: A long-running server will eventually have slow requests. Logging request entry/exit helps diagnose performance issues without needing external observability tools.

---

#### Finding 3.3: ThreadingLock in Async Context

**File**: `/home/user/cowork-mcp/graph/client.py` (lines 119-132)

```python
_instance: GraphClient | None = None
_lock = threading.Lock()  # <-- NOT ASYNC

def get_graph_client(settings: Settings | None = None) -> GraphClient:
    """Return the singleton GraphClient, creating it if necessary."""
    global _instance
    if _instance is None:
        with _lock:  # <-- BLOCKS ENTIRE THREAD
            if _instance is None:
                if settings is None:
                    settings = Settings()
                _instance = GraphClient(settings)
                logger.info("GraphClient initialised")
    return _instance
```

**Issue**: `get_graph_client()` uses `threading.Lock()` instead of `asyncio.Lock()`. In an async context (which the entire server is), blocking on a threading lock will block the entire event loop, preventing other concurrent requests from making progress while the lock is held.

However, in this case, the lock is only held briefly (during GraphClient initialization), and GraphClient is created only once at module import time (line 62 of server.py). So the actual impact is minimal.

**Impact**: Low — the lock contention happens only once, at startup. No operational issue.

**Recommendation**: For consistency and future-proofing, consider using `asyncio.Lock()` instead:

```python
import asyncio

_instance: GraphClient | None = None
_lock_: asyncio.Lock | None = None

async def get_graph_client(settings: Settings | None = None) -> GraphClient:
    global _instance, _lock_
    if _lock_ is None:
        _lock_ = asyncio.Lock()
    if _instance is None:
        async with _lock_:
            if _instance is None:
                if settings is None:
                    settings = Settings()
                _instance = GraphClient(settings)
                logger.info("GraphClient initialised")
    return _instance
```

However, this requires changing the call site in server.py from sync to async, which requires moving it to a startup hook (see Finding 2.1).

**Rationale**: It's a minor issue, but making the intent explicit (this is async code) helps future maintainers.

---

## Verdict

⚠️ **PASS WITH CHANGES**

**The design is fundamentally sound but has three important reliability gaps that will cause operational issues in production.**

### Critical Issues (Block Merge)

1. **Unguarded module-level initialization** — Will cause hard startup failures if .env is misconfigured, with no graceful error message
2. **Concurrent token cache writes without locking** — Will corrupt the encrypted cache file under concurrent load

### Important Issues (Should Address)

3. **No startup/shutdown lifecycle hooks** — Will cause token cache data loss during graceful shutdown and miss opportunities for configuration validation

### Action Items

Before this code ships to production:

1. Implement a FastMCP lifespan hook (startup + shutdown) to replace module-level Settings() and get_graph_client() calls
2. Add asyncio.Lock to TokenStore.save() to prevent concurrent write races
3. Add startup validation that logs a clear "ready" message and fails fast on missing configuration
4. Add graceful shutdown that persists token cache before process exit
5. Add test coverage for startup validation scenarios

These changes are relatively straightforward and follow established async/await patterns. The codebase is well-structured to accommodate them.

---

**Review performed by**: Claude Code (reliability-reviewer)
**Review focus**: Concurrent async safety, Error recovery, Resource lifecycle, Startup/shutdown correctness, Logging hygiene
**Review date**: 2026-03-29
