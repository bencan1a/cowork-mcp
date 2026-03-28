# Outlook MCP Server — Project Spec

## Goal
A self-hosted Python MCP server that gives Claude (Cowork, claude.ai chat, and Claude Code) full read/write access to a personal Outlook account (hotmail.com / Microsoft personal account) via Microsoft Graph API. Runs persistently on Ubuntu server, accessible from anywhere over HTTPS.

---

## Architecture Overview

```
Claude clients (Cowork / claude.ai / Claude Code on Windows laptop)
        │
        │  HTTPS  (Streamable HTTP MCP transport)
        ▼
Cloudflare Tunnel
        │
        ▼
Ubuntu Server
  └── Python MCP Server (FastMCP, port 8000)
          │
          │  Microsoft Graph API (OAuth2)
          ▼
  Personal Outlook account (bencan1a@hotmail.com)
```

---

## Tech Stack

| Component | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | User preference |
| MCP framework | `fastmcp` | Cleanest Streamable HTTP support, minimal boilerplate |
| HTTP server | `uvicorn` | Production-grade ASGI |
| MS Graph client | `msgraph-sdk-python` | Official Microsoft SDK |
| Auth | MSAL (`msal` library) | Handles personal account OAuth2 + token refresh |
| Token storage | Encrypted JSON file on disk | Simple, portable, no external dependencies |
| Tunnel | Cloudflare Tunnel (`cloudflared`) | Free, HTTPS, no port forwarding needed |
| Process manager | `systemd` service | Always-on after reboot |
| Scope control | `.env` file + startup validation | Toggle Graph permission scopes without code changes |

---

## Azure App Registration (Manual — one-time prereq)

> Claude Code cannot do this step. User must complete it manually before running the project.

1. Go to https://portal.azure.com → App registrations → New registration
2. Name: `outlook-mcp-server` (or anything)
3. Supported account types: **"Personal Microsoft accounts only"**
4. Redirect URI: `http://localhost:8400/callback` (type: Web) — used only for initial auth
5. After creation, note the **Application (client) ID**
6. Under **Certificates & secrets** → New client secret → note the value
7. Under **API permissions** → Add the following Microsoft Graph **Delegated** permissions:
   - `Mail.ReadWrite`
   - `Mail.Send`
   - `MailboxSettings.ReadWrite`
   - `Calendars.ReadWrite`
   - `Contacts.ReadWrite`
   - `Tasks.ReadWrite`
   - `User.Read`
8. Grant admin consent (for personal accounts this happens at first OAuth login, not required here)

---

## Project Structure

```
outlook-mcp/
├── README.md
├── requirements.txt
├── .env.example
├── .env                    # gitignored — contains secrets + scope config
├── auth/
│   ├── __init__.py
│   ├── oauth_flow.py       # one-time browser auth flow to get initial token
│   └── token_store.py      # encrypted token read/write/refresh via MSAL
├── graph/
│   ├── __init__.py
│   ├── client.py           # authenticated Graph API client singleton
│   ├── mail.py             # all email operations
│   └── calendar.py         # all calendar operations
├── server.py               # FastMCP server — registers all tools
├── run_auth.py             # standalone script: run once to authenticate
└── deploy/
    ├── outlook-mcp.service # systemd unit file
    └── cloudflare-tunnel-setup.md
```

---

## `.env` Configuration

```env
# Azure app credentials
AZURE_CLIENT_ID=your-client-id-here
AZURE_CLIENT_SECRET=your-client-secret-here

# Token storage
TOKEN_CACHE_PATH=/home/ubuntu/outlook-mcp/.token_cache.json
TOKEN_ENCRYPTION_KEY=generate-with-Fernet

# MCP server auth (simple bearer token — set this yourself)
MCP_API_KEY=generate-a-long-random-string

# Scope toggles — set to "true" or "false" to enable/disable tool groups at startup
SCOPE_MAIL_READ=true
SCOPE_MAIL_WRITE=true
SCOPE_MAIL_SEND=true
SCOPE_CALENDAR_READ=true
SCOPE_CALENDAR_WRITE=true
SCOPE_CONTACTS_READ=true
SCOPE_CONTACTS_WRITE=false
SCOPE_TASKS_READ=true
SCOPE_TASKS_WRITE=false

# Server config
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
```

---

## Authentication Flow

### Initial setup (run once)
1. Run `python run_auth.py`
2. Script opens browser → user logs into Microsoft personal account
3. Grants consent for configured scopes
4. Token (access + refresh) encrypted and saved to `TOKEN_CACHE_PATH`

### Ongoing
- MSAL handles silent token refresh automatically
- If refresh token expires (rare — typically 90 days inactive), re-run `run_auth.py`
- Server checks token validity on startup; logs warning if refresh needed

---

## MCP Tools to Implement

All tools conditionally registered at startup based on scope toggles in `.env`.

### Mail tools (`SCOPE_MAIL_*`)
| Tool | Description |
|---|---|
| `list_emails` | List emails with filters: folder, sender, subject, date range, unread only, limit |
| `get_email` | Get full email by ID including body, attachments list |
| `search_emails` | Full-text search across mailbox |
| `send_email` | Send new email: to, cc, bcc, subject, body (html or plain), attachments |
| `reply_to_email` | Reply or reply-all to an email by ID |
| `forward_email` | Forward email by ID to new recipients |
| `move_email` | Move email to a folder |
| `delete_email` | Move email to Deleted Items (soft delete) |
| `mark_email_read` | Mark as read or unread |
| `list_mail_folders` | List all mail folders |
| `create_mail_folder` | Create a new folder |
| `get_mailbox_settings` | Get timezone, auto-reply status, etc. |
| `set_auto_reply` | Enable/disable out-of-office auto-reply with message |

### Calendar tools (`SCOPE_CALENDAR_*`)
| Tool | Description |
|---|---|
| `list_events` | List events with filters: date range, calendar, limit |
| `get_event` | Get full event details by ID |
| `search_events` | Search events by keyword |
| `create_event` | Create event: title, start, end, location, body, attendees, recurrence, reminders, online meeting flag |
| `update_event` | Update any fields of an existing event by ID |
| `delete_event` | Delete event by ID |
| `accept_event` | Accept a meeting invitation |
| `decline_event` | Decline a meeting invitation |
| `tentative_event` | Mark meeting invitation as tentative |
| `list_calendars` | List all calendars |
| `get_free_busy` | Get free/busy schedule for a date range |

### Contact tools (`SCOPE_CONTACTS_*`)
| Tool | Description |
|---|---|
| `list_contacts` | List contacts with optional search |
| `get_contact` | Get contact by ID |
| `create_contact` | Create new contact |
| `update_contact` | Update contact fields |

### Task tools (`SCOPE_TASKS_*`)
| Tool | Description |
|---|---|
| `list_tasks` | List tasks with filters: list, completed, due date |
| `create_task` | Create task with title, due date, notes |
| `complete_task` | Mark task as complete |
| `delete_task` | Delete task |

---

## MCP Server Security

- **Bearer token auth**: Every request must include `Authorization: Bearer {MCP_API_KEY}` header
- Server returns HTTP 401 for missing or invalid token
- Cloudflare Tunnel provides TLS — do not expose port 8000 directly
- Token cache file has `chmod 600` permissions, owned by the service user

---

## Deployment

### Systemd service (`deploy/outlook-mcp.service`)
Auto-start on boot, restart on failure, runs as dedicated user.

### Cloudflare Tunnel
- Install `cloudflared` on Ubuntu server
- Authenticate with Cloudflare account (free tier is sufficient)
- Create tunnel pointing to `localhost:8000`
- Assign a hostname, e.g. `outlook-mcp.yourdomain.com` or use a free `*.trycloudflare.com` URL
- Tunnel runs as a second systemd service

### Adding to Claude clients
Once the tunnel is live, add the server URL to each client:

**Cowork / Claude Desktop**: Settings → Connectors → Add custom connector
```
https://outlook-mcp.yourdomain.com/mcp
```

**Claude Code**:
```bash
claude mcp add --transport http outlook-mcp https://outlook-mcp.yourdomain.com/mcp \
  --header "Authorization: Bearer YOUR_MCP_API_KEY"
```

---

## Implementation Notes for Claude Code

1. Use `fastmcp>=2.0` — it handles Streamable HTTP transport natively with minimal config
2. MSAL personal account flow requires `authority="https://login.microsoftonline.com/consumers"` — **not** the common or organizations endpoint; this is a common gotcha
3. Tools should handle Graph API pagination transparently (Graph returns max 10–50 items by default)
4. All datetime inputs/outputs should be ISO 8601 with timezone; Graph API uses UTC
5. Scope toggle logic: at server startup, read `.env`, register only tools whose scope group is enabled. Log which tools were registered and which were skipped.
6. Error handling: Graph API errors should surface as meaningful MCP tool errors, not raw stack traces
7. The `run_auth.py` script should spin up a temporary localhost HTTP server on port 8400 to capture the OAuth redirect automatically — no manual copy-pasting of auth codes

---

## Out of Scope (for this build)
- Multi-user support (single personal account only)
- Attachment upload/download (note attachment metadata in email tools is fine)
- Calendar sharing / delegate access
- Push notifications / webhooks (polling on demand only)
