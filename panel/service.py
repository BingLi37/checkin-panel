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
import os
import re
import shutil
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from panel import newapi
from panel.store import Account, AccountStore

CONCURRENCY = 4
BROWSER_METHODS = ('linuxdo', 'github')  # keep in sync with browser_login.PROVIDER_BUTTONS
# Directories Chrome creates *inside* a profile. Their presence is what says a directory is
# a profile rather than a provider holding profiles — the only way to tell an old-layout
# leftover from a provider directory, since both are just a name under the root.
CHROME_OWN_DIRS = frozenset(
	{
		'Default',
		'ShaderCache',
		'GrShaderCache',
		'GraphiteDawnCache',
		'component_crx_cache',
		'extensions_crx_cache',
		'segmentation_platform',
	}
)


def _profile_root() -> Path:
	return Path(os.getenv('CHECKIN_BROWSER_PROFILE_DIR', '.browser_profiles'))


def _is_a_profile(path: Path) -> bool:
	return any(child.name in CHROME_OWN_DIRS for child in path.iterdir() if child.is_dir())


def _size(path: Path) -> int:
	return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())


@dataclass
class OrphanProfile:
	"""A browser profile on disk that no account claims.

	`provider` is None for an old-layout profile sitting directly under the root, which is
	also what makes `key` the right thing to send back: it is the path relative to the root
	either way, so one string addresses both shapes.
	"""

	name: str
	provider: Optional[str]
	bytes: int
	old_layout: bool

	@property
	def key(self) -> str:
		return f'{self.provider}/{self.name}' if self.provider else self.name
TURNSTILE_MISSING = re.compile(r'turnstile', re.I)
NOT_JSON = re.compile(r'非 JSON 响应')  # a WAF answered instead of the API


@dataclass
class CredentialCheck:
	"""What one verification of a stored credential learned.

	Not an `Outcome`: nothing was checked in. This says whether a credential authenticates,
	which is worth asking the moment one is pasted rather than a day later in a failed run.

	`ok=False` is not always the credential's fault, and the difference is the whole point of
	`needs_api_user`: a fork that validates the `new-api-user` header refuses a live session
	that lacks the account's id, and telling someone their cookie is dead sends them to fetch
	one that was never the problem (ADR-0010 is the same shape — a WAF can refuse a good
	credential, which is why a failed check is reported and not treated as an error).
	"""

	ok: bool
	kind: Optional[str] = None  # which cookie authenticated: 'session' | 'new_api_refresh'
	api_user: Optional[str] = None
	username: Optional[str] = None
	quota: Optional[float] = None
	needs_api_user: bool = False
	reason: Optional[str] = None


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

		async def visit_once():
			return await browser_visit(
				base_url=account.base_url,
				account_name=account.name,
				provider=account.login_method,
				username=account.username,
				password=account.password,
				headless=True,
			)

		visit = await visit_once()
		if not visit.after and account.login_method in BROWSER_METHODS:
			# The site session has lapsed and this account has no password to renew it with —
			# but the *IdP* session in the same profile normally outlives it by weeks, so the
			# renewal needs no human at all: run the OAuth hop, then visit again.
			#
			# Measured on anyrouter.top: one card minted this way carried 13 consecutive daily
			# runs (created 08-17, still the live cookie on 08-30, `updated` never touched),
			# because a `visit` run reuses the card and never mints one. Without this branch
			# that account went dark the day the card expired, reporting a password the owner
			# was never able to set — and the only fix was a hand-pressed button.
			renewed = await self._renew_visit_session(account)
			if renewed:
				visit = await visit_once()
		before, after = visit.before, visit.after
		if not after:
			# Do not blame a password the account may not even have: an OAuth identity gets
			# here whenever the profile's site session has lapsed *and* the OAuth renewal
			# above could not land either, and the fix for that is one visible browser
			# login, not a credential the owner never set.
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

	async def _renew_visit_session(self, account: Account) -> bool:
		"""Mint a fresh site session for a `visit` account from the IdP session it already has.

		Headless on purpose: the IdP cookie is in the profile, so the whole hop is redirects
		and a consent click — the two things an unattended run can do. A window would only be
		needed if the IdP session had lapsed too, and `browser_login` says so in its own words
		when that happens (it separates 'the IdP wants a human' from 'nothing is moving').

		Swallows the failure rather than raising: the caller has a second visit to attempt and
        a better message to report either way, and a renewal that could not land is not itself
		the thing the owner asked for. The credential is stored because it costs nothing here
		and the `endpoint` path would otherwise have to win it again.
		"""
		from panel.browser_login import browser_login

		try:
			result = await browser_login(
				base_url=account.base_url,
				provider=account.login_method,
				account_name=account.name,
				headless=True,
			)
		except Exception:
			return False
		if result.credential:
			self.store.update(
				account.id,
				session=result.credential,
				api_user=str(result.user.get('id') or '') or None,
			)
		return bool(result.credential)

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
			# Drop the stored access token: the browser hop just won a fresh session, and
			# `_client` sends both credentials at once — an `Authorization` header the site
			# rejects is fatal even beside a good cookie, because New API's middleware
			# answers "access token 无效" instead of falling back to the session (measured on
			# gorouter.app). A stale pasted token would otherwise block its own fallback.
			login = replace(self._login(account), session=session, api_user=api_user, access_token=None)
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

	async def verify_credential(
		self, account_id: int, candidates: Optional[list[newapi.PastedCredential]] = None
	) -> CredentialCheck:
		"""Does this account's stored credential authenticate? Persists what it learns.

		Called right after a credential is saved, so a bad paste is reported while whoever
		pasted it is still looking at the form rather than a day later in a failed run. Two
		things are written here on purpose:

		- **the rotated refresh cookie**, immediately. A JWT fork's refresh token is spent by
		  the exchange, so a verification that did not store the replacement would leave the
		  account holding a dead credential — the exact hazard `_attempt` guards against.
		- **`api_user`**, which is the id a fork wants back as the `new-api-user` header. It
		  arrives in the same response that proves the credential works, and the alternative
		  is asking someone to read it out of their browser's localStorage.

		`candidates` is every credential a paste contained; the probe decides which one this
		fork actually uses, and the winner replaces what was stored. Without it, whatever is
		already in the account is what gets checked.
		"""
		account = self.store.get(account_id)
		if account is None:
			return CredentialCheck(False, reason=f'账号 {account_id} 不存在')
		try:
			site = await newapi.probe(account.base_url)
		except Exception as e:
			return CredentialCheck(False, reason=f'无法访问站点，凭据没能验证：{newapi.why(e)}')

		value, kind = account.session, 'session'
		if candidates:
			# A JWT fork authenticates only with its refresh cookie and ignores any session
			# beside it; everywhere else it is the other way round. The paste cannot know
			# which — the probe can.
			wanted = newapi.REFRESH_COOKIE if site.refresh_path else 'session'
			picked = next((c for c in candidates if c.cookie_name == wanted), candidates[0])
			value, kind = picked.value, picked.cookie_name
			if value != account.session:
				self.store.update(account_id, session=value)
		# An access-token account authenticates with a header rather than a cookie, and which
		# of the two to check is decided by the **declared login method** — not by whichever
		# column happens to be non-empty. A session left over from an earlier paste must not
		# be what gets verified after someone switches the account over to a token.
		token_account = account.login_method == 'access_token'
		if token_account:
			value, kind = account.access_token, 'access_token'
		if not value:
			return CredentialCheck(
				False,
				kind=kind,
				reason='这个账号没有存访问令牌' if token_account else '这个账号没有存任何会话凭据',
			)

		access_token = account.access_token if token_account else None
		if kind == newapi.REFRESH_COOKIE and site.refresh_path:
			token, rotated, user = await newapi.refresh_access(account.base_url, value)
			if rotated:  # spent on exchange: store the replacement before anything else can fail
				self.store.update(account_id, session=rotated)
			if not token:
				return CredentialCheck(
					False,
					kind=kind,
					reason='这个 refresh 凭据换不到访问令牌：到站点重新登录，再导出一次 cookie',
				)
			access_token = f'Bearer {token}'
			data, reason = user or None, None
			if not data:
				data, reason = await newapi.authenticate(
					account.base_url, access_token=access_token, api_user=account.api_user
				)
		elif token_account:
			# Sent bare, not as a Bearer: New API compares the `Authorization` header against
			# the user's own access token. A wrong one comes back **200 with success=false**
			# on some forks, which is why `_self` weighs the body and not just the status.
			data, reason = await newapi.authenticate(
				account.base_url, access_token=access_token, api_user=account.api_user
			)
		else:
			data, reason = await newapi.authenticate(
				account.base_url, session=value, api_user=account.api_user
			)

		if data is None:
			if reason and newapi.NEEDS_API_USER.search(reason):
				# The credential is live; this fork also wants the account's own id, and
				# without it every authenticated route 401s. Saying "凭据无效" here would
				# send someone off to fetch a cookie that was never the problem.
				return CredentialCheck(
					False,
					kind=kind,
					needs_api_user=True,
					reason='凭据本身没问题，但这个站点还要账号的用户 id：把 API User 填上（站点页面 localStorage 里的 user.id）',
				)
			return CredentialCheck(False, kind=kind, reason=f'这个凭据登录不了：{reason or "站点没有说明原因"}')

		api_user = data.get('id')
		if api_user and str(api_user) != (account.api_user or ''):
			self.store.update(account_id, api_user=str(api_user))
		return CredentialCheck(
			True,
			kind=kind,
			api_user=str(api_user) if api_user else None,
			username=data.get('username'),
			quota=newapi.usd(data, site.quota_per_unit),
		)

	async def inject_idp_cookies(self, account_id: int, raw: str, *, verify: bool = True) -> 'IdpInjection':
		"""Put an IdP session into this account's browser profile, so the daily headless
		OAuth hop needs no human.

		This is the server-deployment answer for an OAuth-only account: the site session in
		the database is short-lived and renewed by that hop, while the *IdP* session it
		renews from normally gets there through one visible window (ADR-0009) — which is the
		one thing a headless box cannot offer. Injecting cookie values supplies it instead.

		Copying a whole profile directory does not work and this does: Windows keeps the
		profile's cookie-encryption key in `Local State`, DPAPI-bound to the Windows account,
		so the file is unreadable elsewhere. Cookie *values* carry no such key — the
		receiving browser encrypts them with its own.
		"""
		from panel.browser_login import inject_idp_cookies as inject

		account = self.store.get(account_id)
		if account is None:
			raise RuntimeError(f'账号 {account_id} 不存在')
		if account.login_method not in BROWSER_METHODS:
			raise RuntimeError(
				f'只有授权登录的账号需要注入 IdP 会话（当前是 {account.login_method}）：'
				'密码 / 会话 Cookie 账号直接粘站点自己的 cookie 就够了'
			)
		result = await inject(
			raw, provider=account.login_method, account_name=account.name, base_url=account.base_url, verify=verify
		)
		if result.credential:
			# The verification logged in for real, so it came back with a live site session
			# and the account's id. Storing them makes the account usable now rather than
			# after the next scheduled run.
			self.store.update(account_id, session=result.credential, api_user=str(result.api_user or '') or None)
		return result

	def delete(self, account_id: int, *, forget_profile: bool = True) -> bool:
		"""Delete an account, and by default the browser profile that went with it.

		Returns whether a profile was actually removed.

		The profile is not a cache. It holds the *IdP* session — the whole github.com or
		linux.do login — and until this existed, deleting an account left that on disk with
		nothing on any screen naming the directory. The database row was the only thing the
		delete button removed, so the credential outlived the account it belonged to.

		Still a flag rather than unconditional, because the two mistakes are not the same
		size: a profile deleted by accident costs one visible login, while a profile kept by
		accident is a live session nobody is looking after. So the default is to delete, and
		the caller is asked. Order matters — the row goes first, so a profile that cannot be
		removed (a browser still holding a file open on Windows) cannot leave an account that
		is half deleted.
		"""
		account = self.store.get(account_id)
		if account is None:
			raise RuntimeError(f'账号 {account_id} 不存在')
		self.store.delete(account_id)
		if not forget_profile:
			return False
		from panel.browser_login import forget_profile as forget

		return forget(account.name, account.login_method)

	def orphan_profiles(self) -> list['OrphanProfile']:
		"""Browser profiles under the root that no account claims any more.

		Two shapes accumulate and only one is an account's. A profile *directory* sits at
		`<root>/<provider>/<name>`, so anything at `<root>/<name>` is from an older layout
		that keyed profiles by site instead — told apart by Chrome's own subdirectory names,
		because a provider directory contains accounts while a profile contains `Default`.
		Both are listed; neither is deleted here. Reporting and deleting are separate calls
		on purpose: the matching rule is mine, the decision is the owner's, and `rmtree` is
		not where a guess belongs.
		"""
		from panel.browser_login import profile_name

		root = _profile_root()
		if not root.is_dir():
			return []
		claimed = {(a.login_method, profile_name(a.name)) for a in self.store.list()}
		found: list[OrphanProfile] = []
		for entry in sorted(root.iterdir()):
			if not entry.is_dir():
				continue
			if _is_a_profile(entry):  # old layout: the profile itself, keyed by nothing we use
				found.append(OrphanProfile(entry.name, None, _size(entry), old_layout=True))
				continue
			for child in sorted(entry.iterdir()):
				if child.is_dir() and (entry.name, child.name) not in claimed:
					found.append(OrphanProfile(child.name, entry.name, _size(child), old_layout=False))
		return found

	def delete_orphan_profiles(self, names: list[str]) -> int:
		"""Delete the named orphans — `<provider>/<name>`, or `<name>` for an old-layout one.

		Takes what to delete rather than re-deriving it, so what the owner saw listed is
		what goes. A name that is no longer an orphan (an account was added back under it
		meanwhile) is skipped rather than deleted, because the list it came from is a
		snapshot and this is `rmtree`.
		"""
		root = _profile_root()
		live = {o.key for o in self.orphan_profiles()}
		removed = 0
		for key in names:
			if key not in live:
				continue
			target = (root / key).resolve()
			if root.resolve() not in target.parents or not target.is_dir():
				continue
			shutil.rmtree(target)
			removed += 1
		return removed

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
