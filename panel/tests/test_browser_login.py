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


EXPORTED = """[
	{
		"domain": ".linux.do", "expirationDate": 1799999999.5, "hostOnly": false, "httpOnly": true,
		"name": "_t", "path": "/", "sameSite": "lax", "secure": true, "session": false,
		"storeId": null, "value": "idp-token"
	},
	{
		"domain": "linux.do", "hostOnly": true, "httpOnly": false, "name": "_forum_session",
		"path": "/", "sameSite": "no_restriction", "secure": true, "session": true,
		"storeId": null, "value": "forum"
	}
]"""


def test_an_exported_jar_is_translated_into_what_playwright_accepts():
	"""Two vocabularies, and an unknown key is rejected outright rather than ignored — so
	`hostOnly`, `storeId` and the `session` boolean have to be dropped, not passed through."""
	cookies = bl.playwright_cookies(EXPORTED)

	assert [c['name'] for c in cookies] == ['_t', '_forum_session']
	assert cookies[0] == {
		'name': '_t',
		'value': 'idp-token',
		'domain': '.linux.do',
		'path': '/',
		'secure': True,
		'httpOnly': True,
		'expires': 1799999999.5,
		'sameSite': 'Lax',
	}
	assert set(cookies[1]) == {'name', 'value', 'domain', 'path', 'secure', 'httpOnly', 'sameSite'}


def test_a_session_cookie_is_not_given_an_expiry():
	"""It is meant to die with the browser. Inventing an `expires` pins it instead, which is
	a different cookie from the one that was exported."""
	assert 'expires' not in bl.playwright_cookies(EXPORTED)[1]


@pytest.mark.parametrize(
	'exported,expected',
	[
		('no_restriction', 'None'),  # Chrome's word for the header's None, not a missing value
		('lax', 'Lax'),
		('strict', 'Strict'),
		('Lax', 'Lax'),  # extensions differ on case
	],
)
def test_same_site_is_translated_to_the_header_vocabulary(exported, expected):
	jar = json.dumps([{'name': 'a', 'value': 'b', 'domain': 'x.test', 'sameSite': exported}])

	assert bl.playwright_cookies(jar)[0]['sameSite'] == expected


@pytest.mark.parametrize('exported', ['unspecified', '', None])
def test_an_unspecified_same_site_is_left_out_entirely(exported):
	"""Chrome's 'unspecified' means "send no attribute". Guessing one changes when the
	cookie travels, which is the difference between a working IdP session and a silent one."""
	jar = json.dumps([{'name': 'a', 'value': 'b', 'domain': 'x.test', 'sameSite': exported}])

	assert 'sameSite' not in bl.playwright_cookies(jar)[0]


@pytest.mark.parametrize(
	'raw,message',
	[
		('', '没有粘贴'),
		('   ', '没有粘贴'),
		('{"name": "a"', '不是有效的 JSON'),
		('[]', '没有一条可用的 cookie'),
		('"just a string"', '没有一条可用的 cookie'),
		('[{"name": "a", "value": "b"}]', '没有一条可用的 cookie'),  # no domain: unusable
		('[{"name": "", "value": "b", "domain": "x.test"}]', '没有一条可用的 cookie'),
	],
)
def test_a_paste_that_is_not_a_cookie_jar_is_refused_with_a_reason(raw, message):
	with pytest.raises(ValueError, match=message):
		bl.playwright_cookies(raw)


def test_a_single_exported_cookie_is_a_jar_of_one():
	jar = '{"name": "_t", "value": "x", "domain": ".linux.do"}'

	assert bl.playwright_cookies(jar) == [
		{'name': '_t', 'value': 'x', 'domain': '.linux.do', 'path': '/'}
	]


async def test_injection_writes_into_the_profile_the_daily_hop_reads(browser):
	"""The whole point is that tomorrow's headless run finds this session. A different
	profile name here would write it somewhere nothing ever looks."""
	context, grants, _ = browser
	grants.append({'name': 'session', 'value': LIVE, 'domain': 'site.test'})

	result = await bl.inject_idp_cookies(EXPORTED, provider='github', account_name='A b/c', base_url=SITE)

	assert context.settings.profile == 'A_b_c', 'same keying as browser_login, or the profile is orphaned'
	assert (result.injected, result.hosts) == (2, ('linux.do',))
	assert (result.verified, result.credential) == (True, LIVE)
	assert result.api_user == str(USER_ID)


async def test_injection_can_skip_the_proof_but_then_claims_none(browser):
	result = await bl.inject_idp_cookies(EXPORTED, provider='github', account_name='A', base_url=SITE, verify=False)

	assert result.verified is None, 'nobody asked, so nothing is known — not False, which means "it failed"'
	assert result.credential is None


async def test_a_failed_proof_passes_the_reason_through(browser):
	"""browser_login already separates "the IdP wants a human" from "nothing is moving" (a
	Cloudflare challenge), and only that difference says whether re-exporting would help."""
	result = await bl.inject_idp_cookies(EXPORTED, provider='github', account_name='A', base_url=SITE)

	assert result.verified is False
	assert result.reason, 'a failure with no reason is not actionable'
	assert result.injected == 2, 'the cookies did go in; it is the login that did not take'


async def test_cookies_from_the_wrong_tab_are_a_warning_not_a_refusal(browser):
	"""An export may legitimately come from a host this table does not know, so refusing
	would block a paste that works.

	The fixture is not optional: injection writes cookies through a launched context even
	when `verify` is false, and without it this test starts a real Chromium.
	"""
	jar = json.dumps([{'name': '_t', 'value': 'x', 'domain': '.example.test'}])

	result = await bl.inject_idp_cookies(jar, provider='linuxdo', account_name='A', base_url=SITE, verify=False)

	assert result.warning and 'linuxdo' in result.warning and 'example.test' in result.warning


async def test_a_bad_paste_never_launches_a_browser(monkeypatch):
	"""Normalising has to happen first, or a paste that is not a cookie jar leaves a
	half-written profile behind."""
	launched = []
	monkeypatch.setattr(bl, 'launch_login_context', lambda *a, **k: launched.append(a) or _async(None))

	with pytest.raises(ValueError, match='不是有效的 JSON'):
		await bl.inject_idp_cookies('not json', provider='github', account_name='A', base_url=SITE)

	assert launched == []


async def test_injecting_for_an_unknown_provider_is_refused_first():
	with pytest.raises(ValueError, match='oidc'):
		await bl.inject_idp_cookies(EXPORTED, provider='oidc', account_name='A', base_url=SITE)


async def test_a_stall_on_the_sites_own_page_is_reported(browser):
	"""The gap that sent one owner looking for a cause the panel already knew.

	`_idp_pages` keeps the tabs whose URL does *not* contain the site's root, so a Cloudflare
	challenge served by the check-in site itself left the stall detector with an empty list
	and said nothing — the run spent its whole 120s and raised a timeout naming no cause
	(measured on gorouter.app). Both stalls must be named; which one it was decides the
	advice, so they must not be named the same way either."""
	context, _, _ = browser
	context.pages.append(FakePage('https://site.test/login'))

	reason = bl._why_a_human_is_needed(context, 'site.test', tick=99, still=99)

	assert reason is not None, 'a challenge on the site is still a challenge'
	assert '人机验证' in reason
	assert 'site.test/login' in reason, 'the owner needs to see which page stalled'
	assert not reason.startswith('IdP'), 'an IdP stall and a site stall need different advice'


async def test_a_site_stall_says_retry_rather_than_log_in_by_hand(browser):
	"""What to do differs by side. An expired IdP session needs one visible window; a
	challenge on the site's own page needs another go, because the run that hit it leaves the
	`cf_clearance` it earned in the profile. Telling that owner to log in by hand sends them
	after a session that was never the problem."""
	context, _, _ = browser
	context.pages.append(FakePage('https://site.test/login'))

	with pytest.raises(RuntimeError) as raised:
		await _login(headless=True)

	said = str(raised.value)
	assert '再试一次' in said, 'a retry is the fix, and it has to be the advice'
	assert '需要先人工登录一次' not in said, 'the IdP session is not what stalled'


async def test_an_idp_stall_still_asks_for_a_visible_window(browser):
	"""The counterpart, so the new branch cannot swallow the old one: a stalled *IdP* tab
	still gets the advice that fixes it."""
	context, _, _ = browser
	context.pages.append(FakePage('https://connect.linux.do/oauth2/authorize?client_id=x'))

	with pytest.raises(RuntimeError) as raised:
		await _login(headless=True)

	said = str(raised.value)
	assert '浏览器登录' in said, 'an expired IdP session needs one visible window'
	assert 'IdP' in said


async def test_a_login_card_that_paints_before_its_status_arrives_gets_a_second_look(browser, monkeypatch):
	"""Measured on anyrouter.top: the logout clears the cached site status, so the card's first
	paint has no OAuth provider to offer and comes up as the email/password form instead. The
	status lands a beat later and the card does not repaint — so the button is genuinely absent,
	and the run reported the site had no LinuxDO login at all. One reload brings the chooser
	back, because the status is cached again by then."""
	context, grants, clicks = browser
	grants.append({'name': 'session', 'value': LIVE, 'domain': 'site.test'})
	looks: list[str] = []

	real_click = bl._click_first

	async def click(page, texts, **kw):
		looks.append(page.url)
		if len(looks) == 1:
			return False  # the card is on the password form; no OAuth button exists yet
		return await real_click(page, texts, **kw)

	monkeypatch.setattr(bl, '_click_first', click)

	credential, _ = await _login()

	assert credential == LIVE, 'the second look has to actually complete the login'
	assert len(looks) == 2, 'looked again rather than giving up on the first miss'
	assert context.pages[0].gotos == [f'{SITE}/login'] * 3, 'and the extra look followed a reload'


async def test_a_site_that_really_has_no_such_button_still_says_so(browser):
	"""The retry must not turn a genuine absence into a hang or a different error — a site
	without this provider still gets the message naming it, once both looks have missed."""
	context, _, _ = browser

	async def never(page, texts, **_):
		return False

	import panel.browser_login as module

	original = module._click_first
	module._click_first = never
	try:
		with pytest.raises(RuntimeError, match='找不到 github 登录入口'):
			await _login()
	finally:
		module._click_first = original

	assert context.pages[0].gotos == [f'{SITE}/login'] * 3, 'exactly one reload, not a loop'


class CoveredButton:
	"""A login button with something drawn over it, the way anyrouter.top's 公告 modal is.

	The two answers agree because they do on a real page: `elementFromPoint` at the button's
	centre comes back as the covering layer, and Playwright's own click refuses for exactly
	that reason. Modelling only one of them would let a fix pass that cannot click.
	"""

	def __init__(self, *, covered_for: int = 0, visible: bool = True, enabled: bool = True, clear_first: bool = False):
		self.covered_for = covered_for  # dismissals still needed before the pointer gets through
		self.visible = visible
		self.enabled = enabled
		# The live shape: nothing is over the button on the very first look, because the layer
		# that will cover it has not rendered yet.
		self.clear_first = clear_first
		self.clicks = 0
		self.hit_tests = 0
		self.clicks_while_unsettled = 0
		self.click_timeouts: list = []

	@property
	def first(self):
		return self

	async def is_visible(self, **_):
		return self.visible

	async def is_enabled(self, **_):
		return self.enabled

	async def evaluate(self, _expression, *_args):
		self.hit_tests += 1
		if self.clear_first and self.hit_tests == 1:
			return True
		return self.covered_for == 0

	async def click(self, timeout=None, **_):
		self.click_timeouts.append(timeout)
		if self.covered_for:
			raise RuntimeError('failed pointer_events check: element is covered by <P>')
		# A click before the page settled is the one that does not raise and does not work:
		# mechanically fine, and the SPA has no OAuth URL to send it to yet.
		if self.clear_first and self.hit_tests <= 1:
			self.clicks_while_unsettled += 1
		self.clicks += 1


@dataclass
class CoveredPage:
	button: CoveredButton
	url: str = 'https://site.test/login'
	dismissals: int = 0

	def locator(self, _selector):
		return self.button

	def dismiss(self) -> None:
		"""What `dismiss_popups` does to this page: one layer per call."""
		self.dismissals += 1
		if self.button.covered_for:
			self.button.covered_for -= 1


@pytest.fixture
def covered(monkeypatch):
	"""A page whose button is under one layer, with the clock and the waits driven by hand."""
	page = CoveredPage(button=CoveredButton(covered_for=1))
	clock = {'now': 0.0}

	async def dismiss(target):
		target.dismiss()
		return 1

	async def sleep(seconds, *_):
		clock['now'] += seconds

	async def noop(*_, **__):
		return None

	monkeypatch.setattr(bl, 'dismiss_popups', dismiss)
	monkeypatch.setattr(bl, '_accept_terms', noop)
	monkeypatch.setattr(bl, '_wait_for_rendered', noop)
	monkeypatch.setattr(bl.asyncio, 'sleep', sleep)
	monkeypatch.setattr(bl.time, 'monotonic', lambda: clock['now'])
	return page


async def test_a_covered_button_is_not_ready_however_enabled_it_is(covered):
	"""The check that was missing. A button under a modal *is* enabled, so `is_enabled` alone
	returned in 0.0s while anyrouter.top's 公告 had yet to render; the click then spent its
	whole budget failing a hit test nothing here had thought to ask about."""
	assert await bl._receives_clicks(covered, ('Linux',)) is False, 'covered is not ready'
	covered.dismiss()
	assert await bl._receives_clicks(covered, ('Linux',)) is True
	assert covered.button.hit_tests == 2, 'it really asked the page, not just the locator'


async def test_waiting_for_the_button_clears_what_is_over_it(covered):
	"""`_unblock_login` has to dismiss, not only wait: the modal will not leave on its own,
	so a loop that just sleeps burns the full 20s and hands the click a covered button."""
	await bl._unblock_login(covered, ('Linux',))

	assert covered.dismissals >= 1, 'it dismissed rather than waiting it out'
	assert await bl._receives_clicks(covered, ('Linux',)) is True, 'and returned once clickable'


async def test_a_blocker_that_lands_mid_click_is_cleared_and_the_click_retried(covered):
	"""The live shape: the button is clear when the click begins and covered 0.5s later, after
	which Playwright's own retries can never pass the hit test again. So one long wait cannot
	recover and a short one plus a dismissal can — the click has to be tried more than once."""
	await bl._click_first(covered, ('Linux',))  # first pass raises, dismisses, second lands

	assert covered.button.clicks == 1, 'the click landed on a later pass'
	assert covered.dismissals >= 1


async def test_a_button_nothing_can_uncover_gives_up_on_the_clock(covered):
	"""Bounded, because this runs inside the poll loop's own budget. Two selectors spending
	20s each reached the same failure in 41s and left the loop no time to do anything else."""
	covered.button.covered_for = 99  # nothing will shift it

	assert await bl._click_first(covered, ('Linux',)) is False
	assert covered.button.clicks == 0


async def test_one_clear_look_is_not_enough_to_start_clicking(covered):
	"""The bug that survived the first fix. Nothing is over the button on the very first look,
	because the thing that will cover it has not rendered — measured on anyrouter.top, that is
	t=0.0 against a Semi skeleton, with the 公告 modal arriving at t=0.5. A gate satisfied by
	one look clicks in that instant, on a card still waiting for the site status
	`_forget_spa_login` cleared, so the click cannot go anywhere; it *succeeds* mechanically
	and the run dies 30s later reporting a stall on a page it never left. Two consecutive
	looks are what makes "ready" mean the page stopped changing.
	"""
	covered.button.clear_first = True
	covered.button.covered_for = 1

	await bl._unblock_login(covered, ('Linux',))
	assert await bl._click_first(covered, ('Linux',)) is True

	assert covered.button.clicks == 1
	assert covered.button.clicks_while_unsettled == 0, 'it did not click during the first instant'
	assert covered.dismissals >= 1, 'it saw the layer that the first look had missed'


async def test_one_try_never_outlasts_the_whole_budget(covered):
	"""The consent page is given 2s on purpose — it is clicked from inside the poll loop, every
	tick, and must not stall it. Retrying in 5s slices is right for a login button with 20s to
	spend and wrong here: eight selectors would spend 40s of a 2s budget."""
	covered.button.covered_for = 99  # every try raises, so every try records its timeout

	assert await bl._click_first(covered, ('Linux',), timeout_ms=bl.CONSENT_TIMEOUT_MS) is False
	assert covered.button.click_timeouts, 'it did try'
	assert max(covered.button.click_timeouts) <= bl.CONSENT_TIMEOUT_MS


async def test_the_two_reasons_a_click_did_not_land_do_not_share_a_message(covered):
	"""A site without this login and a button under a modal need different words because they
	need different fixes. They shared one message that named only the first, so a page whose
	LinuxDO button was drawn, enabled and 40px away told the owner to go find another login."""
	covered.button.covered_for = 99
	assert '点不到' in await bl._why_no_button(covered, ('Linux',), 'linuxdo')

	covered.button.visible = False
	assert '找不到' in await bl._why_no_button(covered, ('Linux',), 'linuxdo')


def test_the_profile_path_is_the_one_the_launcher_uses(tmp_path, monkeypatch):
	"""Deletion has to land on exactly what launching created. These were four copies of one
	line; if they ever drift apart, `forget_profile` silently deletes nothing."""
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	from panel.vendor.utils.browser import load_browser_login_settings

	launched = load_browser_login_settings(bl.profile_name('a b@c'), 'github').profile_dir

	assert bl.profile_dir('a b@c', 'github') == launched
	assert bl.profile_name('a b@c') == 'a_b_c', 'the sanitising rule is part of the path'


def test_forgetting_a_profile_removes_it_and_reports_whether_there_was_one(tmp_path, monkeypatch):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	target = tmp_path / 'github' / 'gone'
	(target / 'Default').mkdir(parents=True)
	(target / 'Default' / 'Cookies').write_bytes(b'the IdP session lives here')

	assert bl.forget_profile('gone', 'github') is True
	assert not target.exists()
	assert bl.forget_profile('gone', 'github') is False, 'a second call has nothing to do'
	assert bl.forget_profile('never-logged-in', 'github') is False


def test_forgetting_a_profile_cannot_reach_outside_the_root(tmp_path, monkeypatch):
	"""The operation on the other side of this check is `rmtree`. `profile_name` strips every
	separator, so no name can escape today; the check is what keeps that true if the rule is
	ever loosened."""
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path / 'root'))
	(tmp_path / 'root').mkdir()
	outside = tmp_path / 'precious'
	outside.mkdir()
	(outside / 'keep.txt').write_text('not a profile')

	# The separator becomes `_` and the leading `.._` is then stripped outright, so the name
	# does not merely stop escaping — it stops mentioning the parent at all.
	assert bl.profile_name('../precious') == 'precious', 'the traversal is neutralised by naming'
	assert bl.forget_profile('../precious', 'github') is False
	assert outside.exists() and (outside / 'keep.txt').exists()

	# Two levels, not one: the path is `<root>/<provider>/<name>`, so a single `..` only
	# climbs back to the root and stays inside it. Getting this wrong is how a containment
	# test passes while testing nothing.
	monkeypatch.setattr(bl, 'profile_name', lambda name: name)  # loosen the rule
	with pytest.raises(ValueError, match='拒绝删除'):
		bl.forget_profile('../../precious', 'github')
	assert outside.exists() and (outside / 'keep.txt').exists(), 'the check stops what naming no longer does'


def _site_cookies(context):
	return [c for c in context._cookies if 'site.test' in c['domain']]


async def test_a_login_that_never_lands_puts_the_cleared_session_back(browser):
	"""The logout is a bet, and losing it must not cost the account its card.

	`_forget_site` drops the site's cookies so a `login_bonus` fork will credit the day —
	necessary there, and pure loss on a `visit` fork, where that cookie *is* the check-in
	and no daily run ever mints another. Measured on anyrouter.top: one card carried 13
	consecutive days. So a run that clears it and then cannot log in has to hand it back,
	or pressing the button is a regression the owner asked for.
	"""
	context, _, _ = browser  # grants stay empty: the click wins nothing
	context.pages.append(FakePage('https://github.com/login'))  # and the IdP wants a human
	before = _site_cookies(context)
	assert before, 'the fixture has to start with a site cookie for this to mean anything'

	with pytest.raises(RuntimeError):
		await _login(headless=True)

	assert _site_cookies(context) == before, 'the failed run must leave the card it found'
	assert context.closed, 'and the restore has to happen before the close, or it never lands'


async def test_a_login_that_lands_does_not_put_the_old_session_back(browser):
	"""The counterpart, so the rollback cannot resurrect what the login replaced: a won
	card is newer than the dropped one, and adding the old value back over it would hand
	the caller a session the site has already rotated away from."""
	context, grants, _ = browser
	grants.append({'name': 'session', 'value': LIVE, 'domain': 'site.test'})

	credential, _ = await _login()

	assert credential == LIVE
	values = [c['value'] for c in _site_cookies(context)]
	assert 'state-only' not in values, 'the pre-login state cookie must not come back'
	assert values == [LIVE], 'exactly the card the login won, and nothing beside it'
