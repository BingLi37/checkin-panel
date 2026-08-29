# AGENTS.md

## Architecture

Two packages plus a vendored upstream clone:

- **`panel/`** — FastAPI backend and all the logic. `newapi.py` is the protocol engine (probe + check-in over HTTP, ADR-0007), `service.py` the one path from a stored account to a check-in, `browser_login.py` the OAuth fallback (ADR-0009), `scheduler.py` the in-process daily loop (ADR-0008), `store.py` the SQLite store, `sandbox.py` the startup sequence all three entry points share, `loopback.py` the startup fix without which no async code runs on this machine at all (ADR-0014). **Must stay OS-neutral** — it is imported inside the Linux container, and its 188 tests pass there.
- **The repo root's `desktop*.py`** — the desktop shell (ADR-0016), where every Windows-only line lives so `panel/` has none: `desktop.py` is the window plus the tray icon, `desktop_dialog.py` the Win32 TaskDialog the X button raises, `desktop_state.py` the GUI-free rule underneath it (tested in `tests/`), `desktop_icon.py` the mark, whose geometry is the SPA favicon's. `desktop.spec` builds it.
- **`frontend/`** — React + HeroUI + Vite SPA. Built to `frontend/dist/`, served as static assets by FastAPI in production. Dev server at `:5173` proxies `/api` → `:8000`. Flat `src/`: `App.tsx` is the list, `AccountForm.tsx` the add/edit modal, `api.ts` every server type, `AccountAvatar.tsx` + `avatar.ts` the per-row avatar and its palette, `icons.tsx` the inline SVGs and login-method labels, `useStuck.ts` the sticky-toolbar hook. Per-layer conventions and the HeroUI traps measured here live in `docs/guidelines/frontend/component-guidelines.md`.
- **`panel/vendor/utils/`** — five files of cloakbrowser helpers copied from upstream `anyrouter-check-in` (BSD-2), the only part of it this panel ever used: `panel/browser_login.py` imports six names from `browser.py` and `popups.py`, which pull in `debug.py` and `proxy.py`. Nothing on the HTTP check-in path touches any of it. They are an ordinary subpackage, so nothing has to be arranged before the import works — there is no `sys.path` step. `panel/vendor/README.md` records the provenance and the one edit made (three internal imports made relative); keep diffs against upstream readable rather than restyling to this project's conventions. `panel/vendor/LICENSE` must ship in both binaries, see THIRD-PARTY.md.

## Commands

### Run the panel — three ways (ADR-0016)

All three share `panel/sandbox.py:prepare()`, whose **order is load-bearing** (loopback fix before any event loop). A new entry point must call it.

```bat
:: 1. the console way
start.bat
.venv\Scripts\python.exe run.py

:: 2. the desktop way — a window plus a tray icon
.venv\Scripts\python.exe desktop.py
.venv\Scripts\pyinstaller.exe desktop.spec    :: -> dist\签到面板\签到面板.exe
```

```bash
# 3. the container way
docker compose build && docker compose up -d && docker compose logs -f
```

Serves `http://127.0.0.1:8000`; if `frontend/dist/` exists it serves the built SPA, otherwise API only. `run.py` binds `0.0.0.0` by default — see `PANEL_HOST` under Env vars, it is the trust boundary.

**Never run two at once.** They share `data/panel.db` and `.browser_profiles/`: two panels mean a locked database and two browsers on one profile. `desktop.py` refuses on its own (a named mutex catches a second desktop instance, a port probe catches `start.bat`), but nothing stops the container from being started beside either — its own volumes are separate, so it would be a *second* set of accounts checking into the same sites.

### Frontend dev / build

```bash
cd frontend
npm run dev      # vite dev server on :5173, proxies /api -> :8000
npm run build    # builds to frontend/dist/
npx tsc --noEmit # type-check only
```

A rebuild does **not** need a panel restart: `/assets` is a `StaticFiles` mount and `/` returns
`dist/index.html`, both read from disk per request. A change to `panel/` does need one.

`run.py` mounts **only** `/assets` — nothing serves `/fonts`, `/favicon.ico`, or anything else
from `frontend/public/`, so a file put there 404s in production. Static assets therefore live in
`frontend/src/assets/` and are referenced from CSS/TS so Vite bundles them into `dist/assets/`
(that is where the eight self-hosted woff2 fonts are); the favicon is an inline SVG data URI in
`index.html`.

### Tests

```bash
.venv\Scripts\python.exe -m pytest              # everything (201 tests)
.venv\Scripts\python.exe -m pytest panel/tests/test_newapi.py -v   # single file
.venv\Scripts\python.exe -m pytest -k test_probe -v                # single test
```

Root `pytest.ini` sets `testpaths = panel/tests tests`; `asyncio_mode = auto`, so async tests need no decorator. The split is not cosmetic: `panel/tests` (188) must be runnable in the Linux container, `tests/` (13) covers the repo-root desktop shell whose modules are deliberately not in the image — so **a test for a root module does not go under `panel/tests/`**, which is what stopped the container collecting the suite at all.

No test hits the network or launches a browser: `test_newapi.py` swaps in an `httpx.MockTransport` fake site, `test_browser_login.py` a fake browser context, `test_service.py` stubs `newapi.check_in`/`probe` and the browser hop.

The window, the tray and the packaged exe are not in the suite — they need a real desktop. They are driven instead by `.scratch/drive_frozen.py` (15 checks against `dist/`, using the messages Windows itself posts), which is what to re-run after touching `desktop*.py` or the spec.

### Lint / format

No linter is configured. Follow existing style: tabs, single quotes, Python 3.11+ (no `from __future__`).

`panel/vendor/utils/` is the exception — it is upstream's code and upstream's style, `from __future__` and all. Leave it alone so a diff against upstream stays readable.

## Environment

### Sandboxed folder (ADR-0006)

All dependencies live inside the project folder — never global paths:

- Python: `.venv/` (created via `python -m venv`, NOT `uv`)
- CloakBrowser binary: `.local/cloakbrowser/` (found by `panel.sandbox.prepare`, which sets `CLOAKBROWSER_BINARY_PATH` only when a binary is really there — a path to a missing file makes `ensure_binary()` raise instead of downloading). `sandbox.ensure_chromium()` downloads it; run that off the startup path, it is ~700MB.
- Browser profiles: `.browser_profiles/<provider>/<account_name>/` (sets `CHECKIN_BROWSER_PROFILE_DIR`)
- SQLite: `data/panel.db`
- Frontend: `frontend/node_modules/`, `frontend/dist/`

Always use `.venv\Scripts\python.exe` to run Python. There is no global `uv` or project Python.

Two deviations, both argued in ADR-0016. **The container** keeps the same layout under `/app`, but `data/`, `.browser_profiles/` and `.local/cloakbrowser/` are volumes rather than directories in a layer. **The frozen build** has two roots: `sys._MEIPASS` is a temp dir PyInstaller deletes on exit, so what is *written* resolves against the exe's own folder and only bundled assets come from the unpacked one — `sandbox.roots()` returns both, and resolving a database against `_MEIPASS` would throw the accounts away on every run.

### If nothing async will start (ADR-0014)

`ConnectionError: Unexpected peer connection` out of `socket.socketpair` means this machine's loopback is being relayed by a local transparent proxy, which breaks the stdlib's address check. It kills `uvicorn.run`, a bare `asyncio.run`, and every async test alike — before any panel code runs. `panel.loopback.install()` fixes it, and every entry point gets it by calling `panel.sandbox.prepare()` first (`run.py`, `desktop.py`, and `panel/tests/conftest.py` for the suite); a **new entry point that builds its own event loop has to call it too**, or it dies the same way. Not an issue in the container — that is a Linux kernel with a real `socketpair`, and the patch stays a no-op there.

### Env vars

All optional; `panel.sandbox.prepare()` fills in the last three itself:

- `PANEL_PORT` — listen port (default `8000`)
- `PANEL_HOST` — bind address (default `0.0.0.0`, i.e. LAN-reachable; `127.0.0.1` = local only). **This is the trust boundary**: there is no auth layer and `GET /api/accounts` answers with every stored password and session in the clear (ADR-0003). In the container it must stay `0.0.0.0` — a container reaches its own published port through the bridge — and the boundary moves to compose's `127.0.0.1:8000:8000` instead.
- `PANEL_SCHEDULER` — `0` disables the daily auto check-in loop
- `PANEL_PROMO` — `0` turns the promo card off; nothing is fetched (`docs/promo-cards.md`)
- `PANEL_PROMO_URL` — a manifest URL that replaces both public mirrors
- `CHECKIN_PROXY_URL` — proxy for the browser login (default `http://127.0.0.1:7897`). **In a container `127.0.0.1` is the container**, so a proxy on the host is `http://host.docker.internal:7897` (compose maps that name on Linux too). A Turnstile token does not appear without this proxy at all (see the trap list below).
- `TZ` — not read by any panel code, but the scheduler's day is **local** time: `checkin_after` says when a site opens its bonus and `service.window_start` measures from it, so a container left on UTC checks in at the wrong hour. `docker-compose.yml` sets it; the image installs `tzdata` for it.
- `CHECKIN_BROWSER_PROFILE_DIR`, `CLOAKBROWSER_BINARY_PATH` — see above

No GitHub token, no secrets: the GitHub Actions path is gone (ADR-0008). `.github/workflows/checkin.yml` is dead code — it ran upstream's `checkin.py`, which is no longer in this repository at all. It is **excluded from the published repo**: its `cron: '0 */6 * * *'` would be live there, firing every six hours against 20 secrets that do not exist.

### Deploying the container without handing out the accounts

`docker-compose.yml` publishes `127.0.0.1:8000:8000` and that is deliberate. Widening it to `"8000:8000"` listens on every interface, and since there is no login and `/api/accounts` returns credentials in the clear (ADR-0003), that hands every account to anyone who can reach the host. Put an authenticating reverse proxy in front before that line changes.

Two more things the image does on purpose: it runs as **uid 10001**, not root, so a *bind* mount (which keeps the host's ownership, unlike a named volume) has to be chowned to 10001 or the panel cannot write its database; and `docker compose down -v` deletes `panel-data`, which is the only copy of the accounts. Back that volume up independently, and never `docker push` an image built with a leaky `.dockerignore` — the current one is a deny-list precisely so a forgotten line cannot bake `data/panel.db` into a layer, where deleting the file later does not remove it. The check after any change to it:

```bash
docker run --rm --entrypoint sh <image> -c "find /app/data /app/.browser_profiles -type f | wc -l"
```

Must print `0`. `find -type f`, not `ls`: `ls` on two directories prints their names and a blank line, so counting its output reports 3 for two empty directories and looks exactly like a leak.

## Domain context

Read `CONTEXT.md` at the repo root for the full glossary. Key facts:

### One check-in, protocol first (ADR-0007, ADR-0009)

`service._attempt` is the whole decision tree:

1. `newapi.probe(base_url)` — public, unauthenticated: login methods, quota divisor, and whether a check-in route exists.
2. `newapi.check_in(login, site)` — `endpoint` sites get a POST; `login_bonus` sites get a fresh password login, whose response carries `checked_in`.
3. Only if that failed **and** `login_method in ('linuxdo', 'github')`: `_browser_check_in` logs in through the IdP in a real browser, stores the session, and finishes over HTTP. A wrong password never launches a browser.

Traps that cost live debugging, do not re-introduce:

- Probe check-in routes with **POST, unauthenticated**. `GET /api/user/checkin` matches the admin route `/api/user/:id` and answers 200; an authenticated POST performs the check-in.
- A `session` cookie proves nothing — New API stores the pre-login OAuth state in one. Verify with `newapi.whoami` (`GET /api/user/self`).
- Some forks (agentrouter.org) validate the **`new-api-user`** header against the session: without the account's own user id every authenticated route 401s, so a perfectly good session looks dead. The browser path reads the id from the SPA's `localStorage['user'].id`, the HTTP path adopts `data['id']` after login, and it is stored beside the session (`accounts.api_user`).
- Clearing cookies is **not** a logout: the SPA keeps the login in `localStorage`, and its router sends `/login` straight to `/console` while that is there — no OAuth button, so the browser login fails with 找不到…登录入口. `browser_login._forget_spa_login` clears the storage and re-navigates.
- A **WAF can answer 200 with an HTML challenge** on `/api/user/self` while public routes still work (agentrouter.org, ADR-0010). So: never trust a body without checking it is JSON, and never make `whoami` the only proof of a login — `browser_login._logged_in` also accepts the SPA's `localStorage['user']`, and that object supplies `id`/`quota`/`checked_in`.
- A quota of `0` from the SPA's stored login response is a *missing* number, not a zero balance. `store.record_result` keeps the last known `last_quota` rather than blanking it.
- `checked_in` in a login response is a **state** flag ("today is done"), not a receipt — it stays true on every re-login. The receipt is the site's quota log (`GET /api/log/self?type=4`, not WAF'd): `service._reconcile` gives it the last word over whatever the attempt concluded, including rescuing a run whose bonus landed but whose read-back failed.

- A headless OAuth hop can stall on a page that **never says it wants a human**. `connect.linux.do/oauth2/authorize` gets answered by a Cloudflare `Just a moment...` challenge (measured: plain HTTP gets 403 with that title), and nothing in that URL matches `/login`, so the old `_idp_wants_a_human` check never fired and the run spent its full 120s to report a `TimeoutError` naming no cause. `browser_login._why_a_human_is_needed` now also bails on **nothing moving**: no page changed URL for ~30s while a tab sits at the IdP. Do not add `/oauth2/authorize` to `IDP_LOGIN_MARKS` — a consent page looks identical, and clicking it is the poll loop's job; movement is what separates them.
- An SPA login page is **empty at `domcontentloaded`** (4.6s on seekai.cc) and Playwright's `is_visible()` does not wait — looking once decided the OAuth button did not exist before it was drawn. And a fork with a 「我已阅读并同意用户协议」 box keeps every login button `disabled` until it is ticked, which looks identical. `browser_login` waits for the render, ticks the box, then clicks.
- Not every fork uses session cookies: seekai.cc issues a **JWT plus a rotating `new_api_refresh` cookie** and authenticates only with `Authorization: Bearer`. `probe` spots the refresh route (401 vs 404), `check_in` spends the cookie for a token and stores the rotated one — and because it rotates, the panel and a human browser cannot share one credential. Never spend it twice in one run: `_logged_in` hands it over **unspent** for exactly that reason, and `refresh_access` returns `rotated=None` when the server did not issue a new one (returning the spent value would kill the account).
- A fork can gate its check-in route behind **Turnstile** (`Turnstile token 为空`) and still not render the widget itself in cloakbrowser. Two things make the token appear: the browser must go out through **`CHECKIN_PROXY_URL`** (Cloudflare's challenge iframe never renders from the bare exit IP — measured: none without it, a 752-char token with it) and the context must be **ephemeral** (a persistent profile accumulates challenge state that makes it refuse). `service._with_turnstile` borrows a browser for that one job — no login — and the token-carrying POST goes out through the same proxy, or the site validates it against a different IP.
- The daily rotation is a **credential hazard**: `check_in` spends the stored refresh token before anything else can fail, so `_attempt` persists the rotated one immediately. Storing it only on the way out left accounts holding a spent token — 凭据无效 on every later run until someone logged in by hand.
- seekai.cc also exposes `GET /api/user/checkin?month=YYYY-MM` → `stats.checked_in_today`, an authoritative status that needs no token. Useful if the ledger regex ever proves too loose.

### Three mechanisms, one declared (ADR-0012)

`accounts.mechanism` is `auto` (probe decides: POST the route, or re-login) or `visit`. `visit` is the one thing probing cannot see — anyrouter.top's **own SPA** posts `/api/user/sign_in` out of its router's mount effect, so any authenticated page load collects the bonus; it writes **nothing** to the quota log and answers every API route with a WAF challenge. So: no protocol attempt, and no receipt beyond the balance itself. Reading that balance also has to happen inside the page (`browser_login._site_user`).

The trap that produced a day of 假签到 reports: that automatic POST spends the bonus during the *first* `/console` load, so a `before` balance read afterwards already contains it — `before == after` on every run forever, reported as 今日已签到 on no evidence at all. `browser_visit` therefore **holds** the SPA's POST back (`_hold_check_in`, Playwright request interception), reads the true pre-bonus balance, then posts the route itself (`_site_check_in`). Two rules follow, and both are load-bearing:

- Compare **`quota + used_quota`**, never bare `quota`. Spending only moves quota into used_quota, so only a grant raises the total; the bare balance called a $25 bonus "no change" on an account that burned $25 of it in the same window. Upstream `checkin.py` prices its reward the same way.
- Claim 今日已签到 (`checked_in=False`) only when `BrowserVisit.held` is true. If the hold failed, an unmoved balance proves nothing — that is `None`, 已重新登录. The route itself is no help: it answers `{"message": "", "success": true}` whether or not it granted anything.

### The scheduler's day (ADR-0005, ADR-0010, ADR-0011)

`scheduler.due` keeps an account due until it *succeeds* in the current window, so a failure retries every 30 minutes. That makes a wrong failure expensive — hence `_reconcile`: an account whose bonus is in the ledger is done, whatever our balance read said.

A failure is **not** retried every tick: `scheduler.backoff_s` widens the gap 30min → 1h → 2h → 4h on consecutive failures (`accounts.failures`, reset by any success, ignored when a new window opens). A site with nothing to give at 09:00 rarely has something at 09:30, and each retry costs a browser launch.

A window is **not** a calendar day: `accounts.checkin_after` ('HH:MM', empty = midnight) says when the site opens its bonus, and `service.window_start(account)` is the boundary both `due` and `_reconcile` measure against. anyrouter.top opens at 08:30 — without this an account that collected at 09:00 looked due from midnight and burned a browser launch every 30 minutes all night. Nothing discovers the hour; the owner types it in.
- A site can register **two check-in routes and answer 401 on both**, with the generic one switched off (sotamodel.net: `/api/user/checkin` is dead, `/api/user/sota-agent-checkin` is live, `checkin_enabled: false`). `CHECKIN_CANDIDATES` is therefore ordered **most specific first** and first-match wins — do not re-sort it alphabetically or move the generic route back to the top (ADR-0013). `checkin_enabled` is recorded but never used to decide: it is absent on agentrouter and unreadable behind a WAF.
- **An unmoved balance is the normal shape of an already-collected day**, so 今日已签到 on its own is unauditable — which is why `Outcome.evidence` carries the reason in the owner's words and `accounts.last_evidence` persists it (ADR-0015). Two traps live here. `last_checked_in` is **three-valued** (`true` this run took it, `false` the site says today is done, `null` nobody could tell us): a truthiness test in the frontend rendered `null` as the confident 今日已签 chip, i.e. showed *no evidence* as *evidence*. `App.tsx:statusChip` now branches on `=== true` / `=== false` / else, and the `null` case reports 已登录·未确认 with a tooltip saying why — do not collapse those branches again. And `last_quota` survives a failed read on purpose (ADR-0010), so without `last_quota_at` a days-old balance appears beside today's timestamp as if it were fresh — agentrouter-github sat at `$100.13` from 08-18 that way. Evidence is a string for humans; never branch on it.
- Some forks answer **GET on the check-in path** with a status read that performs nothing, and that outranks the quota log in `service._reconcile` — the site naming its own day beats any inference. Flat shape on sotamodel.net, nested under `stats` with a `records` calendar on seekai.cc ($20/day, verified live); both are parsed by `newapi.checkin_status`. Probe requires a **401** to believe it, because a 200 is the admin `/api/user/:id` route again. An all-None `CheckinStatus` is "cannot say" and falls through to the ledger.
- The status route carries a **date, not a timestamp**, so it cannot say *who* collected today's bonus. A run that cannot otherwise confirm itself reports 今日已签到 rather than claiming the credit.
- `Outcome.awarded` is the site's own figure for the grant and `Outcome.gain` prefers it over `delta` — the only way to price a per-weekday reward, and immune to usage landing mid-run. `current_quota` from the same response likewise beats a second `/api/user/self`.
- Unknown routes answer OpenAI-shaped JSON (`{"error": {"message": ...}}`), so error extraction must unwrap a dict.
- New API caps passwords at 20 chars, and `PUT /api/user/self` verifies `original_password` — an OAuth-only account cannot be given one (ADR-0009).

### Check-in is always the real check-in (ADR-0005)

There is no dry-run mode: 签到 in the UI performs the real thing, and the daily loop dedupes per local day (`scheduler.due`) rather than per press. An account stays due until it *succeeds* today, so a failure retries every 30 minutes.

### Promo cards come from a remote manifest

`panel/promo.py` fetches a static `promos.json` from the `promos` branch of the author's separate public repo (`BingLi37/Welfare-Express` — its `main` is reader-facing; `raw.githubusercontent.com` first because it is the fresher mirror, jsDelivr as the fallback for networks that block GitHub — and a measured jsDelivr purge cannot be trusted to have landed) every 5 minutes — `raw`'s own `max-age`, so polling faster cannot see anything newer — and `panel/app.py`'s `/api/promos` picks at most one card from it, **at random among the eligible ones** (`priority` is the draw weight, not a rank: ranking meant the second-best site was never offered until the first was closed or registered). Closing a card cools that one card down — its `cooldown_days`, doubled per dismissal (1 → 2 → 4 → 8, capped), counted per card in `promo_state.dismissals` — while the other sites keep coming up. It is the only module outside `store`/`newapi` that opens the network, and the deviation is argued in its docstring.

Two properties are the point of the design, so do not quietly break them: **every targeting rule is evaluated locally** (`missing_hosts` against `accounts.base_url`, `min_panel_age_h` against `min(accounts.created_at)`), which is why the outbound request carries no query, body or cookie — `test_promo.py::test_the_request_carries_nothing_but_the_url` asserts exactly that; and **the manifest controls text only**, never CSS, markup or a class name — the one exception is `theme`, a palette *name* looked up in `PromoCard.tsx`'s own `MESHES` table and discarded when it is not a key there (an unknown or absent name falls back to a hash of the card id), which is an enum lookup and not a value that reaches CSS — with a `cta.url` that is not `https://` dropped rather than repaired. Every failure (dead mirror, 404, bad JSON, wrong `version`, a raising route) ends in `{"card": null}`. Display state lives in `promo_state`, not `localStorage`, so a dismissal survives a cleared browser cache. The `sticker` a card wears (全新 / 未注册) is likewise derived from local state — `promo_state.first_seen_at` against `promo.NEW_FOR_D` — never declared in the manifest, because how new a site is is a fact about this install. User-facing disclosure and the publish flow: `docs/promo-cards.md`.

### Plaintext credential storage (ADR-0003)

Credentials sit in plaintext in `data/panel.db`, with no auth layer in front of the panel. Since the GitHub mirror is gone that file is the only copy — back it up independently, and never print its values.

## Agent skills

### Issue tracker

Issues live as markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical triage labels are used as-is. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

User-facing docs, kept in the owner's words rather than these: `docs/deploying.md` (the three run modes and the exposure decisions in each) and `docs/promo-cards.md` (what the promo card costs the reader). Both are disclosure documents — if a change alters what leaves the machine or who can reach the panel, they change with it.

## Coding guidelines

Per-layer conventions live under `docs/guidelines/` — read the one for the layer you are about to
write in, because several rules there exist to stop a specific bug that already happened:

- `docs/guidelines/backend/` — database, error handling, logging and quality rules for `panel/`.
  `database-guidelines.md` is the one `panel/app.py`, `panel/store.py` and `panel/promo.py` point
  at from their comments; it owns the add-a-column contract.
- `docs/guidelines/frontend/` — components, hooks, state and type safety for `frontend/`.
  `component-guidelines.md` records the HeroUI traps measured in this project.

These were authored with Trellis, whose task-tracking files are not part of this repository —
only the guidelines are, since they are the half that describes the code rather than the process
that produced it.
