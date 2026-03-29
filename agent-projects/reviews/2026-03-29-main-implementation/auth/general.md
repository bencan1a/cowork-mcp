# General Review: auth

**Date**: 2026-03-29 | **Files reviewed**: 3 | **Reviewer**: principal-engineer

## Summary

The auth module is well-structured and correctly implements the critical patterns called out in CLAUDE.md -- Fernet encryption with chmod 600 enforcement, the `/consumers` MSAL authority, and a localhost redirect server for OAuth. There are a handful of correctness and robustness issues worth addressing, most notably thread-safety concerns in the OAuth flow and a `get_account()` method that builds an MSAL app with a dummy client ID.

## Findings

### auth/token_store.py

**[F1]** -- Thread safety of `_build_app` with empty client ID in `get_account`

Severity: Yellow -- Important

`get_account()` calls `self._build_app("")`, which hits the `not client_secret` branch and creates a `PublicClientApplication` with `client_id="placeholder"`. This works because `get_accounts()` only reads from the in-memory cache and never contacts Azure, but it is fragile and misleading. If MSAL ever validates the client ID in that code path, or if a future developer calls other methods on the returned app, this will break silently.

Recommendation: Refactor so `get_account()` reads directly from `self._cache` rather than building a throwaway MSAL app. `SerializableTokenCache` exposes the account data internally; alternatively, accept `client_id` as a constructor parameter stored on `self` so every `_build_app` call uses a real value.

---

**[F2]** -- `_build_app` creates a new MSAL app on every call

Severity: Green -- Suggestion

Each call to `acquire_token_silent` or `get_account` constructs a new `ConfidentialClientApplication` (or `PublicClientApplication`). MSAL apps are not free to build -- they parse authority metadata. In a hot path (every MCP tool invocation triggers `acquire_token_silent`), this could add latency.

Recommendation: Cache the `ConfidentialClientApplication` instance on `self` and reuse it. Invalidate only if the credentials change (which they do not at runtime).

---

**[F3]** -- `save()` is not atomic

Severity: Yellow -- Important

`self._cache_path.write_bytes(encrypted)` followed by `self._cache_path.chmod(...)` is a two-step operation. If the process is interrupted between the write and the chmod, the file could be left with default permissions. More critically, a crash mid-write could leave a corrupted cache file.

Recommendation: Write to a temporary file in the same directory, set permissions on it, then `os.replace()` it over the target. This is a standard atomic-write pattern on POSIX.

---

**[F4]** -- `save()` silently skips when `has_state_changed` is False

Severity: Green -- Suggestion

This is correct MSAL behavior, but callers (like `oauth_flow.py` line 110) call `store.save()` expecting the token to persist. If MSAL's internal flag is somehow not set, the save is silently skipped. A debug log message when skipping would aid troubleshooting.

---

**[F5]** -- Decryption failure silently ignores the corrupt file

Severity: Green -- Suggestion

In `_load()`, when `InvalidToken` is caught, the cache file is left on disk and the store starts empty. This is acceptable for resilience, but the user gets no clear signal that their tokens were lost. Consider logging at WARNING level (already done) and also printing to stderr if running interactively, or raising if the file clearly exists but cannot be read.

---

### auth/oauth_flow.py

**[F6]** -- Global mutable state for auth code capture is not thread-safe

Severity: Red -- Critical

`_auth_code` and `_auth_event` are module-level globals mutated via `global` keyword. If `run_oauth_flow` were called concurrently (unlikely today, but the code does not prevent it), the shared state would race. More practically, the `global _auth_event` on line 65 reassigns the module-level variable, but `_CallbackHandler.do_GET` captures the original `_auth_event` from module scope at class-definition time -- this is actually fine because `global` rebinds the name, and the handler reads the name at call time. However, the pattern is fragile and hard to reason about.

Recommendation: Encapsulate the state in a class or pass it via the handler's server instance (e.g., `server.auth_code = None; server.auth_event = Event()`). The handler can access `self.server.auth_code`. This eliminates the global state entirely.

---

**[F7]** -- No timeout feedback or cleanup on failure

Severity: Yellow -- Important

If `_auth_event.wait(timeout=300)` expires, `server.server_close()` is called, but there is no guarantee the daemon thread has exited. The `HTTPServer` socket may linger. Also, the 5-minute timeout is hardcoded with no way to configure it.

Recommendation: After `server.server_close()`, join the thread with a short timeout. Consider making the timeout configurable via Settings (low priority).

---

**[F8]** -- `_CallbackHandler` does not check for `error` parameter in redirect

Severity: Yellow -- Important

OAuth error responses redirect back with `?error=access_denied&error_description=...` rather than `?code=...`. The handler sends a generic 400 but does not log the error reason. The error detail from Microsoft is lost.

Recommendation: Parse `error` and `error_description` from the query parameters and include them in both the HTTP response body and the log output.

---

**[F9]** -- OAuth flow accesses `store._cache` (private attribute)

Severity: Green -- Suggestion

Line 75 in `oauth_flow.py` accesses `store._cache` to share the MSAL cache with the `ConfidentialClientApplication`. This couples `oauth_flow` to the internal implementation of `TokenStore`. A public property or method like `store.msal_cache` would make this explicit.

---

### auth/__init__.py

**[F10]** -- Empty init file

Severity: Green -- Suggestion

The `__init__.py` is essentially empty (just a blank file). This is fine -- the module's public API is accessed via direct imports from `auth.token_store` and `auth.oauth_flow`. No action needed, but if the module grows, consider re-exporting key symbols for convenience.

---

### tests/test_auth.py

**[F11]** -- No tests for `oauth_flow.py`

Severity: Yellow -- Important

The entire `oauth_flow.py` module has zero test coverage. While it is inherently interactive (browser, HTTP server), the core logic can be tested:
- Token exchange failure path (line 106-108)
- Timeout path (line 97-98)
- `_CallbackHandler` response for success and error cases

These can be tested by mocking `msal.ConfidentialClientApplication`, `webbrowser.open`, and driving the handler directly.

---

**[F12]** -- Tests directly manipulate `_cache` internals

Severity: Green -- Suggestion

Several tests set `tmp_cache._cache.has_state_changed = True` and call `_cache.deserialize()` directly. This is pragmatic for testing MSAL integration, but it couples tests to MSAL's internal API. If MSAL changes `SerializableTokenCache`, tests break. This is acceptable given the alternative (mocking everything), but worth noting.

---

**[F13]** -- No test for `needs_reauth` returning False (happy path)

Severity: Green -- Suggestion

`test_needs_reauth_returns_true_when_empty` covers the True case. The False case (token available) is implicitly covered by `test_acquire_token_silent_calls_msal`, but an explicit `test_needs_reauth_returns_false_when_token_cached` would improve clarity.

---

**[F14]** -- No test for corrupted cache file handling

Severity: Yellow -- Important

The `InvalidToken` and generic `Exception` paths in `_load()` are not tested. Writing garbage bytes to the cache file and verifying the store initializes cleanly would cover an important resilience path.

---

## Edge Cases and Risks

| Scenario | Current Behavior | Risk |
|----------|-----------------|------|
| Fernet key rotated after tokens saved | `InvalidToken` caught, cache ignored, silent re-auth needed | Medium -- user loses tokens with only a WARNING log |
| Disk full during `save()` | `write_bytes` raises `OSError`, unhandled | Low -- crash, but retry on next call is fine |
| OAuth redirect server port 8400 already in use | `OSError: Address already in use`, unhandled | Medium -- confusing error for the user |
| Multiple concurrent `acquire_token_silent` calls | Multiple MSAL app instances built; potential cache state race | Low -- unlikely in single-threaded MCP server |

## Verdict

**Yellow -- Important issues identified**

The auth module is functionally correct for its current single-user, single-server deployment. The architecture aligns well with CLAUDE.md requirements: encrypted token storage, correct MSAL authority, chmod 600 enforcement, and localhost redirect capture.

The critical finding is the global mutable state in `oauth_flow.py` (F6), which should be refactored to instance state on the server object. The non-atomic save (F3), missing OAuth error parameter handling (F8), and absence of `oauth_flow.py` tests (F11) are the most impactful items to address next. The remaining findings are genuine improvements but lower priority.

Priority order for remediation:
1. F6 -- Eliminate global state in OAuth flow
2. F11 -- Add tests for `oauth_flow.py`
3. F3 -- Atomic file write for token cache
4. F8 -- Parse OAuth error parameters in callback handler
5. F1 -- Clean up `get_account()` to not use placeholder client ID
6. F14 -- Add test for corrupted cache resilience
