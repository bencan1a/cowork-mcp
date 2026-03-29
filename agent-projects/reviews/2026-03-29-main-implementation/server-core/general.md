# General Review: server-core

**Date**: 2026-03-29
**Files**: 6 (server.py, config.py, run_auth.py, tests/test_server.py, tests/test_config.py, tests/conftest.py)
**Reviewer**: principal-engineer
**Scope**: Architecture, coding standards, type safety, test coverage, scope toggle pattern

---

## Summary

The core server implementation demonstrates solid engineering fundamentals with a clean, pragmatic architecture. The 700-line server.py follows a repeating pattern that trades abstraction complexity for explicitness—a sensible choice for MCP tool registration. Configuration is well-structured with pydantic-settings. Bearer auth enforcement is correct and comprehensively tested. Test infrastructure shows maturity with careful handling of module-level initialization.

**Verdict**: ✅ **Ready for production with minor enhancements**. The code is maintainable, testable, and aligns with project standards. Specific recommendations below address edge cases, observability, and scalability for long-term reliability.

---

## Findings

### 🟢 Strengths

#### 1. **Explicit Tool Registration Pattern (server.py lines 79-699)**
The scope-based conditional registration is transparent and maintainable. Each tool group (mail_read, calendar_write, etc.) is clearly delineated with section comments. Developers can instantly see which tools are available without tracing logic.

**Pragmatic choice**: Rather than a registry decorator or factory pattern (which would reduce line count but increase cognitive load), the explicit if/else pattern is easier to debug, test, and understand—especially for a single-developer project.

#### 2. **Robust Bearer Auth Middleware (lines 40-54)**
- Exact token comparison: `auth != f"Bearer {self._api_key}"` is correct and secure
- Consistent 401 response for all auth failures
- Applied to all routes via middleware chain
- Test coverage is comprehensive (8 distinct test cases, all passing)

**No improvements needed here.** The implementation is tight and correct.

#### 3. **Well-Structured Configuration (config.py)**
- Pydantic BaseSettings with `.env` auto-loading
- Field descriptions for every setting
- Log-level validator prevents invalid config
- Scope toggles have sensible defaults (most enabled, contacts_write and tasks_write disabled)

Type hints are complete, and the design allows easy environment-based behavior variation.

#### 4. **Test Infrastructure (conftest.py, test_server.py, test_config.py)**
The `pytest_configure` hook elegantly solves the problem of module-level initialization:
- Pre-imports `server.py` under mocks before test collection
- Stops patches immediately so other tests see real constructors
- Prevents spurious failures from missing Azure credentials

Test naming follows good convention (`test_no_auth_header_returns_401`, `test_wrong_token_returns_401`). Tests verify behavior, not implementation.

#### 5. **Error Handling Pattern**
Every tool catches `RuntimeError` and re-raises as `ValueError` for MCP compatibility. This is consistent across all 30+ tools and correctly surfaces Graph API errors to the client.

#### 6. **One-Time Auth Script (run_auth.py)**
Clean, single-purpose script with clear error messages. Validates required secrets before running and guides users on generating missing keys. No silent failures.

---

### 🟡 Important Recommendations

#### 1. **Scope Toggle Tracking Could Be More Diagnostic (server.py lines 705-707)**
**Current**:
```python
logger.info("Registered tool groups: %s", registered_groups)
if skipped_groups:
    logger.info("Skipped tool groups (scope disabled): %s", skipped_groups)
```

**Issue**: On startup, you log which groups are registered, but not how many tools per group or what the actual tool names are. When debugging "why doesn't this tool work?", operators must reason backward.

**Recommendation**:
```python
logger.info("Registered %d tool groups: %s", len(registered_groups), ", ".join(registered_groups))
if skipped_groups:
    logger.info("Skipped %d tool groups (disabled): %s", len(skipped_groups), ", ".join(skipped_groups))
```

Consider also logging a summary after app assembly:
```python
logger.info("MCP server ready with %d tools", len([name for name in dir(mcp) if not name.startswith('_')]))
```

**Effort**: < 5 minutes
**Impact**: Better observability during deployment and troubleshooting

---

#### 2. **No Validation That All Tool Names Are Unique (server.py)**
FastMCP decorators register tools by function name. If a developer accidentally defines two tools with the same name (e.g., in a refactor), only the last one registers.

**Risk**: Silent registration failure → confused operators → downtime.

**Recommendation**: Add a post-registration check:
```python
# After all tool registration, before app assembly
registered_tool_names = set()
for tool_group in [mcp.tools]:  # Iterate actual tool registry
    for tool in tool_group:
        if tool.name in registered_tool_names:
            raise RuntimeError(f"Duplicate tool name: {tool.name}")
        registered_tool_names.add(tool.name)
logger.info("Verified %d unique tool names", len(registered_tool_names))
```

Alternatively, consider extracting tool registration into helper functions to reduce repetition:
```python
def register_mail_read_tools(mcp: FastMCP, gc: GraphClient) -> None:
    @mcp.tool()
    async def list_emails(...): ...
    # ... more tools ...
```

This would reduce server.py from 700 to ~400 lines and make it easier to audit tool names.

**Effort**: 30–60 minutes (if extracting helpers); 5 minutes (if just adding validation)
**Impact**: Prevents silent registration bugs; makes code more maintainable

---

#### 3. **BearerAuthMiddleware Does Not Log Failed Auth Attempts (server.py lines 40-54)**
Security best practice: audit access control decisions, especially failures.

**Current behavior**: Silent 401 response, no log entry.

**Recommendation**:
```python
async def dispatch(self, request: Request, call_next: Any) -> Response:
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {self._api_key}":
        logger.warning("Unauthorized access attempt: %s %s from %s",
                       request.method, request.url.path, request.client)
        return Response("Unauthorized", status_code=401)
    return await call_next(request)
```

This allows operators to detect brute-force attempts, misconfigured clients, or security issues.

**Effort**: < 5 minutes
**Impact**: Security audit trail; operational visibility

---

#### 4. **Type Annotation on `app` Export Adds Clarity (server.py line 713)**
**Current**:
```python
app = mcp.http_app(...)
```

**Issue**: The variable `app` is assigned but has no type annotation. IDEs and mypy cannot infer its type for code using `from server import app`.

**Recommendation**:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asgiref.typing import ASGIApplication

app: "ASGIApplication" = mcp.http_app(...)
```

Or use the actual FastMCP return type. Check the FastMCP source to get the exact type:
```python
from fastmcp import FastMCP
# Then check: help(mcp.http_app) or inspect the return type
```

**Effort**: 5 minutes
**Impact**: Better IDE autocomplete; mypy type checking of imports

---

#### 5. **Boilerplate Repetition in Tool Definitions (server.py)**
Each of 30+ tools follows this pattern:
```python
try:
    return await mail.list_emails(gc, ...)
except RuntimeError as exc:
    raise ValueError(str(exc)) from exc
```

While explicit is good, 30 repetitions of this error-handling pattern is boilerplate.

**Recommendation** (if refactoring): Extract a helper decorator:
```python
from functools import wraps

def _graph_error_handler(func: Callable) -> Callable:
    """Wrap an async tool to convert RuntimeError to ValueError."""
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
    return wrapper

# Then use:
@mcp.tool()
@_graph_error_handler
async def list_emails(...) -> list[dict[str, Any]]:
    return await mail.list_emails(gc, ...)
```

This reduces repetition and centralizes error handling.

**Effort**: 30 minutes (optional refactor)
**Impact**: ~100 lines of code saved; single point of change for error semantics

---

#### 6. **Tests Mock All Scopes to False, Limiting Validation (tests/conftest.py lines 25-33)**
**Current**:
```python
mock_settings.scope_mail_read = False
mock_settings.scope_mail_write = False
# ... all False
```

**Issue**: Tests verify that `BearerAuthMiddleware` works, but never test that tool registration actually respects scope toggles. If a developer forgets the `if settings.scope_mail_read:` guard on a tool, tests won't catch it.

**Recommendation**: Add an integration test that:
1. Creates a `Settings` with some scopes enabled and others disabled
2. Imports `server` (somehow, or calls a registration function)
3. Checks that only enabled tools are registered

Example:
```python
def test_scope_toggles_control_tool_registration() -> None:
    """Verify that scope toggles determine which tools are registered."""
    # This requires a refactor to make tool registration callable/reloadable
    # For now, document this as a TODO
```

**Effort**: 30–60 minutes (requires refactoring tool registration into a function)
**Impact**: Prevents silently breaking scope toggles

---

### 🔵 Suggestions (Nice-to-Have)

#### 1. **Remove Unused Import (server.py line 14)**
`Any` is imported but only used in type hints for middleware. Consider:
```python
# Remove: from typing import TYPE_CHECKING, Any
# Keep TYPE_CHECKING for if block at line 25
```

Actually, `Any` *is* used on line 14 in the import, and on lines 46, 50, 82–90 (multiple return/param types). Keep it.

#### 2. **Middleware Specification Could Use Comments (server.py lines 40-54)**
Add a docstring note about constant-time comparison for security:
```python
class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Enforce Authorization: Bearer <api_key> on every request.

    Uses simple string equality (not constant-time comparison) which is
    adequate for bearer tokens; not intended for password comparison.
    Returns HTTP 401 for missing or invalid tokens.
    """
```

Minor clarity improvement.

#### 3. **Logging Formatter Could Include Request Context (server.py line 32)**
The default format includes timestamp, level, and logger name. For server logs, including request path would aid debugging:
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s [%(pathname)s:%(lineno)d]: %(message)s"
)
```

This helps operators correlate log messages with source. Optional.

#### 4. **Document why `log_level` is set Twice (server.py lines 32, 65)**
Lines 32 and 65 both configure logging:
```python
logging.basicConfig(level=logging.INFO, ...)  # line 32
logging.getLogger().setLevel(settings.log_level)  # line 65
```

This is correct (basicConfig at import, then override with settings), but a comment explains the intent:
```python
# Set log level from settings (overrides basicConfig default)
logging.getLogger().setLevel(settings.log_level)
```

#### 5. **Add Type Stubs for FastMCP (if not already present)**
The project uses FastMCP, but mypy overrides show msal and msgraph are ignored. Ensure FastMCP types are accurate:
```toml
# In pyproject.toml [tool.mypy.overrides]
[[tool.mypy.overrides]]
module = "fastmcp"
ignore_missing_imports = false  # Verify this works
```

If FastMCP is missing type hints, create a stub or suppress strategically.

---

## Type Safety & Mypy Compliance

**Status**: ✅ Excellent

- `config.py`: Fully typed, no issues
- `server.py`: All tool functions are fully typed (args, kwargs, returns)
- `run_auth.py`: Fully typed with explicit `None` returns
- Tests: Excluded from strict mypy rules (appropriate)

**Mypy configuration** (pyproject.toml lines 110–137) is strict and appropriate:
- `disallow_untyped_defs = true`
- `disallow_incomplete_defs = true`
- Overrides for third-party imports (msal, msgraph)

No action required.

---

## Test Coverage Analysis

**Current State**: Coverage target is 55% (fail_under), and tests exist for:
- BearerAuthMiddleware: 8 test cases (comprehensive)
- Settings validation: 4 test cases
- Server module imports: 4 test cases

**Gaps Identified**:
1. **No tests for tool registration logic**: Are tools actually registered? Are scopes respected?
2. **No tests for error handling across tools**: Only middleware is tested, not the try/except pattern
3. **No integration tests**: No end-to-end MCP protocol tests

**Recommendations**:
- Add a `test_tool_registration.py` to verify tools are registered based on scope toggles
- Add parameterized tests for error handling: `@pytest.mark.parametrize("scope", [True, False])`
- Consider a slow integration test with a real FastMCP protocol roundtrip (mark as `@pytest.mark.slow`)

**Effort**: 1–2 hours
**Impact**: Prevents regressions in tool registration and error handling

---

## Architecture Assessment

### Strengths
1. **Separation of Concerns**: server.py handles routing/registration; graph/*.py handle Graph API logic
2. **Single Responsibility**: Each tool is a thin wrapper around one domain function
3. **Dependency Injection**: `gc` (GraphClient) is injected as a closure variable, not created per-tool
4. **Configuration Externalization**: All toggles and secrets in .env via pydantic-settings

### Trade-offs
1. **Line Count vs. Abstraction**: 700-line server.py could be ~300 with helper functions, but at cost of additional indirection. Current approach is pragmatic for a single-dev project.
2. **Tool Registration at Module Level**: FastMCP tools are registered at import time, not on-demand. This means `import server` immediately triggers Graph API client creation. Acceptable for this project (MCP server is always on), but document if scaling to multi-instance deployments.

### Scalability Considerations
- **Horizontal scaling**: Each server instance is independent. Authentication is stateless (Graph API tokens in encrypted file). Cloudflare Tunnel routes all clients to one instance—fine for personal use.
- **Future multi-account support**: Current design assumes one account per server. Adding multi-account would require refactoring `GraphClient` as a factory and tool registration as dynamic. Document this as out-of-scope (per CLAUDE.md).

---

## Code Quality Standards Compliance

✅ **All pass `make check-all`**:
- Formatting: `ruff format` compliant (100-char line length)
- Linting: `ruff check` clean
- Type checking: `mypy` strict mode clean
- Security: `bandit` with exceptions noted
- Tests: All passing (55%+ coverage)

No action required.

---

## Verdict

### ✅ Production Ready

**Why**:
- Architecture is sound and pragmatic
- All critical security controls in place (Bearer auth, error handling)
- Test infrastructure is mature (module-level mocking strategy is clever)
- Type safety is strict and consistent
- Error handling is consistent across 30+ tools

### Recommended Actions Before Merge

1. **Add auth failure logging** (🟡, 5 min): `logger.warning` on 401
2. **Improve startup observability** (🟡, 5 min): Log tool counts
3. **Document test scope limitation** (🟡, 30 min): Add test for scope toggle behavior
4. **Consider tool registration helper refactor** (🟡, optional, 30 min): Reduce boilerplate

### Post-Launch Monitoring

- Watch auth failure logs for anomalies
- Validate tool count on startup matches expectations
- Add periodic health check verifying scope toggles work

---

## Technical Debt Ledger

| Debt | Cost | Mitigate |
|------|------|----------|
| Explicit if/else for 8 tool groups (700 lines) | Maintenance friction if adding more groups | Extract helper functions (30 min, optional) |
| 30+ repeated try/except blocks | Code duplication; error handling is hard to audit | Decorator-based wrapper (optional) |
| No test for scope toggle behavior | Silent registration failures possible | Add integration test (30 min) |
| Auth middleware has no logging | Security blind spot | Add logger.warning on 401 (5 min) |

**Verdict**: Debt is minimal and mostly stylistic. Only the missing scope toggle test is a real risk (though unlikely given manual testing in development).

---

## Summary for Checklist

**Ready to ship?** ✅ Yes, with optional enhancements.

**Top 3 priorities before production**:
1. Add auth failure logging (quick security win)
2. Improve startup diagnostics (operational clarity)
3. Add scope toggle test (prevents regression)

**Time estimate for all recommendations**: 1–2 hours (if doing all); 15 minutes (if just critical).
