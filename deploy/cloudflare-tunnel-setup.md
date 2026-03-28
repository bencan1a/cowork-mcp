# Cloudflare Tunnel Setup

This guide walks through setting up a Cloudflare Tunnel to expose the Outlook MCP server over HTTPS without opening firewall ports.

**Prerequisites:** A Cloudflare account (free tier is sufficient). A domain managed by Cloudflare is optional — free `*.trycloudflare.com` URLs are available without a domain.

---

## 1. Install cloudflared on Ubuntu

```bash
curl -L --output cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
cloudflared --version
```

---

## 2. Authenticate with Cloudflare

```bash
cloudflared tunnel login
```

This opens a browser window. Log in to your Cloudflare account and select the domain you want to use. A credentials file is saved to `~/.cloudflared/cert.pem`.

If you do not have a domain, skip this step and use a temporary URL instead (see the note at the end).

---

## 3. Create a named tunnel

```bash
cloudflared tunnel create outlook-mcp
```

Note the **tunnel ID** printed in the output (a UUID like `a1b2c3d4-...`). A credentials file is saved to `~/.cloudflared/<tunnel-id>.json`.

---

## 4. Configure the tunnel

Create `~/.cloudflared/config.yml`, substituting your tunnel ID and hostname:

```yaml
tunnel: <your-tunnel-id>
credentials-file: /home/ubuntu/.cloudflared/<your-tunnel-id>.json

ingress:
  - hostname: outlook-mcp.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

Replace `outlook-mcp.yourdomain.com` with the hostname you want to use.

---

## 5. Create the DNS record

```bash
cloudflared tunnel route dns outlook-mcp outlook-mcp.yourdomain.com
```

This creates a CNAME record in Cloudflare DNS pointing to your tunnel. It may take a minute to propagate.

---

## 6. Install cloudflared as a systemd service

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

Check status:

```bash
sudo systemctl status cloudflared
```

---

## 7. Verify the tunnel is working

```bash
# Check tunnel health
cloudflared tunnel info outlook-mcp

# Test the MCP endpoint (should return 401 Unauthorized — that means the server is reachable)
curl -i https://outlook-mcp.yourdomain.com/mcp

# Test with auth header
curl -i https://outlook-mcp.yourdomain.com/mcp \
  -H "Authorization: Bearer YOUR_MCP_API_KEY"
```

---

## Using a temporary URL (no domain required)

If you do not have a domain on Cloudflare, you can get a free temporary `*.trycloudflare.com` URL:

```bash
cloudflared tunnel --url http://localhost:8000
```

This prints a URL like `https://random-words.trycloudflare.com`. The URL changes each time you run the command, so it is suitable for testing only. For persistent deployments, use a named tunnel with a real domain.

---

## Updating Claude client configuration

After the tunnel is live, add the server to your Claude clients using the tunnel URL. See the main README for exact commands.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `cloudflared` exits immediately | Config file not found | Check path in `~/.cloudflared/config.yml` |
| DNS not resolving | Record not propagated | Wait a few minutes; verify with `dig outlook-mcp.yourdomain.com` |
| `502 Bad Gateway` from Cloudflare | MCP server not running | Check `sudo systemctl status outlook-mcp` |
| `401 Unauthorized` from curl | Server is up; no auth header sent | Expected — add `-H "Authorization: Bearer ..."` |
| Tunnel shows inactive in dashboard | Service not started | Run `sudo systemctl start cloudflared` |
