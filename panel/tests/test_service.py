"""CheckInService + scheduler tests — newapi.check_in is stubbed, DB is real."""
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from panel import newapi, scheduler
from panel.service import CheckInService, window_start
from panel.store import Account, AccountStore


@pytest.fixture
def store():
	with tempfile.TemporaryDirectory() as tmpdir:
		yield AccountStore(Path(tmpdir) / 'test.db')


@pytest.fixture
def service(store):
	return CheckInService(store)


@pytest.fixture
def stub(monkeypatch):
	"""Replace the protocol client with a recorder returning a canned Outcome."""
	calls: list[newapi.Login] = []
	box = {
		'outcome': newapi.Outcome(True, True, 1.0, 1.25, session='fresh', username='alice'),
		'site': newapi.SiteInfo(base_url='https://x.test'),
		'checkin_log': None,  # what the site's quota log reports, epoch seconds
		'status': newapi.CheckinStatus(),  # what a status route reports; all-None = no route
	}

	async def check_in(login, site=None):
		calls.append(login)
		if isinstance(box['outcome'], Exception):
			raise box['outcome']
		return box['outcome']

	async def probe(base_url):
		return box['site']

	async def last_checkin_at(base_url, **_):
		return box['checkin_log']

	async def checkin_status(base_url, path, **_):
		return box['status']

	monkeypatch.setattr(newapi, 'check_in', check_in)
	monkeypatch.setattr(newapi, 'probe', probe)
	monkeypatch.setattr(newapi, 'last_checkin_at', last_checkin_at)
	monkeypatch.setattr(newapi, 'checkin_status', checkin_status)
	return calls, box


@pytest.fixture
def browser(monkeypatch):
	"""Stub the browser hop and the balance reads around it."""
	import panel.browser_login as browser_login

	calls: dict = {}
	balances = [1.0, 1.25]  # before, after

	async def fake_login(*, base_url, provider, account_name, headless):
		calls.update(base_url=base_url, provider=provider, account_name=account_name, headless=headless)
		return browser_login.BrowserLogin('oauth-session', {'id': 4242, 'username': 'alice', 'quota': 1_000_000})

	async def fake_balance(base_url, *, session=None, access_token=None, api_user=None, quota_per_unit=0.0):
		return balances.pop(0) if balances else None

	monkeypatch.setattr(browser_login, 'browser_login', fake_login)
	monkeypatch.setattr(newapi, 'balance', fake_balance)
	return calls, balances


async def test_check_in_records_result_and_persists_new_session(service, store, stub):
	calls, _ = stub
	account = store.create(name='A', base_url='https://x.test', password='pw')

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.delta) == (True, 0.25)
	assert calls[0].base_url == 'https://x.test'
	saved = store.get(account.id)
	assert (saved.session, saved.username) == ('fresh', 'alice')
	assert (saved.last_success, saved.last_checked_in, saved.last_quota) == (True, True, 1.25)


async def test_check_in_keeps_an_existing_username(service, store, stub):
	account = store.create(name='A', base_url='https://x.test', username='mine', password='pw')
	await service.check_in(account.id)
	assert store.get(account.id).username == 'mine'


async def test_check_in_survives_an_exploding_client(service, store, stub):
	_, box = stub
	box['outcome'] = RuntimeError('boom')
	account = store.create(name='A', base_url='https://x.test', password='pw')

	outcome = await service.check_in(account.id)

	assert outcome.success is False and outcome.error == 'RuntimeError: boom'
	assert store.get(account.id).last_error == 'RuntimeError: boom'


async def test_check_in_rejects_unknown_and_urlless_accounts(service, store, stub):
	blank = store.create(name='A', base_url='')
	assert (await service.check_in(9999)).error == '账号 9999 不存在'
	assert 'Base URL' in (await service.check_in(blank.id)).error


async def test_check_in_many_runs_every_account(service, store, stub):
	ids = [store.create(name=n, base_url='https://x.test', password='pw').id for n in 'ABC']

	results = await service.check_in_many(ids)

	assert set(results) == set(ids)
	assert all(o.success for o in results.values())


async def test_browser_fallback_logs_in_when_the_protocol_cannot(service, store, stub, browser):
	"""OAuth-only account on a login_bonus site: the browser login *is* the check-in,
	and the balance around it is the only signal that the bonus landed."""
	_, box = stub
	box['outcome'] = newapi.Outcome(False, error='该站点靠"重新登录"发放额度')
	calls, _ = browser
	account = store.create(name='A', base_url='https://x.test', login_method='github', session='old')

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.checked_in, outcome.delta) == (True, True, 0.25)
	assert calls == {
		'base_url': 'https://x.test',
		'provider': 'github',
		'account_name': 'A',
		'headless': True,
	}
	saved = store.get(account.id)
	assert saved.session == 'oauth-session'
	assert saved.api_user == '4242', 'the id the SPA sends is part of the session, not optional'
	assert (saved.last_success, saved.last_quota) == (True, 1.25)


async def test_browser_fallback_reports_no_gain_as_already_checked_in(service, store, stub, browser):
	_, box = stub
	box['outcome'] = newapi.Outcome(False, error='nope')
	_, balances = browser
	balances[:] = [1.25, 1.25]
	account = store.create(name='A', base_url='https://x.test', login_method='linuxdo', session='old')

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.checked_in, outcome.delta) == (True, False, 0.0)


async def test_browser_fallback_still_posts_the_check_in_route(service, store, stub, browser):
	"""On an endpoint site the login only gets us in — the check-in is still a POST."""
	calls, box = stub
	box['outcome'] = newapi.Outcome(False, error='凭据无效')
	box['site'] = newapi.SiteInfo(base_url='https://x.test', checkin_path='/api/user/checkin')
	account = store.create(name='A', base_url='https://x.test', login_method='github', session='old')

	await service.check_in(account.id)

	assert len(calls) == 2, 'protocol attempt, then a retry with the fresh session'
	assert calls[1].session == 'oauth-session'


async def test_no_browser_for_password_accounts(service, store, stub, browser):
	_, box = stub
	box['outcome'] = newapi.Outcome(False, error='密码错误')
	calls, _ = browser
	account = store.create(name='A', base_url='https://x.test', username='a', password='pw')

	outcome = await service.check_in(account.id)

	assert outcome.error == '密码错误' and calls == {}, 'a wrong password is not a browser problem'


async def test_bootstrap_stores_the_minted_password(service, store, monkeypatch):
	async def bootstrap_password(base_url, session, *, api_user=None, password=None):
		assert (base_url, session) == ('https://x.test', 'sess')
		return 'alice', 'minted'

	monkeypatch.setattr(newapi, 'bootstrap_password', bootstrap_password)
	account = store.create(name='A', base_url='https://x.test', login_method='linuxdo', session='sess')

	assert await service.bootstrap(account.id) == 'alice'
	saved = store.get(account.id)
	assert (saved.username, saved.password, saved.has_password) == ('alice', 'minted', True)
	assert saved.login_method == 'linuxdo', 'the account must remember which IdP to recover with'


async def test_bootstrap_without_a_session_explains_what_to_do(service, store):
	account = store.create(name='A', base_url='https://x.test')
	with pytest.raises(RuntimeError, match='浏览器登录'):
		await service.bootstrap(account.id)


async def test_one_run_at_a_time_per_account(service, store, stub):
	"""Two runs of one account would fight over its browser profile — and pressing
	签到 while the scheduler is already on it used to just wait in silence."""
	import asyncio

	_, box = stub
	account = store.create(name='A', base_url='https://x.test', username='a', password='pw')
	started = asyncio.Event()
	release = asyncio.Event()

	async def slow_check_in(login, site=None):
		started.set()
		await release.wait()
		return newapi.Outcome(True, True, 1.0, 1.25)

	box['outcome'] = newapi.Outcome(True)
	monkeypatch = pytest.MonkeyPatch()
	monkeypatch.setattr(newapi, 'check_in', slow_check_in)
	try:
		first = asyncio.create_task(service.check_in(account.id))
		await started.wait()
		second = await service.check_in(account.id)
		assert second.success is False and '正在签到' in second.error
		assert store.get(account.id).last_error is None, 'the refusal must not overwrite the real state'
		release.set()
		assert (await first).success is True
	finally:
		monkeypatch.undo()

	assert (await service.check_in(account.id)).success is True, 'and the lock is released afterwards'


async def test_browser_fallback_falls_back_to_the_balance_the_browser_already_read(service, store, stub, browser):
	"""A WAF in front of /api/user/self blocks the panel but not the SPA, whose
	localStorage still holds the quota it rendered its own page with (ADR-0010)."""
	_, box = stub
	box['outcome'] = newapi.Outcome(False, error='凭据无效')
	_, balances = browser
	balances[:] = []  # every HTTP read answers None
	account = store.create(name='A', base_url='https://x.test', login_method='github', session='old')

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.after_quota) == (True, 2.0), '1_000_000 quota / 500_000 per unit'
	assert store.get(account.id).last_quota == 2.0


async def test_browser_fallback_reports_a_check_in_it_cannot_price(service, store, stub, browser, monkeypatch):
	"""Under the WAF the login response is all we get, and its quota is 0 — a missing
	number, not a zero balance. The check-in still happened, so say so and keep the
	last balance we did read."""
	import panel.browser_login as browser_login

	_, box = stub
	box['outcome'] = newapi.Outcome(False, error='凭据无效')
	_, balances = browser
	balances[:] = []  # every HTTP read answers None

	async def login_without_a_quota(**_):
		return browser_login.BrowserLogin('oauth-session', {'id': 4242, 'username': 'alice', 'quota': 0, 'checked_in': True})

	monkeypatch.setattr(browser_login, 'browser_login', login_without_a_quota)
	account = store.create(name='A', base_url='https://x.test', login_method='github', session='old')
	store.record_result(account.id, success=True, quota=7.5)

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.checked_in, outcome.after_quota) == (True, True, None)
	assert store.get(account.id).last_quota == 7.5, 'an unknown balance must not blank the known one'


async def test_the_quota_log_has_the_last_word(service, store, stub):
	"""The site's own ledger outranks our read-back: the bonus can land while the
	confirmation fails, and a login response's `checked_in` stays true all day."""
	_, box = stub
	account = store.create(name='A', base_url='https://x.test', username='a', password='pw')

	box['outcome'] = newapi.Outcome(False, error='浏览器登录成功，但读不到余额')
	box['checkin_log'] = time.time() + 1  # written during this very run
	outcome = await service.check_in(account.id)
	assert (outcome.success, outcome.checked_in, outcome.error) == (True, True, None), 'credited anyway'

	box['outcome'] = newapi.Outcome(True, True)
	box['checkin_log'] = window_start(store.get(account.id)) + 1  # today, but an earlier run got it
	assert (await service.check_in(account.id)).checked_in is False, 'that is 今日已签到, not 签到成功'

	box['outcome'] = newapi.Outcome(True, True)
	box['checkin_log'] = window_start(store.get(account.id)) - 3600  # yesterday, and nothing since
	outcome = await service.check_in(account.id)
	assert outcome.success is False and '没有签到记录' in outcome.error, 'a claim the ledger denies'

	box['checkin_log'] = None  # a fork whose log we cannot read keeps the attempt's word
	box['outcome'] = newapi.Outcome(True, True)
	assert (await service.check_in(account.id)).checked_in is True


async def test_a_turnstile_gated_route_gets_a_token_after_the_login_too(service, store, stub, browser, monkeypatch):
	"""Even after a browser login the token has to come from a fresh context: Cloudflare
	will not render its widget for a profile that just went through an OAuth round trip."""
	import panel.browser_login as browser_login

	calls, box = stub
	box['outcome'] = newapi.Outcome(False, error='Turnstile token 为空')
	box['site'] = newapi.SiteInfo(
		base_url='https://x.test',
		checkin_path='/api/user/checkin',
		turnstile=True,
		turnstile_key='0xSITEKEY',
	)

	async def with_token(**kw):
		return browser_login.BrowserLogin('oauth-session', {'id': 7})

	minted = []

	async def mint(**kw):
		minted.append(kw['sitekey'])
		return 'ts-token' if len(minted) > 1 else None  # the first attempt is refused

	monkeypatch.setattr(browser_login, 'mint_turnstile', mint)
	monkeypatch.setattr(browser_login, 'browser_login', with_token)
	account = store.create(name='A', base_url='https://x.test', login_method='github', session='old')

	await service.check_in(account.id)

	assert minted == ['0xSITEKEY', '0xSITEKEY'], 'once before the login, once after'
	assert calls[-1].turnstile == 'ts-token', 'the retry must carry it, or the site refuses again'


async def test_no_turnstile_token_is_our_failure_to_report(service, store, stub, browser, monkeypatch):
	"""Cloudflare does not always render for this browser. Reporting the site's own
	`Turnstile token 为空` back would read like a panel bug, so say what happened."""
	import panel.browser_login as browser_login

	_, box = stub
	box['outcome'] = newapi.Outcome(False, error='Turnstile token 为空')
	box['site'] = newapi.SiteInfo(
		base_url='https://x.test', checkin_path='/api/user/checkin', turnstile=True, turnstile_key='0xK'
	)

	async def no_token(**_):
		return browser_login.BrowserLogin('oauth-session', {'id': 7})

	async def no_mint(**_):
		return None  # the mint-only path declines, so the login path is what runs

	monkeypatch.setattr(browser_login, 'mint_turnstile', no_mint)
	monkeypatch.setattr(browser_login, 'browser_login', no_token)
	account = store.create(name='A', base_url='https://x.test', login_method='github', session='old')

	outcome = await service.check_in(account.id)

	assert outcome.success is False and 'Cloudflare 没给' in outcome.error
	assert store.get(account.id).session == 'oauth-session', 'the login still counted'


async def test_a_missing_turnstile_token_borrows_a_browser_for_that_alone(service, store, stub, monkeypatch):
	"""The credential is fine — only the token is missing. Minting one costs a browser but
	not a login, which is both faster and immune to the IdP wanting a human."""
	import panel.browser_login as browser_login

	calls, box = stub
	box['site'] = newapi.SiteInfo(
		base_url='https://x.test', checkin_path='/api/user/checkin', turnstile=True, turnstile_key='0xK'
	)
	attempts = []

	async def check_in(login, site=None):
		attempts.append(login)
		if login.turnstile:
			return newapi.Outcome(True, True, 1.0, 1.25, session='rotated-2')
		return newapi.Outcome(False, error='Turnstile token 为空', session='rotated-1')

	async def mint(**kw):
		assert kw['sitekey'] == '0xK'
		return 'fresh-token'

	monkeypatch.setattr(newapi, 'check_in', check_in)
	monkeypatch.setattr(browser_login, 'mint_turnstile', mint)
	account = store.create(name='A', base_url='https://x.test', login_method='github', session='r0')

	outcome = await service.check_in(account.id)

	assert outcome.success is True and outcome.checked_in is True
	assert attempts[1].turnstile == 'fresh-token'
	assert attempts[1].session == 'rotated-1', 'the retry must use the token the first attempt rotated to'


async def test_a_rotated_credential_survives_a_later_failure(service, store, stub, browser, monkeypatch):
	"""A JWT fork's refresh token dies the moment it is used. If the run then fails and
	we only store on the way out, the account is left holding the spent one — 凭据无效 on
	every run after, until someone logs in by hand."""
	import panel.browser_login as browser_login

	_, box = stub
	box['outcome'] = newapi.Outcome(False, error='凭据无效', session='rotated')

	async def explode(**_):
		raise RuntimeError('IdP wants a human')

	monkeypatch.setattr(browser_login, 'browser_login', explode)
	monkeypatch.setattr(browser_login, 'mint_turnstile', explode)
	account = store.create(name='A', base_url='https://x.test', login_method='github', session='spent')

	outcome = await service.check_in(account.id)

	assert outcome.success is False
	assert store.get(account.id).session == 'rotated', 'the live credential must outlive the failed run'


async def test_a_waf_blocked_password_account_visits_in_a_browser(service, store, stub, monkeypatch):
	"""anyrouter.top answers even its login route with a WAF challenge, so a password
	account gets a browser too — and the site's own ledger, not the login, says whether
	the bonus landed."""
	import panel.browser_login as browser_login

	_, box = stub
	box['outcome'] = newapi.Outcome(False, error='登录失败: HTTP 200 (非 JSON 响应)')
	visits = []

	async def visit(**kw):
		visits.append(kw)
		user = {'id': 216481, 'username': 'someone', 'quota': 87_500_000}
		return browser_login.BrowserVisit(
			before=None, after=user, session='sess', checkin_at=box['checkin_log']
		)

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	account = store.create(
		name='A', base_url='https://x.test', username='someone', password='pw', checkin_after='08:30'
	)

	box['checkin_log'] = 0.0  # a readable ledger that has never recorded a check-in
	outcome = await service.check_in(account.id)
	assert (outcome.success, outcome.checked_in) == (False, False)
	assert '没有签到记录' in outcome.error, 'logging in is not collecting'
	assert visits[0]['username'] == 'someone'

	box['checkin_log'] = time.time() + 1  # ...and one that just recorded this run
	outcome = await service.check_in(account.id)
	assert (outcome.success, outcome.checked_in, outcome.after_quota) == (True, True, 175.0)


async def test_the_visit_mechanism_needs_no_receipt(service, store, stub, monkeypatch):
	"""anyrouter.top runs its own check-in when an authenticated page loads and writes
	nothing to the quota log, so an empty ledger is not evidence of failure — and there is
	no protocol attempt to make first."""
	import panel.browser_login as browser_login

	calls, _ = stub
	visited = []

	async def visit(**kw):
		visited.append(kw['base_url'])
		return browser_login.BrowserVisit(
			before=None, after={'id': 7, 'quota': 87_500_000}, session='sess', checkin_at=0.0
		)  # ledger: nothing, ever

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	account = store.create(
		name='A', base_url='https://x.test', username='a', password='pw',
		mechanism='visit', checkin_after='08:30',
	)

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.checked_in, outcome.after_quota) == (True, None, 175.0)
	assert outcome.error is None, 'a silent ledger is how this site works, not a failure'
	assert calls == [], 'and nothing was POSTed or re-logged-in first'
	assert visited == ['https://x.test']


async def test_a_declared_visit_still_posts_a_route_the_probe_can_see(service, store, stub, monkeypatch):
	"""`visit` declares what probing cannot see — so a route it *can* see outranks it.

	api.justwoker.icu signs in from its own UI (which is why the owner picks 打开页面即签到)
	and still registers `/api/user/checkin`. The browser is a dead end there: it is a JWT
	fork, so the in-page reads need a Bearer token, and an OAuth account with no password
	never reaches a logged-in state — while the protocol path handles it in three requests.
	"""
	import panel.browser_login as browser_login

	calls, box = stub
	box['site'] = newapi.SiteInfo(base_url='https://x.test', checkin_path='/api/user/checkin')
	visited = []

	async def visit(**kw):
		visited.append(kw['base_url'])
		raise AssertionError('the browser must not be used where a route answers')

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	account = store.create(
		name='A', base_url='https://x.test', login_method='github', session='refresh-cookie',
		mechanism='visit', checkin_after='00:01',
	)

	outcome = await service.check_in(account.id)

	assert outcome.success and visited == []
	assert [c.session for c in calls] == ['refresh-cookie'], 'the protocol path ran instead'


async def test_a_visit_with_no_password_is_told_to_log_in_again(service, store, stub, monkeypatch):
	"""An OAuth account gets here when the profile's site session has lapsed. Blaming a
	password it never had sent the owner looking for a WAF that was not there."""
	import panel.browser_login as browser_login

	async def visit(**_):
		return browser_login.BrowserVisit(before=None, after=None, session=None, checkin_at=None)

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	account = store.create(
		name='A', base_url='https://x.test', login_method='github', mechanism='visit',
		session='dead', checkin_after='00:01',
	)

	outcome = await service.check_in(account.id)

	assert outcome.success is False
	assert '浏览器登录' in outcome.error and '密码' not in outcome.error


async def test_a_visit_cannot_claim_today_is_done_when_the_bonus_beat_the_measurement(
	service, store, stub, monkeypatch
):
	"""The 假签到 report. On anyrouter.top the SPA posts the check-in itself on mount, so
	without holding that POST the bonus lands during the very page load that produces the
	`before` reading: before == after on every run, forever, reported as 今日已签到 — a claim
	nothing established. Unheld and unmoved is 'cannot say' (已重新登录)."""
	import panel.browser_login as browser_login

	collected = {'id': 216481, 'quota': 127_318_741, 'used_quota': 10_181_259}  # measured, $275 total

	async def visit(**_):
		return browser_login.BrowserVisit(  # both readings post-bonus, and no ledger
			before=dict(collected), after=dict(collected), session='sess', checkin_at=None,
			checkin_path='/api/user/sign_in', receipt={'success': True, 'message': ''},
			held=False,  # the interception did not take: the page load already collected it
		)

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	account = store.create(
		name='any-20220402125', base_url='https://anyrouter.top', username='u', password='p',
		mechanism='visit', checkin_after='08:30',
	)

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.after_quota) == (True, 254.64)
	assert outcome.checked_in is None, 'an unmeasured bonus is not proof it already landed'
	assert store.get(account.id).last_checked_in is None


async def test_a_held_check_in_that_grants_nothing_really_is_already_done(service, store, stub, monkeypatch):
	"""With the SPA's POST held back, `before` is genuinely pre-bonus — so nothing moving
	across our own deliberate POST does mean today was already collected. That is the one
	case where 今日已签到 is earned rather than assumed."""
	import panel.browser_login as browser_login

	collected = {'id': 216481, 'quota': 127_318_741, 'used_quota': 10_181_259}

	async def visit(**_):
		return browser_login.BrowserVisit(
			before=dict(collected), after=dict(collected), session='sess', checkin_at=None,
			checkin_path='/api/user/sign_in', receipt={'success': True, 'message': ''}, held=True,
		)

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	account = store.create(
		name='A', base_url='https://anyrouter.top', username='u', password='p',
		mechanism='visit', checkin_after='08:30',
	)

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.checked_in) == (True, False), 'earned 今日已签到'


async def test_a_visit_prices_the_bonus_across_the_deliberate_check_in(service, store, stub, monkeypatch):
	"""And when the SPA's POST is held back, the balance across the deliberate one is a
	real before/after — so the $25 daily bonus can finally be reported as 签到成功."""
	import panel.browser_login as browser_login

	after = {'id': 216481, 'quota': 127_318_741, 'used_quota': 10_181_259}
	before = {**after, 'quota': after['quota'] - 25 * 500_000}

	async def visit(**_):
		return browser_login.BrowserVisit(
			before=before, after=after, session='sess', checkin_at=None,
			checkin_path='/api/user/sign_in', receipt={'success': True, 'message': ''}, held=True,
		)

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	account = store.create(
		name='A', base_url='https://anyrouter.top', username='u', password='p',
		mechanism='visit', checkin_after='08:30',
	)

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.checked_in, outcome.delta) == (True, True, 25.0)


async def test_a_visit_sees_a_bonus_that_was_spent_in_the_same_run(service, store, stub, monkeypatch):
	"""quota+used_quota is what only a grant can raise: an account that collected $25 and
	burned $25 on Claude in the same window has an unchanged balance and a $25 bigger
	total. Comparing bare quota reported that perfectly good check-in as 已重新登录."""
	import panel.browser_login as browser_login

	before = {'id': 7, 'quota': 100 * 500_000, 'used_quota': 10 * 500_000}
	after = {'id': 7, 'quota': 100 * 500_000, 'used_quota': 35 * 500_000}  # +$25 granted, $25 spent

	async def visit(**_):
		return browser_login.BrowserVisit(
			before=before, after=after, session='sess', checkin_at=None, receipt={'success': True}
		)

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	account = store.create(
		name='A', base_url='https://anyrouter.top', username='u', password='p', mechanism='visit'
	)

	outcome = await service.check_in(account.id)

	assert outcome.checked_in is True, 'the total grew by $25, so the bonus landed'
	assert outcome.delta == 0.0, 'even though the balance itself did not move'


async def test_a_visit_reports_a_route_that_refused(service, store, stub, monkeypatch):
	"""The check-in route answering `success: false` is the one hard signal this site
	gives. Reporting it as success would be the same lie in the other direction."""
	import panel.browser_login as browser_login

	user = {'id': 7, 'quota': 50 * 500_000, 'used_quota': 0}

	async def visit(**_):
		return browser_login.BrowserVisit(
			before=dict(user), after=dict(user), session='sess', checkin_at=None,
			checkin_path='/api/user/sign_in', receipt={'success': False, 'message': '用户已被封禁'},
		)

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	account = store.create(
		name='A', base_url='https://anyrouter.top', username='u', password='p', mechanism='visit'
	)

	outcome = await service.check_in(account.id)

	assert outcome.success is False and '用户已被封禁' in outcome.error
	assert store.get(account.id).last_quota == 50.0, 'the balance it did read is still worth keeping'


async def test_a_status_route_outranks_the_quota_log(service, store, stub):
	"""sotamodel.net answers "did today's bonus land" directly. That is the site naming its
	own day, so it settles the question — and it is checked *instead of* the quota log,
	which on such a fork records nothing a check-in would match."""
	_, box = stub
	box['site'] = newapi.SiteInfo(
		base_url='https://x.test',
		checkin_path='/api/user/sota-agent-checkin',
		status_path='/api/user/sota-agent-checkin',
	)
	box['checkin_log'] = 0.0  # a readable ledger that has never recorded a check-in...
	box['status'] = newapi.CheckinStatus(today=True, awarded_today=100.0)
	box['outcome'] = newapi.Outcome(False, error='读不到余额')
	account = store.create(name='A', base_url='https://x.test', username='a', password='pw')

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.error) == (True, None), '...loses to the site saying it landed'
	assert outcome.awarded == 100.0, 'and the status route prices it'
	assert store.get(account.id).last_success is True


async def test_a_status_route_denying_today_fails_the_run(service, store, stub):
	"""The same authority in the other direction: claiming success when the site says
	nothing was collected is the 假签到 this whole layer exists to prevent."""
	_, box = stub
	box['site'] = newapi.SiteInfo(
		base_url='https://x.test',
		checkin_path='/api/user/sota-agent-checkin',
		status_path='/api/user/sota-agent-checkin',
	)
	box['status'] = newapi.CheckinStatus(today=False)
	box['outcome'] = newapi.Outcome(True, True, 1.0, 1.0)
	account = store.create(name='A', base_url='https://x.test', username='a', password='pw')

	outcome = await service.check_in(account.id)

	assert outcome.success is False and '还没有签到记录' in outcome.error


async def test_a_status_route_that_cannot_say_falls_back_to_the_quota_log(service, store, stub):
	"""An all-None status is 'cannot say', not 'no' — so the ledger still gets its turn."""
	_, box = stub
	box['site'] = newapi.SiteInfo(
		base_url='https://x.test',
		checkin_path='/api/user/checkin',
		status_path='/api/user/checkin',
	)
	box['status'] = newapi.CheckinStatus()  # the route answered nothing usable
	box['checkin_log'] = time.time() + 1  # but the ledger recorded this very run
	box['outcome'] = newapi.Outcome(False, error='读不到余额')
	account = store.create(name='A', base_url='https://x.test', username='a', password='pw')

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.checked_in) == (True, True), 'the ledger rescued it'


async def test_a_site_without_a_status_route_never_asks_for_one(service, store, stub, monkeypatch):
	"""Every existing site has no status route, and must not pay a request for it."""
	_, box = stub
	box['site'] = newapi.SiteInfo(base_url='https://x.test', checkin_path='/api/user/checkin')
	asked = []

	async def checkin_status(base_url, path, **_):
		asked.append(path)
		return newapi.CheckinStatus()

	monkeypatch.setattr(newapi, 'checkin_status', checkin_status)
	account = store.create(name='A', base_url='https://x.test', username='a', password='pw')

	await service.check_in(account.id)

	assert asked == [], 'no status_path means no status request'


def _account(**kw) -> Account:
	return Account(id=1, name='A', base_url='https://x.test', **kw)


def test_scheduler_due_needs_credentials_and_a_url():
	assert scheduler.due(_account(password='pw', username='alice')) is True
	assert scheduler.due(_account(access_token='tok')) is True
	assert scheduler.due(_account(session='s')) is True
	assert scheduler.due(_account()) is False, 'nothing to authenticate with'
	assert scheduler.due(_account(password='pw', username='alice', enabled=False)) is False
	assert scheduler.due(Account(id=1, name='A', base_url='', session='s')) is False


def test_scheduler_due_is_once_per_window_and_backs_off_on_failure():
	yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
	now = datetime.now(timezone.utc)
	done = dict(session='s', last_success=True, last_run_at=now.isoformat())

	assert scheduler.due(_account(**done)) is False, 'collected already: leave it alone'
	assert scheduler.due(_account(**{**done, 'last_run_at': yesterday})) is True, 'a new window'
	assert scheduler.due(_account(session='s', last_success=True, last_run_at='not-a-date')) is True

	# A failure is retried, but not on every tick: 30min, then 1h, 2h, 4h. The window has
	# to be an old one for that to matter — a fresh window always gets its attempt — so
	# pin it to a minute from now, which puts its opening a whole day back.
	soon = (datetime.now().astimezone() + timedelta(minutes=1)).strftime('%H:%M')
	failing = dict(session='s', last_success=False, checkin_after=soon)

	assert scheduler.due(_account(**failing, failures=1, last_run_at=now.isoformat())) is False
	assert scheduler.due(
		_account(**failing, failures=1, last_run_at=(now - timedelta(minutes=31)).isoformat())
	) is True
	assert scheduler.due(
		_account(**failing, failures=4, last_run_at=(now - timedelta(hours=3)).isoformat())
	) is False, 'four failures in a row means the next look is hours away'
	assert scheduler.backoff_s(9) == scheduler.MAX_BACKOFF_S, 'and it stops widening at the cap'


def test_a_site_whose_day_starts_at_0830_is_not_due_all_night():
	"""anyrouter.top opens its bonus at 08:30, so the day's boundary is 08:30 — a run that
	succeeded at 09:00 is still the current window at 02:00, and the account must not
	churn all night."""
	noon = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
	late = _account(session='s', checkin_after='08:30')
	early = _account(session='s', checkin_after='08:30')

	assert window_start(late, noon) == noon.replace(hour=8, minute=30).timestamp()
	assert window_start(late, noon.replace(hour=2)) == (
		noon.replace(hour=8, minute=30) - timedelta(days=1)
	).timestamp(), 'before it opens, the current window is yesterday morning'
	assert window_start(_account(session='s'), noon) == noon.replace(hour=0).timestamp(), 'default is midnight'
	assert window_start(_account(session='s', checkin_after='nonsense'), noon) == noon.replace(hour=0).timestamp()

	opened = window_start(late)
	inside = datetime.fromtimestamp(opened + 60).astimezone().isoformat()
	before = datetime.fromtimestamp(opened - 60).astimezone().isoformat()
	assert scheduler.due(_account(session='s', checkin_after='08:30', last_success=True, last_run_at=inside)) is False
	assert scheduler.due(_account(session='s', checkin_after='08:30', last_success=True, last_run_at=before)) is True


@pytest.fixture
def auth(monkeypatch):
	"""Stub the two calls a credential check makes: authenticate, and the JWT exchange."""
	box = {
		'user': {'id': 4242, 'username': 'alice', 'quota': 1_000_000},
		'reason': None,
		'refresh': ('token-1', 'rotated-cookie', {}),  # (access token, rotated cookie, user)
	}
	seen: dict = {}

	async def authenticate(base_url, *, session=None, access_token=None, api_user=None):
		seen.update(session=session, access_token=access_token, api_user=api_user)
		return box['user'], box['reason']

	async def refresh_access(base_url, refresh):
		seen['spent'] = refresh
		return box['refresh']

	monkeypatch.setattr(newapi, 'authenticate', authenticate)
	monkeypatch.setattr(newapi, 'refresh_access', refresh_access)
	return box, seen


async def test_verifying_a_paste_stores_the_id_the_site_wants_back(store, service, stub, auth):
	"""`api_user` arrives in the response that proves the credential works, so nobody
	should have to read it out of their own localStorage."""
	_, seen = auth
	account = store.create(name='A', base_url='https://x.test', login_method='session', session='pasted')

	check = await service.verify_credential(account.id)

	assert (check.ok, check.kind, check.api_user, check.username) == (True, 'session', '4242', 'alice')
	assert check.quota == 2.0
	assert seen['session'] == 'pasted'
	assert store.get(account.id).api_user == '4242'


async def test_a_refresh_cookie_is_picked_over_a_session_and_its_replacement_stored(store, service, stub, auth):
	"""The exchange spends the cookie, so the rotated value has to land in the database in
	the same breath — not on the way out."""
	box, seen = auth
	_, sbox = stub
	sbox['site'] = newapi.SiteInfo(base_url='https://x.test', refresh_path='/api/user/auth/refresh')
	account = store.create(name='A', base_url='https://x.test', login_method='session')
	candidates = [
		newapi.PastedCredential('site-session', 'session'),
		newapi.PastedCredential('refresh-cookie', newapi.REFRESH_COOKIE),
	]

	check = await service.verify_credential(account.id, candidates)

	assert (check.ok, check.kind) == (True, newapi.REFRESH_COOKIE), 'the probe, not the paste, picks'
	assert seen['spent'] == 'refresh-cookie'
	assert seen['access_token'] == 'Bearer token-1'
	assert store.get(account.id).session == 'rotated-cookie'


async def test_a_dead_refresh_cookie_still_leaves_the_rotation_stored(store, service, stub, auth):
	box, _ = auth
	_, sbox = stub
	sbox['site'] = newapi.SiteInfo(base_url='https://x.test', refresh_path='/api/user/auth/refresh')
	box['refresh'] = (None, 'rotated-anyway', {})
	account = store.create(name='A', base_url='https://x.test', login_method='session', session='refresh-cookie')

	check = await service.verify_credential(
		account.id, [newapi.PastedCredential('refresh-cookie', newapi.REFRESH_COOKIE)]
	)

	assert check.ok is False and '换不到访问令牌' in check.reason
	assert store.get(account.id).session == 'rotated-anyway', 'a failed check must not leave a spent cookie'


async def test_a_missing_api_user_is_reported_as_itself_not_as_a_bad_credential(store, service, stub, auth):
	"""A fork that validates `new-api-user` 401s a perfectly live session. Saying 凭据无效
	would send someone off to re-export a cookie that was never the problem."""
	box, _ = auth
	box['user'], box['reason'] = None, 'New-Api-User header not provided'
	account = store.create(name='A', base_url='https://x.test', login_method='session', session='live')

	check = await service.verify_credential(account.id)

	assert (check.ok, check.needs_api_user) == (False, True)
	assert 'API User' in check.reason and '凭据本身没问题' in check.reason


async def test_a_credential_that_authenticates_nothing_says_so(store, service, stub, auth):
	box, _ = auth
	box['user'], box['reason'] = None, '无权进行此操作，未登录且未提供 access token'
	account = store.create(name='A', base_url='https://x.test', login_method='session', session='stale')

	check = await service.verify_credential(account.id)

	assert (check.ok, check.needs_api_user) == (False, False)
	assert '登录不了' in check.reason and '未登录' in check.reason


async def test_an_unreachable_site_does_not_report_a_bad_credential(store, service, stub, auth, monkeypatch):
	"""A probe that failed says nothing about the paste, and ADR-0010 says a WAF can be the
	one refusing. Blaming the credential would be evidence-free."""
	account = store.create(name='A', base_url='https://x.test', login_method='session', session='pasted')

	async def boom(base_url):
		raise RuntimeError('WAF challenge')

	monkeypatch.setattr(newapi, 'probe', boom)

	check = await service.verify_credential(account.id)

	assert check.ok is False and '无法访问站点' in check.reason and 'WAF' in check.reason
	assert store.get(account.id).session == 'pasted', 'the account is saved either way'


async def test_verifying_without_any_credential_is_refused_not_attempted(store, service, stub, auth):
	account = store.create(name='A', base_url='https://x.test', login_method='session')

	check = await service.verify_credential(account.id)

	assert check.ok is False and '没有存任何会话凭据' in check.reason


async def test_scheduler_run_once_only_touches_due_accounts(store, service, stub, capsys):
	calls, _ = stub
	store.create(name='ready', base_url='https://x.test', username='a', password='pw')
	store.create(name='no-creds', base_url='https://x.test')
	store.create(name='disabled', base_url='https://x.test', password='pw', username='a', enabled=False)

	results = await scheduler.run_once(store, service)

	assert len(results) == 1 and len(calls) == 1
	assert 'OK' in capsys.readouterr().out
	assert await scheduler.run_once(store, service) == {}, 'already done today'


async def test_an_access_token_is_verified_as_a_header_not_a_cookie(store, service, stub, auth):
	"""The credential a headless deployment reaches for first, and the one that used to be
	saved unverified: it arrives in its own field, so `_pasted` never saw it."""
	_, seen = auth
	account = store.create(
		name='A', base_url='https://x.test', login_method='access_token', access_token='sk-real'
	)

	check = await service.verify_credential(account.id)

	assert (check.ok, check.kind) == (True, 'access_token')
	assert seen['access_token'] == 'sk-real', 'sent bare in the header, not as a cookie'
	assert seen['session'] is None
	assert store.get(account.id).api_user == '4242'


async def test_a_stale_session_is_not_verified_in_place_of_the_token(store, service, stub, auth):
	"""Switching an account to a token leaves the old session in its column. Checking that
	instead would report a dead cookie as this account's verdict."""
	_, seen = auth
	account = store.create(
		name='A',
		base_url='https://x.test',
		login_method='access_token',
		access_token='sk-real',
		session='left-over-from-before',
	)

	check = await service.verify_credential(account.id)

	assert (check.ok, check.kind) == (True, 'access_token')
	assert seen['access_token'] == 'sk-real'
	assert seen['session'] is None, 'the declared login method decides, not a non-empty column'


async def test_an_access_token_account_with_no_token_says_so(store, service, stub, auth):
	account = store.create(name='A', base_url='https://x.test', login_method='access_token')

	check = await service.verify_credential(account.id)

	assert check.ok is False
	assert check.kind == 'access_token'
	assert '访问令牌' in check.reason


async def test_a_stale_pasted_token_does_not_block_its_own_browser_fallback(
	service, store, stub, browser, monkeypatch
):
	"""An OAuth account may now hold a site token *beside* its IdP login, so the token can
	be the thing that died. `_client` sends both credentials at once and New API answers a
	rejected `Authorization` header rather than falling back to the cookie (measured on
	gorouter.app: 200 + `access token 无效`), so carrying the dead token into the retry
	would make the browser hop fail on the credential it just replaced."""
	calls, box = stub
	box['site'] = newapi.SiteInfo(base_url='https://x.test', checkin_path='/api/user/checkin')
	outcomes = [
		newapi.Outcome(False, error='access token 无效'),  # the pasted token has expired
		newapi.Outcome(True, True, 1.0, 1.25, session='oauth-session'),  # the browser's session works
	]

	async def check_in(login, site=None):
		calls.append(login)
		return outcomes.pop(0) if outcomes else newapi.Outcome(False, error='unexpected third attempt')

	monkeypatch.setattr(newapi, 'check_in', check_in)
	account = store.create(
		name='A', base_url='https://x.test', login_method='github', access_token='sk-expired'
	)

	outcome = await service.check_in(account.id)

	assert outcome.success is True
	assert len(calls) == 2, 'the HTTP attempt, then the retry after the browser hop'
	assert calls[0].access_token == 'sk-expired', 'the paste is what gets tried first'
	assert calls[1].access_token is None, 'the dead token must not ride along with the fresh session'
	assert calls[1].session == 'oauth-session'


# --- deleting an account, and the profile that outlived it ----------------------------------


def _profile(root, provider, name):
	d = root / provider / name
	(d / 'Default').mkdir(parents=True)
	(d / 'Default' / 'Cookies').write_bytes(b'an IdP session')
	return d


def test_deleting_an_account_takes_its_profile_by_default(service, store, tmp_path, monkeypatch):
	"""What the delete button used to leave behind. The profile holds the *IdP* session — the
	whole github.com login — so an account deleted from the database left its credential on
	disk with nothing on any screen naming the directory."""
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	prof = _profile(tmp_path, 'github', 'gone')
	account_id = store.create(name='gone', base_url='https://x.test', login_method='github').id

	assert service.delete(account_id) is True
	assert store.get(account_id) is None
	assert not prof.exists(), 'the IdP session must not outlive the account'


def test_keeping_a_profile_is_possible_and_says_so(service, store, tmp_path, monkeypatch):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	prof = _profile(tmp_path, 'github', 'kept')
	account_id = store.create(name='kept', base_url='https://x.test', login_method='github').id

	assert service.delete(account_id, forget_profile=False) is False
	assert store.get(account_id) is None
	assert prof.exists(), 'asked to keep it, so it stays'


def test_the_row_goes_even_if_the_profile_cannot(service, store, tmp_path, monkeypatch):
	"""Windows keeps files open. If removing the directory raises, the account must already be
	gone — the alternative is a row that survives its own deletion because a browser had a
	lock on a cache file."""
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	_profile(tmp_path, 'github', 'locked')
	account_id = store.create(name='locked', base_url='https://x.test', login_method='github').id

	import panel.browser_login as bl

	def boom(*_a, **_kw):
		raise OSError('being used by another process')

	monkeypatch.setattr(bl, 'forget_profile', boom)
	with pytest.raises(OSError):
		service.delete(account_id)
	assert store.get(account_id) is None, 'the row goes first, so this cannot half-delete'


def test_orphans_are_the_profiles_no_account_claims(service, store, tmp_path, monkeypatch):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	_profile(tmp_path, 'github', 'live')
	_profile(tmp_path, 'github', 'orphan')
	_profile(tmp_path, 'linuxdo', 'renamed-away')
	store.create(name='live', base_url='https://x.test', login_method='github')

	keys = {o.key for o in service.orphan_profiles()}

	assert keys == {'github/orphan', 'linuxdo/renamed-away'}
	assert all(o.bytes > 0 for o in service.orphan_profiles()), 'the size is what makes it worth clearing'


def test_a_name_that_sanitises_to_the_same_directory_is_not_an_orphan(service, store, tmp_path, monkeypatch):
	"""`profile_name` is part of the match. An account called `a@b.com` owns `a_b.com`, and
	comparing raw names would offer a live account's profile up for deletion."""
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	_profile(tmp_path, 'github', 'a_b.com')
	store.create(name='a@b.com', base_url='https://x.test', login_method='github')

	assert service.orphan_profiles() == []


def test_an_old_layout_profile_is_told_apart_from_a_provider_directory(service, store, tmp_path, monkeypatch):
	"""A profile keyed by site sits directly under the root, so its Chrome subdirectories
	would otherwise be read as account names — which is how one listing reported `Default` and
	`ShaderCache` as three separate orphaned accounts."""
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	old = tmp_path / 'keyed-by-site'
	(old / 'Default').mkdir(parents=True)
	(old / 'ShaderCache').mkdir()
	(old / 'Default' / 'Cookies').write_bytes(b'x')
	_profile(tmp_path, 'github', 'orphan')

	found = {o.key: o for o in service.orphan_profiles()}

	assert set(found) == {'keyed-by-site', 'github/orphan'}
	assert found['keyed-by-site'].old_layout is True
	assert found['keyed-by-site'].provider is None
	assert found['github/orphan'].old_layout is False


def test_deleting_orphans_takes_only_what_was_listed(service, store, tmp_path, monkeypatch):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	go = _profile(tmp_path, 'github', 'go')
	stay = _profile(tmp_path, 'github', 'stay')

	assert service.delete_orphan_profiles(['github/go']) == 1
	assert not go.exists()
	assert stay.exists(), 'an orphan not named must not be swept up with it'


def test_deleting_orphans_skips_one_that_stopped_being_an_orphan(service, store, tmp_path, monkeypatch):
	"""The list is a snapshot and this is `rmtree`. If an account was added back under that
	name while the dialog sat open, the profile is in use again."""
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	prof = _profile(tmp_path, 'github', 'reclaimed')
	store.create(name='reclaimed', base_url='https://x.test', login_method='github')

	assert service.delete_orphan_profiles(['github/reclaimed']) == 0
	assert prof.exists(), 'the profile of a live account must survive a stale list'


def test_deleting_orphans_refuses_a_key_that_climbs_out(service, store, tmp_path, monkeypatch):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path / 'root'))
	(tmp_path / 'root').mkdir()
	outside = tmp_path / 'precious'
	outside.mkdir()
	(outside / 'keep.txt').write_text('not a profile')

	assert service.delete_orphan_profiles(['../precious', '../../precious']) == 0
	assert outside.exists() and (outside / 'keep.txt').exists()


async def test_a_visit_account_renews_its_own_card_from_the_idp_session(service, store, stub, monkeypatch):
	"""A `visit` account with no password is not stuck when its site session lapses.

	The site session is what the whole mechanism runs on — load an authenticated page and
	the bonus is collected — and no `visit` run ever mints one, so the day it expires the
	account goes dark. But the *IdP* session in the same profile outlives it by weeks, and
	the hop that trades one for the other is redirects and a consent click: nothing a
	headless run cannot do. Measured on anyrouter.top, one card carried 13 days.

	Before this, the only branch that could re-login required a password an OAuth-only
	account is unable to have (`PUT /api/user/self` verifies `original_password`), so the
	owner was told to check credentials they never set.
	"""
	import panel.browser_login as browser_login

	logins, visits = [], []
	user = {'id': 169050, 'username': 'linuxdo_1', 'quota': 87_500_000}

	async def visit(**kw):
		visits.append(kw)
		if len(visits) == 1:  # the lapsed card: the page loads, nobody is logged in
			return browser_login.BrowserVisit(before=None, after=None, session=None, checkin_at=None, held=True)
		return browser_login.BrowserVisit(
			before={'id': 169050, 'quota': 87_000_000}, after=user, session='fresh-card', checkin_at=None, held=True
		)

	async def login(**kw):
		logins.append(kw)
		return browser_login.BrowserLogin('fresh-card', user)

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	monkeypatch.setattr(browser_login, 'browser_login', login)
	account = store.create(name='any-ld', base_url='https://x.test', login_method='linuxdo', mechanism='visit')

	outcome = await service.check_in(account.id)

	assert outcome.success, 'the renewal is the whole point; a lapsed card is not a dead account'
	assert len(logins) == 1, 'exactly one OAuth hop — it costs a browser launch'
	assert logins[0]['headless'] is True, 'no window: the IdP cookie is already in the profile'
	assert len(visits) == 2, 'and the visit is retried with the new card'
	assert store.get(account.id).session == 'fresh-card', 'the won card is kept, not thrown away'


async def test_a_visit_account_with_a_password_does_not_burn_an_oauth_hop(service, store, stub, monkeypatch):
	"""The counterpart: `browser_visit` renews a password account by itself, inside the one
	launch it already has. Adding a second launch for those would double the cost of every
	lapsed day for nothing."""
	import panel.browser_login as browser_login

	logins = []

	async def visit(**kw):
		return browser_login.BrowserVisit(
			before=None, after={'id': 216481, 'quota': 87_500_000}, session='sess', checkin_at=None, held=True
		)

	async def login(**kw):
		logins.append(kw)
		raise AssertionError('a password account must not reach the OAuth renewal')

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	monkeypatch.setattr(browser_login, 'browser_login', login)
	account = store.create(
		name='any-pw', base_url='https://x.test', username='someone', password='pw', mechanism='visit'
	)

	outcome = await service.check_in(account.id)

	assert outcome.success
	assert logins == [], 'the password branch inside browser_visit already handled it'


async def test_a_renewal_that_cannot_land_still_says_what_would_fix_it(service, store, stub, monkeypatch):
	"""When the IdP session has lapsed too, the headless hop cannot help and the owner has
	to open a window once. So a failed renewal must not swallow the advice — and must not
	blame a password an OAuth-only account never had."""
	import panel.browser_login as browser_login

	visits = []

	async def visit(**kw):
		visits.append(kw)
		return browser_login.BrowserVisit(before=None, after=None, session=None, checkin_at=None, held=True)

	tried = []

	async def login(**kw):
		tried.append(kw)
		raise RuntimeError('linuxdo 需要先人工登录一次')

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	monkeypatch.setattr(browser_login, 'browser_login', login)
	account = store.create(name='any-ld', base_url='https://x.test', login_method='linuxdo', mechanism='visit')

	outcome = await service.check_in(account.id)

	assert len(tried) == 1, 'the renewal has to be attempted, or this tests nothing'
	assert outcome.success is False
	assert '浏览器登录' in outcome.error, 'the one thing that fixes a lapsed IdP session'
	assert '账号密码' not in outcome.error, 'never blame a credential this account cannot have'
	assert len(visits) == 1, 'no retry when the renewal did not land — the second visit would fail alike'


async def test_a_renewal_is_only_attempted_when_the_card_is_actually_gone(service, store, stub, monkeypatch):
	"""An OAuth `visit` account whose card is still live must not pay for an OAuth hop it
	does not need. This is the 13-day case: the same card, reused every day, no login."""
	import panel.browser_login as browser_login

	logins = []

	async def visit(**kw):
		return browser_login.BrowserVisit(
			before={'id': 169050, 'quota': 87_000_000},
			after={'id': 169050, 'quota': 87_500_000},
			session='same-old-card',
			checkin_at=None,
			held=True,
		)

	async def login(**kw):
		logins.append(kw)
		raise AssertionError('a live card must never trigger a renewal')

	monkeypatch.setattr(browser_login, 'browser_visit', visit)
	monkeypatch.setattr(browser_login, 'browser_login', login)
	account = store.create(name='any-ld', base_url='https://x.test', login_method='linuxdo', mechanism='visit')

	outcome = await service.check_in(account.id)

	assert (outcome.success, outcome.checked_in) == (True, True), 'the balance moved: the bonus landed'
	assert logins == [], 'no OAuth round trip on a day the card still works'
