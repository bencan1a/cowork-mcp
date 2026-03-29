# Security Review: graph-mail
**Date**: 2026-03-29 | **Files reviewed**: 2 | **Reviewer**: security-reviewer

## Summary

Reviewed `graph/mail.py` (617 lines) and `tests/test_mail.py` (487 lines). The mail module handles email listing, search, send, reply, forward, move, delete, folder management, and auto-reply configuration via the Microsoft Graph SDK. All Graph interactions go through the `GraphClient` wrapper as required. OData errors are caught and wrapped into `RuntimeError` with code and message -- no raw stack traces leak to callers. The main security concerns are OData filter injection via unvalidated user-controlled strings (`sender`, `subject`, `folder`) and a lack of `limit` bounds checking.

---

## Findings

### [IMPORTANT] OData Filter Injection via `sender` and `subject` Parameters
**File**: `graph/mail.py` (lines 142-152)

**Issue**: The `sender` and `subject` parameters are interpolated directly into OData `$filter` strings using f-strings without any sanitization:

```python
filters.append(f"from/emailAddress/address eq '{sender}'")
filters.append(f"contains(subject, '{subject}')")
```

A user-controlled `sender` value like `foo@bar.com' or isRead eq true or from/emailAddress/address eq 'x` would break out of the quoted string and inject arbitrary OData filter clauses. Similarly for `subject`. While OData injection is less dangerous than SQL injection (it cannot modify data or access other tenants -- this is scoped to the authenticated user's own mailbox), it can bypass intended filters, return unexpected data sets, or cause server-side errors that reveal information about the query structure.

**Recommendation**: At minimum, escape single quotes by replacing `'` with `''` in OData string literals (the OData escaping convention). Better: validate `sender` against an email regex pattern and reject `subject` values containing single quotes or other OData special characters.

```python
def _escape_odata_string(value: str) -> str:
    return value.replace("'", "''")

# Then:
filters.append(f"from/emailAddress/address eq '{_escape_odata_string(sender)}'")
filters.append(f"contains(subject, '{_escape_odata_string(subject)}')")
```

**Rationale**: OWASP A03 (Injection). All MCP tool inputs are user-controlled per the security model.

---

### [IMPORTANT] No Input Validation on Email Addresses
**File**: `graph/mail.py` (lines 96-101, 302, 369)

**Issue**: The `_make_recipient` helper and all functions accepting email addresses (`send_email`, `forward_email`) pass user-provided strings directly to the Graph SDK without any structural validation. While the Graph API will reject clearly malformed addresses, the lack of local validation means:
1. Malformed inputs make unnecessary API round-trips before failing.
2. Unusual payloads (e.g., extremely long strings, strings with control characters or newlines) are sent directly to the API.

The `sender` parameter in `list_emails` is also used unvalidated in an OData filter (see above finding).

**Recommendation**: Add a basic email validation helper using a regex or Python's `email.utils.parseaddr()` and call it in `_make_recipient` and for the `sender` filter parameter. Reject obviously invalid addresses before they reach the Graph API.

**Rationale**: OWASP A03 (Injection), project invariant on input validation ("All MCP tool inputs are user-controlled. Validate types and ranges before passing to Graph API. Email addresses: validate structure before use.").

---

### [IMPORTANT] No Upper Bound on `limit` Parameter
**File**: `graph/mail.py` (lines 125, 229)

**Issue**: `list_emails` and `search_emails` accept a `limit` parameter with no upper bound validation. While `page_size` is capped at 50 via `min(limit, 50)`, the pagination loop will continue fetching pages until `limit` is reached. A caller passing `limit=1000000` would cause the server to make potentially thousands of sequential Graph API requests, causing resource exhaustion (CPU, memory, network) and potentially hitting Graph API throttling.

The `server.py` tool definitions also do not enforce an upper bound before delegating to the mail module.

**Recommendation**: Enforce a reasonable maximum (e.g., 500 or 1000) at the top of each function:

```python
limit = min(limit, 500)
```

Also validate that `limit` is positive (`limit = max(1, limit)`).

**Rationale**: OWASP A04 (Insecure Design) -- resource exhaustion via unbounded pagination.

---

### [SUGGESTION] `search_emails` Query String Not Sanitized
**File**: `graph/mail.py` (line 243)

**Issue**: The `query` parameter is wrapped in double quotes and passed to `$search`:

```python
search=f'"{query}"',
```

If `query` contains a double quote character, this breaks the quoting and could alter the KQL search expression. The Graph `$search` parameter uses KQL syntax; injecting KQL operators could change search behavior (e.g., broadening results unexpectedly). The blast radius is limited since `$search` is read-only and scoped to the authenticated user's mailbox.

**Recommendation**: Escape or strip double quotes from the query string before wrapping:

```python
sanitized = query.replace('"', '\\"')
search=f'"{sanitized}"',
```

**Rationale**: OWASP A03 (Injection) -- defense in depth for query parameter handling.

---

### [SUGGESTION] `folder` Parameter in `list_emails` Not Validated
**File**: `graph/mail.py` (line 158)

**Issue**: The `folder` parameter is passed directly to `by_mail_folder_id(folder)`. While the Graph SDK handles URL encoding and the Graph API will reject invalid folder IDs with an OData error, there is no local validation that the value looks like either a well-known folder name or a valid folder ID format. This is low risk since the SDK constructs the URL path safely, but explicit validation of well-known names (inbox, sentitems, drafts, deleteditems, junkemail, archive) with a fallback for ID-format strings would be a hardening improvement.

**Recommendation**: Consider validating against a known set of well-known folder names plus an alphanumeric/base64 pattern for folder IDs.

**Rationale**: Defense in depth -- input validation per project invariants.

---

### [SUGGESTION] Test Coverage Gap for Injection Vectors
**File**: `tests/test_mail.py`

**Issue**: No tests exercise malicious or boundary inputs for `sender`, `subject`, `query`, `folder`, or `limit`. The test suite validates happy paths and error handling but does not verify that injection attempts in filter parameters are handled safely.

**Recommendation**: Add test cases for:
- `sender` containing single quotes (OData injection)
- `subject` containing single quotes
- `query` containing double quotes (KQL injection)
- `limit` values of 0, negative, and very large numbers
- Empty string email addresses

**Rationale**: Security regression prevention -- tests should encode the expected sanitization behavior.

---

## Verdict

**PASS WITH CHANGES** -- The OData filter injection and missing input validation on email addresses are the primary concerns. While exploitation is bounded by the single-user, single-tenant architecture (an attacker would need valid MCP bearer auth first, and can only affect their own mailbox), these represent violations of the project's own input validation invariants and OWASP A03. The unbounded `limit` creates a denial-of-service vector against the server itself. No critical invariant violations were found -- all Graph calls go through `GraphClient`, OData errors are properly wrapped, and no secrets are logged.

---

**Review performed by**: Claude Code (security-reviewer)
**Review focus**: MCP Bearer Auth, Token Storage (Fernet + chmod 600), MSAL OAuth, Graph Input Validation, Secrets/Env, Subprocess Safety, Dependency CVEs
