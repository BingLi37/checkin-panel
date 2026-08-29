"""CheckInService — the single path from a stored account to a check-in.

Local-only: the panel does the check-in itself over HTTP (panel/newapi.py).
There is no GitHub Actions mirror any more — one implementation, one source of
truth, no secret format to keep in sync.

Protocol first, always. A browser is launched only for an account the protocol
path cannot log in at all: an OAuth-only identity on a fork that refuses to set
a password (ADR-0009). Those cost one browser launch a day; everyone else costs
a handful of HTTP requests.
"""
import asyncio
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional

from panel import newapi
from panel.store import Account, AccountStore

CONCURRENCY = 4
BROWSER_METHODS = ('linuxdo', 'github')  # keep in sync with browser_login.PROVIDER_BUTTONS
TURNSTILE_MISSING = re.compile(r'turnstile', re.I)
NOT_JSON = re.compile(r'非 JSON 响应')  # a WAF answered instead of the API


def _total(user: Optional[dict], quota_per_unit: float) -> Optional[float]:
	"""Everything this account was ever given, in USD: quota + used_quota.

	Spending only moves quota into used_quota, so this number never falls — which makes it
	the honest way to see a grant. A bare balance can sit still because the bonus landed
	and an equal amount was spent, and `quota` alone then reads as "nothing happened".
	Needs both fields: `used_quota` missing (the SPA's stored login response) means the
	total is unknown, not equal to the quota.
	"""
	if not isinstance(user, dict) or user.get('quota') is None or user.get('used_quota') is None:
		return None
	per_unit = quota_per_unit or newapi.DEFAULT_QUOTA_PER_UNIT
	return round((user['quota'] + user['used_quota']) / per_unit, 2)


def _grew(before: Optional[float], after: Optional[float]) -> Optional[bool]:
	"""Did it go up? None when either side is unknown — that is 'cannot say', not 'no'."""
	if before is None or after is None:
		return None
	return after > before


def window_start(account: Account, now: Optional[datetime] = None) -> float:
	"""When the account's current check-in window opened.

	Most sites open the day's bonus at midnight, but not all: anyrouter.top opens it at
	08:30, so a run at 08:00 belongs to *yesterday's* window and an account that already
	collected yesterday must not look due all night. `checkin_after` is 'HH:MM' local;
	empty means midnight.
	"""
	now = now or datetime.now().astimezone()
	hour, minute = 0, 0
	try:
		if account.checkin_after:
			hour, minute = (int(part) for part in account.checkin_after.split(':', 1))
	except ValueError:  # a hand-typed value that is not HH:MM: treat the day as normal
		hour, minute = 0, 0
	start = now.replace(hour=hour % 24, minute=minute % 60, second=0, microsecond=0)
	return (start if start <= now else start - timedelta(days=1)).timestamp()


class CheckInService:
	def __init__(self, store: AccountStore, *, concurrency: int = CONCURRENCY):
		self.store = store
		self._gate = asyncio.Semaphore(concurrency)
		self._running: set[int] = set()

	@staticmethod
	def _login(account: Account) -> newapi.Login:
		return newapi.Login(
			base_url=account.base_url,
			login_method=account.login_method,
			username=account.username,
			password=account.password,
			access_token=account.access_token,
			session=newapi.parse_session(account.session),
			api_user=account.api_user,
		)

	async def check_in(self, account_id: int) -> newapi.Outcome:
		account = self.store.get(account_id)
		if account is None:
			return newapi.Outcome(False, error=f'账号 {account_id} 不存在')
		if not account.base_url:
			return newapi.Outcome(False, error='缺少网站地址 (Base URL)')
		# One run at a time per account: two browser logins on one profile directory
		# fight over Chrome's profile lock, and pressing 签到 while the scheduler is
		# already on that account would just wait for it in silence.
		if account_id in self._running:
			# Almost always the scheduler: it ticks on startup and takes every overdue
			# account, so opening the panel and pressing 签到 collides by design. Saying
			# only "busy" left the owner thinking their press had failed.
			return newapi.Outcome(
				False,
				error='该账号正在签到中（多半是面板启动时自动签到还没跑完，浏览器登录要 1~2 分钟），等这一次跑完再看结果',
			)

		self._running.add(account_id)
		started = time.time()
		site = None
		try:
			async with self._gate:
				try:
					outcome, site = await self._attempt(account)
				except Exception as e:  # network/browser/anything — one account must not kill a batch
					outcome = newapi.Outcome(False, error=newapi.why(e))
		finally:
			self._running.discard(account_id)

		await self._reconcile(account, outcome, started, site)

		updates = {}
		if outcome.session and outcome.session != account.session:
			updates['session'] = outcome.session
		if outcome.api_user and str(outcome.api_user) != (account.api_user or ''):
			updates['api_user'] = str(outcome.api_user)
		if outcome.username and not account.username:
			updates['username'] = outcome.username
		if updates:
			self.store.update(account_id, **updates)

		quota = outcome.after_quota if outcome.after_quota is not None else outcome.before_quota
		self.store.record_result(
			account_id,
			success=outcome.success,
			checked_in=outcome.checked_in,
			quota=quota,
			error=outcome.error,
		)
		return outcome

	async def _reconcile(
		self,
		account: Account,
		outcome: newapi.Outcome,
		started: float,
		site: Optional[newapi.SiteInfo] = None,
	) -> None:
		"""Make the report match what the site itself says, whatever our read-back managed.

		Two sources, in order of authority. A fork with a **status route** answers "did today's
		bonus land" directly, which settles it — no inference from a balance, and no quota log
		to be missing. Otherwise the quota log decides: the bonus can land while the
		confirmation fails (a WAF ate the balance read), and a login response's `checked_in`
		stays true all day. An entry from this run is 签到成功, an older one from today is
		今日已签到, and no entry today means the login worked but the bonus did not land.
		"""
		credentials = dict(
			session=newapi.parse_session(outcome.session or account.session),
			access_token=outcome.access_token or account.access_token,
			api_user=outcome.api_user or account.api_user,
		)
		if site is not None and site.status_path:
			status = await newapi.checkin_status(
				account.base_url, site.status_path, quota_per_unit=site.quota_per_unit, **credentials
			)
			if status.awarded_today is not None and outcome.awarded is None:
				outcome.awarded = status.awarded_today
			if status.today is not None:
				# The site named its own day. `checked_in` still means "*this run* collected
				# it", so a bonus that was already there before we started is 今日已签到.
				if status.today:
					if not outcome.success:  # collected anyway — do not retry every 30 minutes
						outcome.success, outcome.error = True, None
					if outcome.checked_in is None:
						outcome.checked_in = False
				elif outcome.success:
					outcome.success = False
					outcome.error = '站点说今天还没有签到记录'
				return
		stamp = await newapi.last_checkin_at(account.base_url, **credentials)
		if stamp is None:  # the site does not say; keep whatever the attempt concluded
			return
		if stamp >= window_start(account):
			outcome.checked_in = stamp >= started
			if not outcome.success:  # credited anyway — do not retry it every 30 minutes
				outcome.success, outcome.error = True, None
		elif outcome.success:
			outcome.success = False
			outcome.error = '登录成功，但站点额度明细里今天没有签到记录'

	async def _attempt(self, account: Account) -> tuple[newapi.Outcome, newapi.SiteInfo]:
		"""One check-in attempt, plus the probed site — `_reconcile` needs it to know
		whether this fork has a status route worth trusting over the quota log."""
		login = self._login(account)
		site = await newapi.probe(account.base_url)
		if account.mechanism == 'visit' and not site.checkin_path:
			# The owner told us this site grants the bonus when an authenticated page
			# loads. There is nothing to POST and nothing to re-login for, and on a
			# WAF'd site the protocol attempt cannot even reach the login route.
			#
			# Only where the probe found no route, though: `visit` is a declaration of what
			# probing *cannot* see, and it is the natural pick for any site whose
			# UI signs in by itself — api.justwoker.icu does that and still registers
			# `/api/user/checkin`. Taking the browser there is a dead end: on a JWT fork the
			# in-page reads need a Bearer token the page alone will not hand over, so an
			# OAuth account with no password could never reach a logged-in state.
			return await self._browser_visit(account, site), site
		outcome = await newapi.check_in(login, site)
		# A JWT fork rotates its refresh token on every exchange, so the value we just
		# used is already dead. Store the new one *now*: if a later step fails, the run
		# is discarded and the account would be left holding the spent credential —
		# which is 凭据无效 on every run after, until someone logs in by hand again.
		if outcome.session and outcome.session != account.session:
			self.store.update(account.id, session=outcome.session)
			account = replace(account, session=outcome.session)
		if not outcome.success and site.turnstile_key and TURNSTILE_MISSING.search(outcome.error or ''):
			# The credential is fine, the site just wants a token no HTTP client can make.
			# Borrow a browser for that one job — far cheaper and less fragile than logging
			# in again. The session may have rotated during the failed attempt; carry it.
			outcome = await self._with_turnstile(account, site, login, outcome)
		if outcome.success:
			return outcome, site
		if account.login_method in BROWSER_METHODS:
			# The failed attempt may still have read a balance (and rotated a token) — keep
			# both, they are the only "before" the browser hop will get.
			return await self._browser_check_in(account, site, outcome), site
		if account.has_password and NOT_JSON.search(outcome.error or ''):
			# Not a credential problem: a WAF answered the login route with a JS challenge,
			# which only a browser can run (ADR-0012).
			return await self._browser_visit(account, site), site
		return outcome, site

	async def _browser_visit(self, account: Account, site: newapi.SiteInfo) -> newapi.Outcome:
		"""Load the site in a browser, hold its automatic check-in, then collect it on purpose.

		Logging in is not the same as collecting: the site's own ledger decides where there
		is one, read from inside the page because a WAF answers everything else (ADR-0012).
		Where there is none, the balance across the *deliberate* check-in POST is the
		measurement — which is only meaningful because `browser_visit` held the SPA's own
		POST back long enough to read a pre-bonus balance. No movement and no receipt is
		'cannot say', never 今日已签到: that lie is how 假签到 got reported.
		"""
		from panel.browser_login import browser_visit

		started = time.time()
		visit = await browser_visit(
			base_url=account.base_url,
			account_name=account.name,
			provider=account.login_method,
			username=account.username,
			password=account.password,
			headless=True,
		)
		before, after = visit.before, visit.after
		if not after:
			# Do not blame a password the account may not even have: an OAuth identity gets
			# here whenever the profile's site session has lapsed, and the fix for that is
			# one visible browser login, not a credential the owner never set.
			return newapi.Outcome(
				False,
				error='浏览器里也没进到已登录状态：'
				+ ('检查账号密码是否正确' if account.has_password else '点「浏览器登录」重新登录一次（站点会话已过期）'),
			)
		# quota=0 out of a login response is a missing number, not a zero balance
		before_quota = (newapi.usd(before, site.quota_per_unit) or None) if before else None
		after_quota = newapi.usd(after, site.quota_per_unit) or None
		common = dict(
			before_quota=before_quota,
			after_quota=after_quota,
			session=visit.session,
			api_user=after.get('id'),
			username=after.get('username'),
		)
		# A grant is visible in quota+used_quota even when the account spent money mid-run:
		# spending moves quota into used_quota and leaves the total alone, so only a grant
		# can raise it. Comparing bare quota called a $25 bonus 'no change' on an account
		# that burned $25 of it in the same window (upstream checkin.py does the same).
		gained = _grew(_total(before, site.quota_per_unit), _total(after, site.quota_per_unit))
		if gained is None:  # totals unreadable — bare quota is the only reading left
			gained = _grew(before_quota, after_quota)
		if account.mechanism == 'visit':
			# No quota-log receipt on this mechanism (measured: anyrouter.top records a
			# check-in under no log type at all), so the route's own answer plus the balance
			# across it is everything. A refusal is worth reporting as one.
			if visit.refused:
				return newapi.Outcome(
					success=False, checked_in=False, error=f'站点拒绝了签到: {visit.message or "未说明原因"}', **common
				)
			# `held` is what makes an unmoved balance mean anything. With the SPA's own POST
			# held back, `before` really is pre-bonus, so no movement across our deliberate
			# POST does say "today was already collected" — 今日已签到, earned. Without the
			# hold, the page load may have collected it before `before` was ever read, and
			# then an unmoved balance proves nothing: 已重新登录, not a claim we cannot make.
			if gained is False and not visit.held:
				gained = None
			return newapi.Outcome(success=True, checked_in=gained, **common)
		if visit.checkin_at is not None:  # the ledger can speak, so it decides
			landed = visit.checkin_at >= window_start(account)
			return newapi.Outcome(
				success=landed,
				checked_in=visit.checkin_at >= started if landed else False,
				error=None if landed else '登录成功，但站点额度明细里这一轮没有签到记录',
				**common,
			)
		return newapi.Outcome(
			success=bool(gained),
			checked_in=gained,
			error=None if gained else '登录成功，但余额没变，也读不到站点的签到记录',
			**common,
		)

	async def _with_turnstile(
		self, account: Account, site: newapi.SiteInfo, login: newapi.Login, failed: newapi.Outcome
	) -> newapi.Outcome:
		from panel.browser_login import mint_turnstile

		token = await mint_turnstile(
			base_url=account.base_url,
			provider=account.login_method if account.login_method in BROWSER_METHODS else 'github',
			account_name=account.name,
			sitekey=site.turnstile_key,
		)
		if not token:
			return failed
		retry = replace(login, session=failed.session or login.session, turnstile=token)
		return await newapi.check_in(retry, site)

	async def _browser_check_in(
		self, account: Account, site: newapi.SiteInfo, failed: Optional[newapi.Outcome] = None
	) -> newapi.Outcome:
		"""Log in through the IdP in a real browser, then finish over HTTP.

		On a `login_bonus` site that login *is* the check-in and its response goes to the
		browser, not to us — so what the browser knows (its `checked_in`, its balance) is
		the primary signal, and the balance moving across the login is the fallback. On an
		`endpoint` site the login only gets us in; a Turnstile-gated route then needs a
		token, which is minted in a *fresh* context — never this one.
		"""
		from panel.browser_login import browser_login

		before = (failed.before_quota if failed else None) or await newapi.balance(
			account.base_url,
			session=newapi.parse_session(failed.session if failed else account.session),
			api_user=account.api_user,
			quota_per_unit=site.quota_per_unit,
		)
		result = await browser_login(
			base_url=account.base_url,
			provider=account.login_method,
			account_name=account.name,
			headless=True,
		)
		session, user = result.credential, result.user
		api_user = user.get('id') or account.api_user
		self.store.update(account.id, session=session, api_user=api_user)
		if site.checkin_path:  # the login only got us in; the check-in is still a POST
			login = replace(self._login(account), session=session, api_user=api_user)
			outcome = await newapi.check_in(login, site)
			if not outcome.success and site.turnstile_key and TURNSTILE_MISSING.search(outcome.error or ''):
				# Mint in a *fresh* context (that is what `_with_turnstile` does): this
				# profile has just been through an OAuth round trip, and Cloudflare will
				# not render its widget for a context carrying that much challenge state.
				outcome = await self._with_turnstile(account, site, login, outcome)
				if not outcome.success and TURNSTILE_MISSING.search(outcome.error or ''):
					outcome.error = '站点签到要 Turnstile，这次浏览器里没拿到 token（Cloudflare 没给），下一轮会重试'
			return outcome
		after = None
		bearer = None
		if site.refresh_path:  # a JWT fork: the token exchange reports the balance itself
			bearer, rotated, fresh = await newapi.refresh_access(account.base_url, session)
			if rotated:
				session = rotated  # it rotates on every exchange; keep the live one
				self.store.update(account.id, session=session)
			after = newapi.usd(fresh, site.quota_per_unit)
			user = user or fresh
		if after is None:
			after = await newapi.balance(
				account.base_url,
				session=session,
				api_user=api_user,
				quota_per_unit=site.quota_per_unit,
			)
		if after is None:
			# A WAF in front of /api/user/self blocks us but not the browser (ADR-0010).
			# The login response the SPA kept is then the only reading there is, and it
			# often carries quota=0 — which is a missing number, not a zero balance.
			after = newapi.usd(user, site.quota_per_unit) or None
		bearer = f'Bearer {bearer}' if bearer else None
		# The site says whether today's bonus landed if it can; otherwise the balance
		# moving across the login is the only signal.
		checked_in = (
			bool(user['checked_in'])
			if 'checked_in' in user
			else (None if before is None or after is None else after > before)
		)
		if after is None and checked_in is None:
			return newapi.Outcome(False, error='浏览器登录成功，但既读不到余额也拿不到签到结果', session=session)
		return newapi.Outcome(
			success=True,
			checked_in=checked_in,
			before_quota=before,
			after_quota=after,
			session=session,
			access_token=bearer,
			api_user=api_user,
			username=user.get('username'),
		)

	async def check_in_many(self, account_ids: list[int]) -> dict[int, newapi.Outcome]:
		"""Concurrent — bounded by the semaphore, so 20 accounts take one round."""
		outcomes = await asyncio.gather(*(self.check_in(i) for i in account_ids))
		return dict(zip(account_ids, outcomes))

	async def probe(self, base_url: str) -> newapi.SiteInfo:
		"""What does this site support? Used by the add-account form so nothing
		about a site has to be hardcoded. Public data only — no credentials needed."""
		return await newapi.probe(base_url)

	async def bootstrap(self, account_id: int) -> str:
		"""Trade the stored session for a permanent password, so every later
		check-in is pure HTTP. Returns the site username."""
		account = self.store.get(account_id)
		if account is None:
			raise RuntimeError(f'账号 {account_id} 不存在')
		session = newapi.parse_session(account.session)
		if not session:
			raise RuntimeError('没有可用会话：先用浏览器登录一次，或把浏览器里的 session cookie 粘进来')
		username, password = await newapi.bootstrap_password(
			account.base_url, session, api_user=account.api_user
		)
		self.store.update(account_id, username=username, password=password)
		return username
