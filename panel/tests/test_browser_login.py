"""browser_login's poll loop against a fake browser — no cloakbrowser, no network.

The one piece that cannot be validated live without a human at the IdP, so the
loop's decisions are pinned here instead.
"""
import json
from dataclasses import dataclass, field

import pytest

from panel import browser_login as bl


@dataclass(frozen=True)
class FakeSettings:
	headless: bool = True
	profile: str = 'profile'  # what load_browser_login_settings was handed as the name


STALE = {'user': '{"id": 1}'}  # yesterday's SPA login: /login would bounce to /console


@dataclass
class FakePage:
	url: str = 'about:blank'
	storage: dict = field(default_factory=dict)
	gotos: list = field(default_factory=list)

	async def goto(self, url, **_):
		self.url = url
		self.gotos.append(url)

	async def evaluate(self, expression, *_):
		if 'clear' in expression:
			self.storage.clear()
			return None
		return json.loads(self.storage.get('user') or 'null')


class FakeContext:
	def __init__(self, cookies, pages):
		self._cookies = list(cookies)
		self.pages = list(pages)
		self.closed = False
		self.settings = None  # what browser_login asked to launch
		self.launch_kwargs = {}

	async def cookies(self):
		return list(self._cookies)

	async def clear_cookies(self):
		self._cookies.clear()

	async def add_cookies(self, cookies):
		self._cookies.extend(cookies)

	async def new_page(self):
		page = FakePage(storage=dict(STALE))
		self.pages.append(page)
		return page

	async def close(self):
		self.closed = True


SITE = 'https://site.test'
LIVE = 'session@site.test'  # the value whoami accepts, i.e. a real login
USER_ID = 343832  # the id the SPA stores and sends as `new-api-user`
USER = {'id': USER_ID, 'username': 'alice', 'quota': 500_000}


@pytest.fixture
def browser(monkeypatch):
	"""A fake context whose cookies change when the OAuth button is clicked."""
	context = FakeContext(
		cookies=[
			{'name': 'session', 'value': 'state-only', 'domain': 'site.test'},  # pre-login OAuth state
			{'name': 'sso', 'value': 'idp', 'domain': 'github.com'},  # the IdP login to preserve
		],
		pages=[],
	)
	grants: list[dict] = []  # cookies the click "wins"; empty = the IdP wants a human
	clicks: list[dict] = []
	steps: list[str] = []  # order of the page-level steps, which is where the bugs live
	clock = {'now': 0.0}  # a fake wall clock, so the poll loop's deadline is testable

	async def click(page, texts, **_):
		clicks.append({'url': page.url, 'storage': dict(page.storage), 'after': list(steps)})
		page.storage['user'] = json.dumps(USER)  # a real login writes it back
		await context.add_cookies(grants)
		steps.append('click')
		return True

	async def accept_terms(page, *_, **__):
		steps.append('terms')

	async def whoami(base_url, *, session=None, api_user=None, **_):
		# The header is not optional on forks that validate it against the session.
		return {**USER} if (session, api_user) == (LIVE, USER_ID) else None

	async def sleep(seconds, *_):
		clock['now'] += seconds

	async def noop(*_, **__):
		return None

	def launch(settings, **kw):
		context.settings = settings
		context.launch_kwargs = kw
		return _async(context)

	monkeypatch.setattr(bl, 'load_browser_login_settings', lambda name, provider, **k: FakeSettings(profile=name))
	monkeypatch.setattr(bl, 'launch_login_context', launch)
	monkeypatch.setattr(bl, 'prepare_browser_page', noop)
	monkeypatch.setattr(bl, 'wait_for_waf_ready', noop)
	monkeypatch.setattr(bl, 'dismiss_popups', noop)
	monkeypatch.setattr(bl, '_wait_for_rendered', noop)
	monkeypatch.setattr(bl, '_unblock_login', accept_terms)
	monkeypatch.setattr(bl, '_click_first', click)
	monkeypatch.setattr(bl.newapi, 'whoami', whoami)
	monkeypatch.setattr(bl.asyncio, 'sleep', sleep)  # do not really wait out the poll loop
	monkeypatch.setattr(bl.time, 'monotonic', lambda: clock['now'])
	return context, grants, clicks


async def _async(value):
	return value


async def _login(headless=True):
	found = await bl.browser_login(base_url=SITE, provider='github', account_name='A', headless=headless)
	return found.credential, found.user


async def test_returns_the_session_that_authenticates_not_the_first_one(browser):
	context, grants, _ = browser
	grants += [
		{'name': 'session', 'value': 'decoy', 'domain': 'site.test'},  # a stale one can sit alongside
		{'name': 'session', 'value': LIVE, 'domain': 'site.test'},
	]

	session, user = await _login()

	assert (session, user['id']) == (LIVE, USER_ID), 'the id travels with the session or nothing authenticates'
	assert {'name': 'sso', 'value': 'idp', 'domain': 'github.com'} in await context.cookies(), 'IdP login must survive'
	assert 'state-only' not in [c['value'] for c in await context.cookies()], 'the site session must be dropped first'
	assert context.closed


async def test_a_waf_in_front_of_the_api_does_not_block_the_login(browser, monkeypatch):
	"""agentrouter.org now answers /api/user/self with an Aliyun CAPTCHA page, which no
	cookie gets past — but the SPA next door is logged in and says so (ADR-0010)."""
	context, grants, _ = browser
	grants += [
		{'name': 'session', 'value': 'oauth-state', 'domain': 'site.test'},  # shorter: no user payload
		{'name': 'session', 'value': LIVE, 'domain': 'site.test'},
	]

	async def blocked_by_a_waf(*_, **__):
		return None  # what _self() reports when the answer is an HTML challenge

	monkeypatch.setattr(bl.newapi, 'whoami', blocked_by_a_waf)

	session, user = await _login()

	assert session == LIVE, 'the longest cookie is the logged-in one; the state cookie is shorter'
	assert user['quota'] == 500_000, 'and the balance comes from the SPA, which already had it'


async def test_a_waf_blocked_balance_comes_from_inside_the_page(browser, monkeypatch):
	"""agentrouter's WAF blocks httpx but not the page, and the SPA's stored login response
	carries quota=0 — so the fresh reading has to come from an in-page /api/user/self."""
	context, grants, _ = browser
	grants.append({'name': 'session', 'value': LIVE, 'domain': 'site.test'})

	async def blocked(*_, **__):
		return None  # whoami over HTTP: a challenge page, not JSON

	async def in_page(page, api_user=None):
		return {'id': USER_ID, 'quota': 3_500_000}

	monkeypatch.setattr(bl.newapi, 'whoami', blocked)
	monkeypatch.setattr(bl, '_site_user', in_page)

	_session, user = await _login()

	assert user['quota'] == 3_500_000, 'the balance must be the fresh one, not the stored 0'


async def test_a_stale_spa_login_is_cleared_before_looking_for_the_button(browser):
	"""Cookies alone are not a logout: with localStorage still saying "logged in"
	the SPA sends /login to /console and there is no OAuth button to click."""
	context, grants, clicks = browser
	grants.append({'name': 'session', 'value': LIVE, 'domain': 'site.test'})

	await _login()

	assert clicks[0]['storage'] == {}, 'the stale SPA login must be gone before we look for the button'
	assert context.pages[0].gotos == [f'{SITE}/login', f'{SITE}/login'], 'and /login revisited afterwards'


async def test_the_wait_is_bounded_by_the_clock_not_the_tick_count(browser, monkeypatch):
	"""On a flaky network one whoami blocks for the whole HTTP timeout, so counting
	ticks turned a 5-minute wait into a request that never came back."""
	polls = {'n': 0}

	async def never_authenticates(base_url, **_):
		polls['n'] += 1
		await bl.asyncio.sleep(25)  # what one dead connection costs
		return None

	monkeypatch.setattr(bl.newapi, 'whoami', never_authenticates)

	with pytest.raises(TimeoutError):
		await _login(headless=False)

	assert polls['n'] <= 13, f'{polls["n"]} polls in a 300s window means the clock was ignored'


async def test_a_path_unsafe_name_cannot_reach_outside_its_profile_folder(browser):
	context, grants, _ = browser
	grants.append({'name': 'session', 'value': LIVE, 'domain': 'site.test'})

	await bl.browser_login(base_url=SITE, provider='github', account_name='../a b:c', headless=True)

	assert context.settings.profile == 'a_b_c', 'the account name keys a folder, so it has to be one'


async def test_a_jwt_fork_hands_over_a_refresh_cookie_instead_of_a_session(browser, monkeypatch):
	"""seekai.cc sets no cookie at all until a login succeeds and keeps no user in
	localStorage — `new_api_refresh` appearing is the proof, and it must be handed over
	*unspent*: exchanging it here would invalidate the one the check-in needs."""
	context, grants, _ = browser
	grants.append({'name': bl.newapi.REFRESH_COOKIE, 'value': 'r1', 'domain': 'site.test'})
	spent = []

	async def refresh_access(base_url, refresh):
		spent.append(refresh)
		return 'jwt', f'{refresh}-rotated', {'id': 17928, 'quota': 500_000}

	async def no_spa_user(*_, **__):
		return None  # this fork keeps the user in memory, not in localStorage

	monkeypatch.setattr(bl.newapi, 'refresh_access', refresh_access)
	monkeypatch.setattr(bl, '_spa_user', no_spa_user)

	credential, _user = await _login()

	assert credential == 'r1', 'the cookie comes back exactly as found'
	assert spent == [], 'and unspent — the check-in gets to be the one to trade it in'


async def test_the_login_never_mints_a_token_itself(browser):
	"""Minting lives in `mint_turnstile`, which uses a fresh context and the proxy on
	purpose: Cloudflare refuses to render for a profile that just did an OAuth round trip,
	and this one always has."""
	context, grants, _ = browser
	grants.append({'name': 'session', 'value': LIVE, 'domain': 'site.test'})

	await _login()

	assert context.launch_kwargs == {}, 'the login itself needs no proxy and no widget'


async def test_the_terms_box_is_ticked_before_reaching_for_the_button(browser):
	"""seekai.cc keeps every login button `disabled` until 我已阅读并同意用户协议 is
	checked — clicking first reads as "this site has no OAuth button"."""
	_, grants, clicks = browser
	grants.append({'name': 'session', 'value': LIVE, 'domain': 'site.test'})

	await _login()

	assert clicks[0]['after'] == ['terms'], 'the box must already be ticked when we click'


async def test_bails_out_early_when_the_idp_needs_a_human(browser):
	context, _, _ = browser
	context.pages.append(FakePage('https://github.com/login'))  # IdP session in this profile expired

	with pytest.raises(RuntimeError, match='人工登录'):
		await _login(headless=True)


async def test_a_visible_window_waits_instead_of_bailing_out(browser):
	"""The bail-out exists because headless cannot type a password — with a human
	watching, an IdP login page is exactly what should happen."""
	context, _, _ = browser
	context.pages.append(FakePage('https://github.com/login'))

	with pytest.raises(TimeoutError):
		await _login(headless=False)


async def test_an_idp_page_that_never_moves_is_a_human_too(browser):
	"""Measured on agentrouter-linuxdo: the tab parks on
	`connect.linux.do/oauth2/authorize?...` — Cloudflare answers that URL with a
	`Just a moment...` challenge — and nothing there says `/login`, so the old check
	never fired and the run spent the full 120s to report a TimeoutError naming no
	cause. A stopped page is the signal: consent pages get clicked and navigate."""
	context, _, _ = browser
	context.pages.append(FakePage('https://connect.linux.do/oauth2/authorize?client_id=x&state=y'))

	with pytest.raises(RuntimeError, match='人机验证') as raised:
		await _login(headless=True)

	assert 'oauth2/authorize' in str(raised.value), 'the owner needs to see which page stalled'


STEPS_BEFORE_LOGIN = bl.STUCK_AFTER_TICKS + 5  # outlast the stuck check, so only movement saves it


async def test_an_authorize_page_that_progresses_is_not_a_human(browser, monkeypatch):
	"""The counterpart: consent *is* this loop's job, so an authorize URL alone must not
	bail out. Here the page moves on every tick, which is what a real consent page does,
	and the login lands only after more ticks than the stuck check allows."""
	context, grants, _ = browser
	page = FakePage('https://connect.linux.do/oauth2/authorize?client_id=x')
	context.pages.append(page)
	moves = {'n': 0}
	clicked = bl._click_first

	async def click_then_move(target, texts, **kw):
		result = await clicked(target, texts, **kw)
		if target is page:
			moves['n'] += 1
			page.url = f'https://connect.linux.do/oauth2/authorize?step={moves["n"]}'
			if moves['n'] > STEPS_BEFORE_LOGIN:
				grants.append({'name': 'session', 'value': LIVE, 'domain': 'site.test'})
		return result

	monkeypatch.setattr(bl, '_click_first', click_then_move)

	session, _user = await _login(headless=True)

	assert moves['n'] > bl.STUCK_AFTER_TICKS, 'the test is worthless unless it outlasts the stuck check'
	assert session == LIVE, 'a moving consent page must be allowed to finish'


async def test_a_blank_tab_is_not_an_idp_page(browser):
	"""`about:blank` is the tab the context opens with. Counting it as an IdP page would
	bail out on every run that has not navigated yet."""
	context, _, _ = browser
	context.pages.append(FakePage('about:blank'))

	assert bl._idp_pages(context, 'site.test') == []
	assert bl._why_a_human_is_needed(context, 'site.test', tick=99, still=99) is None


async def test_unknown_provider_is_refused_before_launching_a_browser():
	with pytest.raises(ValueError, match='oidc'):
		await bl.browser_login(base_url=SITE, provider='oidc', account_name='A')
