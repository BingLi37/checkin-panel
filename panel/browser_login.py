"""Browser OAuth login: get a fresh site session through an IdP.

Used for accounts the protocol path cannot log in by itself — OAuth-only
identities on forks that refuse to set a password (see ADR-0009). On a
`login_bonus` site the login *is* the check-in, so this runs once per day for
those accounts; everywhere else it runs once, to mint the first session.

Requires the cloakbrowser helpers from anyrouter-check-in/ on sys.path
(`panel.sandbox.prepare` puts them there, as does the test conftest).
"""
import asyncio
import re
import time
from dataclasses import dataclass, replace
from typing import Optional

from utils.browser import (
	launch_login_context,
	load_browser_login_settings,
	login_with_email_form,
	prepare_browser_page,
	wait_for_waf_ready,
)
from utils.popups import dismiss_popups

from panel import newapi


@dataclass
class BrowserLogin:
	"""What one browser hop won: the credential to store and who it belongs to."""

	credential: str
	user: dict


@dataclass
class BrowserVisit:
	"""What one visit to a receipt-less site saw.

	`before` is read with the site's own check-in held back, so unlike every earlier
	version of this it is a genuine pre-bonus balance; `receipt` is what the check-in
	route answered. Together they can price a bonus on a site whose quota log never
	mentions one (ADR-0012).
	"""

	before: Optional[dict]
	after: Optional[dict]
	session: Optional[str]
	checkin_at: Optional[float]  # ledger: 0.0 = never, None = cannot say
	checkin_path: Optional[str] = None  # the route that answered, if any
	receipt: Optional[dict] = None  # its response body
	held: bool = False  # was the SPA's automatic check-in actually held back?

	@property
	def refused(self) -> bool:
		"""The route answered, and said no. Not the same as never having asked."""
		return isinstance(self.receipt, dict) and not self.receipt.get('success')

	@property
	def message(self) -> str:
		"""Whatever the route said, for a panel the owner has to debug from.
		anyrouter.top answers `{"message": "", "success": true}` — so often nothing."""
		if not isinstance(self.receipt, dict):
			return ''
		message = self.receipt.get('message') or self.receipt.get('error') or ''
		if isinstance(message, dict):  # OpenAI-shaped errors, same as newapi._fail
			message = message.get('message') or ''
		return str(message).strip()

PROVIDER_BUTTONS = {
	'linuxdo': ('LINUX DO', 'LinuxDO', 'LINUX.DO', 'Linux'),
	'github': ('GitHub', 'Github', 'GITHUB'),
}
CONSENT_TEXTS = ('允许', 'Allow', 'Authorize', 'Continue')
CONSENT_BOXES = ('[role=checkbox][aria-checked="false"]', 'input[type=checkbox]:not(:checked)')
EMAIL_LOGIN_TEXTS = ('邮箱或用户名', '邮箱登录', '使用邮箱', '用户名登录', 'Email')
TURNSTILE_PAGES = ('/profile', '/login')  # routes of these SPAs that load turnstile's api.js
SESSION_TIMEOUT_S = 120  # unattended: only redirects and consent clicks happen
HUMAN_TIMEOUT_S = 300  # visible window: someone is typing a password and a 2FA code
POLL_S = 2
HUMAN_AFTER_TICKS = 5  # ~10s of redirects is plenty; after that a login page means a human is needed
# ~30s with no page moving at all. An OAuth hop is a chain of navigations, and consent
# gets clicked every tick, so a stopped IdP tab is a challenge or a form, not progress.
STUCK_AFTER_TICKS = 15
# How an IdP tab announces it wants credentials. `/oauth2/authorize` is deliberately not
# here: that is also what a consent page looks like, and clicking one is this loop's job.
IDP_LOGIN_MARKS = ('/login', '/signin', '/sign_in', '/sessions/new', '/u/login')
BUTTON_TIMEOUT_MS = 20_000  # SPA render + however long Turnstile keeps the button disabled
CONSENT_TIMEOUT_MS = 2_000  # a consent page is already open; do not stall the poll loop
UNSAFE_IN_A_PATH = re.compile(r'[^0-9A-Za-z._-]+')


def _idp_pages(context, root: str) -> list:
	"""The tabs that are at the IdP rather than at the site (and not blank)."""
	return [p for p in context.pages if root not in p.url and p.url not in ('', 'about:blank')]


def _idp_wants_a_human(context, root: str) -> bool:
	"""True while some IdP tab is parked on a login page (github.com/login,
	linux.do/login, ...) — i.e. the IdP session in this profile has expired."""
	return any(mark in p.url for p in _idp_pages(context, root) for mark in IDP_LOGIN_MARKS)


def _why_a_human_is_needed(context, root: str, tick: int, still: int) -> Optional[str]:
	"""Why a headless run cannot finish on its own, or None while it still might.

	Two shapes, because an IdP does not always say what it wants. A login *page* names
	itself, and ~10s of redirects is plenty before believing one. But `connect.linux.do`
	renders whatever it wants at `/oauth2/authorize` — measured: Cloudflare answers that
	URL with a `Just a moment...` challenge — so the tab sits on an authorize URL that
	looks exactly like a consent page we are about to click. Nothing there matches
	`/login`, so the old check never fired and the run burned the full 120s to reach a
	TimeoutError that named no cause.

	So the second shape is simply **nothing moving**: no page changed URL for ~30s while
	a tab sits at the IdP. A consent page does not do that — we click it every tick and it
	navigates. Only a challenge or a form waiting for hands does.
	"""
	if tick >= HUMAN_AFTER_TICKS and _idp_wants_a_human(context, root):
		return 'IdP 显示的是它自己的登录页'
	if still >= STUCK_AFTER_TICKS and _idp_pages(context, root):
		urls = ' | '.join(p.url for p in _idp_pages(context, root))
		return f'IdP 页面卡住不动（多半是 Cloudflare 人机验证）: {urls}'
	return None


def _root(base_url: str) -> str:
	host = base_url.split('://', 1)[-1].split('/')[0]
	return '.'.join(host.split('.')[-2:])


def _selectors(texts) -> tuple[str, ...]:
	return tuple(f'{tag}:has-text("{t}")' for t in texts for tag in ('button', 'a'))


async def _unblock_login(page, texts, timeout_ms: int = BUTTON_TIMEOUT_MS) -> None:
	"""Get one of `texts` into a clickable state, or give up quietly.

	Covers both ways a login page is not ready: it is still empty at
	`domcontentloaded` (4.6s on seekai.cc), and its buttons stay `disabled` until
	「我已阅读并同意用户协议」 is ticked — a box that can render a beat *after* the button
	does, so one pass at ticking it loses the race about half the time. A page with no
	such box has its button enabled and returns on the first look.
	"""
	button = page.locator(', '.join(_selectors(texts))).first
	deadline = time.monotonic() + timeout_ms / 1000
	while time.monotonic() < deadline:
		try:
			if await button.is_enabled(timeout=1000):
				return
		except Exception:  # not drawn yet
			pass
		await _accept_terms(page)
		await asyncio.sleep(0.5)


async def _accept_terms(page) -> None:
	"""Tick every visible unticked box on the page — on a login form that is the terms
	box and nothing else worth leaving alone.

	`check()` rather than `click()`: it waits for the element to stop moving (a freshly
	hydrated SPA is still settling) and then verifies the box really is checked, which a
	click cannot promise.
	"""
	for selector in CONSENT_BOXES:
		boxes = page.locator(selector)
		for index in range(await boxes.count()):
			box = boxes.nth(index)
			try:
				if await box.is_visible():
					await box.check(timeout=2000)
			except Exception:  # a hidden mirror input, or it vanished on the first check
				continue


async def _wait_for_rendered(page, texts, timeout_ms: int = BUTTON_TIMEOUT_MS) -> None:
	"""Wait for one of `texts` to be drawn. An SPA login page is still empty at
	`domcontentloaded` — 4.6s on seekai.cc — and `is_visible()` does not wait, so
	looking once concluded the OAuth button did not exist before it was ever drawn."""
	try:
		await page.locator(', '.join(_selectors(texts))).first.wait_for(state='visible', timeout=timeout_ms)
	except Exception:
		pass


async def _click_first(page, texts, timeout_ms: int = BUTTON_TIMEOUT_MS) -> bool:
	"""Click the first of `texts` that is there, in priority order (buttons before links).

	Playwright's click waits for the element to become enabled on its own, which covers
	a button that is still disabled when we get to it.
	"""
	await _wait_for_rendered(page, texts, timeout_ms)
	for selector in _selectors(texts):
		try:
			button = page.locator(selector).first
			if await button.is_visible():
				await button.click(timeout=timeout_ms)
				return True
		except Exception:
			continue
	return False


async def _spa_user(context, root: str) -> Optional[dict]:
	"""The user object the New API SPA writes to localStorage after a login.

	It carries the id the site wants back as the `new-api-user` header, the username
	and the current quota — everything an authenticated read would have told us, from
	a page that is already authenticated. Some sites now put a WAF CAPTCHA in front of
	`/api/user/self`, which no cookie gets past, so this is not a shortcut but the
	only reading available (ADR-0010).
	"""
	for page in context.pages:
		if root not in page.url:
			continue
		try:
			user = await page.evaluate('() => JSON.parse(localStorage.getItem("user") || "null")')
		except Exception:  # not on the site yet, or a WAF interstitial
			continue
		if isinstance(user, dict) and user.get('id'):
			return user
	return None


def _cookies_named(cookies, name: str, root: str) -> list[str]:
	return [c['value'] for c in cookies if c.get('name') == name and root in (c.get('domain') or '') and c.get('value')]


async def _site_user_anywhere(context, root: str, api_user=None) -> Optional[dict]:
	"""`/api/user/self` read from whichever open page is on the site.

	The page is behind the WAF's JS challenge already, so this returns the *current*
	balance where httpx only gets a challenge (ADR-0010) — and unlike the SPA's stored
	login response it is never quota=0.
	"""
	for page in context.pages:
		if root not in page.url:
			continue
		user = await _site_user(page, api_user)
		if user:
			return user
	return None


async def _logged_in(context, base_url: str, root: str) -> Optional[tuple[str, dict]]:
	"""(the credential worth keeping, the user) once this run has really logged in.

	Three proofs, because forks differ in what they even set. `GET /api/user/self` is
	the strongest and says *which* session cookie is live — a `session` cookie proves
	nothing by itself, New API keeps the pre-login OAuth state in one. A JWT fork
	(seekai.cc) sets no cookie at all until a login succeeds, so its `new_api_refresh`
	appearing after `_forget_site` cleared it is proof in itself — and it must be handed
	over *unspent*, because exchanging it invalidates it and the check-in needs it. When
	a WAF answers the API instead of the site, the SPA's own localStorage stands in: we
	emptied it before clicking the button, so a user in it means *this* login worked
	(ADR-0010).
	"""
	user = await _spa_user(context, root)
	api_user = user.get('id') if user else None
	cookies = await context.cookies()
	sessions = _cookies_named(cookies, 'session', root)
	for value in sessions:
		confirmed = await newapi.whoami(base_url, session=value, api_user=api_user)
		if confirmed:
			return value, confirmed
	for value in _cookies_named(cookies, newapi.REFRESH_COOKIE, root):
		return value, user or {}
	fresh = await _site_user_anywhere(context, root, api_user)  # a WAF'd API still answers in-page
	if fresh and sessions:
		return max(sessions, key=len), fresh
	# ponytail: longest wins. Without the API we cannot ask which cookie is live, and a
	# logged-in New API session carries the user payload, so it is longer than the
	# pre-login state cookie (668 vs 400 chars on agentrouter). Revisit if a fork
	# starts issuing same-length cookies for both.
	return (max(sessions, key=len), user) if sessions and user else None


async def _forget_site(context, root: str) -> None:
	"""Log out of the site while staying logged in at the IdP.

	Sites that grant the daily bonus on login show no OAuth button while a
	session is live, and reusing that session would credit nothing — so drop the
	site's cookies and keep everyone else's. This is the "logout then re-login"
	those sites require, minus the logout button.
	"""
	keep = [c for c in await context.cookies() if root not in (c.get('domain') or '')]
	await context.clear_cookies()
	if keep:
		await context.add_cookies(keep)


async def _forget_spa_login(page, base_url: str) -> None:
	"""Cookies are only half of the logout: the SPA keeps the user in localStorage
	and its router sends /login straight to /console while that is there, so the
	OAuth button we came for would not exist. Clear it and come back."""
	try:
		await page.evaluate('() => { localStorage.clear(); sessionStorage.clear(); }')
	except Exception:  # nothing stored yet — a WAF interstitial has no site origin
		return
	await page.goto(f'{base_url}/login', wait_until='domcontentloaded')


MINT_TURNSTILE_JS = """async (sitekey) => {
	const wait = (ms) => new Promise(r => setTimeout(r, ms));
	// api.js is loaded async by the page, so window.turnstile shows up late — minting the
	// moment the DOM is ready is how tabitoken.com produced no token at all.
	for (let i = 0; i < 40 && typeof window.turnstile !== 'object'; i++) await wait(250);
	if (typeof window.turnstile !== 'object') return null;
	const box = document.createElement('div');
	box.style.cssText = 'position:fixed;bottom:4px;right:4px;width:300px;height:65px;z-index:99999';
	document.body.appendChild(box);
	return await new Promise((resolve) => {
		const giveUp = setTimeout(() => resolve(null), 20000);
		const finish = (t) => { clearTimeout(giveUp); resolve(t || null); };
		try {
			// No turnstile.ready(): it throws when the site loads api.js with async/defer.
			window.turnstile.render(box, {sitekey, callback: finish, 'error-callback': () => finish(null)});
		} catch (e) { finish(null); }
	});
}"""


async def _mint_turnstile(page, base_url: str, sitekey: str) -> Optional[str]:
	"""Get a Turnstile token out of the site's own widget, or None if Cloudflare refuses.

	Some forks reject their check-in route with `Turnstile token 为空` and never render the
	widget themselves in this browser — but the API is loaded (the page ships
	`turnstile/v0/api.js?render=explicit`), so rendering it ourselves can mint a token the
	route accepts: measured 730 chars, and `POST /api/user/checkin?turnstile=…` answered
	`签到成功`. Cloudflare does not always agree to render, and only some routes of the SPA
	load the API at all, so try the pages most likely to have it and take the first token.
	"""
	for path in ('', *TURNSTILE_PAGES):
		if path:
			try:
				await page.goto(f'{base_url}{path}', wait_until='domcontentloaded')
			except Exception:
				continue
		try:
			token = await page.evaluate(MINT_TURNSTILE_JS, sitekey)
		except Exception:
			token = None
		if token:
			return token
	return None


async def mint_turnstile(
	*, base_url: str, provider: str, account_name: str, sitekey: str, headless: bool = True
) -> Optional[str]:
	"""Open a browser only to mint a Turnstile token — no login, no logout, no OAuth.

	A check-in that fails for want of a token usually has a perfectly good credential;
	logging in again to get one is both slower and more fragile (the IdP may want a human
	on a new exit IP). The widget does not care whether anyone is signed in, so this
	visits `/login` and renders it there.
	"""
	base_url = base_url.rstrip('/')
	profile_name = UNSAFE_IN_A_PATH.sub('_', account_name).strip('._') or 'account'
	# Ephemeral on purpose: there is nothing to remember, and a persistent profile
	# accumulates Cloudflare challenge state that makes the widget refuse to render —
	# measured, a fresh context mints where the account's own profile does not.
	settings = replace(
		load_browser_login_settings(profile_name, provider, persist_profile=False), headless=headless
	)
	context = await launch_login_context(settings, use_proxy=True)  # the challenge needs the detour
	try:
		page = await context.new_page()
		await prepare_browser_page(page)
		await page.goto(f'{base_url}/login', wait_until='domcontentloaded')
		# /login bounces to /sign-in on these forks, and a navigation part-way through the
		# render tears down the widget's JS context — so let the page settle first.
		try:
			await page.wait_for_load_state('networkidle', timeout=15_000)
		except Exception:
			await asyncio.sleep(2)
		return await _mint_turnstile(page, base_url, sitekey)
	finally:
		await context.close()


SITE_JSON_JS = """async ([path, apiUser]) => {
	try {
		const headers = {Accept: 'application/json'};
		if (apiUser) headers['new-api-user'] = String(apiUser);
		const r = await fetch(path, {headers});
		return JSON.parse(await r.text());
	} catch (e) { return null; }
}"""

SITE_POST_JS = """async ([path, apiUser]) => {
	try {
		const headers = {Accept: 'application/json'};
		if (apiUser) headers['new-api-user'] = String(apiUser);
		const r = await fetch(path, {method: 'POST', headers});
		const text = await r.text();
		try { return JSON.parse(text); } catch (e) { return {success: false, message: text.slice(0, 200)}; }
	} catch (e) { return null; }
}"""

# The route a `visit` site's own SPA posts on mount. anyrouter.top's bundle holds
# `async function uD(){const e=await be.post("/api/user/sign_in")...}`, called from the
# router's `useEffect(() => { id > 0 && uD() }, [id])` — so *the page load is the POST*.
VISIT_CHECKIN_PATHS = ('/api/user/sign_in', '/api/user/checkin', '/api/user/check_in')


async def _site_checkin_at(page, api_user=None) -> Optional[float]:
	"""When the site's own quota log last recorded a check-in, read from inside the page.

	Same receipt `newapi.last_checkin_at` fetches over HTTP, for the sites where only a
	browser can reach the API at all. None means "cannot say", never "no".
	"""
	try:
		body = await page.evaluate(SITE_JSON_JS, ['/api/log/self?p=0&page_size=20', api_user])
	except Exception:
		return None
	if not isinstance(body, dict) or not body.get('success'):
		return None
	data = body.get('data')
	items = data.get('items') if isinstance(data, dict) else data
	if not isinstance(items, list):
		return None
	stamps = [
		item.get('created_at')
		for item in items
		if isinstance(item, dict) and newapi.CHECKIN_LOG.search(str(item.get('content') or ''))
	]
	# An empty log is not proof of anything; an entry-carrying log without a check-in is.
	return max((s for s in stamps if isinstance(s, (int, float))), default=0.0) if items else None


async def _hold_check_in(page) -> bool:
	"""Abort the SPA's automatic check-in POST for as long as this route is installed.

	Without this the site collects the bonus during the first authenticated page load and
	the `before` reading taken afterwards already contains it — so the balance never
	appears to move and the run cannot tell 签到成功 from 今日已签到. Aborting costs nothing:
	the bonus is still there to collect, and `_site_check_in` posts it a moment later.
	"""

	async def abort(route):
		try:
			await route.abort()
		except Exception:  # the page navigated out from under it
			pass

	try:
		for path in VISIT_CHECKIN_PATHS:
			await page.route(f'**{path}', abort)
		return True
	except Exception:  # no interception available: fall back to the old, blind behaviour
		return False


async def _release_check_in(page) -> None:
	for path in VISIT_CHECKIN_PATHS:
		try:
			await page.unroute(f'**{path}')
		except Exception:
			continue


async def _site_check_in(page, api_user=None) -> tuple[Optional[str], Optional[dict]]:
	"""POST the site's own check-in route from inside the page. (path, response body).

	This is not an extra action: it is the exact request the SPA fires on mount, and the
	route is idempotent — a second call on a collected day answers without granting
	anything. Doing it explicitly is what makes the bonus *measurable*, because the
	balance either side of this one call is a real before/after (ADR-0012).
	"""
	for path in VISIT_CHECKIN_PATHS:
		body = await page.evaluate(SITE_POST_JS, [path, api_user])
		if isinstance(body, dict) and 'success' in body:
			return path, body
	return None, None


async def _site_user(page, api_user=None) -> Optional[dict]:
	"""`GET /api/user/self` from inside the page.

	The browser has already run the WAF's JS challenge, so this reaches the API that
	httpx only ever gets a challenge page from — and unlike the SPA's stored login
	response, it carries the *current* balance instead of quota=0 (ADR-0012). The
	`new-api-user` header is as mandatory here as anywhere else on these forks.
	"""
	try:
		body = await page.evaluate(SITE_JSON_JS, ['/api/user/self', api_user])
	except Exception:
		return None
	data = body.get('data') if isinstance(body, dict) else None
	return data if isinstance(data, dict) and data.get('id') else None


async def browser_visit(
	*,
	base_url: str,
	account_name: str,
	provider: str = 'password',
	username: Optional[str] = None,
	password: Optional[str] = None,
	headless: bool = True,
) -> BrowserVisit:
	"""Load the site in a browser that is (or gets) logged in, and collect the bonus.

	Such a site grants the day's bonus from its *own SPA*: anyrouter.top's bundle posts
	`/api/user/sign_in` out of the router's mount effect, so merely loading an
	authenticated page collects it. That is why this used to be unmeasurable — the first
	`/console` load spent the bonus, and the `before` reading taken afterwards already
	contained it, so before == after on every run forever (ADR-0012).

	So the automatic POST is **blocked** for the first load, the true `before` is read,
	and only then is the route posted deliberately. The balance either side of that one
	call is a real before/after, and its answer is a receipt on a site whose quota log
	never records a check-in.

	And when a WAF answers every API path with a JS challenge, a browser is the only
	client that can talk to the site at all: it runs the challenge and moves on.
	"""
	base_url = base_url.rstrip('/')
	root = _root(base_url)
	profile_name = UNSAFE_IN_A_PATH.sub('_', account_name).strip('._') or 'account'
	settings = replace(load_browser_login_settings(profile_name, provider), headless=headless)
	context = await launch_login_context(settings)
	try:
		page = await context.new_page()
		await prepare_browser_page(page)
		# Hold the SPA's own check-in until the balance has been read, or the bonus lands
		# before anything can measure it — which is the whole bug this exists to prevent.
		held = await _hold_check_in(page)
		await page.goto(f'{base_url}/console', wait_until='domcontentloaded')
		await wait_for_waf_ready(page)
		spa = await _spa_user(context, root)
		before = await _site_user(page, (spa or {}).get('id')) or spa
		if not before and username and password:
			await page.goto(f'{base_url}/login', wait_until='domcontentloaded')
			await wait_for_waf_ready(page)
			# anyrouter.top opens the day with an announcement modal over the form, so the
			# username field is there but unreachable — "Cannot open email login form".
			await dismiss_popups(page)
			# The password form is behind 「使用 邮箱或用户名 登录」 — the page starts with only
			# the OAuth buttons, so the username field does not exist until this is clicked.
			await _click_first(page, EMAIL_LOGIN_TEXTS)
			await _accept_terms(page)
			await login_with_email_form(page, username, password, BUTTON_TIMEOUT_MS)
			await asyncio.sleep(3)
			spa = await _spa_user(context, root)
			before = await _site_user(page, (spa or {}).get('id')) or spa
		api_user = (before or spa or {}).get('id')
		# Now collect it on purpose, with the before-balance already in hand.
		await _release_check_in(page)
		path, receipt = await _site_check_in(page, api_user)
		await asyncio.sleep(2)  # the grant is committed before the next read
		after = await _site_user(page, api_user) or await _spa_user(context, root)
		session = next(iter(_cookies_named(await context.cookies(), 'session', root)), None)
		checkin_at = await _site_checkin_at(page, api_user)
		return BrowserVisit(
			before=before,
			after=after,
			session=session,
			checkin_at=checkin_at,
			checkin_path=path,
			receipt=receipt,
			held=held,
		)
	finally:
		await context.close()


async def browser_login(
	*, base_url: str, provider: str, account_name: str, headless: bool = False
) -> BrowserLogin:
	"""Log in through `provider`; return the credential to keep and the user behind it.

	The profile lives at <CHECKIN_BROWSER_PROFILE_DIR>/<provider>/<account_name>, so
	two accounts never share an identity and the IdP login survives for next time.
	"""
	buttons = PROVIDER_BUTTONS.get(provider)
	if not buttons:
		raise ValueError(f'{provider} 不是浏览器登录方式，可选: {tuple(PROVIDER_BUTTONS)}')

	base_url = base_url.rstrip('/')
	root = _root(base_url)
	timeout_s = SESSION_TIMEOUT_S if headless else HUMAN_TIMEOUT_S
	# The profile dir is <base>/<provider>/<name>, so the name has to be a legal folder
	# name — and cannot be all dots, or it would land on the parent. Sharing one name
	# means sharing one profile: the store forbids that per site, and across sites it
	# means "the same IdP identity", which is what you want.
	profile_name = UNSAFE_IN_A_PATH.sub('_', account_name).strip('._') or 'account'
	# the caller decides visibility; the env default (CHECKIN_HEADLESS) is for CI
	settings = replace(load_browser_login_settings(profile_name, provider), headless=headless)
	context = await launch_login_context(settings)
	try:
		await _forget_site(context, root)
		page = await context.new_page()
		await prepare_browser_page(page)
		await page.goto(f'{base_url}/login', wait_until='domcontentloaded')
		await wait_for_waf_ready(page)
		await _forget_spa_login(page, base_url)
		await _unblock_login(page, buttons)  # render, then tick whatever keeps it disabled

		if not await _click_first(page, buttons):
			raise RuntimeError(f'{base_url}/login 上找不到 {provider} 登录入口')

		# Wall-clock, not a tick count: on a flaky network one whoami blocks for the
		# whole HTTP timeout, and 60 ticks × 25s was a request that never came back.
		deadline = time.monotonic() + timeout_s
		tick = 0
		still = 0  # consecutive ticks in which no page changed URL
		seen: list[str] = []
		while time.monotonic() < deadline:
			found = await _logged_in(context, base_url, root)
			if found:
				credential, user = found
				# ponytail: this always re-logs in first, even when the profile is still
				# logged in from yesterday — one OAuth round trip a day. Reuse the live
				# session instead if the launch cost ever matters.
				return BrowserLogin(credential, user)
			urls = [p.url for p in context.pages]
			still = still + 1 if urls == seen else 0
			seen = urls
			# Headless runs cannot type a password or tick a Cloudflare box, so stop as soon
			# as it is clear one is wanted, instead of burning the whole timeout on a page
			# that will never move and then reporting a bare TimeoutError.
			if headless:
				reason = _why_a_human_is_needed(context, root, tick, still)
				if reason:
					raise RuntimeError(
						f'{provider} 需要先人工登录一次（{reason}）：在面板里点「浏览器登录」'
						f'（会打开可见窗口），登录并授权一次后，之后每天都能自动完成。'
					)
			for open_page in context.pages:  # consent may be a popup or the same tab
				if 'oauth' in open_page.url or 'authorize' in open_page.url:
					await dismiss_popups(open_page)
					await _click_first(open_page, CONSENT_TEXTS, timeout_ms=CONSENT_TIMEOUT_MS)
			tick += 1
			await asyncio.sleep(POLL_S)
		raise TimeoutError(f'{timeout_s}s 内没拿到会话；当前页面: {" | ".join(p.url for p in context.pages)}')
	finally:
		await context.close()
