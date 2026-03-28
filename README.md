# Outlook MCP Server

A self-hosted Python MCP server that gives Claude full read/write access to a personal Outlook account (hotmail.com / Microsoft personal account) via the Microsoft Graph API. Runs persistently on an Ubuntu server behind a Cloudflare Tunnel, accessible over HTTPS from any Claude client.

```
Claude clients (Claude Code / claude.ai / Cowork on Windows laptop)
        |  HTTPS (Streamable HTTP MCP transport)
        v
Cloudflare Tunnel
        v
Ubuntu Server -- Python MCP Server (FastMCP, port 8000)
        |  Microsoft Graph API (OAuth2 via MSAL)
        v
Personal Outlook account
```

---

## Prerequisites

- **Python 3.11+** on the Ubuntu server
- **Ubuntu server** (VPS, home server, or any always-on Linux machine)
- **Cloudflare account** (free tier is sufficient; a domain is optional)
- **Azure app registration** (one-time manual setup — see below)

---

## Azure App Registration

This is a one-time manual step that must be completed before running the server.

1. Go to [https://portal.azure.com](https://portal.azure.com) → **App registrations** → **New registration**
2. Name: `outlook-mcp-server` (or anything you like)
3. Supported account types: **"Personal Microsoft accounts only"**
4. Redirect URI: `http://localhost:8400/callback` (type: **Web**)
5. After creation, note the **Application (client) ID**
6. Under **Certificates & secrets** → **New client secret** → note the secret value (shown only once)
7. Under **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions** — add:
   - `Mail.ReadWrite`
   - `Mail.Send`
   - `MailboxSettings.ReadWrite`
   - `Calendars.ReadWrite`
   - `Contacts.ReadWrite`
   - `Tasks.ReadWrite`
   - `User.Read`
8. Admin consent is not required — consent is granted interactively during the first OAuth login

---

## Installation

```bash
git clone https://github.com/bencan1a/cowork-mcp.git
cd cowork-mcp
python3.11 -m venv venv
. venv/bin/activate
pip install --upgrade pip
pip install -e .
```

---

## Configuration

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Open `.env` in a text editor and set the following:

```env
# From your Azure app registration
AZURE_CLIENT_ID=your-client-id-here
AZURE_CLIENT_SECRET=your-client-secret-here

# Path where the encrypted token cache will be stored
TOKEN_CACHE_PATH=/home/ubuntu/outlook-mcp/.token_cache.json

# Encryption key for the token cache — generate with:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY=<generated-key>

# Bearer token for MCP server auth — generate with:
# python -c "import secrets; print(secrets.token_urlsafe(32))"
MCP_API_KEY=<long-random-string>
```

The remaining settings (scope toggles, host, port, log level) have sensible defaults. See `.env.example` for all options.

### Generating secret values

```bash
# TOKEN_ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# MCP_API_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Initial Authentication

Run this once to authenticate with your Microsoft personal account. A browser window will open.

```bash
. venv/bin/activate
python run_auth.py
```

The script spins up a temporary server on `http://localhost:8400` to capture the OAuth redirect automatically. After you log in and grant consent, the tokens are encrypted and saved to `TOKEN_CACHE_PATH`.

MSAL handles silent token refresh automatically on subsequent runs. If the refresh token expires (after ~90 days of inactivity), re-run `python run_auth.py`.

---

## Running Locally (for testing)

```bash
. venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 8000
```

Test that the server is up:

```bash
# Should return 401 (server is running, auth header missing)
curl -i http://localhost:8000/mcp

# With auth header
curl -i http://localhost:8000/mcp \
  -H "Authorization: Bearer YOUR_MCP_API_KEY"
```

---

## Deployment on Ubuntu

### 1. Copy the project to the server

```bash
# On the server
git clone https://github.com/bencan1a/cowork-mcp.git /home/ubuntu/outlook-mcp
cd /home/ubuntu/outlook-mcp
python3.11 -m venv venv
. venv/bin/activate
pip install -e .
```

### 2. Set up the .env file

Copy and configure `.env` on the server (same steps as above). Make sure `TOKEN_CACHE_PATH` points to an absolute path the service user can write to.

Set secure permissions:

```bash
chmod 600 /home/ubuntu/outlook-mcp/.env
```

### 3. Run the one-time auth flow on the server

If the server has a desktop environment or you can forward a browser session:

```bash
python run_auth.py
```

Alternatively, run `run_auth.py` on your local machine with the same `.env`, then copy the token cache file to the server:

```bash
scp .token_cache.json ubuntu@your-server:/home/ubuntu/outlook-mcp/
chmod 600 /home/ubuntu/outlook-mcp/.token_cache.json
```

### 4. Install the systemd service

```bash
sudo cp deploy/outlook-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable outlook-mcp
sudo systemctl start outlook-mcp
```

Check status and logs:

```bash
sudo systemctl status outlook-mcp
sudo journalctl -u outlook-mcp -f
```

---

## Cloudflare Tunnel

See [deploy/cloudflare-tunnel-setup.md](deploy/cloudflare-tunnel-setup.md) for step-by-step instructions.

The short version:

```bash
# Install cloudflared
curl -L --output cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

# Authenticate and create tunnel
cloudflared tunnel login
cloudflared tunnel create outlook-mcp

# Configure, route DNS, and install as service
# (see full guide for config.yml contents)
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

---

## Adding to Claude Clients

### Claude Code

```bash
claude mcp add --transport http outlook-mcp https://your-tunnel.com/mcp \
  --header "Authorization: Bearer YOUR_MCP_API_KEY"
```

### Claude Desktop

Settings → Developer → Edit Config → add to `mcpServers`:

```json
{
  "mcpServers": {
    "outlook-mcp": {
      "type": "http",
      "url": "https://your-tunnel.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_API_KEY"
      }
    }
  }
}
```

### Cowork / Other Claude Clients

Settings → Connectors (or MCP Servers) → Add custom connector:

- URL: `https://your-tunnel.com/mcp`
- Header: `Authorization: Bearer YOUR_MCP_API_KEY`

---

## Available Tools

Tools are registered at startup based on scope toggles in `.env`. Disabled scopes are skipped entirely.

### Mail — Read (`SCOPE_MAIL_READ`)

| Tool | Description |
|------|-------------|
| `list_emails` | List emails with filters: folder, sender, subject, date range, unread only, limit |
| `get_email` | Get full email by ID including body and attachment metadata |
| `search_emails` | Full-text search across mailbox using Graph `$search` |

### Mail — Write (`SCOPE_MAIL_WRITE`)

| Tool | Description |
|------|-------------|
| `move_email` | Move an email to a folder |
| `delete_email` | Soft-delete an email to Deleted Items |
| `mark_email_read` | Mark an email as read or unread |
| `list_mail_folders` | List all top-level mail folders |
| `create_mail_folder` | Create a new mail folder |
| `get_mailbox_settings` | Get timezone, auto-reply status, and other mailbox settings |
| `set_auto_reply` | Enable or disable out-of-office auto-reply with scheduled start/end |

### Mail — Send (`SCOPE_MAIL_SEND`)

| Tool | Description |
|------|-------------|
| `send_email` | Send a new email (to, cc, bcc, subject, HTML or plain body) |
| `reply_to_email` | Reply or reply-all to an email by ID |
| `forward_email` | Forward an email to new recipients |

### Calendar — Read (`SCOPE_CALENDAR_READ`)

| Tool | Description |
|------|-------------|
| `list_events` | List events within a date range (expands recurring events) |
| `get_event` | Fetch a single calendar event by ID |
| `search_events` | Search calendar events by subject or body |
| `list_calendars` | List all calendars for the authenticated user |
| `get_free_busy` | Query free/busy availability for a list of email addresses |

### Calendar — Write (`SCOPE_CALENDAR_WRITE`)

| Tool | Description |
|------|-------------|
| `create_event` | Create a new calendar event with attendees, online meeting, reminders |
| `update_event` | Update any fields of an existing event by ID |
| `delete_event` | Delete a calendar event by ID |
| `accept_event` | Accept a meeting invitation |
| `decline_event` | Decline a meeting invitation |
| `tentative_event` | Tentatively accept a meeting invitation |

### Contacts — Read (`SCOPE_CONTACTS_READ`)

| Tool | Description |
|------|-------------|
| `list_contacts` | List contacts with optional search filter |
| `get_contact` | Fetch a single contact by ID |

### Contacts — Write (`SCOPE_CONTACTS_WRITE`, default off)

| Tool | Description |
|------|-------------|
| `create_contact` | Create a new contact |
| `update_contact` | Update existing contact fields |

### Tasks — Read (`SCOPE_TASKS_READ`)

| Tool | Description |
|------|-------------|
| `list_tasks` | List tasks filtered by list, completion status, limit |

### Tasks — Write (`SCOPE_TASKS_WRITE`, default off)

| Tool | Description |
|------|-------------|
| `create_task` | Create a task with title, due date, and notes |
| `complete_task` | Mark a task as completed |
| `delete_task` | Delete a task by ID |

---

## Troubleshooting

### "AADSTS50020: User account from identity provider does not exist in tenant"

You are using the wrong MSAL authority. Personal Microsoft accounts (`@hotmail.com`, `@outlook.com`) require:

```python
authority="https://login.microsoftonline.com/consumers"
```

Using `common` or `organizations` will fail for personal accounts.

### Token expired / refresh fails

Re-run the one-time auth flow:

```bash
. venv/bin/activate
python run_auth.py
```

Refresh tokens typically last 90 days of inactivity. Regular use keeps them alive indefinitely.

### "401 Unauthorized" from the MCP server

The `Authorization: Bearer <key>` header is missing or the key does not match `MCP_API_KEY` in `.env`. Verify the header value matches exactly.

### Port 8000 already in use

```bash
sudo lsof -i :8000
# or change PORT in .env and update the systemd service ExecStart line
```

### Server starts but tools are missing

Check that the relevant scope toggle is `true` in `.env` and that the service was restarted after the change:

```bash
sudo systemctl restart outlook-mcp
sudo journalctl -u outlook-mcp -n 50
```

The startup log line `Registered tool groups: [...]` shows which groups are active.

### "Module not found" errors

The virtual environment is not activated. Make sure the systemd service `ExecStart` points to the full path of the venv Python:

```
ExecStart=/home/ubuntu/outlook-mcp/venv/bin/uvicorn server:app ...
```

### Cloudflare Tunnel shows "unhealthy"

Check that the MCP server is running and listening on port 8000:

```bash
sudo systemctl status outlook-mcp
curl -i http://localhost:8000/mcp
```

---

## Development

```bash
# Install dev dependencies
pip install -e '.[dev]'
pre-commit install

# Run tests
pytest

# All quality checks
make check-all

# Auto-fix formatting and lint
make fix
```

See [CLAUDE.md](CLAUDE.md) for full development guidance.
