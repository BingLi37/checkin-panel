"""AccountStore — SQLite persistence for accounts.

Local-only, single user: plaintext credentials (ADR-0003), DB inside the
sandboxed folder (ADR-0006). An account is identified by (name, base_url); the
old built-in "provider" table is gone — a site is just its Base URL, and what it
supports is discovered at run time (see panel/newapi.probe).
"""
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOGIN_METHODS = ('password', 'access_token', 'session', 'linuxdo', 'github')
# What grants the bonus. 'auto' (or empty) lets newapi.probe decide — an endpoint to
# POST, or a fresh login. 'visit' is the one thing probing cannot see: the site runs
# its own check-in when an authenticated page loads, and issues no receipt (ADR-0012).
MECHANISMS = ('auto', 'visit')

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    login_method TEXT NOT NULL DEFAULT 'password',
    mechanism TEXT,
    username TEXT,
    password TEXT,
    access_token TEXT,
    session TEXT,
    api_user TEXT,
    checkin_after TEXT,
    avatar_color TEXT,
    avatar_shape TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_success INTEGER,
    last_checked_in INTEGER,
    last_quota REAL,
    last_error TEXT,
    failures INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- What the panel has already done with each remote promo card (panel/promo.py). It lives
-- here rather than in localStorage because "do not ask me again" has to survive a cleared
-- browser cache; nothing about a card or its state ever leaves the machine.
CREATE TABLE IF NOT EXISTS promo_state (
    promo_id TEXT PRIMARY KEY,
    first_seen_at TEXT,
    last_shown_at TEXT,
    shows INTEGER NOT NULL DEFAULT 0,
    dismissed_at TEXT,
    dismissals INTEGER NOT NULL DEFAULT 0
);
'''

# An account *is* (name, base_url) — and the name also keys its browser profile
# (.browser_profiles/<login_method>/<name>), so two same-named accounts on one site
# would silently check in the same identity twice.
# Same idea for promo_state, which is younger than the column it grew: a DB opened by an
# earlier build has the table but not `dismissals`, and CREATE TABLE IF NOT EXISTS adds nothing.
_ADDED_PROMO_COLUMNS = {'dismissals': 'INTEGER NOT NULL DEFAULT 0'}

_ADDED_COLUMNS = {
	'checkin_after': 'TEXT',
	'failures': 'INTEGER NOT NULL DEFAULT 0',
	'mechanism': 'TEXT',
	'avatar_color': 'TEXT',
	'avatar_shape': 'TEXT',
}

_IDENTITY_INDEX = 'CREATE UNIQUE INDEX IF NOT EXISTS accounts_identity ON accounts (name, base_url)'

_FIELDS = (
	'name',
	'base_url',
	'login_method',
	'mechanism',
	'username',
	'password',
	'access_token',
	'session',
	'api_user',
	'checkin_after',
	'avatar_color',
	'avatar_shape',
	'enabled',
)

_LEGACY_SITES = {
	'anyrouter': 'https://anyrouter.top',
	'nanorouter': 'https://anyrouter.top',
	'agentrouter': 'https://agentrouter.org',
}


def _now() -> str:
	return datetime.now(timezone.utc).isoformat()


@dataclass
class Account:
	id: int
	name: str
	base_url: str
	login_method: str = 'password'
	mechanism: Optional[str] = None  # None/'auto' = discovered; 'visit' = page load grants it
	username: Optional[str] = None
	password: Optional[str] = None
	access_token: Optional[str] = None
	session: Optional[str] = None
	api_user: Optional[str] = None
	checkin_after: Optional[str] = None  # 'HH:MM' — when this site opens its daily window
	# How the list renders this account: a colour slug and a shape slug. The slug values
	# themselves live in frontend/src/avatar.ts — the backend only checks their shape, so
	# the palette has one owner (.trellis/spec/backend/database-guidelines.md). None = never
	# chosen; the frontend picks a default.
	avatar_color: Optional[str] = None
	avatar_shape: Optional[str] = None
	enabled: bool = True
	last_run_at: Optional[str] = None
	last_success: Optional[bool] = None
	last_checked_in: Optional[bool] = None
	last_quota: Optional[float] = None
	last_error: Optional[str] = None
	failures: int = 0  # consecutive failures, for the scheduler's backoff
	created_at: Optional[str] = None
	updated_at: Optional[str] = None

	@property
	def has_password(self) -> bool:
		return bool(self.username and self.password)


def _to_account(row: sqlite3.Row) -> Account:
	def flag(key):
		value = row[key]
		return None if value is None else bool(value)

	return Account(
		id=row['id'],
		name=row['name'],
		base_url=row['base_url'],
		login_method=row['login_method'],
		mechanism=row['mechanism'],
		username=row['username'],
		password=row['password'],
		access_token=row['access_token'],
		session=row['session'],
		api_user=row['api_user'],
		checkin_after=row['checkin_after'],
		avatar_color=row['avatar_color'],
		avatar_shape=row['avatar_shape'],
		enabled=bool(row['enabled']),
		last_run_at=row['last_run_at'],
		last_success=flag('last_success'),
		last_checked_in=flag('last_checked_in'),
		last_quota=row['last_quota'],
		last_error=row['last_error'],
		failures=row['failures'] or 0,
		created_at=row['created_at'],
		updated_at=row['updated_at'],
	)


class AccountStore:
	def __init__(self, db_path: Path):
		self.db_path = Path(db_path)
		self.db_path.parent.mkdir(parents=True, exist_ok=True)
		self._init_db()

	@contextmanager
	def _conn(self):
		conn = sqlite3.connect(self.db_path)
		conn.row_factory = sqlite3.Row
		try:
			yield conn
			conn.commit()
		finally:
			conn.close()

	def _init_db(self) -> None:
		legacy = False
		if self.db_path.exists():
			with self._conn() as conn:
				columns = [r[1] for r in conn.execute('PRAGMA table_info(accounts)')]
			legacy = bool(columns) and 'provider' in columns
		if legacy:
			backup = self.db_path.with_suffix('.v1.bak')
			if not backup.exists():
				shutil.copy2(self.db_path, backup)
		with self._conn() as conn:
			if legacy:
				self._migrate_v1(conn)
			conn.executescript(_SCHEMA)
			for table, added in (('accounts', _ADDED_COLUMNS), ('promo_state', _ADDED_PROMO_COLUMNS)):
				columns = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
				for column, ddl in added.items():  # older DBs predate these
					if column not in columns:
						conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
			try:
				conn.execute(_IDENTITY_INDEX)
			except sqlite3.IntegrityError:  # already duplicated: stay usable, say what to fix
				print('[STORE] 有同名且同网站的账号，它们会共用一个浏览器 profile；改名后重启即可生效')

	@staticmethod
	def _migrate_v1(conn: sqlite3.Connection) -> None:
		"""provider+custom_domain+cookies+email -> base_url+session+username."""
		from panel.newapi import parse_session

		rows = [dict(r) for r in conn.execute("SELECT * FROM accounts WHERE status != 'removed'")]
		conn.execute('ALTER TABLE accounts RENAME TO accounts_v1')
		conn.executescript(_SCHEMA)
		for row in rows:
			base_url = (row.get('custom_domain') or _LEGACY_SITES.get(row.get('provider'), '')).rstrip('/')
			session = parse_session(row.get('cookies'))
			method = 'password' if row.get('password') else ('session' if session else 'password')
			conn.execute(
				'''INSERT INTO accounts (id, name, base_url, login_method, username, password,
				       session, api_user, enabled, created_at, updated_at)
				   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)''',
				(
					row['id'], row['name'], base_url, method, row.get('email'), row.get('password'),
					session, row.get('api_user'), row.get('created_at') or _now(), _now(),
				),
			)
		conn.execute('DROP TABLE accounts_v1')

	def create(self, *, name: str, base_url: str, **fields) -> Account:
		values = {k: v for k, v in fields.items() if k in _FIELDS}
		values['name'] = name
		values['base_url'] = base_url.rstrip('/')
		values.setdefault('login_method', 'password')
		values['enabled'] = int(values.get('enabled', True))
		now = _now()
		columns = ', '.join(values) + ', created_at, updated_at'
		marks = ', '.join('?' * (len(values) + 2))
		with self._conn() as conn:
			cursor = conn.execute(
				f'INSERT INTO accounts ({columns}) VALUES ({marks})',
				(*values.values(), now, now),
			)
			row = conn.execute('SELECT * FROM accounts WHERE id = ?', (cursor.lastrowid,)).fetchone()
			return _to_account(row)

	def get(self, account_id: int) -> Optional[Account]:
		with self._conn() as conn:
			row = conn.execute('SELECT * FROM accounts WHERE id = ?', (account_id,)).fetchone()
			return _to_account(row) if row else None

	def list(self) -> list[Account]:
		with self._conn() as conn:
			return [_to_account(r) for r in conn.execute('SELECT * FROM accounts ORDER BY id')]

	def list_enabled(self) -> list[Account]:
		return [a for a in self.list() if a.enabled and a.base_url]

	def update(self, account_id: int, **fields) -> None:
		values = {k: v for k, v in fields.items() if k in _FIELDS and v is not None}
		if 'base_url' in values:
			values['base_url'] = str(values['base_url']).rstrip('/')
		if 'enabled' in values:
			values['enabled'] = int(bool(values['enabled']))
		if not values:
			return
		assignments = ', '.join(f'{k} = ?' for k in values)
		with self._conn() as conn:
			conn.execute(
				f'UPDATE accounts SET {assignments}, updated_at = ? WHERE id = ?',
				(*values.values(), _now(), account_id),
			)

	def delete(self, account_id: int) -> None:
		with self._conn() as conn:
			conn.execute('DELETE FROM accounts WHERE id = ?', (account_id,))

	def record_result(
		self,
		account_id: int,
		*,
		success: bool,
		checked_in: Optional[bool] = None,
		quota: Optional[float] = None,
		error: Optional[str] = None,
	) -> None:
		"""Store one run's result. An unknown quota keeps the last known one — blanking
		the balance column because a WAF ate one reading is worse than a stale number."""
		now = _now()
		with self._conn() as conn:
			conn.execute(
				'''UPDATE accounts
				   SET last_success = ?, last_checked_in = ?, last_quota = COALESCE(?, last_quota),
				       last_error = ?, last_run_at = ?, updated_at = ?,
				       failures = CASE WHEN ? THEN 0 ELSE failures + 1 END
				   WHERE id = ?''',
				(
					int(success),
					None if checked_in is None else int(checked_in),
					quota,
					error,
					now,
					now,
					int(success),
					account_id,
				),
			)

	def promo_state(self) -> dict[str, dict]:
		"""Per-card display state, keyed by the card id the manifest gave it."""
		with self._conn() as conn:
			return {r['promo_id']: dict(r) for r in conn.execute('SELECT * FROM promo_state')}

	def promo_seen(self, promo_id: str) -> None:
		"""Record one impression. UPSERT rather than read-modify-write, and `first_seen_at`
		is written only by the INSERT — it answers "since when has this been offered", which
		a later show must not move."""
		now = _now()
		with self._conn() as conn:
			conn.execute(
				'''INSERT INTO promo_state (promo_id, first_seen_at, last_shown_at, shows)
				   VALUES (?, ?, ?, 1)
				   ON CONFLICT(promo_id) DO UPDATE
				   SET shows = shows + 1, last_shown_at = excluded.last_shown_at''',
				(promo_id, now, now),
			)

	def promo_dismiss(self, promo_id: str) -> None:
		"""The user closed this card. The cooldown is measured from `dismissed_at` and doubled
		per `dismissals`, so the count has to be the running total and not a flag."""
		now = _now()
		with self._conn() as conn:
			conn.execute(
				'''INSERT INTO promo_state (promo_id, first_seen_at, dismissed_at, dismissals)
				   VALUES (?, ?, ?, 1)
				   ON CONFLICT(promo_id) DO UPDATE
				   SET dismissed_at = excluded.dismissed_at, dismissals = promo_state.dismissals + 1''',
				(promo_id, now, now),
			)
