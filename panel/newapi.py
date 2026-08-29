"""Protocol-level New API client — no browser, no UI automation.

Every New API / One API fork exposes the same public surface:

	GET  /api/status      capability discovery (oauth methods, quota_per_unit)
	POST /api/user/login  {username, password} -> `session` cookie + user data
	GET  /api/user/self   balance; also accepts an access token
	PUT  /api/user/self   set username/password (session-authenticated)

Check-in comes in two flavours (see CONTEXT.md "Check-in Mechanism"):

	endpoint     — the site registers a check-in route; POST it.
	login_bonus  — no route; a *fresh login* credits the daily bonus and the
	               login response carries `checked_in`.

Verified against agentrouter.org: its SPA reads `data.checked_in` from the
plain password-login response, so login_bonus needs no OAuth at all.
"""
import json
import os
import re
import secrets
from dataclasses import dataclass, replace
from datetime import datetime
from http.cookies import SimpleCookie
from typing import Optional

import httpx

DEFAULT_QUOTA_PER_UNIT = 500_000.0
TIMEOUT_S = 25.0
CONNECT_TIMEOUT_S = 8.0  # a TCP connect that has not landed by now is not going to
UA = (
	'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
	'(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)
# Most specific first. A fork that adds its own check-in usually leaves the generic route
# registered as well — sotamodel.net answers both, and the generic one is the *disabled*
# one (`checkin_enabled: false`), so first-match order is what picks the working route.
# Measured: every other site 404s the sota-agent path, so trying it first costs one request
# and changes nothing for them.
CHECKIN_CANDIDATES = (
	'/api/user/sota-agent-checkin',
	'/api/user/checkin',
	'/api/user/check_in',
	'/api/user/sign_in',
	'/api/user/clock_in',
)
REFRESH_PATH = '/api/user/auth/refresh'  # JWT forks: trade the refresh cookie for a Bearer token
REFRESH_COOKIE = 'new_api_refresh'
OAUTH_FLAGS = ('linuxdo', 'github', 'oidc', 'telegram', 'wechat')
ALREADY_DONE = re.compile(r'已签到|已经签到|重复签到|already', re.I)
CHECKIN_LOG = re.compile(r'签到|check.?in', re.I)
SYSTEM_LOG_TYPE = 4  # New API: 1 topup, 2 consume, 3 manage, 4 system, 5 error


def parse_session(raw: Optional[str]) -> Optional[str]:
	"""Accept any shape a session cookie gets pasted in: bare value,
	`session=v; other=w`, a JSON dict, or a Playwright cookie list."""
	raw = (raw or '').strip()
	if not raw:
		return None
	if raw.startswith('{'):
		try:
			d = json.loads(raw)
			return d.get('session') or next((str(v) for v in d.values()), None)
		except (json.JSONDecodeError, StopIteration):
			pass
	elif raw.startswith('['):
		try:
			return next(c['value'] for c in json.loads(raw) if c.get('name') == 'session')
		except (json.JSONDecodeError, StopIteration, TypeError, KeyError):
			pass
	if 'session=' in raw:
		jar = SimpleCookie()
		jar.load(raw)
		if 'session' in jar:
			return jar['session'].value
	return raw


@dataclass
class SiteInfo:
	"""What a New API instance told us about itself."""

	base_url: str
	login_methods: tuple[str, ...] = ('password',)
	quota_per_unit: float = DEFAULT_QUOTA_PER_UNIT
	turnstile: bool = False
	turnstile_key: Optional[str] = None  # its Turnstile sitekey, for minting a token in a browser
	checkin_path: Optional[str] = None
	refresh_path: Optional[str] = None  # set when the fork issues JWTs instead of sessions
	# Same path, GET: a calendar/status read that performs nothing. Where a fork offers one
	# it outranks every other signal — it is the site naming its own day, rather than us
	# inferring one from a balance that moves for many reasons.
	status_path: Optional[str] = None
	# The generic check-in toggle from /api/status. False on a fork that registered the
	# route but turned it off (sotamodel.net), None on forks that never report it, and
	# unreadable behind a WAF — so it is recorded, not used to decide anything.
	checkin_enabled: Optional[bool] = None

	@property
	def mechanism(self) -> str:
		return 'endpoint' if self.checkin_path else 'login_bonus'


@dataclass
class Login:
	"""The credentials for one identity on one site."""

	base_url: str
	login_method: str = 'password'  # password | access_token | session | linuxdo | github
	username: Optional[str] = None
	password: Optional[str] = None
	access_token: Optional[str] = None
	session: Optional[str] = None
	api_user: Optional[str] = None
	turnstile: Optional[str] = None  # a freshly minted Turnstile token, if the site wants one


@dataclass
class Outcome:
	success: bool
	checked_in: Optional[bool] = None
	before_quota: Optional[float] = None
	after_quota: Optional[float] = None
	error: Optional[str] = None
	session: Optional[str] = None  # fresh session worth persisting
	access_token: Optional[str] = None  # a Bearer won during this run, for one more read
	api_user: Optional[str] = None  # the `new-api-user` id that session needs
	username: Optional[str] = None
	awarded: Optional[float] = None  # USD the site itself said it just granted

	@property
	def delta(self) -> Optional[float]:
		if self.before_quota is None or self.after_quota is None:
			return None
		return round(self.after_quota - self.before_quota, 2)

	@property
	def gain(self) -> Optional[float]:
		"""What today's check-in was worth: the site's own figure when it gives one, else
		the balance movement. A fork that reports `quota_awarded` is the only thing that
		can price a bonus whose amount we could not have predicted."""
		return self.awarded if self.awarded is not None else self.delta


@dataclass
class CheckinStatus:
	"""What a fork's check-in status route says, having performed nothing.

	`today` is the strongest signal there is where it is not None: the site is naming its
	own day, which beats a balance comparison (a balance moves for many reasons) and beats
	a login response's `checked_in` (a state flag that stays true all day). None
	everywhere means "cannot say" — never "no".
	"""

	today: Optional[bool] = None  # collected today, per the site
	awarded_today: Optional[float] = None  # USD granted today, where the route says
	reward: Optional[float] = None  # USD this site grants per day, where it advertises it
	total: Optional[int] = None  # lifetime check-in count


async def checkin_status(
	base_url: str, path: str, *, quota_per_unit: float = DEFAULT_QUOTA_PER_UNIT, **credentials
) -> CheckinStatus:
	"""GET a fork's check-in status route. Performs no check-in.

	Two shapes are known and both are read here: sotamodel.net answers
	`{checked_in_today, reward_credits, quota_awarded_today}` flat, and seekai.cc nests the
	same idea under `stats` with a `records` calendar (it wants `?month=YYYY-MM`). Anything
	unrecognised comes back as an all-None status, which every caller treats as "cannot say".
	"""
	try:
		async with _client(base_url, **credentials) as client:
			response = await client.get(path, params={'month': datetime.now().strftime('%Y-%m')})
			body = _body(response)
	except Exception:  # its whole contract is "cannot say" on any trouble
		return CheckinStatus()
	if not body.get('success'):
		return CheckinStatus()
	data = body.get('data')
	if not isinstance(data, dict):
		return CheckinStatus()
	stats = data.get('stats') if isinstance(data.get('stats'), dict) else data
	today = stats.get('checked_in_today')
	awarded = stats.get('quota_awarded_today')
	if awarded is None:  # seekai-shaped: today's row of the month's calendar
		stamp = datetime.now().strftime('%Y-%m-%d')
		for record in stats.get('records') or []:
			if isinstance(record, dict) and str(record.get('checkin_date') or '').startswith(stamp):
				awarded = record.get('quota_awarded')
				break
	total = stats.get('total_checkins')
	return CheckinStatus(
		today=today if isinstance(today, bool) else None,
		awarded_today=_usd_raw(awarded, quota_per_unit),
		# `reward_credits` is already a display amount (sotamodel shows it as `$100`), not
		# raw quota units — dividing it by quota_per_unit would report $0.0002.
		reward=float(data['reward_credits']) if isinstance(data.get('reward_credits'), (int, float)) else None,
		total=total if isinstance(total, int) else None,
	)


def _usd_raw(quota, per_unit: float) -> Optional[float]:
	"""Raw quota units -> USD. Unlike `usd()` this takes the number, not a user dict."""
	if not isinstance(quota, (int, float)) or isinstance(quota, bool):
		return None
	return round(quota / (per_unit or DEFAULT_QUOTA_PER_UNIT), 2)


def _client(
	base_url: str,
	*,
	session: Optional[str] = None,
	access_token: Optional[str] = None,
	api_user: Optional[str] = None,
	proxy: Optional[str] = None,
) -> httpx.AsyncClient:
	headers = {'Accept': 'application/json', 'User-Agent': UA}
	if access_token:
		headers['Authorization'] = access_token
	if api_user:
		headers['new-api-user'] = str(api_user)
	return httpx.AsyncClient(
		base_url=base_url.rstrip('/'),
		timeout=httpx.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
		headers=headers,
		cookies={'session': session} if session else None,
		follow_redirects=True,
		proxy=proxy,
		# Never inherit the machine's proxy env: httpx builds a transport per env proxy at
		# construction time, so a Clash-style `ALL_PROXY=socks5://...` made every client raise
		# ImportError (no `socksio`) before a request went out. Proxying here is explicit —
		# see `_turnstile_proxy`.
		trust_env=False,
	)


def _turnstile_proxy() -> Optional[str]:
	"""The proxy a Turnstile-carrying request has to go out through.

	A token is minted in a browser that had to use the proxy to get one at all, and the
	site validates it against the caller's IP — so the POST has to come from the same
	place or the token reads as invalid.
	"""
	return os.getenv('CHECKIN_PROXY_URL') or None


def why(e: BaseException) -> str:
	"""Readable reason for an exception, for a panel the owner has to debug from.

	httpx raises some errors with an empty message (a bare `ConnectError`), and
	`ConnectError: ` in a log says strictly nothing — so name the class instead.
	"""
	text = str(e).strip()
	return f'{type(e).__name__}: {text}' if text else type(e).__name__


def _body(response: httpx.Response) -> dict:
	try:
		body = response.json()
	except ValueError:
		return {'success': False, 'message': f'HTTP {response.status_code} (非 JSON 响应)'}
	return body if isinstance(body, dict) else {'success': False, 'message': str(body)[:200]}


def _fail(body: dict, response: httpx.Response) -> str:
	message = body.get('message') or body.get('error') or f'HTTP {response.status_code}'
	if isinstance(message, dict):  # OpenAI-shaped: {"error": {"message": ..., "type": ...}}
		message = message.get('message') or message
	return str(message)


def _cookie_of(client: httpx.AsyncClient, name: str = 'session') -> Optional[str]:
	"""The cookie the *server* set, if any.

	A seeded cookie (domain '') and a server-set one (domain = host) coexist in the
	jar, and httpx's .get() raises CookieConflict on that — so pick explicitly.
	"""
	cookies = [c for c in client.cookies.jar if c.name == name]
	if not cookies:
		return None
	return next((c.value for c in reversed(cookies) if c.domain), cookies[-1].value)


async def _self(client: httpx.AsyncClient) -> tuple[Optional[dict], Optional[str]]:
	"""GET /api/user/self -> (data, error)."""
	response = await client.get('/api/user/self')
	body = _body(response)
	if response.status_code == 200 and body.get('success'):
		return body.get('data') or {}, None
	return None, _fail(body, response)


def usd(data: Optional[dict], per_unit: float) -> Optional[float]:
	quota = (data or {}).get('quota')
	if quota is None:
		return None
	return round(quota / (per_unit or DEFAULT_QUOTA_PER_UNIT), 2)


async def whoami(
	base_url: str,
	*,
	session: Optional[str] = None,
	access_token: Optional[str] = None,
	api_user: Optional[str] = None,
) -> Optional[dict]:
	"""The user behind a credential, or None if it does not authenticate.

	The only honest "am I logged in?" test: a `session` cookie exists long before
	anyone logs in (New API stores the OAuth state in it).
	"""
	async with _client(base_url, session=session, access_token=access_token, api_user=api_user) as client:
		data, _ = await _self(client)
	return data


async def balance(base_url: str, *, quota_per_unit: float = DEFAULT_QUOTA_PER_UNIT, **credentials) -> Optional[float]:
	"""Balance in USD for whatever credential is given, or None if it is dead."""
	return usd(await whoami(base_url, **credentials), quota_per_unit)


async def last_checkin_at(base_url: str, **credentials) -> Optional[float]:
	"""When the site's own quota log last recorded a check-in bonus, or None if it
	cannot say (no such route, a WAF, an empty log, a fork that words it differently).

	The only receipt there is. A login response's `checked_in` is a *state* flag — it
	stays true all day, so it cannot tell "just credited" from "already had it" — and a
	balance that cannot be read proves nothing at all (ADR-0010). This route is a plain
	authenticated GET and, unlike `/api/user/self`, was not behind the WAF.
	"""
	try:
		async with _client(base_url, **credentials) as client:
			response = await client.get(
				'/api/log/self', params={'p': 0, 'page_size': 20, 'type': SYSTEM_LOG_TYPE}
			)
			body = _body(response)
	except Exception:  # its whole contract is "None when it cannot say"
		return None
	if not body.get('success'):
		return None
	data = body.get('data')
	items = data.get('items') if isinstance(data, dict) else data
	stamps = [
		item.get('created_at')
		for item in items or []
		if isinstance(item, dict) and CHECKIN_LOG.search(str(item.get('content') or ''))
	]
	return max((s for s in stamps if isinstance(s, (int, float))), default=None)


async def refresh_access(base_url: str, refresh: str) -> tuple[Optional[str], Optional[str], dict]:
	"""Trade a `new_api_refresh` cookie for a Bearer access token.

	Some forks (seekai.cc) keep no server-side session at all: a login mints a short
	lived JWT plus a rotating refresh cookie, and every route wants `Authorization:
	Bearer` — a session cookie authenticates nothing there, which is why pasting one
	reads as 凭据无效. Returns (access token, the rotated refresh cookie, the user).

	The refresh cookie rotates on every exchange, so the caller must store the new one
	or the pasted credential eventually stops working. `rotated` is None when the server
	did not hand one back — then the one we already have is still the live one, and
	returning the spent value would kill the account on the next run.
	"""
	async with _client(base_url) as client:
		client.cookies.set(REFRESH_COOKIE, refresh)
		body = _body(await client.post(REFRESH_PATH))
		if not body.get('success'):
			return None, None, {}
		data = body.get('data') or {}
		rotated = _cookie_of(client, REFRESH_COOKIE)
		return data.get('access_token'), (rotated if rotated != refresh else None), data.get('user') or {}


async def probe(base_url: str) -> SiteInfo:
	"""Ask a site what it supports: login methods, quota divisor, check-in route.

	Everything here is public, and the route probes run unauthenticated on purpose —
	an authenticated POST would *perform* the check-in and steal the before-balance
	from the real run.
	"""
	info = SiteInfo(base_url=base_url.rstrip('/'))
	async with _client(info.base_url) as anon:
		try:
			data = _body(await anon.get('/api/status')).get('data') or {}
		except httpx.HTTPError as e:
			raise RuntimeError(f'无法访问 {info.base_url}: {why(e)}') from e
		methods = ['password'] + [k for k in OAUTH_FLAGS if data.get(f'{k}_oauth')]
		info.login_methods = tuple(methods)
		info.quota_per_unit = float(data.get('quota_per_unit') or DEFAULT_QUOTA_PER_UNIT)
		info.turnstile = bool(data.get('turnstile_check'))
		info.turnstile_key = data.get('turnstile_site_key') or None
		enabled = data.get('checkin_enabled')
		info.checkin_enabled = enabled if isinstance(enabled, bool) else None
		# ponytail: POST-only detection. A GET probe is worthless — `GET /api/user/checkin`
		# matches the admin route `/api/user/:id` and answers 200, which used to mistake
		# every login_bonus site for an endpoint one. Add GET back only for a fork that
		# really registers a GET-only check-in route.
		for path in CHECKIN_CANDIDATES:
			try:
				response = await anon.post(path, json={})
				# JSON or it did not happen: a WAF challenge answers 200 HTML to every
				# path, which would make every site look like an endpoint one.
				if response.status_code != 404 and 'json' in response.headers.get('content-type', ''):
					info.checkin_path = path
					break
			except httpx.HTTPError:
				continue
		if info.checkin_path:
			# Does the same path answer GET with a status read? A fork that offers one hands
			# us the only unambiguous "did today's bonus land" there is. 401 is the proof:
			# the route exists and wants a credential. 200 would be the admin `/api/user/:id`
			# route eating the segment, which says nothing about a check-in.
			try:
				response = await anon.get(info.checkin_path)
				if response.status_code == 401 and 'json' in response.headers.get('content-type', ''):
					info.status_path = info.checkin_path
			except httpx.HTTPError:
				pass
		try:  # 401 here means the fork authenticates with JWTs, not session cookies
			response = await anon.post(REFRESH_PATH)
			if response.status_code != 404 and 'json' in response.headers.get('content-type', ''):
				info.refresh_path = REFRESH_PATH
		except httpx.HTTPError:
			pass
	return info


async def _password_login(client: httpx.AsyncClient, login: Login) -> tuple[Optional[dict], Optional[str]]:
	if not (login.username and login.password):
		return None, '缺少用户名/密码'
	response = await client.post(
		'/api/user/login', json={'username': login.username, 'password': login.password}
	)
	body = _body(response)
	if not body.get('success'):
		return None, _fail(body, response)
	data = body.get('data') or {}
	if data.get('id'):
		# Some forks validate `new-api-user` against the session and 401 every
		# authenticated route without it — the SPA sends it, so we send it too.
		client.headers['new-api-user'] = str(data['id'])
	return data, None


async def check_in(login: Login, site: Optional[SiteInfo] = None) -> Outcome:
	"""Do the daily check-in over HTTP. Never launches a browser."""
	if site is None:
		site = await probe(login.base_url)
	if site.refresh_path and login.session and not login.access_token:
		# A JWT fork stores no session: what sits in the session field is a refresh
		# cookie, and it has to be spent for a Bearer token before anything works.
		token, rotated, user = await refresh_access(login.base_url, login.session)
		if token:
			login = replace(
				login,
				access_token=token,
				session=rotated or login.session,  # rotated, so it must be persisted
				api_user=login.api_user or user.get('id'),
			)
		elif not (login.username and login.password):
			return Outcome(
				False,
				error='refresh 凭据已失效：到站点重新登录，复制新的 new_api_refresh cookie 再粘进来',
			)
	if site.checkin_path:
		return await _check_in_endpoint(login, site)
	return await _check_in_login_bonus(login, site)


async def _check_in_endpoint(login: Login, site: SiteInfo) -> Outcome:
	async with _client(
		site.base_url,
		session=login.session,
		access_token=login.access_token,
		api_user=login.api_user,
		proxy=_turnstile_proxy() if login.turnstile else None,
	) as client:
		adopted = login.api_user
		if not (login.session or login.access_token):
			data, error = await _password_login(client, login)
			if error:
				return Outcome(False, error=f'登录失败: {error}')
			adopted = data.get('id') or adopted
		before, error = await _self(client)
		if before is None and login.username and login.password:
			# The stored credential did not authenticate — a dead session, or a fork that
			# demands `new-api-user` and we have no id yet ("New-Api-User header not
			# provided"). A password login fixes both: it adopts the id from its response.
			client.cookies.clear()
			data, error = await _password_login(client, login)
			if error:
				return Outcome(False, error=f'登录失败: {error}', session=login.session)
			adopted = data.get('id') or adopted
			before, error = await _self(client)
		if before is None and not login.password:
			# Carry the credential out even in failure: a JWT fork rotated its refresh cookie
			# a moment ago (`check_in`), and returning without it leaves the account holding a
			# spent one — 凭据无效 on every run after this, until someone logs in by hand.
			return Outcome(False, error=f'凭据无效: {error}', session=login.session)
		# The token rides as a query param, which is where this fork's own SPA puts it:
		# POST /api/user/checkin?turnstile=... — without it the answer is `Turnstile token 为空`.
		response = await client.post(
			site.checkin_path, params={'turnstile': login.turnstile} if login.turnstile else None, json={}
		)
		body = _body(response)
		granted = body.get('data') if isinstance(body.get('data'), dict) else {}
		after, _ = await _self(client)
		# Some forks report the result rather than leaving it to be inferred: sotamodel.net
		# answers `{quota_awarded, current_quota, reward_credits}`. `current_quota` is the
		# balance *after* the grant, straight from the site, so it beats a second read that
		# could race with usage; `quota_awarded` is the only way to price a bonus whose
		# amount is per-weekday configuration we cannot see.
		after_quota = _usd_raw(granted.get('current_quota'), site.quota_per_unit)
		outcome = Outcome(
			success=bool(body.get('success')),
			checked_in=bool(body.get('success')),
			before_quota=usd(before, site.quota_per_unit),
			after_quota=after_quota if after_quota is not None else usd(after, site.quota_per_unit),
			session=_cookie_of(client) or login.session,
			access_token=login.access_token,
			api_user=adopted,
			awarded=_usd_raw(granted.get('quota_awarded'), site.quota_per_unit),
		)
		if not outcome.success:
			message = _fail(body, response)
			if ALREADY_DONE.search(message):
				return replace(outcome, success=True, checked_in=False, error=None)
			outcome.error = message
		return outcome


async def _check_in_login_bonus(login: Login, site: SiteInfo) -> Outcome:
	"""No check-in route: a fresh login is the check-in. The login response's
	`checked_in` is the only honest success signal — quota may legitimately not
	move if the bonus already landed today."""
	if not (login.username and login.password):
		return Outcome(
			False,
			error='该站点靠"重新登录"发放额度，而当前凭据无法重新登录。'
			'请填账号密码，或把登录方式改成 LinuxDO / GitHub（面板会自动用浏览器登录一次）。',
		)
	async with _client(site.base_url, session=login.session, api_user=login.api_user) as client:
		before, _ = await _self(client)  # old session may be dead; that is fine
		client.cookies.clear()  # a fresh login replaces the old session, it does not stack
		data, error = await _password_login(client, login)
		if error:
			return Outcome(False, before_quota=usd(before, site.quota_per_unit), error=f'登录失败: {error}')
		after, _ = await _self(client)
		return Outcome(
			success=True,
			checked_in=bool(data.get('checked_in')),
			before_quota=usd(before, site.quota_per_unit),
			after_quota=usd(after or data, site.quota_per_unit),
			session=_cookie_of(client),
			api_user=data.get('id'),
			username=data.get('username'),
		)


async def bootstrap_password(
	base_url: str,
	session: str,
	*,
	api_user: Optional[str] = None,
	password: Optional[str] = None,
) -> tuple[str, str]:
	"""Turn a browser-minted session into a permanent username+password, so every
	later check-in is pure HTTP. Only the password changes — username and
	display_name are read back and echoed so PUT /api/user/self cannot blank them.
	Returns (username, password).

	Not every fork allows this: some verify `original_password` first, which an
	OAuth-only account does not have (ADR-0009). Those accounts stay on the
	browser login path.
	"""
	password = password or secrets.token_urlsafe(12)  # New API validates max=20 chars
	async with _client(base_url, session=session, api_user=api_user) as client:
		data, error = await _self(client)
		if error:
			raise RuntimeError(f'会话无效，无法设置密码: {error}')
		username = data.get('username') or ''
		response = await client.put(
			'/api/user/self',
			json={
				'username': username,
				'password': password,
				'display_name': data.get('display_name') or username,
			},
		)
		body = _body(response)
		if not body.get('success'):
			message = _fail(body, response)
			if '原密码' in message:
				message += '（该站点不允许给 OAuth 账号直接设置密码，继续用 OAuth 登录方式签到即可）'
			raise RuntimeError(f'设置密码失败: {message}')
	return username, password
