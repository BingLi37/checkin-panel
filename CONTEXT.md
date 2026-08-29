# Check-in Panel

A local management panel (FastAPI + React) for the daily check-in of many accounts on New API / One API forks (`agentrouter.org`, `anyrouter.top`, …). Check-ins run over each site's HTTP API inside the panel's own process (ADR-0007, ADR-0008); a browser is launched only for accounts that cannot log in any other way (ADR-0009).

## Language

### Core entities

**Site**:
One New API / One API instance, identified by its Base URL (`https://agentrouter.org`). There is no list of supported sites and no per-site code — what a site offers is discovered at run time by probing it.
_Avoid_: provider, platform, service

**Account**:
One sign-in identity on one Site, identified by (name, Base URL). Carries whatever credentials that identity has: username+password, an access token, a session cookie, `api_user`.
_Avoid_: user, login, profile, credential (use "credentials" only for the raw secret values)

**Login Method**:
How an Account authenticates: `password`, `access_token`, `session`, `linuxdo`, `github`. The first three are pure HTTP; the last two mean "this identity only exists at that IdP", which is what makes an Account eligible for Browser Login.
_Avoid_: auth type, provider (a Login Method is not a Site)

**Check-in Mechanism**:
How a Site grants the daily bonus. `endpoint` — it registers a check-in route, so POST it. `login_bonus` — it has no route and credits the bonus on any *fresh* login, reporting it as `checked_in` in the login response. Both are discovered per Site by Probe. `visit` is the exception: it cannot be probed and is declared on the Account by the Panel Owner (ADR-0012).
_Avoid_: mode, strategy, type

**Visit**:
The Check-in Mechanism of a Site whose *own SPA* posts the check-in on mount, so merely loading an authenticated page collects the bonus — and which records nothing in its Quota Log. anyrouter.top is the case: its bundle calls `POST /api/user/sign_in` from the router's mount effect. A Visit is not "no route to POST": the route exists, it just cannot be found from outside because a WAF answers every probe with a JS challenge. The panel therefore *holds* the SPA's automatic POST, reads a pre-bonus balance, and posts the route itself — which is the only way the bonus can be priced (ADR-0012).
_Avoid_: page-load check-in, passive check-in

**Receipt**:
Evidence that *this run* collected the bonus, as opposed to finding it already collected. Three sources, in descending authority: a Status Route where the fork has one, else the Site's Quota Log (`GET /api/log/self?type=4`), else the balance movement. `service._reconcile` picks the best available and gives it the last word. A Visit Site issues none of the first two — measured: anyrouter.top records a check-in under no log type at all — so there the receipt is the balance across the deliberate check-in POST.
_Avoid_: proof, confirmation, log entry

**Status Route**:
A GET on the check-in path that reports whether today's bonus landed *without performing anything* — the strongest signal there is, because the Site is naming its own day rather than leaving us to infer one from a balance that moves for many reasons. Two shapes are parsed: flat (`{checked_in_today, reward_credits, quota_awarded_today}`, sotamodel.net) and nested under `stats` with a `records` calendar (seekai.cc). Discovered by Probe, which requires a **401** to believe it — a 200 is the admin `/api/user/:id` route eating the path segment. An all-None `CheckinStatus` means "cannot say", never "no" (ADR-0013).
_Avoid_: check status, status endpoint (it is the same path as the check-in route, not a separate one)

**Awarded**:
What the Site itself said it just granted (`quota_awarded`), as opposed to what the balance moved by. The only way to price a bonus whose amount is per-weekday configuration the panel cannot read, and immune to usage landing mid-run. `Outcome.gain` prefers it and falls back to the delta.
_Avoid_: reward, bonus amount (reserve "bonus" for the thing itself, not its size)

### Operations

**Probe**:
An unauthenticated read of a Site's public surface (`GET /api/status`, plus POSTs to the candidate check-in routes) returning its login methods, its quota→USD divisor and its Check-in Mechanism. Runs before every Check-in, and behind the 检测 button in the account form.
_Avoid_: detect, scan, test

**Check-in**:
One real attempt to collect one Account's daily bonus over HTTP: probe the Site, log in or reuse the session, POST the route or re-login, and read the balance before and after. There is no dry-run mode (ADR-0005) — pressing 签到 performs the real thing.
_Avoid_: test, verify, validate

**Browser Login**:
Logging an Account in through its IdP in a real browser, to win the fresh site session the protocol path cannot get. One persistent profile per identity, so the IdP login survives to the next day. Needs a human once, in a visible window; after that it runs headless. It reports what the site's SPA knows about the Account (id, username, quota, whether today's bonus landed) — on a site whose API sits behind a WAF that is the only reading available (ADR-0010).
_Avoid_: OAuth flow, headless login

**Evidence**:
Why a Check-in Attempt concluded what it did, in one sentence for the Panel Owner: the Site's status route, its Quota Log with the timestamp found there, a balance total that grew, or plainly "读不到签到记录（只能看余额）". Stored on the Account as `last_evidence` and shown under the status chip. It exists because **an unmoved balance is the normal shape of an already-collected day** — so 今日已签到 on its own is indistinguishable from a run that concluded nothing, and the panel used to show exactly that (ADR-0015). A string for humans; nothing parses it.
_Avoid_: proof, receipt (a Receipt is the Site's record; Evidence is our account of what we read)

**Bootstrap**:
Trading a browser-won session for a real username+password (`PUT /api/user/self`), which retires the browser for that Account for good. Offered, never assumed — forks that verify `original_password` refuse it for an OAuth-only identity (ADR-0009).
_Avoid_: register, sign up, provision

**Scheduler**:
The loop inside the running panel that checks in every enabled Account once per local day, retrying every 30 minutes until each one succeeds. Disabled with `PANEL_SCHEDULER=0`. There is no external scheduler (ADR-0008). It ticks **immediately on startup**, not after the first interval — so opening the panel after a day off collects every overdue Account within a minute or two, before the Panel Owner can press anything. A 签到 pressed into that window either collides with the run in progress (「正在签到中」) or correctly reports 今日已签到 on a bonus the scheduler just took.
_Avoid_: cron, job, worker

**Promo Card**:
The one card the panel may show in its bottom-right corner: a Site the Panel Owner does not have yet, behind the author's affiliate link, wearing a sticker that says 全新 or 未注册 — never the word 推广. Its text comes from a static `promos.json` published to a separate public repo and read through a CDN every 5 minutes — there is no server (ADR-0008), so it is a read-only publication, not a remote database. Whether a card applies is decided locally from `accounts.base_url` and `min(accounts.created_at)`, so the outbound request is a bare GET carrying nothing about the Panel Owner; the manifest controls text plus one palette **name** looked up in a local table, and a `cta.url` that is not `https://` is dropped. When several apply, one is drawn at random per load (`priority` weights the draw, it does not rank), so every Site the Owner lacks gets offered. Dismissal, impressions and cooldown live in `promo_state`, so closing a card outlives a cleared browser cache and closing the same card again doubles how long it stays away. Off with `PANEL_PROMO=0`, repointed with `PANEL_PROMO_URL`; full disclosure in `docs/promo-cards.md`.
_Avoid_: ad, banner, telemetry

### Security model

**Panel Owner**:
The single person who runs the panel on their own machine. No authentication layer — the panel starts and is immediately usable. Trust boundary is the host OS: anyone who can reach the panel port or read the SQLite file can see credentials. Which knob *is* that boundary depends on the Run Mode: `PANEL_HOST` for the console and desktop ways, and compose's published address for the container, where `PANEL_HOST` must stay `0.0.0.0` for the panel to be reachable at all.
_Avoid_: admin, user, operator

**Credential Store**:
A local SQLite file (`data/panel.db`) containing account credentials in plaintext. Nothing mirrors it — since the GitHub Actions path is gone it is the only copy, so back it up independently.
_Avoid_: vault, secrets store

### Environment constraint

**Run Mode**:
One of the three ways the same panel is started (ADR-0016): the **console** (`start.bat` / `run.py`), the **desktop app** (a window plus a tray icon, where clicking X hides rather than quits), or the **container** (`docker compose up -d`). They differ only in the shell around it — the same `create_app`, the same scheduler, the same check-in — and all three call `panel.sandbox.prepare()` first, which is where the Sandboxed Folder layout and the Relayed Loopback fix are applied. Only one may run at a time on a machine: they share the Credential Store and the browser profiles.
_Avoid_: deployment target, flavour, edition

**Sandboxed Folder**:
The project folder (`D:\web-project\any-AutomaticCheckIn`) is the only writable location for dependencies. Python packages live in `.venv/`, the cloakbrowser binary in `.local/cloakbrowser/`, browser profiles in `.browser_profiles/`, Node modules in `node_modules/`, and the database in `data/`. Nothing is installed to global user paths. Deleting the folder removes everything. Two Run Modes bend this and both are argued in ADR-0016: the container keeps the same layout under `/app` with the three stateful directories as volumes, and the packaged desktop build writes beside its exe while reading bundled assets from a temp directory.
_Avoid_: isolated environment

**Relayed Loopback**:
This machine's `127.0.0.1` traffic goes through a local transparent proxy that rewrites source ports, which makes the stdlib `socket.socketpair()` reject its own connection — and asyncio needs one per event loop, so nothing async starts at all. `panel/loopback.install()` swaps in a socketpair that authenticates with a random token instead of with addresses, and only when the stdlib is actually broken (ADR-0014).
_Avoid_: network error, connection bug (the socket works; only the address check is wrong)

## Open questions

(All resolved during grilling. See ADRs for irreversible decisions.)
