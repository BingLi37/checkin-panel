# Deploying the panel

Three ways to run it, and one thing they all share: **the panel has no login.** Anyone who can
reach its port can read every account's password and session in plaintext, because `GET
/api/accounts` returns them that way by design (ADR-0003 — a single-user panel on the owner's
own machine). Everything below follows from that.

Pick a way:

| | you get | you accept |
|---|---|---|
| `start.bat` | nothing to install | a console window that must stay open |
| the desktop app | a window and a tray icon; closing it keeps checking in | Windows only, and a build step |
| the container | it survives reboots, runs anywhere Docker does | Docker, and one line to get the exposure right |

Only run **one** at a time. They share `data/panel.db` and `.browser_profiles/`, so two panels
mean a locked database and two browsers fighting over one profile. The desktop app refuses to
start beside another one on its own; the container has separate volumes, so starting it beside
`start.bat` gives you two *different* sets of accounts checking into the same sites instead.

## 1. The console

```bat
start.bat
```

Then open <http://127.0.0.1:8000>. The window has to stay open — closing it stops the scheduler,
and nothing warns you.

`run.py` binds `0.0.0.0` by default, which means **anyone on your network can open it**. If you
are not deliberately reaching it from a phone or another machine, set `PANEL_HOST=127.0.0.1`:

```bat
set PANEL_HOST=127.0.0.1
.venv\Scripts\python.exe run.py
```

## 2. The desktop app

```bat
.venv\Scripts\python.exe -m desktop                :: run it from source
.venv\Scripts\pyinstaller.exe desktop\desktop.spec :: or build dist\签到面板\
```

Clicking **X** does not quit. It asks once, offers to remember the answer, and hides to the
notification area — left-click the tray icon to bring it back, right-click and pick **退出** to
really stop. That is the point of this way of running it: the scheduler keeps going without a
window in your way.

The build in `dist\签到面板\` is movable — copy the whole folder anywhere. `data\panel.db`,
`.browser_profiles\` and `.local\cloakbrowser\` are created **beside the exe**, so the accounts
travel with it. The browser (~700MB) downloads on first use, in the background; every HTTP
check-in works before it finishes.

It binds `127.0.0.1` by default, unlike `run.py`.

## 3. The container

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

Then open <http://127.0.0.1:8000>. Set the timezone before the first check-in, in
`docker-compose.yml`:

```yaml
environment:
  TZ: Asia/Shanghai
```

That one is not cosmetic. A site opens its bonus at a particular hour (`checkin_after` on the
account) and the panel measures the day in **local** time, so a container left on UTC tries at
the wrong hour and burns retries.

If your proxy runs on the host, it is not `127.0.0.1` from inside a container:

```yaml
  CHECKIN_PROXY_URL: http://host.docker.internal:7897
```

### The one line that matters

```yaml
ports:
  - "127.0.0.1:8000:8000"     # only this machine
```

Change it to `"8000:8000"` and the panel is on every interface your host has. Publishing that to
the internet is not "exposing a dashboard" — it is **putting a plaintext credential viewer
online**, because `GET /api/accounts` answers any caller with every account's password and
session. Assume it is found: scanners reach a new open port in minutes, and there is no login
to fail, no rate limit, and no log of who read what.

So leave the line alone, and reach it one of these two ways instead.

**A private network — simplest, and nothing is published.** Tailscale, WireGuard or a VPN puts
your other devices on the same network as the host, and the panel stays bound to loopback or to
the private interface. No certificate, no password to choose, no public port at all. If you just
want the panel from your phone, stop here.

**A reverse proxy, if it must be on the public internet.** Three things, and all three are
required — any two of them without the third is not enough:

1. **Authentication in front of the panel.** Basic auth is acceptable *only* over TLS; anything
   with real sessions is better. The panel itself will never ask for a password.
2. **TLS.** Without it the credentials the panel returns cross the network in the clear, and so
   does the password protecting it. Caddy does this by itself; with nginx use certbot.
3. **The panel still not published.** Keep `127.0.0.1:8000:8000` and have the proxy reach it
   over loopback (or put both on a Docker network and publish only the proxy). If the panel's own
   port is open, the proxy is decoration — anyone can go around it.

A Caddy example that satisfies all three:

```
panel.example.com {
	basicauth {
		owner $2a$14$...          # caddy hash-password
	}
	reverse_proxy 127.0.0.1:8000
}
```

And whatever you build, keep in mind what it is guarding: not a UI, but the only copy of your
accounts.

### If you paste an IdP session in, the panel is guarding more than the accounts

A server with no desktop cannot show the login window an OAuth-only account needs, so the way to
set one up there is to export the IdP session from your own browser with a cookie extension and
paste it into the panel（README「服务器上怎么做浏览器登录」方案 B）.

Pasting the *site's* own cookie instead (方案 A, the 「会话 Cookie」 login method) exposes one
site's account and nothing else, so prefer it wherever it is enough — it covers any site whose
check-in is a POST to a route. The rest of this section is about the IdP paste specifically, which
`login_bonus` and `visit` accounts have no alternative to. Two things about it change what the
exposure decisions above are worth:

- **What you paste is the whole linux.do or github.com account**, not a per-site credential. It
  is written into that account's browser profile, and the panel does not copy it into the
  database — but the panel has no login of its own, so anyone who can reach the panel can use
  that identity. Every line about `PANEL_HOST` and the published port matters more after this
  step, not less.
- **It crosses the network to get there.** Over plain HTTP to a published port, that paste is in
  the clear on the wire. If the panel is anywhere but loopback or a private interface when you do
  this, do it through TLS or an SSH tunnel — the alternative is handing your forum account to
  whatever is between you and the server.

The panel writes cookie *values*, which is why this works at all where copying a profile
directory does not: the profile's cookie key is DPAPI-bound to one Windows account, while values
are re-encrypted by the receiving browser.

- **Deleting the account offers to delete that profile too, and the default is to delete it.**
  The session you pasted lives in the directory, not in the database, so keeping the profile
  keeps a working forum or GitHub login on the disk of a machine whose panel has no login of its
  own — while the account it belonged to is gone from the list. Kept profiles, and the ones a
  rename leaves behind (the directory is named after the account), are listed under
  「清理 profile」 beside the page title, with their size, and can be deleted there.

### Back up the database yourself

`panel-data` is the only copy of your accounts. `docker compose down -v` deletes it, along with
the profiles and the browser.

```bash
docker volume ls | grep panel-data      # the name is prefixed with the folder's name
docker run --rm -v any-automaticcheckin_panel-data:/d -v "$PWD:/out" alpine \
  cp /d/panel.db /out/panel-backup.db
```

Treat that file the way you would treat a password file, because it is one.

### If you built an image to share

Don't, without checking it first. `.dockerignore` is written as a deny-list so a forgotten line
cannot bake `data/panel.db` into a layer — and a layer keeps it even if the file is deleted
later. After any change to that file:

```bash
docker run --rm --entrypoint sh anyrouter-checkin-panel \
  -c "find /app/data /app/.browser_profiles -type f | wc -l"
```

It must print `0`. Anything else means the image carries account data: rebuild it, and do not
push the old one anywhere.

### Bind mounts need a chown

The container runs as uid 10001, not root. Named volumes (the default here) inherit that
ownership automatically; if you swap one for a host directory, chown it or the panel cannot
write its database:

```bash
sudo chown -R 10001:10001 ./data
```

## What none of them do

- No account is checked in without the panel running. There is no external scheduler and no
  server-side component (ADR-0008) — that is why the run mode matters at all.
- Nothing is encrypted at rest. The trust boundary is the host, in all three ways.
- No telemetry. The only outbound request the panel makes on its own is the promo manifest, it
  carries nothing about you, and `PANEL_PROMO=0` stops it entirely (`docs/promo-cards.md`).
