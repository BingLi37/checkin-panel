"""panel.newapi against a fake New API instance — no network, no browser."""
from datetime import datetime

import httpx
import pytest

from panel import newapi

BONUS = 125_000.0  # quota units -> $0.25 at the default divisor


def ok(data=None, **extra):
	return httpx.Response(200, json={'success': True, 'data': data or {}, **extra})


class FakeSite:
	"""Just enough New API: status, login, self, PUT self, optional check-in route."""

	def __init__(self, *, checkin_path=None, oauth=('linuxdo', 'github'), refresh=None):
		self.checkin_path = checkin_path
		self.oauth = oauth
		self.quota = 500_000.0
		self.checked_in = False
		self.username = 'alice'
		self.password = 'pw'
		self.new_password = None
		self.needs_api_user = False  # forks that 401 a session without `new-api-user`
		self.refresh = refresh  # a JWT fork: this cookie buys a Bearer token, and rotates
		self.access_token = 'jwt-access'
		self.calls: list[str] = []
		# A fork that registered the generic route and turned it *off* (sotamodel.net):
		# it must never be the one we POST, even though it answers.
		self.dead_checkin_path = None
		# A fork that reports its own result and offers a GET status read on the same path.
		self.reports_result = False
		self.status_path = None
		self.nests_status = False  # seekai.cc puts it under `stats` with a month's calendar
		self.checkin_enabled = None  # what /api/status advertises, when it says anything

	def _authed(self, request) -> bool:
		if self.refresh:  # JWT forks accept nothing but the Bearer token
			return request.headers.get('authorization', '').endswith(self.access_token)
		if self.needs_api_user and not request.headers.get('new-api-user'):
			return False
		return 'session=' in request.headers.get('cookie', '') or bool(request.headers.get('authorization'))

	def handler(self, request: httpx.Request) -> httpx.Response:
		path, method = request.url.path, request.method
		self.calls.append(f'{method} {path}')

		if path == '/api/status':
			status = {'quota_per_unit': 500000, **{f'{p}_oauth': True for p in self.oauth}}
			if self.checkin_enabled is not None:
				status['checkin_enabled'] = self.checkin_enabled
			return ok(status)

		if path == newapi.REFRESH_PATH and method == 'POST':
			if not self.refresh:
				return httpx.Response(404, json={'error': {'message': 'Invalid URL'}})
			if f'{newapi.REFRESH_COOKIE}={self.refresh}' not in request.headers.get('cookie', ''):
				return httpx.Response(401, json={'success': False, 'message': 'Unauthorized'})
			self.refresh += '-rotated'
			return httpx.Response(
				200,
				json={'success': True, 'data': {'access_token': self.access_token, 'user': {'id': 17928}}},
				headers={'set-cookie': f'{newapi.REFRESH_COOKIE}={self.refresh}; Path=/'},
			)

		if path == '/api/user/login' and method == 'POST':
			body = request.read().decode()
			if f'"{self.password}"' not in body:
				return httpx.Response(200, json={'success': False, 'message': '用户名或密码错误'})
			granted = not self.checked_in and self.checkin_path is None  # login_bonus sites only
			if granted:
				self.checked_in, self.quota = True, self.quota + BONUS
			return httpx.Response(
				200,
				json={'success': True, 'data': {'id': 7, 'username': self.username, 'checked_in': granted}},
				headers={'set-cookie': 'session=fresh; Path=/'},
			)

		if path == '/api/user/self':
			if not self._authed(request):
				return httpx.Response(401, json={'success': False, 'message': 'unauthorized'})
			if method == 'PUT':
				self.new_password = request.read().decode()
				return ok()
			return ok({'username': self.username, 'quota': self.quota, 'display_name': 'Alice'})

		# Registered but switched off. It answers 401 unauthenticated exactly like the live
		# route, so only the *order* of the candidates keeps us off it.
		if self.dead_checkin_path and path == self.dead_checkin_path:
			if not self._authed(request):
				return httpx.Response(401, json={'success': False, 'message': 'unauthorized'})
			return httpx.Response(200, json={'success': False, 'message': '签到功能未启用'})

		if self.status_path and path == self.status_path and method == 'GET':
			if not self._authed(request):
				return httpx.Response(401, json={'success': False, 'message': 'unauthorized'})
			if self.nests_status:  # seekai.cc shape: a month's calendar under `stats`
				today = datetime.now().strftime('%Y-%m-%d')
				return ok({
					'stats': {
						'checked_in_today': self.checked_in,
						'total_checkins': 1 if self.checked_in else 0,
						'records': (
							[{'checkin_date': today, 'quota_awarded': BONUS}] if self.checked_in else []
						),
					}
				})
			return ok({  # sotamodel.net shape: flat
				'checked_in_today': self.checked_in,
				'quota_awarded_today': BONUS if self.checked_in else 0,
				'reward_credits': 100,
				'total_checkins': 1 if self.checked_in else 0,
			})

		if self.checkin_path and path == self.checkin_path and method == 'POST':
			if not self._authed(request):
				return httpx.Response(401, json={'success': False, 'message': 'unauthorized'})
			if self.checked_in:
				return httpx.Response(200, json={'success': False, 'message': '今日已签到'})
			self.checked_in, self.quota = True, self.quota + BONUS
			if self.reports_result:  # sotamodel.net: the site prices its own bonus
				return ok({'quota_awarded': BONUS, 'current_quota': self.quota, 'reward_credits': 100})
			return ok()

		if path.startswith('/api/user/') and method == 'GET':
			return ok({'id': 7, 'username': self.username})  # admin /api/user/:id eats any segment

		return httpx.Response(  # what New API forks really answer for an unknown route
			404,
			json={'error': {'message': f'Invalid URL ({method} {path})', 'type': 'invalid_request_error'}},
		)


@pytest.fixture
def site(monkeypatch):
	"""A fake site, wired in by replacing newapi._client's transport."""
	fake = FakeSite()

	def client(base_url, *, session=None, access_token=None, api_user=None, proxy=None):
		headers = {'Accept': 'application/json'}
		if access_token:
			headers['Authorization'] = access_token
		if api_user:
			headers['new-api-user'] = str(api_user)
		return httpx.AsyncClient(
			base_url=base_url.rstrip('/'),
			transport=httpx.MockTransport(fake.handler),
			headers=headers,
			cookies={'session': session} if session else None,
		)

	monkeypatch.setattr(newapi, '_client', client)
	return fake


def login(site, **kw):
	return newapi.Login(base_url='https://x.test', username=site.username, password=site.password, **kw)


def test_client_ignores_an_ambient_socks_proxy(monkeypatch):
	"""A Clash-style `ALL_PROXY` in the environment must not reach httpx.

	httpx builds a transport per env proxy when the client is constructed, and a SOCKS one
	needs `socksio` — so an inherited ALL_PROXY turned every check-in into an ImportError
	before a request left the process.
	"""
	monkeypatch.setenv('ALL_PROXY', 'socks5://127.0.0.1:7897')
	monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:7897')
	client = newapi._client('https://example.com')
	assert client._mounts == {}, 'the environment must not route panel requests'


@pytest.mark.parametrize(
	'raw, expected',
	[
		('abc123', 'abc123'),
		('session=abc123; other=x', 'abc123'),
		('{"session": "abc123"}', 'abc123'),
		('[{"name": "session", "value": "abc123"}]', 'abc123'),
		('  ', None),
		(None, None),
	],
)
def test_parse_session_shapes(raw, expected):
	assert newapi.parse_session(raw) == expected


async def test_probe_reports_capabilities_and_login_bonus(site):
	info = await newapi.probe('https://x.test/')
	assert info.base_url == 'https://x.test'
	assert info.login_methods == ('password', 'linuxdo', 'github')
	assert info.mechanism == 'login_bonus'
	assert info.checkin_path is None


async def test_probe_finds_endpoint_without_consuming_it(site):
	site.checkin_path = '/api/user/check_in'
	info = await newapi.probe('https://x.test')
	assert (info.checkin_path, info.mechanism) == ('/api/user/check_in', 'endpoint')
	assert site.checked_in is False, 'probing must never perform the check-in'


async def test_probe_is_not_fooled_by_the_admin_user_route(site):
	"""`GET /api/user/checkin` matches the admin route `/api/user/:id` and answers
	200 — mistaking that for a check-in endpoint breaks every login_bonus site."""
	info = await newapi.probe('https://x.test')
	assert (info.checkin_path, info.mechanism) == (None, 'login_bonus')
	assert info.refresh_path is None, 'a session-cookie fork has no refresh route'


async def test_probe_prefers_a_forks_own_route_over_a_disabled_generic_one(site):
	"""sotamodel.net registers both `/api/user/checkin` and `/api/user/sota-agent-checkin`,
	answers 401 on each — and the generic one is the *disabled* one (`checkin_enabled:
	false`). POSTing it collects nothing, so the specific route has to win."""
	site.checkin_path = '/api/user/sota-agent-checkin'
	site.dead_checkin_path = '/api/user/checkin'
	site.checkin_enabled = False

	info = await newapi.probe('https://x.test')

	assert info.checkin_path == '/api/user/sota-agent-checkin', 'the working route, not the first one'
	assert info.checkin_enabled is False, 'recorded, because it explains why the generic one is dead'
	assert site.checked_in is False, 'and probing still performs nothing'


async def test_probe_records_a_status_route_only_when_one_answers(site):
	"""A GET that 401s is a real status route. A GET that 200s is the admin `/api/user/:id`
	route eating the segment, which says nothing about a check-in."""
	site.checkin_path = '/api/user/sota-agent-checkin'
	site.status_path = '/api/user/sota-agent-checkin'
	assert (await newapi.probe('https://x.test')).status_path == '/api/user/sota-agent-checkin'

	site.status_path = None  # same check-in route, but GET now falls through to the admin 200
	assert (await newapi.probe('https://x.test')).status_path is None


async def test_check_in_reports_the_amount_the_site_named(site):
	"""A fork that answers `{quota_awarded, current_quota}` is the only thing that can price
	a bonus set by per-weekday configuration we cannot read. Its own figure wins, and
	`current_quota` beats a second balance read that could race with usage."""
	site.checkin_path = '/api/user/sota-agent-checkin'
	site.reports_result = True

	outcome = await newapi.check_in(login(site, session='s'))

	assert (outcome.success, outcome.checked_in) == (True, True)
	assert outcome.awarded == 0.25, 'the site said so'
	assert outcome.after_quota == 1.25, 'read from current_quota, not a second /api/user/self'
	assert outcome.gain == 0.25


async def test_gain_falls_back_to_the_balance_movement(site):
	"""Every other fork says nothing about the amount, so the delta is still the answer."""
	site.checkin_path = '/api/user/checkin'

	outcome = await newapi.check_in(login(site, session='s'))

	assert outcome.awarded is None and outcome.delta == 0.25
	assert outcome.gain == 0.25, 'no figure from the site: the movement is the figure'


async def test_checkin_status_reads_both_known_shapes(site):
	"""sotamodel.net answers flat; seekai.cc nests the same idea under `stats` with a
	`records` calendar. Anything else is 'cannot say', which is not 'no'."""
	site.checkin_path = site.status_path = '/api/user/sota-agent-checkin'
	site.checked_in = True

	flat = await newapi.checkin_status('https://x.test', site.status_path, session='s')

	assert (flat.today, flat.awarded_today) == (True, 0.25)
	assert (flat.reward, flat.total) == (100.0, 1), 'reward_credits is already a display amount'

	site.nests_status = True
	nested = await newapi.checkin_status('https://x.test', site.status_path, session='s')
	assert (nested.today, nested.awarded_today) == (True, 0.25), "today's row of the calendar"
	assert nested.total == 1

	site.checked_in = False  # the same route, on a day nothing has been collected
	fresh = await newapi.checkin_status('https://x.test', site.status_path, session='s')
	assert (fresh.today, fresh.awarded_today) == (False, None), 'an empty calendar prices nothing'

	blank = await newapi.checkin_status('https://x.test', '/api/user/nope', session='s')
	assert (blank.today, blank.awarded_today) == (None, None), 'an unknown shape says nothing'


async def test_a_refresh_cookie_is_spent_for_a_bearer_token(site):
	"""seekai.cc keeps no server session: the cookie in the session field is a refresh
	token, every route wants `Authorization: Bearer`, and the cookie rotates — so the
	rotated one has to come back for storing or the credential dies on its own."""
	site.checkin_path, site.refresh = '/api/user/checkin', 'r1'

	info = await newapi.probe('https://x.test')
	assert info.refresh_path == newapi.REFRESH_PATH

	outcome = await newapi.check_in(newapi.Login(base_url='https://x.test', session='r1'), info)

	assert (outcome.success, outcome.checked_in) == (True, True)
	assert outcome.session == 'r1-rotated', 'the rotated cookie must be persisted'
	assert outcome.api_user == 17928, 'and the id the exchange handed us'
	assert site.quota == 625_000.0


async def test_a_dead_refresh_cookie_says_what_to_paste(site):
	site.checkin_path, site.refresh = '/api/user/checkin', 'r1'
	info = await newapi.probe('https://x.test')

	outcome = await newapi.check_in(newapi.Login(base_url='https://x.test', session='stale'), info)

	assert outcome.success is False and 'new_api_refresh' in outcome.error
	assert site.checked_in is False


def test_fail_unwraps_an_openai_shaped_error():
	response = httpx.Response(404, json={'error': {'message': 'Invalid URL (POST /x)', 'type': 'x'}})
	assert newapi._fail(response.json(), response) == 'Invalid URL (POST /x)'


async def test_endpoint_check_in_credits_quota(site):
	site.checkin_path = '/api/user/checkin'
	outcome = await newapi.check_in(login(site))
	assert (outcome.success, outcome.checked_in) == (True, True)
	assert outcome.delta == round(BONUS / newapi.DEFAULT_QUOTA_PER_UNIT, 2)


async def test_a_session_without_the_required_header_falls_back_to_the_password(site):
	"""A fork can answer `New-Api-User header not provided` to a perfectly good session.
	The password login is the way out: its response carries the id we were missing."""
	site.checkin_path = '/api/user/checkin'
	site.needs_api_user = True

	outcome = await newapi.check_in(login(site, session='no-header-with-it'))

	assert (outcome.success, outcome.checked_in) == (True, True)
	assert outcome.api_user == 7, 'and the id is kept, so the next run does not need the detour'


async def test_endpoint_already_checked_in_is_success(site):
	site.checkin_path = '/api/user/checkin'
	site.checked_in = True
	outcome = await newapi.check_in(login(site))
	assert (outcome.success, outcome.checked_in, outcome.error) == (True, False, None)


async def test_login_bonus_reports_checked_in_and_new_session(site):
	outcome = await newapi.check_in(login(site, session='old'))
	assert (outcome.success, outcome.checked_in) == (True, True)
	assert (outcome.session, outcome.username) == ('fresh', 'alice')
	assert outcome.delta == round(BONUS / newapi.DEFAULT_QUOTA_PER_UNIT, 2)


async def test_login_bonus_second_run_says_not_checked_in(site):
	site.checked_in = True
	outcome = await newapi.check_in(login(site, session='old'))
	assert (outcome.success, outcome.checked_in, outcome.delta) == (True, False, 0.0)


async def test_login_bonus_without_session_still_reports_balance(site):
	"""Password-only account: no before-balance to read, but the after one lands."""
	outcome = await newapi.check_in(login(site))
	assert (outcome.success, outcome.checked_in) == (True, True)
	assert (outcome.before_quota, outcome.after_quota, outcome.delta) == (None, 1.25, None)


async def test_login_bonus_without_password_says_what_can_be_done(site):
	outcome = await newapi.check_in(newapi.Login(base_url='https://x.test', session='old'))
	assert outcome.success is False
	assert '账号密码' in outcome.error and 'GitHub' in outcome.error


async def test_bad_password_is_reported(site):
	bad = newapi.Login(base_url='https://x.test', username='alice', password='nope')
	outcome = await newapi.check_in(bad)
	assert outcome.success is False and '密码' in outcome.error


async def test_bootstrap_password_sets_one(site):
	username, password = await newapi.bootstrap_password('https://x.test', 'old-session')
	assert username == 'alice'
	assert 8 <= len(password) <= 20, 'New API rejects passwords longer than 20 chars'
	assert password in site.new_password and 'Alice' in site.new_password


async def test_bootstrap_password_rejects_dead_session(site, monkeypatch):
	monkeypatch.setattr(site, '_authed', lambda request: False)
	with pytest.raises(RuntimeError, match='会话无效'):
		await newapi.bootstrap_password('https://x.test', 'dead')
