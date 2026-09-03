# Check-in Panel

[![Download for Windows](https://img.shields.io/badge/Download-Windows_Desktop-2563eb?style=flat-square&logo=windows&logoColor=white)](https://github.com/BingLi37/checkin-panel/releases)
[![License MIT](https://img.shields.io/badge/License-MIT-16a34a?style=flat-square)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Three ways to run](https://img.shields.io/badge/Run-Desktop_/_Docker_/_Console-64748b?style=flat-square&logo=docker&logoColor=white)](#three-ways-to-run)
[![@BinbingLi](https://img.shields.io/badge/@BinbingLi-000000?style=flat-square&logo=x&logoColor=white)](https://x.com/BinbingLi)
[![agentrouter.org referral](https://img.shields.io/badge/agentrouter.org-Referral-f59e0b?style=flat-square)](https://agentrouter.org/register?aff=fRlT)

[简体中文](README.md) · **English**

> The last badge is the author's **referral link** for `agentrouter.org` — clicking it is how
> the author gets paid for this panel, the same arrangement as the promo card inside the panel
> ([`docs/promo-cards.md`](docs/promo-cards.md)). Register from the site's own front page
> instead if you would rather not; the panel works the same either way.

A self-hosted panel that collects the daily bonus from New API style relay sites. Add your
accounts, watch the balances, let it claim every day — on one machine, depending on no
external service.

It checks in over HTTP first and starts a browser only when a site genuinely blocks the
protocol (an expired OAuth session, Turnstile, a WAF). So a day's check-in is a handful of
HTTP requests for most accounts: fast, and cheap enough to ignore.

Measured against anyrouter.top, agentrouter.org, seekai.cc and sotamodel.net, each awkward in
its own way: one hides the check-in route under its own name, one uses a JWT with a rotating
cookie, one puts the whole API behind a WAF. Every odd-looking branch in the code was written
for one of those, and the comment explaining it sits on the line beside it.

**The UI is in Chinese.** This document names the buttons in Chinese with an English gloss, so
you can find them on screen.

![The panel's account list: one row per account showing the site, the login method, whether today's check-in succeeded, the balance and the last run](docs/images/panel.png)

## Read this first — it decides how you deploy

**The panel has no login.** Anyone who can reach its port can read **every account's password
and session in the clear** from `GET /api/accounts`. That is deliberate (ADR-0003: a
single-user panel running on your own machine).

So:

- On your own computer → it binds `127.0.0.1` by default. Fine, skip this part.
- Reaching it from a phone or another device → put it on a private network (Tailscale,
  WireGuard). Do **not** publish the port.
- It must be on the public internet → authentication, TLS, **and** the panel's own port kept
  off the interface. All three, not two of them. See [`docs/deploying.md`](docs/deploying.md).

`data/panel.db` is the only copy of your accounts. Treat it as a password file: back it up
separately, never commit it, never bake it into an image.

## Three ways to run

| | Who it suits | You get | You accept |
|---|---|---|---|
| **Desktop** | using it on your own machine | double-click to start, closing the window keeps checking in, a tray icon | Windows only |
| **Container** | deploying on a server | survives reboots, runs anywhere Docker does | needs Docker, and needs the exposure handled properly |
| **Console** | deploying on a server / changing the code | works as soon as it is installed | a terminal window has to stay open |

**Never run two of them at once.** They share `data/panel.db` and `.browser_profiles/`, so two
panels mean a locked database and two browsers fighting over one profile. The desktop app
refuses to start a second instance on its own. The container uses its own volumes, so starting
it beside either of the others raises no error at all — it just becomes **two different sets of
accounts checking into the same sites**, which is much harder to notice.

### Desktop

If you would rather not install Python: download the zip from Releases, unpack it, double-click
`签到面板.exe`.

From source, or to build the executable yourself — both require [the UI to have been built
once](#installing-from-source) (`frontend/dist/`), or `desktop/desktop.spec` fails outright
with `Unable to find ...frontend\dist`:

```bat
.venv\Scripts\python.exe -m desktop                :: run it directly
.venv\Scripts\pyinstaller.exe desktop\desktop.spec :: package into dist\签到面板\
```

**Clicking X does not quit.** It asks once (with a "don't ask again" box), then hides to the
notification area. Left-click the tray icon to bring the window back; right-click and pick
**退出** (Quit) to really stop it. That is the whole point of the desktop build: the daily
check-in keeps running without a window occupying your screen.

The `dist\签到面板\` folder can be moved anywhere. `data\`, `.browser_profiles\` and
`.local\cloakbrowser\` are all created next to the executable, so the accounts travel with the
folder. It binds `127.0.0.1` by default.

**Three things happen on a first run. None of them is a bug:**

- Windows raises SmartScreen ("Windows protected your PC") — the executable has no code
  signing certificate. Click **More info** → **Run anyway**.
- Some antivirus products flag PyInstaller output. It needs an exclusion.
- The first **browser login** downloads roughly 500MB of browser engine, and the UI looks
  frozen while it does. Accounts that check in over plain HTTP do not wait for it.

### Container

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

This is not decoration. A site opens its daily bonus at a particular hour (`checkin_after` on
the account) and the panel measures a day in **local** time, so a container left on UTC retries
at the wrong hour, over and over.

`docker-compose.yml` publishes `127.0.0.1:8000:8000`, which is this machine only. Before you
widen that, read the section above and `docs/deploying.md`.

### Console

```bat
start.bat
```

or `.venv\Scripts\python.exe run.py`. Close the window and the scheduler stops, with no
warning of any kind.

Note that `run.py` binds `0.0.0.0` by default (reachable on your LAN) and `start.bat` narrows it
to `127.0.0.1` for you. If you run `run.py` directly, set `PANEL_HOST` yourself.

## Installing from source

Python 3.11+ (measured on 3.14), plus Node 20+ if you intend to change the frontend.

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements\browser.txt
```

The four requirements files are layered. Install what you need:

| File | What it adds |
|---|---|
| `requirements/base.txt` | the panel itself; HTTP check-in only |
| `requirements/browser.txt` | the browser (OAuth re-login, `visit` sites, Turnstile) — **most people want this one** |
| `requirements/desktop.txt` | the window, the tray icon, the packager |
| `requirements/dev.txt` | pytest |

The UI has to be built once — `frontend/dist/` is a build artifact and is not in the repository
(the container path handles this itself; the image builds it):

```bash
cd frontend
npm ci --legacy-peer-deps
npm run build
```

`--legacy-peer-deps` is required: `@heroui/theme` declares a peer dependency on
`tailwindcss>=4` while this project uses `3.4.x`. It is a known, unresolved frontend dependency
conflict and it does not affect the build output.

Without `frontend/dist/` the panel serves the API and no UI.

Then add your first account: **添加账号** (Add account) at the top right, type the site's
address and press **检测** (Probe) — the check-in mechanism and which login methods the site
accepts both come from the probe result, so you do not have to work them out.

![The add-account dialog: name, base URL, check-in mechanism, login method, username and password](docs/images/add-account.png)

## Doing a browser login on a server

This is the one thing that gets stuck after deploying to a server, so it gets its own section.

The panel's daily automatic check-in uses a **headless** browser, which is no problem on a
server. But the **浏览器登录** (Browser login) button in the UI opens a **visible window** by
default — and that window opens on the machine running the panel, i.e. the server, where you
cannot see it. Inside a container it is more absolute: the image has no X display at all.

The good news: **a human is needed once, at first authorisation**, and only for one kind of
account.

### First work out whether you actually need it

| The account | Human needed? |
|---|---|
| a site where you can set a password | **No.** When the session expires the headless browser logs in again with the password |
| OAuth-only, but check-in is a POST to a route (`endpoint`) | **No browser.** Paste the site's access token if it offers one, otherwise its session — see Option A |
| OAuth-only, and the bonus comes from logging in again or loading a page (`login_bonus` / `visit`) | Yes, but only when the IdP session dies — typically once every few weeks to months |
| the site's API sits behind a WAF (anyrouter.top) | Yes, and **every day** — only a real browser passes that JS challenge, and no pasted credential helps |

An access token is a property of **the account**, not of the login method: even on a site that
only lets you in through GitHub or LinuxDO, you can generate a token on your profile page once
you are in. So most accounts in the second row can skip the browser as well — it is worth
looking for that option on the site's own profile page first.

**Hence the first piece of advice: set a password wherever you can.** That is not a workaround,
it removes the problem — a password account never touches a browser for its daily check-in. When
you add an account, the panel points this out if the site allows a password.

For sites where a password is impossible, the options below run easiest-first: Option A covers
most of them, B is for what A cannot reach. anyrouter.top (a WAF plus the `visit` mechanism, so
a browser every single time) starts at B.

### Option A: paste the site's own credential (the most direct)

No window on the server, no X, and the panel opens no browser. What moves is **a credential the
site issued to you**, and the upstream provider (GitHub / LinuxDO / Google) is not involved at
all — how you originally logged in does not matter on this path.

Sites issue two kinds. Use the first if you can.

**Preferred: an access token.** Most New API sites have "访问令牌 / Access Token" on the profile
page (measured here on `api.hcnsec.cn`: `/profile` → 访问令牌 → 生成). It is a plain string you
copy from the site's own UI, it needs **no browser extension**, and it does not expire in a month
the way a cookie does. Pick **Access Token** as the **登录方式** (Login method) and paste it —
the panel sends it as the `Authorization` header, which the site's `/api/user/self` and its
check-in route both accept.

**Fallback: the session cookie.** Only when the site offers no access token.

1. Open **the check-in site itself** in your own browser and log in.
2. Take the **value** of the `session` cookie. It is usually **HttpOnly** (measured on
   `api.hcnsec.cn`: `HttpOnly; SameSite=Strict; Max-Age=2592000`), so page JS cannot read it;
   the reliable route is the browser's own **DevTools → Application → Cookies → the site →
   `session` → copy Value**. A cookie extension can read HttpOnly too, but check that it has
   permission on that site and that you are standing **on the site's own page** — standing on
   the panel (`127.0.0.1:8000`) shows "This page does not have any cookies", because the panel
   issues none of its own.
3. In the panel → **登录方式** (Login method) → **会话 Cookie** (Session cookie) → paste into
   the session field → save.

The session field takes either a single value or the whole JSON blob a cookie extension exports.
Paste the whole thing; the panel picks the one it needs: `session` on an ordinary site,
`new_api_refresh` on a JWT site such as seekai.cc. Which one it picks is decided by **the probe
result**, not by what you pasted. Both kinds of paste are verified the moment you save, so you
find out immediately.

One more case worth knowing in advance: the cookie is good, but the site also wants the account's
own user id (the `new-api-user` header), and without it every endpoint answers 401. The panel
recognises this one and says so — "the credential is fine, but this site also wants the account's
user id" — instead of reporting a dead credential. Put that value in **API User（可选）**
(API User, optional) in the dialog: on the site's page press F12 → Network, pick any API request
the site made itself, and the `New-Api-User` request header is the value (`user.id` in
localStorage is the same number).

![DevTools Network panel: a sign_in request the site made itself, with a New-Api-User row among its request headers](docs/images/api-user.png)

Two limits, which the UI also states:

- **The panel cannot renew a session cookie.** When it expires you paste again (measured: 30 days
  on `api.hcnsec.cn`). An access token does not have this problem, an account with a password
  re-logs in by itself, and an OAuth-only account should use Option B, which lasts weeks to
  months per paste.
- **It only covers `endpoint` sites.** `login_bonus` grants the bonus by logging in again (the
  protocol itself needs the password) and `visit` needs a page genuinely loaded while logged in
  (anyrouter.top). Neither is something a cookie or a token can stand in for.

Worth saying: if the site still offers password login and does not gate it behind Turnstile
(`api.hcnsec.cn` is exactly this), you do not need to paste anything — fill in the username and
password, and the panel runs the whole thing over plain HTTP with a credential that never
expires. Try that first.

### Option B: an OAuth-only identity — inject the IdP session

The site only offers GitHub / LinuxDO login and a password is simply not possible (ADR-0009),
and the site session from Option A has expired. Now you move **the upstream layer**, after which
the panel trades it for a fresh site session every day on its own. The point is to keep the two
layers apart:

| Which layer | Lives where | Lasts | Who fetches it |
|---|---|---|---|
| site session | `accounts.session` in the database | short, swapped daily | the panel (a headless OAuth round trip) |
| **IdP session** (linux.do / github.com) | the browser profile directory | weeks to months | **a human, once** |

1. Log in to LinuxDO or GitHub in your own browser.
2. Export with a cookie extension, **standing on the linux.do / github.com page**.

   ![The Cookie-Editor extension: the export button at the bottom right, format JSON](docs/images/cookie-editor.png)

3. Click **注入会话** (Inject session) on that account's row, paste the whole blob, save.

   ![The inject-session dialog: paste the exported JSON into the text box, with a "verify immediately after injecting" switch below it](docs/images/inject-session.png)

"注入后立刻验证一次" (verify immediately after injecting) is on by default: the panel runs one
headless authorisation there and then and tells you whether it worked, rather than letting you
find out tomorrow when the scheduled check-in fails. The three outcomes are "verified", "login
failed + reason" and "not verified" — only the first is evidence.

Three things to be clear about:

- **What you pasted is your entire forum or GitHub account**, not a credential for the check-in
  site. It is written into that account's browser profile and the panel does not copy it into the
  database — but the panel has no login of its own (ADR-0003), so anyone who can reach the panel
  can use that identity. Every line about `PANEL_HOST` and the published port matters *more*
  after this step, not less.

- When the session expires, export it again. The frequency is the same as Options C and D: weeks
  to months.
- The **first** headless round trip after an injection is the one link on this path that has not
  been measured: whether an injected session and one the browser logged in itself look equivalent
  to Cloudflare is not something this repository has evidence for. So leave that verification
  switch on, and if it does not work, Options C and D are still below.
- **Deleting the account offers to delete the profile too, and the default is to delete it.** The
  IdP session from the table above lives in that directory, so keeping it leaves a working forum
  or GitHub login on the disk while the account it belonged to is gone from the panel. If you do
  keep it, it turns up later under **清理 profile** (Clean up profiles) beside the page title —
  renamed accounts leave one there as well, because a profile directory is named after the
  account.

  ![The profile cleanup dialog: profiles no account claims, with the space each takes](docs/images/profile-cleanup.png)

### Option C: a temporary VNC in the container, and click the window yourself

The image **already has `Xvfb`** (`playwright install-deps` brings it along). What is missing is
only a bridge you can see it through.

Add an on-demand service to `docker-compose.yml`. Note that `profiles:` keeps it from starting by
default:

```yaml
services:
  panel:
    environment:
      DISPLAY: ":99"          # put the panel's browser on the virtual display

  vnc:
    profiles: ["vnc"]         # off by default, started only when you need to authorise
    image: anyrouter-checkin-panel
    container_name: checkin-vnc
    network_mode: "service:panel"
    volumes:
      - panel-profiles:/app/.browser_profiles
    user: root
    entrypoint: >
      sh -c "apt-get update && apt-get install -y --no-install-recommends x11vnc websockify novnc &&
             Xvfb :99 -screen 0 1280x800x24 &
             sleep 2 &&
             x11vnc -display :99 -forever -localhost -nopw &
             websockify --web=/usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900"
```

To use it:

```bash
docker compose --profile vnc up -d vnc          # only when you need to authorise
ssh -L 6080:127.0.0.1:6080 you@your-server      # tunnel from your own machine
# open http://127.0.0.1:6080/vnc.html, then click 浏览器登录 in the panel
docker compose --profile vnc down               # shut it down once you are done
```

**Two requirements that do not bend:**

1. **The VNC port goes through an SSH tunnel or a private network, never onto the public
   internet.** That is what `x11vnc -localhost` and `websockify 127.0.0.1` above are for — a
   remote desktop with a logged-in IdP in it is worth more than the panel itself.
2. **Shut it down when you are done.** It is not meant to stay up.

One admission: I have not measured a headed Chromium actually starting under Xvfb (it wants the
500MB engine downloaded first). It is what Xvfb is for and it ought to work, but that is
inference rather than measurement, and your first run may need adjusting.

### Option D: SSH X11 forwarding

```bash
ssh -X you@your-server
# in the container: docker exec -e DISPLAY=$DISPLAY -it checkin-panel ...
```

The window opens on your own screen and nothing stays exposed on the server. The cost is an X
server on your machine (something like VcXsrv on Windows), and in the container case passing
`DISPLAY` and the X socket in as well — more fiddly than Option C.

### One road that does not work: copying the profile directory

**Do not plan on "authorise locally, then copy the whole profile directory to the server".** A
Windows profile's `Local State` holds `os_crypt.encrypted_key`, which is DPAPI-encrypted and
bound to the current Windows account, so on Linux the cookies cannot be decrypted.

**And that is exactly why Options A and B do work.** Keep the two apart:

| What you move | Works? | Why |
|---|---|---|
| the whole profile directory | no | the cookies inside are encrypted with a DPAPI key, and the key cannot travel |
| cookie **values** (the exported JSON) | yes | plain name/value pairs, re-encrypted by the receiving browser with its own key |

The `session` column in the database was always movable (it is just a string) — that is Option A.
A `visit` account's site session lives in the profile, but the headless round trip fetches it
fresh every day, so what you move for that kind of account is the IdP session one layer up.

## Environment variables

All optional.

| Variable | Default | What it does |
|---|---|---|
| `PANEL_HOST` | `run.py`: `0.0.0.0` / desktop: `127.0.0.1` | bind address. **This is the trust boundary** |
| `PANEL_PORT` | `8000` | port |
| `PANEL_SCHEDULER` | on | `0` turns off the daily automatic check-in |
| `PANEL_PROMO` | on | `0` turns off the promo card; nothing is fetched |
| `CHECKIN_PROXY_URL` | `http://127.0.0.1:7897` | browser login only. Inside a container, a proxy on the host is `http://host.docker.internal:7897` |
| `TZ` | system | must be right inside a container, see above |

## Development

```bat
.venv\Scripts\python.exe -m pytest              :: 285 tests
cd frontend && npm run dev                       :: frontend hot reload, :5173 proxies to :8000
```

After forking, install this hook first. It stops credential-shaped strings and database files at
`git commit`:

```bat
.venv\Scripts\python.exe scripts\check_secrets.py --install
.venv\Scripts\python.exe scripts\check_secrets.py --all   :: or scan the whole tree by hand
```

It matches only shapes with a fixed prefix that have no innocent meaning (`ghp_`, `sk-`, `AKIA`,
private key headers), so a hit is a hit. It does not do entropy or `password=` heuristics, because
in this repository those produced nothing but false positives — and a check that cries wolf gets
bypassed with `--no-verify`, which is worse than no check. It never prints the value it matched.

Changing the frontend needs no panel restart; changing `panel/` does.

`panel/` must stay OS-neutral, because it is imported inside the Linux container — every
Windows-only line lives in `desktop/` at the repository root. That is why the 285 tests in
`panel/tests/` have to be runnable in the container; the desktop shell's own tests are not here,
they are kept with the development tree.

The comment density in the code is high on purpose: next to every non-obvious decision is why it
looks that way, not just what it does. A marker like `ADR-0007` in a comment points at a decision
record in the development tree, and those files are not in this repository — read it as "there is
a non-obvious trade-off here, and the reason is the comment beside it". The comments stand on
their own.

## What it does not do

- **No panel, no check-in.** There is no external scheduler and no server-side component
  (ADR-0008), which is also why the choice of run mode matters.
- **No dry run.** Pressing 签到 (Check in) in the UI performs the real thing (ADR-0005).
- **No encryption at rest.** In all three modes the trust boundary is the host itself.
- **No telemetry.** The panel makes exactly one outbound request of its own: the promo card
  manifest, which carries nothing about you, and `PANEL_PROMO=0` turns it off entirely
  ([`docs/promo-cards.md`](docs/promo-cards.md)). The browser engine download is made by
  cloakbrowser itself — see [`THIRD-PARTY.md`](THIRD-PARTY.md).

## Licence

MIT, see [`LICENSE`](LICENSE).

Third-party licences and the obligations that come with them are in
[`THIRD-PARTY.md`](THIRD-PARTY.md). One is worth noting: `pystray`, which draws the tray icon, is
**LGPLv3** and is compiled into the desktop executable. That does not require your code to be
closed or open — this project is open anyway — but distributing the zip means keeping that notice
with it.

There is no GitHub Actions check-in workflow in this repository. The panel schedules itself
(ADR-0008), so a fork needs none of those secrets.

This project is not affiliated with any of the sites it checks into. Their terms of service are
yours to follow.

