"""AccountStore tests — real temp SQLite, including the v1 -> v2 migration."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from panel.store import AccountStore


@pytest.fixture
def db_path():
	with tempfile.TemporaryDirectory() as tmpdir:
		yield Path(tmpdir) / 'test.db'


@pytest.fixture
def store(db_path):
	return AccountStore(db_path)


def test_create_defaults_and_strips_trailing_slash(store):
	account = store.create(name='我的主账号', base_url='https://x.test/', password='pw')

	assert account.id is not None
	assert (account.base_url, account.login_method, account.enabled) == ('https://x.test', 'password', True)
	assert account.password == 'pw'
	assert account.has_password is False, 'a password without a username cannot log in'


def test_has_password_needs_both_halves(store):
	account = store.create(name='A', base_url='https://x.test', username='alice', password='pw')
	assert account.has_password is True


def test_get_list_and_delete(store):
	first = store.create(name='A', base_url='https://a.test')
	store.create(name='B', base_url='https://b.test')

	assert store.get(first.id).name == 'A'
	assert store.get(9999) is None
	assert [a.name for a in store.list()] == ['A', 'B']

	store.delete(first.id)
	assert store.get(first.id) is None
	assert len(store.list()) == 1


def test_list_enabled_skips_disabled_and_urlless(store):
	store.create(name='on', base_url='https://a.test')
	store.create(name='off', base_url='https://b.test', enabled=False)
	store.create(name='blank', base_url='')

	assert [a.name for a in store.list_enabled()] == ['on']


def test_update_only_touches_given_fields(store):
	account = store.create(name='A', base_url='https://a.test', username='alice', password='pw')

	store.update(account.id, name='Renamed', base_url='https://b.test/', session='s1')

	updated = store.get(account.id)
	assert (updated.name, updated.base_url, updated.session) == ('Renamed', 'https://b.test', 's1')
	assert (updated.username, updated.password) == ('alice', 'pw')


def test_update_with_nothing_useful_is_a_noop(store):
	account = store.create(name='A', base_url='https://a.test')
	store.update(account.id, unknown_column='x', name=None)
	assert store.get(account.id).name == 'A'


def test_avatar_choice_round_trips(store):
	account = store.create(name='A', base_url='https://a.test', avatar_color='blue', avatar_shape='dot')

	assert (account.avatar_color, account.avatar_shape) == ('blue', 'dot')
	saved = store.get(account.id)
	assert (saved.avatar_color, saved.avatar_shape) == ('blue', 'dot')


def test_a_new_account_has_no_avatar_choice(store):
	account = store.create(name='A', base_url='https://a.test')
	assert (account.avatar_color, account.avatar_shape) == (None, None)


def test_updating_one_avatar_field_leaves_the_other(store):
	"""Editing an account submits no avatar fields, so update() dropping None is what
	keeps a chosen avatar alive."""
	account = store.create(name='A', base_url='https://a.test', avatar_color='blue', avatar_shape='dot')

	store.update(account.id, avatar_shape='letter')
	assert (store.get(account.id).avatar_color, store.get(account.id).avatar_shape) == ('blue', 'letter')

	store.update(account.id, name='Renamed')
	saved = store.get(account.id)
	assert (saved.name, saved.avatar_color, saved.avatar_shape) == ('Renamed', 'blue', 'letter')


def test_the_same_name_on_the_same_site_is_refused(store):
	"""The name keys the browser profile, so a duplicate would silently check in the
	same identity twice. Across different sites it is fine — same IdP, two sites."""
	store.create(name='A', base_url='https://a.test')

	with pytest.raises(sqlite3.IntegrityError):
		store.create(name='A', base_url='https://a.test/')

	store.create(name='A', base_url='https://b.test')
	other = store.create(name='B', base_url='https://a.test')
	with pytest.raises(sqlite3.IntegrityError):
		store.update(other.id, name='A')


def test_record_result_stores_success_and_failure(store):
	account = store.create(name='A', base_url='https://a.test')

	store.record_result(account.id, success=True, checked_in=True, quota=1.25)
	saved = store.get(account.id)
	assert (saved.last_success, saved.last_checked_in, saved.last_quota) == (True, True, 1.25)
	assert saved.last_run_at and saved.last_error is None

	store.record_result(account.id, success=False, error='WAF blocked')
	saved = store.get(account.id)
	assert (saved.last_success, saved.last_checked_in, saved.last_error) == (False, None, 'WAF blocked')


_V1_SCHEMA = '''
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, provider TEXT NOT NULL, custom_domain TEXT,
    email TEXT, password TEXT, cookies TEXT, api_user TEXT,
    status TEXT NOT NULL DEFAULT 'draft', created_at TEXT, updated_at TEXT
);
'''


def test_migrates_v1_accounts_and_keeps_a_backup(db_path):
	conn = sqlite3.connect(db_path)
	conn.executescript(_V1_SCHEMA)
	conn.executemany(
		'INSERT INTO accounts (name, provider, custom_domain, email, password, cookies, api_user, status)'
		' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
		[
			('pw-account', 'anyrouter', None, 'a@x.com', 'pw', None, '1', 'active'),
			('cookie-account', 'agentrouter', None, None, None, 'bare-session-value', '2', 'verified'),
			('custom', 'custom', 'https://mine.test/', None, None, '{"session": "js"}', None, 'draft'),
			('gone', 'anyrouter', None, None, None, None, None, 'removed'),
		],
	)
	conn.commit()
	conn.close()

	accounts = {a.name: a for a in AccountStore(db_path).list()}

	assert set(accounts) == {'pw-account', 'cookie-account', 'custom'}, 'removed rows are dropped'
	assert accounts['pw-account'].base_url == 'https://anyrouter.top'
	assert (accounts['pw-account'].username, accounts['pw-account'].login_method) == ('a@x.com', 'password')
	assert accounts['cookie-account'].base_url == 'https://agentrouter.org'
	assert (accounts['cookie-account'].session, accounts['cookie-account'].login_method) == (
		'bare-session-value',
		'session',
	)
	assert accounts['custom'].base_url == 'https://mine.test'
	assert accounts['custom'].session == 'js'
	assert db_path.with_suffix('.v1.bak').exists()


def test_reopening_a_migrated_db_is_idempotent(db_path):
	AccountStore(db_path).create(name='A', base_url='https://a.test')
	assert [a.name for a in AccountStore(db_path).list()] == ['A']


_PRE_AVATAR_SCHEMA = '''
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, base_url TEXT NOT NULL,
    login_method TEXT NOT NULL DEFAULT 'password', mechanism TEXT,
    username TEXT, password TEXT, access_token TEXT, session TEXT, api_user TEXT,
    checkin_after TEXT, enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT, last_success INTEGER, last_checked_in INTEGER, last_quota REAL,
    last_error TEXT, failures INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
'''


def test_adds_the_avatar_columns_to_an_older_db(db_path):
	"""A DB written before the avatar columns existed must keep opening — _to_account
	reads both by name, so a missing column is a hard KeyError on every list()."""
	conn = sqlite3.connect(db_path)
	conn.executescript(_PRE_AVATAR_SCHEMA)
	conn.execute(
		'INSERT INTO accounts (name, base_url, created_at, updated_at) VALUES (?, ?, ?, ?)',
		('old', 'https://old.test', '2025-01-01', '2025-01-01'),
	)
	conn.commit()
	conn.close()

	store = AccountStore(db_path)

	assert [a.name for a in store.list()] == ['old']
	assert store.get(1).avatar_color is None
	check = sqlite3.connect(db_path)
	columns = {r[1] for r in check.execute('PRAGMA table_info(accounts)')}
	check.close()
	assert {'avatar_color', 'avatar_shape'} <= columns

	store.update(1, avatar_color='rose', avatar_shape='letter')
	assert (store.get(1).avatar_color, store.get(1).avatar_shape) == ('rose', 'letter')


def test_promo_seen_counts_impressions_and_keeps_first_seen(store):
	store.promo_seen('seekai-2026-08')
	first = store.promo_state()['seekai-2026-08']
	store.promo_seen('seekai-2026-08')
	again = store.promo_state()['seekai-2026-08']

	assert (first['shows'], again['shows']) == (1, 2)
	assert again['first_seen_at'] == first['first_seen_at'], 'first_seen_at is "since when offered"'
	assert again['dismissed_at'] is None


def test_promo_dismiss_records_a_card_never_shown(store):
	"""The cooldown is measured from dismissed_at, so the write must not need a prior show."""
	store.promo_dismiss('x')

	row = store.promo_state()['x']
	assert row['dismissed_at'] is not None
	assert row['shows'] == 0
	assert row['dismissals'] == 1


def test_every_dismissal_is_counted(store):
	"""The cooldown doubles per dismissal, so this has to be a running total and not a flag."""
	assert store.promo_state().get('x') is None
	for expected in (1, 2, 3):
		store.promo_dismiss('x')
		assert store.promo_state()['x']['dismissals'] == expected


def test_adds_the_dismissals_column_to_an_older_db(db_path):
	"""promo_state is younger than this column, and CREATE TABLE IF NOT EXISTS adds nothing to a
	table that already exists — so a DB from the first promo build needs the ALTER."""
	conn = sqlite3.connect(db_path)
	conn.executescript(_PRE_AVATAR_SCHEMA)
	conn.executescript(
		'''CREATE TABLE promo_state (
			promo_id TEXT PRIMARY KEY, first_seen_at TEXT, last_shown_at TEXT,
			shows INTEGER NOT NULL DEFAULT 0, dismissed_at TEXT
		);'''
	)
	conn.execute("INSERT INTO promo_state (promo_id, shows) VALUES ('old-card', 4)")
	conn.commit()
	conn.close()

	store = AccountStore(db_path)

	assert store.promo_state()['old-card'] == {
		'promo_id': 'old-card',
		'first_seen_at': None,
		'last_shown_at': None,
		'shows': 4,
		'dismissed_at': None,
		'dismissals': 0,
	}
	store.promo_dismiss('old-card')
	assert store.promo_state()['old-card']['dismissals'] == 1


def test_creates_promo_state_in_an_older_db(db_path):
	"""promo_state is a new table, so old databases get it from executescript(_SCHEMA) on
	open. A store that only creates it for fresh files would raise OperationalError on
	every existing install."""
	conn = sqlite3.connect(db_path)
	conn.executescript(_PRE_AVATAR_SCHEMA)
	conn.execute(
		'INSERT INTO accounts (name, base_url, created_at, updated_at) VALUES (?, ?, ?, ?)',
		('old', 'https://old.test', '2025-01-01', '2025-01-01'),
	)
	conn.commit()
	conn.close()

	store = AccountStore(db_path)

	assert [a.name for a in store.list()] == ['old']
	assert store.promo_state() == {}
	store.promo_seen('card')
	assert store.promo_state()['card']['shows'] == 1
