# Phone page and phone alerts

**State on 15 Nov 2026:** the phone page stays on Tailscale Serve `/` →
`127.0.0.1:8765`. ntfy is on (tailnet only, no Funnel) as of v0.7.4. The
topic and phone login are in `~/.config/herdr-attention/ntfy-subscribe.txt`
(mode 600) and in the login-protected Settings panel; they are not written
in this file or in the logs.

What you get when it is on:

- `https://<laptop>.<tailnet>.ts.net/`: the same list as the panel, read-only
  apart from *Mark read* and a few settings. Login with a passphrase.
  The overview also mirrors agent quotas from the Omarchy Agents panel
  (plan, usage windows, reset times in Europe/Amsterdam, stale age).
- ntfy alerts on the OnePlus: agent, workspace and event type only, with a
  link to the page. Sent only after 5 min without laptop input (adjustable).

Both live on the laptop. When the laptop sleeps or is offline, nothing is
sent and the page is unreachable; the page then says "last seen …", which
does not prove the laptop is asleep.

## Proposed setup (waiting for approval)

Facts checked on 7 Nov 2026: Tailscale is running, MagicDNS and HTTPS
certificates are available on the tailnet, `tailscale serve` has no config,
ntfy is not in the Arch repos, Docker 29 is running and you are in the
`docker` group, ports 2586 and 8765 are free.

### 1. ntfy server in Docker, local only

New files (no secrets in them):

`~/.config/ntfy-server/server.yml`
```yaml
base-url: "https://<laptop>.<tailnet>.ts.net:8443"
listen-http: ":80"
behind-proxy: true
cache-file: "/var/cache/ntfy/cache.db"
cache-duration: "12h"            # ntfy default, as in the plan
auth-file: "/var/lib/ntfy/user.db"
auth-default-access: "deny-all"  # login required for everything
enable-signup: false
enable-login: true
web-root: "disable"              # no web app, the Android app is enough
attachment-cache-dir: ""         # no attachments
```

Container, bound to localhost only (not reachable from the LAN):
```bash
docker run -d --name ntfy --restart unless-stopped \
  -p 127.0.0.1:2586:80 \
  -v ~/.config/ntfy-server:/etc/ntfy:ro \
  -v ntfy-cache:/var/cache/ntfy -v ntfy-auth:/var/lib/ntfy \
  binwiederhier/ntfy:v2.11.0 serve   # pin the newest release at setup time
```

Users and access (passwords typed by you, interactively):
```bash
docker exec -it ntfy ntfy user add herdr-phone          # the phone logs in with this
docker exec -it ntfy ntfy access herdr-phone herdr read-only
docker exec -it ntfy ntfy user add herdr-sender
docker exec -it ntfy ntfy access herdr-sender herdr write-only
docker exec -it ntfy ntfy token add herdr-sender        # copy the tk_... token
herdr-attention secret set ntfy-token                   # paste it; goes into the GNOME keyring
```

### 2. Phone page on localhost

Edit `~/.config/herdr-attention/config.toml`:
```toml
[phone]
enabled = true
bind = "127.0.0.1"
port = 8765
allowed_origins = ["https://<laptop>.<tailnet>.ts.net"]
cookie_secure = true

[notify]
backend = "ntfy"
ntfy_url = "https://<laptop>.<tailnet>.ts.net:8443"
ntfy_topic = "herdr"
dashboard_url = "https://<laptop>.<tailnet>.ts.net"
```
Then:
```bash
herdr-attention secret set dashboard-passphrase   # at least 12 characters
systemctl --user restart herdr-attention
```

### 3. Tailscale Serve (tailnet only, no Funnel)

```bash
tailscale serve --bg --https=443  http://127.0.0.1:8765   # phone page
tailscale serve --bg --https=8443 http://127.0.0.1:2586   # ntfy
```
Only devices on your tailnet can reach these. Being on the tailnet is not
enough on its own: the page needs the passphrase and ntfy needs a user.
**Note:** the HTTPS certificate puts the machine name
(`<laptop>.<tailnet>.ts.net`) in the public certificate log.

### 4. On the phone

1. Tailscale app: connected, set to always on.
2. ntfy app → Settings → add server `https://<laptop>.<tailnet>.ts.net:8443`
   with user `herdr-phone`; subscribe to topic `herdr`; turn on **instant delivery**
   (accept the permanent notification).
3. Android settings → Apps → ntfy → Battery → **Unrestricted** (OnePlus also
   has its own app-killing; allow background activity).
4. On the laptop: `herdr-attention notify-test`, then test with the screen off.

## Undo

```bash
tailscale serve --https=443 off; tailscale serve --https=8443 off
docker rm -f ntfy && docker volume rm ntfy-cache ntfy-auth
rm -r ~/.config/ntfy-server
# set phone.enabled = false and notify.backend = "none", then:
systemctl --user restart herdr-attention
herdr-attention secret clear ntfy-token; herdr-attention secret clear dashboard-passphrase
```

## Sync topic access (ntfy 2.11)

This install pins `binwiederhier/ntfy:v2.11.0` with `auth-default-access: deny-all`.
That version does not automatically allow a user to use their own account sync
topic (`st_…`), which the official phone app subscribes to at login. After
creating `herdr-phone`, grant that user read-write on the `sync_topic` from
`GET /v1/account` (`ntfy access herdr-phone <sync-topic> read-write`). ntfy
2.25+ does this itself. Do not put the sync topic, the notification topic, or
passwords in this file.

## Tailnet allow list (0.9.7.4)

Settings > **Tailnet access**, shown only in the laptop's own window (the 127.0.0.1 desktop
listener). It lists the tailnet devices from `tailscale status --json` (name, OS, online, IP,
"signed in ... ago"); add or remove them, or type a MagicDNS name or a 100.x address.

- A switch "Only allow the devices below", **off by default**. Off (or an empty list): every
  tailnet device can open the login page, as before; off keeps the saved list for later.
- On with devices listed: Tailscale Serve passes the caller's tailnet address as `X-Forwarded-For` (the
  listener is bound to 127.0.0.1, so only Serve or a process on the laptop can reach it). A device
  that is not on the list gets `403 forbidden` before the login page, files or data. The laptop's
  own tailnet addresses always pass; the passphrase login still applies on top.
- Names are resolved to tailnet IPs every minute; a name that no longer resolves allows nothing.
- `GET/POST /api/tailnet-access` answer only on the Unix socket and the desktop listener (CSRF
  for changes); the phone listener answers 403 and never sends the device list.
- Stored in `state.db` (`meta` table: `tailnet_allow`, `tailnet_allow_on`, `tailnet_seen`), so it survives restarts and
  is in the installer's state backup; "reset settings" does not clear it.
- Locked out? On the laptop: Settings > Tailnet access > switch off > Save, or
  `sqlite3 ~/.local/state/herdr-attention/state.db "UPDATE meta SET value='0' WHERE key='tailnet_allow_on'"`
  and restart `herdr-attention.service`.

## Security notes

- The page and every API route need a session cookie (HttpOnly, SameSite=Strict,
  Secure). Changes also need a CSRF token and an allowed Origin. Five wrong
  passphrases lock a client out for five minutes.
- Strict Content-Security-Policy, no inline scripts, agent text is inserted
  with `textContent` only.
- Unwatched workspaces never reach the phone's views, details or alerts. The
  workspace chooser (behind login, changes need the CSRF token) lists their
  id and label only, so you can switch them on from the phone (since v0.2).
- The ntfy message has no output, no task title and no paths.
- Output shown on tap is cleaned of control codes and passed through
  best-effort redaction (`output.redact`). Redaction is pattern based and
  cannot catch everything.
