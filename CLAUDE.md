# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Goal

A self-hosted Python MCP server that gives Claude full read/write access to a personal Outlook account (hotmail.com / Microsoft personal account) via the Microsoft Graph API. Runs persistently on an Ubuntu server behind a Cloudflare Tunnel, accessible over HTTPS.

The project spec is in [agent-projects/Outlook MCP Server — Project Spec.md](agent-projects/Outlook%20MCP%20Server%20%E2%80%94%20Project%20Spec.md).

## Architecture

```
Claude clients (Claude Code / claude.ai / Cowork on Windows laptop)
        │  HTTPS (Streamable HTTP MCP transport)
        ▼
Cloudflare Tunnel
        ▼
Ubuntu Server — Python MCP Server (FastMCP, port 8000)
        │  Microsoft Graph API (OAuth2 via MSAL)
        ▼
Personal Outlook account
```

**Key components:**
- `server.py` — FastMCP server; registers all tools at startup based on scope toggles
- `auth/oauth_flow.py` — One-time browser auth to get initial OAuth tokens
- `auth/token_store.py` — Encrypted token persistence + silent MSAL refresh
- `graph/client.py` — Authenticated Graph API client singleton
- `graph/mail.py`, `graph/calendar.py` — Domain-specific Graph operations
- `run_auth.py` — Standalone script; run once on first setup to authenticate
- `deploy/` — systemd unit file + Cloudflare Tunnel setup notes
- `.env` — Secrets and scope toggles (gitignored); see `.env.example`

## Critical Implementation Notes

**MSAL authority URL** — personal Microsoft accounts (`@hotmail.com`, `@outlook.com`) require:
```python
authority="https://login.microsoftonline.com/consumers"
```
Using `common` or `organizations` will fail silently for personal accounts.

**Scope toggle system** — `.env` contains `SCOPE_MAIL_READ`, `SCOPE_CALENDAR_WRITE`, etc. On startup, `server.py` reads these and registers only enabled tool groups. Log which tools were registered and which were skipped.

**Graph API pagination** — Graph returns 10–50 items by default. All list tools must handle `@odata.nextLink` pagination transparently.

**Datetime** — All inputs/outputs use ISO 8601 with timezone. Graph API uses UTC internally.

**Error handling** — Surface Graph API errors as meaningful MCP tool errors, not raw stack traces.

**OAuth redirect capture** — `run_auth.py` must spin up a temporary `http://localhost:8400` server to capture the redirect automatically. No manual code copy-pasting.

**Token cache** — File at `TOKEN_CACHE_PATH` must be `chmod 600`, owned by the service user.

**Bearer auth** — Every MCP request must include `Authorization: Bearer {MCP_API_KEY}`. Return HTTP 401 for missing/invalid tokens.

## Virtual Environment

**Always activate before running Python commands:**
```bash
. venv/bin/activate
```

"Module not found" errors mean the venv is not activated.

## Common Commands

```bash
# Setup
python3 -m venv venv && . venv/bin/activate
pip install --upgrade pip && pip install -e '.[dev]'
pre-commit install

# Run the MCP server (requires .env configured)
uvicorn server:app --host 0.0.0.0 --port 8000

# One-time auth setup (opens browser)
python run_auth.py

# Tests
pytest                          # all tests
pytest tests/test_mail.py       # single file
pytest -k "test_list_emails"    # pattern match
pytest -v --cov=src             # verbose with coverage

# Code quality
make check-all                  # all checks at once
make fix                        # auto-fix format + lint
ruff check --fix .
mypy src/
```

## Code Quality Standards

All code must pass `make check-all` before commit:
1. **Formatting**: `ruff format` (100-char line length)
2. **Linting**: `ruff check`
3. **Type checking**: both `mypy` and `pyright` (configured in `pyproject.toml` and `pyrightconfig.json`)
4. **Security**: `bandit -r src/`
5. **Tests**: all pass with >80% coverage

For unavoidable suppressions: `# noqa: <code>`, `# type: ignore[<code>]`, `# nosec` — always add a justification comment.

## File Organization

| Folder | Purpose | Git |
|--------|---------|-----|
| `agent-tmp/` | Scratch/debug/intermediates (auto-deleted after 7 days) | No |
| `agent-projects/<project>/` | Active implementation plans | Yes |
| `docs/` | Permanent documentation | Yes |
| `docs/_generated/` | Auto-generated — do not edit manually | Yes |

- Do not create planning documents in the project root.
- Use `agent-tmp/` for all temporary work.
- Plans in `agent-projects/<name>/plan.md` need frontmatter: `status`, `owner`, `created`, `summary`.

## Deployment (Ubuntu Server)

1. `systemd` service in `deploy/outlook-mcp.service` — auto-start on boot, restart on failure
2. Cloudflare Tunnel pointing to `localhost:8000` — provides TLS, no port forwarding needed
3. Add to Claude Code: `claude mcp add --transport http outlook-mcp https://<tunnel-url>/mcp --header "Authorization: Bearer YOUR_MCP_API_KEY"`

## Out of Scope

- Multi-user support
- Attachment upload/download (metadata is fine)
- Calendar sharing / delegate access
- Push notifications / webhooks
