"""HTTP API tests — TestClient over a real store with a mocked service."""
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from panel import newapi, promo
from panel.app import create_app
from panel.store import AccountStore


@pytest.fixture
def store():
	with tempfile.TemporaryDirectory() as tmpdir:
		yield AccountStore(Path(tmpdir) / 'test.db')


@pytest.fixture
def service():
	mock = MagicMock()
	mock.check_in = AsyncMock(return_value=newapi.Outcome(True, True, 1.0, 1.25, session='secret-session'))
	mock.check_in_many = AsyncMock(return_value={})
	mock.probe = AsyncMock(return_value=newapi.SiteInfo(base_url='https://x.test', login_methods=('password',)))
	mock.bootstrap = AsyncMock(return_value='alice')
	return mock


@pytest.fixture
def client(store, service):
	return TestClient(create_app(store=store, service=service))


def test_crud_round_trip(client, store):
	assert client.get('/api/accounts').json() == []

	created = client.post('/api/accounts', json={'name': 'A', 'base_url': 'https://x.test/'})
	assert created.status_code == 201
	account_id = created.json()['id']
	assert created.json()['base_url'] == 'https://x.test'

	assert client.get(f'/api/accounts/{account_id}').json()['name'] == 'A'
	assert len(client.get('/api/accounts').json()) == 1

	updated = client.put(f'/api/accounts/{account_id}', json={'name': 'B', 'login_method': 'github'})
	assert (updated.json()['name'], updated.json()['login_method']) == ('B', 'github')

	assert client.delete(f'/api/accounts/{account_id}').status_code == 204
	assert client.get(f'/api/accounts/{account_id}').status_code == 404


def test_a_duplicate_name_on_one_site_is_refused_with_a_reason(client):
	client.post('/api/accounts', json={'name': 'A', 'base_url': 'https://x.test'})

	clash = client.post('/api/accounts', json={'name': 'A', 'base_url': 'https://x.test'})
	assert clash.status_code == 409 and '同名' in clash.json()['detail']

	other = client.post('/api/accounts', json={'name': 'B', 'base_url': 'https://x.test'}).json()
	assert client.put(f'/api/accounts/{other["id"]}', json={'name': 'A'}).status_code == 409
	assert client.post('/api/accounts', json={'name': 'A', 'base_url': 'https://y.test'}).status_code == 201


def test_missing_account_is_404_everywhere(client):
	assert client.get('/api/accounts/999').status_code == 404
	assert client.put('/api/accounts/999', json={'name': 'x'}).status_code == 404
	assert client.delete('/api/accounts/999').status_code == 404
	assert client.post('/api/accounts/999/check-in').status_code == 404


@pytest.mark.parametrize(
	'payload',
	[
		{'name': 'A', 'base_url': 'x.test'},
		{'name': 'A', 'base_url': 'https://x.test', 'login_method': 'carrier-pigeon'},
		{'name': 'A', 'base_url': 'https://x.test', 'mechanism': 'telepathy'},
		{'name': 'A', 'base_url': 'https://x.test', 'checkin_after': '25:00'},
		{'name': 'A', 'base_url': 'https://x.test', 'checkin_after': '8:5'},
	],
)
def test_bad_input_is_rejected(client, payload):
	assert client.post('/api/accounts', json=payload).status_code == 422


def test_avatar_choice_round_trips_over_http(client):
	created = client.post(
		'/api/accounts',
		json={'name': 'A', 'base_url': 'https://x.test', 'avatar_color': 'violet', 'avatar_shape': 'dot'},
	).json()
	assert (created['avatar_color'], created['avatar_shape']) == ('violet', 'dot')

	updated = client.put(f'/api/accounts/{created["id"]}', json={'avatar_color': 'emerald'}).json()
	assert (updated['avatar_color'], updated['avatar_shape']) == ('emerald', 'dot')


@pytest.mark.parametrize('value', ['Blue', 'b', '鲜红', 'blue-500', 'a' * 17])
@pytest.mark.parametrize('field', ['avatar_color', 'avatar_shape'])
def test_a_misshapen_avatar_slug_is_refused(client, field, value):
	"""The backend checks the slug's shape only; the palette itself lives in the
	frontend, so a valid-looking unknown slug is deliberately accepted."""
	resp = client.post('/api/accounts', json={'name': 'A', 'base_url': 'https://x.test', field: value})
	assert resp.status_code == 422 and '头像' in resp.json()['detail']


def test_editing_an_account_does_not_clear_its_avatar(client):
	"""AccountForm submits no avatar fields, so a plain edit must not wipe the choice."""
	created = client.post(
		'/api/accounts',
		json={'name': 'A', 'base_url': 'https://x.test', 'avatar_color': 'amber', 'avatar_shape': 'letter'},
	).json()

	edited = client.put(
		f'/api/accounts/{created["id"]}',
		json={'name': 'A renamed', 'base_url': 'https://x.test', 'login_method': 'github'},
	).json()

	assert (edited['avatar_color'], edited['avatar_shape']) == ('amber', 'letter')


def test_check_in_never_leaks_the_session_and_reports_delta(client, store, service):
	account = store.create(name='A', base_url='https://x.test')

	body = client.post(f'/api/accounts/{account.id}/check-in').json()

	assert (body['success'], body['checked_in'], body['delta']) == (True, True, 0.25)
	assert 'session' not in body
	service.check_in.assert_awaited_once_with(account.id)


def test_batch_check_in_keys_results_by_id(client, service):
	service.check_in_many.return_value = {
		1: newapi.Outcome(True, True, after_quota=2.0),
		2: newapi.Outcome(False, error='登录失败'),
	}

	body = client.post('/api/check-in', json={'account_ids': [1, 2]}).json()

	assert body['1']['success'] is True and body['2']['error'] == '登录失败'
	assert all('session' not in outcome for outcome in body.values())


def test_probe_exposes_the_mechanism(client, service):
	service.probe.return_value = newapi.SiteInfo(
		base_url='https://x.test', login_methods=('password', 'github'), checkin_path='/api/user/checkin'
	)

	body = client.post('/api/probe', json={'base_url': 'https://x.test'}).json()

	assert body['mechanism'] == 'endpoint'
	assert body['login_methods'] == ['password', 'github']


def test_probe_failure_is_a_bad_gateway(client, service):
	service.probe.side_effect = RuntimeError('无法访问')
	resp = client.post('/api/probe', json={'base_url': 'https://x.test'})
	assert resp.status_code == 502 and '无法访问' in resp.json()['detail']


def test_bootstrap_reports_the_username(client, store, service):
	account = store.create(name='A', base_url='https://x.test', session='s')
	assert client.post(f'/api/accounts/{account.id}/bootstrap').json() == {'username': 'alice'}

	service.bootstrap.side_effect = RuntimeError('会话无效')
	resp = client.post(f'/api/accounts/{account.id}/bootstrap')
	assert resp.status_code == 400 and resp.json()['detail'] == '会话无效'


def test_browser_login_stores_the_session_then_bootstraps(client, store, service, monkeypatch):
	import panel.browser_login as module  # noqa: F401  (import guard: it must exist)

	async def browser_login(*, base_url, provider, account_name, headless):
		assert (base_url, provider, account_name, headless) == ('https://x.test', 'github', 'A', True)
		from panel.browser_login import BrowserLogin

		return BrowserLogin('fresh-session', {'id': 4242, 'username': 'alice'})

	monkeypatch.setattr(module, 'browser_login', browser_login)
	account = store.create(name='A', base_url='https://x.test', login_method='github')

	body = client.post(f'/api/accounts/{account.id}/browser-login', json={'headless': True}).json()

	assert body == {'session_stored': True, 'username': 'alice'}
	saved = store.get(account.id)
	assert (saved.session, saved.api_user) == ('fresh-session', '4242')


def test_no_browser_installed_is_a_reason_not_a_crash(client, store, monkeypatch):
	"""The container image has no cloakbrowser, so the import itself can fail. That has to
	reach the owner as a 400 saying what happened, not a 500 saying nothing."""
	monkeypatch.setitem(sys.modules, 'panel.browser_login', None)
	account = store.create(name='A', base_url='https://x.test', login_method='github')

	response = client.post(f'/api/accounts/{account.id}/browser-login', json={'headless': True})

	assert response.status_code == 400
	assert response.json()['detail']


def test_the_health_route_never_touches_an_account(client, store):
	"""It runs unauthenticated every few seconds and its URL lands in `docker inspect`,
	while /api/accounts returns credentials in the clear (ADR-0003)."""
	store.create(name='A', base_url='https://x.test', username='alice', password='pw-should-never-appear')

	body = client.get('/api/health').json()

	assert body == {'status': 'ok', 'scheduler': False}
	assert 'pw-should-never-appear' not in client.get('/api/health').text


def test_without_a_build_there_is_no_spa_but_the_api_still_answers(store, service):
	client = TestClient(create_app(store=store, service=service, dist_dir=None))

	assert client.get('/api/accounts').status_code == 200
	assert client.get('/').status_code == 404


def test_a_built_frontend_is_served_and_unknown_account_paths_fall_back_to_it(tmp_path, store, service):
	dist = tmp_path / 'dist'
	(dist / 'assets').mkdir(parents=True)
	(dist / 'index.html').write_text('<div id=root></div>', encoding='utf-8')
	(dist / 'assets' / 'app.js').write_text('console.log(1)', encoding='utf-8')

	client = TestClient(create_app(store=store, service=service, dist_dir=dist))

	assert client.get('/').text == '<div id=root></div>'
	assert client.get('/assets/app.js').status_code == 200
	# The SPA owns its own routing, so a deep link has to reach index.html rather than 404.
	assert client.get('/accounts/17/edit').text == '<div id=root></div>'
	# And the API must still win over the fallback.
	assert client.get('/api/accounts').json() == []


@pytest.fixture
def promo_cards():
	"""Prime the manifest cache so /api/promos runs its real targeting without a network."""
	def prime(*cards):
		promo._CACHE.update(at=time.monotonic(), etag=None, cards=list(cards))
	promo.clear_cache()
	yield prime
	promo.clear_cache()


def a_card(**target):
	return promo.parse_cards(
		{
			'version': 1,
			'cards': [
				{
					'id': 'seekai-2026-08',
					'title': '新公益站：SeekAI',
					'body': '签到每天 $20',
					'hero': {'title': '每天 $20 额度', 'badge': '公益站'},
					'cta': {'label': '去注册 →', 'url': 'https://seekai.test/register?aff=me'},
					'target': target or {'missing_hosts': ['seekai.test']},
				}
			],
		}
	)[0]


def test_a_promo_card_is_targeted_locally_and_counted_once(client, store, promo_cards):
	promo_cards(a_card(missing_hosts=['seekai.test'], min_accounts=1))
	client.post('/api/accounts', json={'name': 'A', 'base_url': 'https://other.test'})

	card = client.get('/api/promos').json()['card']

	assert set(card) == {'id', 'title', 'body', 'sticker', 'theme', 'hero', 'cta'}, 'targeting rules stay on the server'
	assert card['sticker'] == 'new', 'a card this panel has never been offered before is brand new'
	assert card['cta']['url'].startswith('https://')
	assert store.promo_state()['seekai-2026-08']['shows'] == 1
	client.get('/api/promos')
	assert store.promo_state()['seekai-2026-08']['shows'] == 1, 'a page reload is not an impression'


def test_no_card_before_the_first_account(client, promo_cards):
	promo_cards(a_card(missing_hosts=['seekai.test'], min_accounts=1))

	assert client.get('/api/promos').json() == {'card': None}


def test_a_site_already_in_the_panel_gets_no_promo(client, store, promo_cards):
	promo_cards(a_card(missing_hosts=['seekai.test']))
	client.post('/api/accounts', json={'name': 'A', 'base_url': 'https://api.seekai.test/'})

	assert client.get('/api/promos').json() == {'card': None}
	assert store.promo_state() == {}, 'a card that was never shown must not be recorded'


def test_dismissal_outlives_the_browser(client, store, promo_cards):
	promo_cards(a_card(missing_hosts=['seekai.test']))
	assert client.get('/api/promos').json()['card'] is not None

	assert client.post('/api/promos/seekai-2026-08/dismiss').status_code == 204

	assert store.promo_state()['seekai-2026-08']['dismissed_at'] is not None
	assert client.get('/api/promos').json() == {'card': None}, 'state is in SQLite, not localStorage'


def test_a_broken_manifest_never_becomes_an_error(client, monkeypatch, promo_cards):
	"""AC4: whatever the promo path does, the panel keeps working."""
	promo_cards()
	assert client.get('/api/promos').json() == {'card': None}

	async def explode(**kwargs):
		raise RuntimeError('boom')

	monkeypatch.setattr(promo, 'cards', explode)
	response = client.get('/api/promos')

	assert response.status_code == 200 and response.json() == {'card': None}
	assert client.get('/api/accounts').status_code == 200, 'and the rest of the panel is untouched'
