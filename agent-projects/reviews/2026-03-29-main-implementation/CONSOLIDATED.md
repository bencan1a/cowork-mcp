# Comprehensive Review: main implementation

**Date**: 2026-03-29
**Branch**: main (full implementation review)
**Base**: initial commit (30e803c)
**Review types**: security, performance, reliability, general
**Chunks reviewed**: 7 | **Files reviewed**: ~20

## Executive Summary

The Outlook MCP server is well-architected with clean separation of concerns, consistent error handling, proper async patterns, and comprehensive scope-based tool registration. However, a critical timing-attack vulnerability in the bearer auth middleware (`server.py` line 52) violates an explicit project invariant and must be fixed before merge. Beyond that, the codebase has a recurring pattern of missing input validation across all Graph domain modules (OData filter injection, unbounded `limit` parameters, unvalidated IDs and email addresses) and synchronous blocking of the async event loop during MSAL token refresh. Token cache writes lack concurrency protection, creating a corruption risk over sustained operation.

**Finding counts**: :red_circle: 5 critical | :yellow_circle: 18 important | :blue_circle: 10 suggestions

---

## Overall Verdict

:x: NEEDS REWORK

The bearer token timing-attack vulnerability is a critical security finding against an explicit project invariant. Two chunk reviews (auth/security, graph-client/security) returned NEEDS REWORK verdicts.

---

## :red_circle: Critical Findings -- Must Fix Before Merge

### C1. Bearer token comparison uses `==` instead of `hmac.compare_digest()`
**Lens(es)**: security (auth, graph-client, server-core)
**File**: `server.py` (line 52)

**Issue**: `BearerAuthMiddleware.dispatch` compares the incoming bearer token with `!=` (string equality), which short-circuits on the first differing byte. This is vulnerable to timing side-channel attacks where an attacker iteratively guesses the API key by measuring response times. The project's own security invariants explicitly require `hmac.compare_digest()` for token comparison.

**Recommendation**: Replace with constant-time comparison:
```python
import hmac
expected = f"Bearer {self._api_key}"
if not hmac.compare_digest(auth, expected):
    return Response("Unauthorized", status_code=401)
```

---

### C2. No startup guard against empty `MCP_API_KEY` / weak secret defaults
**Lens(es)**: security (auth, server-core), reliability (server-core)
**Files**: `config.py` (lines 18-28), `server.py` (lines 61-62)

**Issue**: All security-critical settings (`mcp_api_key`, `azure_client_id`, `azure_client_secret`, `token_encryption_key`) default to empty strings in `Settings`. If `.env` is missing or misconfigured, the server starts with `MCP_API_KEY=""`, effectively disabling authentication (any request with `Authorization: Bearer ` would be accepted). Module-level initialization (`Settings()`, `get_graph_client()`) runs without validation, producing cryptic errors on first request rather than failing fast at startup.

**Recommendation**: Remove defaults for security-sensitive fields (use `Field(...)` with no default to force explicit configuration), or add a startup validator that raises `RuntimeError` if any required secret is empty.

---

### C3. Synchronous blocking MSAL calls in async token provider
**Lens(es)**: performance (auth, graph-client), reliability (auth, graph-client, server-core)
**Files**: `graph/client.py` (lines 69-78), `auth/token_store.py` (lines 61-78)

**Issue**: `_TokenStoreAccessTokenProvider.get_authorization_token()` is `async def` but calls `self._store.acquire_token_silent()` synchronously. When a token refresh is needed (~every 60 minutes), MSAL makes a synchronous HTTPS request to Azure AD using the `requests` library, blocking the entire asyncio event loop for 200-1000ms. During this time, all concurrent MCP requests are stalled. The subsequent `save()` call also performs synchronous file I/O and Fernet encryption on the event loop.

**Recommendation**: Wrap the synchronous call in `asyncio.to_thread()`:
```python
async def get_authorization_token(self, uri, ...) -> str:
    return await asyncio.to_thread(
        self._store.acquire_token_silent, self._scopes,
        self._settings.azure_client_id, self._settings.azure_client_secret,
    )
```

---

### C4. Token cache file writes not protected by a lock
**Lens(es)**: reliability (auth, graph-client, server-core)
**File**: `auth/token_store.py` (lines 44-53)

**Issue**: `TokenStore.save()` is called from `acquire_token_silent()` on every Graph API request path. When multiple MCP tool calls are in-flight, concurrent requests can both trigger a token refresh, both see `has_state_changed == True`, and both write to the same file. MSAL's `SerializableTokenCache` is not documented as thread-safe for concurrent writes. Interleaved writes can corrupt the encrypted cache file, requiring manual re-authentication via `run_auth.py`.

**Recommendation**: Add an `asyncio.Lock` to serialize all `acquire_token_silent` + `save` sequences. Combined with the `asyncio.to_thread()` fix from C3, add a `threading.Lock` around the synchronous code to protect against thread-pool concurrency.

---

### C5. OData filter injection via unescaped user input
**Lens(es)**: security (graph-mail), general (graph-mail), reliability (graph-mail)
**File**: `graph/mail.py` (lines 142-152)

**Issue**: The `sender` and `subject` parameters are interpolated directly into OData `$filter` strings using f-strings without escaping single quotes. A sender like `O'Brien@example.com` produces a malformed filter (400 error). A deliberately crafted value can inject arbitrary OData filter clauses. While exploitation is bounded by single-user architecture, this violates the project's own input validation invariants.

**Recommendation**: Add an OData string escape helper:
```python
def _escape_odata_string(value: str) -> str:
    return value.replace("'", "''")
```
Apply to all user-supplied OData filter values across all graph modules.

---

## :yellow_circle: Important Findings -- Should Fix Before Merge

### Security

**S1. OAuth callback server binds to `localhost` instead of `127.0.0.1`**
**File**: `auth/oauth_flow.py` (line 85)
On systems with misconfigured `/etc/hosts` or dual-stack IPv6, `localhost` could resolve to a non-loopback address. The project invariant requires explicit `127.0.0.1`.

**S2. OAuth flow does not validate `state` parameter (CSRF)**
**File**: `auth/oauth_flow.py` (lines 79-82, 100-104)
No `state` parameter is generated or validated on the OAuth callback, making the flow vulnerable to CSRF. Mitigated by brief localhost-only callback window.

**S3. `AllowedHostsValidator` initialized with empty list permits tokens to any host**
**File**: `graph/client.py` (line 67)
An empty allowed-hosts list means the provider will supply access tokens to any URL. Should be restricted to `["graph.microsoft.com"]`.

**S4. No upper bound on `limit` parameter across all list operations**
**Files**: `graph/mail.py`, `graph/calendar.py`, `graph/contacts.py`, `graph/tasks.py`
All list/search functions accept unbounded `limit` values. A caller passing `limit=1000000` causes thousands of sequential Graph API requests, exhausting server resources. Fix: `limit = max(1, min(limit, 500))`.

**S5. No email validation on addresses across mail and calendar modules**
**Files**: `graph/mail.py` (lines 96-101, 302, 369), `graph/calendar.py` (lines 240-249)
Email addresses are passed directly to Graph SDK without structural validation. Add a basic regex check before use.

**S6. `setattr` with user-controlled keys lacks allowlist in contacts module**
**Files**: `graph/contacts.py` (lines 128-131, 153-156), `server.py` (lines 588, 600)
Both `create_contact` and `update_contact` accept `**fields` and call `setattr` for any attribute that exists on the Contact SDK object, including internal properties like `odata_type` and `additional_data`. Unlike the calendar module, `server.py` does not filter fields before forwarding.

**S7. No validation on `list_id`, `task_id`, `contact_id`, `event_id` path parameters**
**Files**: `graph/tasks.py`, `graph/contacts.py`, `graph/calendar.py`
User-controlled ID strings are embedded in Graph API URL path segments without format validation. Add a lightweight regex check for expected ID format.

**S8. No format validation on `due_date` before Graph API submission**
**File**: `graph/tasks.py` (lines 147-151)
The `due_date` parameter is assigned directly without ISO 8601 validation. Malformed values (including strings with newlines) are sent to the API as-is.

### Performance

**P1. MSAL app rebuilt on every token acquisition**
**File**: `auth/token_store.py` (lines 61-79, 89-105)
`acquire_token_silent()` constructs a new `msal.ConfidentialClientApplication` on every invocation. MSAL apps parse authority metadata on construction. Cache the app instance on `TokenStore` and reuse it.

**P2. Missing `$select` projections on calendar and contacts queries**
**Files**: `graph/calendar.py` (lines 123-131, 157-194), `graph/contacts.py` (lines 57-64, 90-99)
Calendar `list_events`, `get_event`, `search_events` and contacts `list_contacts`, `get_contact` all fetch full objects without `$select`. Calendar Event objects have 50+ fields; only ~12 are used. Add `$select` to reduce payload size.

**P3. Redundant `_resolve_list_id()` API call on every task operation**
**File**: `graph/tasks.py` (lines 54-70)
Every task operation calls `_resolve_list_id()` which makes a Graph API call when `list_id` is None. The default list ID is stable and should be cached.

**P4. `list_calendars` and `search_events` do not handle pagination**
**File**: `graph/calendar.py` (lines 83-102, 174-194)
`list_calendars` returns only the first page. `search_events` fetches a single page and slices to `limit`. Both can silently truncate results.

### Reliability

**R1. Pagination loops lack hard upper bound (safety valve)**
**Files**: `graph/mail.py` (lines 181-189, 260-268, 463-469), `graph/tasks.py` (lines 113-123)
If the Graph API returns cyclic `nextLink` values (a documented edge case), pagination loops would spin indefinitely. Add a `MAX_PAGES` safety cap.

**R2. `_wrap_odata_error` can raise `AttributeError` when `exc.error` is malformed**
**File**: `graph/mail.py` (lines 104-107) -- pattern duplicated in all graph modules
If `exc.error` is set to an object where `.code` or `.message` is not the expected type, attribute access could raise `AttributeError`, escaping the `except ODataError` block. Add defensive `try/except AttributeError` inside the wrapper.

### General

**G1. Missing `delete_contact` function**
**File**: `graph/contacts.py`
The module exposes list, get, create, update but not delete. Graph API supports `DELETE /me/contacts/{id}`. This is a functional gap.

**G2. Missing `update_task` function**
**File**: `graph/tasks.py`
The module implements create/read/complete/delete but not update, breaking feature parity with calendar and contacts modules.

**G3. `update_contact` does not normalize `email_addresses` like `create_contact` does**
**File**: `graph/contacts.py` (lines 149-167 vs 107-141)
`create_contact` converts plain email strings to `EmailAddress` objects. `update_contact` uses generic `setattr`, so passing `email_addresses=["new@example.com"]` would send raw strings to the SDK, causing an API error.

**G4. `_wrap_odata_error` duplicated identically across all four graph modules**
**Files**: `graph/mail.py:104`, `graph/calendar.py:71`, `graph/contacts.py:39`, `graph/tasks.py:47`
Byte-for-byte identical implementations. Extract to `graph/errors.py`.

---

## :blue_circle: Suggestions -- Worth Considering

1. **Token cache file race condition window**: `auth/token_store.py` calls `write_bytes()` then `chmod()`. Use atomic write pattern (`os.open` with mode flags, or write-to-temp then `os.replace()`). (security, general)

2. **OAuth callback handler does not check `error` parameter**: When user denies consent, Microsoft returns `?error=access_denied`. Handler falls through to generic 400. Parse and surface the error immediately. (security, general)

3. **`get_email` could use `$expand=attachments` to eliminate second API call**: Currently fetches message, then conditionally fetches attachments in a second call. (performance)

4. **Remove `body` from `list_emails` `$select`**: Fetching full HTML bodies for every message in a list is excessive. `bodyPreview` is sufficient for list views; full body available via `get_email`. (performance)

5. **Add logging inside `_wrap_odata_error`**: All graph modules define a `logger` but never log OData errors before re-raising. For a systemd service, journal-based diagnosis requires log entries. (reliability)

6. **`_contact_to_dict` can raise `AttributeError` on malformed email entries**: `c.email_addresses[0].address` lacks defensive access. Use `getattr(first, "address", None)`. (reliability)

7. **Global mutable state in `oauth_flow.py`**: `_auth_code` and `_auth_event` are module-level globals mutated via `global`. Encapsulate in the server instance. (general)

8. **Non-atomic `save()` in TokenStore**: A crash mid-write could leave a corrupted cache file. Write to a temp file, set permissions, then `os.replace()`. (general)

9. **No server startup/shutdown lifecycle hooks**: No FastMCP lifespan hooks for configuration validation or graceful token cache persistence on SIGTERM. (reliability)

10. **`_to_dict` helpers use `Any` annotation instead of concrete SDK types**: `_event_to_dict(event: Any)`, `_message_to_dict(msg: Any)`, etc. should use their respective SDK model types for mypy coverage. (general)

---

## Chunk x Review Matrix

| Chunk | Security | Performance | Reliability | General |
|-------|----------|-------------|-------------|---------|
| auth | :x: NEEDS REWORK | :warning: PASS WITH CHANGES | :warning: PASS WITH CHANGES | :warning: PASS WITH CHANGES |
| graph-client | :x: NEEDS REWORK | :warning: PASS WITH CHANGES | :warning: PASS WITH CHANGES | :warning: PASS WITH CHANGES |
| graph-mail | :warning: PASS WITH CHANGES | :white_check_mark: PASS | :warning: PASS WITH CHANGES | :warning: PASS WITH CHANGES |
| graph-calendar | :warning: PASS WITH CHANGES | :warning: PASS WITH CHANGES | :white_check_mark: PASS | :warning: PASS WITH CHANGES |
| graph-contacts | :warning: PASS WITH CHANGES | :white_check_mark: PASS | :white_check_mark: PASS | :warning: PASS WITH CHANGES |
| graph-tasks | :warning: PASS WITH CHANGES | :warning: PASS WITH CHANGES | :white_check_mark: PASS | :warning: PASS WITH CHANGES |
| server-core | :warning: PASS WITH CHANGES | :white_check_mark: PASS | :warning: PASS WITH CHANGES | :white_check_mark: PASS |

---

*Generated by `/comp-review` · 2026-03-29 · main implementation review*
