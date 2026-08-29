"""Startup sequence tests — the sandbox layout (ADR-0006) and the binary-path trap.

`prepare()` mutates `os.environ`, so every test restores it; without that the first test
to run would decide the answers for the rest.
"""

import os
import sys
from pathlib import Path

import pytest

from panel import sandbox


@pytest.fixture
def clean_env(monkeypatch):
	"""Drop the sandbox vars so setdefault actually sets."""
	for key in (
		'CHECKIN_BROWSER_PROFILE_DIR',
		'CHECKIN_PROXY_URL',
		'CLOAKBROWSER_CACHE_DIR',
		'CLOAKBROWSER_BINARY_PATH',
	):
		monkeypatch.delenv(key, raising=False)


def _make_chromium(root: Path, version: str = '146.0.7680.177.5') -> Path:
	"""Lay out a binary the way cloakbrowser's own cache does."""
	binary = root / '.local' / 'cloakbrowser' / f'chromium-{version}' / sandbox.CHROME_NAME
	binary.parent.mkdir(parents=True, exist_ok=True)
	binary.write_bytes(b'not really chrome')
	return binary


def test_paths_all_land_inside_the_folder(tmp_path, clean_env):
	prepared = sandbox.prepare(tmp_path)

	assert prepared.root == tmp_path.resolve()
	assert prepared.db_path == tmp_path / 'data' / 'panel.db'
	assert os.environ['CHECKIN_BROWSER_PROFILE_DIR'] == str(tmp_path / '.browser_profiles')
	assert os.environ['CLOAKBROWSER_CACHE_DIR'] == str(tmp_path / '.local' / 'cloakbrowser')
	assert os.environ['CHECKIN_PROXY_URL'] == 'http://127.0.0.1:7897'


def test_a_real_env_var_wins_over_the_default(tmp_path, clean_env, monkeypatch):
	monkeypatch.setenv('CHECKIN_PROXY_URL', 'http://10.0.0.1:1080')
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', r'X:\elsewhere')

	sandbox.prepare(tmp_path)

	assert os.environ['CHECKIN_PROXY_URL'] == 'http://10.0.0.1:1080'
	assert os.environ['CHECKIN_BROWSER_PROFILE_DIR'] == r'X:\elsewhere'


def test_the_binary_path_is_set_only_when_the_binary_is_there(tmp_path, clean_env):
	binary = _make_chromium(tmp_path)

	prepared = sandbox.prepare(tmp_path)

	assert prepared.chromium == str(binary)
	assert os.environ['CLOAKBROWSER_BINARY_PATH'] == str(binary)


def test_no_binary_means_no_binary_path(tmp_path, clean_env):
	"""The trap: cloakbrowser raises FileNotFoundError on a path that does not exist,
	so pointing at a missing file is strictly worse than saying nothing."""
	prepared = sandbox.prepare(tmp_path)

	assert prepared.chromium is None
	assert 'CLOAKBROWSER_BINARY_PATH' not in os.environ
	# The cache dir is still declared, so a later download lands in the sandbox.
	assert os.environ['CLOAKBROWSER_CACHE_DIR'] == str(tmp_path / '.local' / 'cloakbrowser')


def test_the_newest_version_wins(tmp_path, clean_env):
	_make_chromium(tmp_path, '146.0.7680.177.5')
	newest = _make_chromium(tmp_path, '147.0.1000.1')

	assert sandbox.prepare(tmp_path).chromium == str(newest)


def test_chromium_false_skips_the_browser_entirely(tmp_path, clean_env):
	_make_chromium(tmp_path)

	prepared = sandbox.prepare(tmp_path, chromium=False)

	assert prepared.chromium is None
	assert 'CLOAKBROWSER_BINARY_PATH' not in os.environ
	assert 'CLOAKBROWSER_CACHE_DIR' not in os.environ


def test_dist_dir_is_none_until_the_frontend_is_built(tmp_path, clean_env):
	assert sandbox.prepare(tmp_path).dist_dir is None

	(tmp_path / 'frontend' / 'dist').mkdir(parents=True)
	assert sandbox.prepare(tmp_path).dist_dir == tmp_path / 'frontend' / 'dist'


def test_calling_it_twice_changes_nothing(tmp_path, clean_env):
	_make_chromium(tmp_path)

	first = sandbox.prepare(tmp_path)
	second = sandbox.prepare(tmp_path)

	assert first == second


def test_a_stale_binary_path_is_dropped_rather_than_handed_on(tmp_path, clean_env, monkeypatch):
	"""ensure_chromium must not return a path that is not there — cloakbrowser would
	raise FileNotFoundError on it instead of downloading."""
	monkeypatch.setenv('CLOAKBROWSER_BINARY_PATH', str(tmp_path / 'gone' / sandbox.CHROME_NAME))
	monkeypatch.setitem(sys.modules, 'cloakbrowser', None)  # force the ImportError branch

	assert sandbox.ensure_chromium() is None
	assert 'CLOAKBROWSER_BINARY_PATH' not in os.environ


def test_an_existing_binary_short_circuits_the_download(tmp_path, clean_env, monkeypatch):
	binary = _make_chromium(tmp_path)
	monkeypatch.setenv('CLOAKBROWSER_BINARY_PATH', str(binary))

	called = []
	monkeypatch.setitem(
		sys.modules,
		'cloakbrowser',
		type(sys)('cloakbrowser'),
	)
	sys.modules['cloakbrowser'].ensure_binary = lambda *a, **k: called.append(1)

	assert sandbox.ensure_chromium() == str(binary)
	assert called == [], 'a binary already on disk must not trigger a download'


def test_a_failed_download_is_reported_not_raised(tmp_path, clean_env, monkeypatch):
	"""Every HTTP check-in still works without a browser, so this must not be fatal."""
	fake = type(sys)('cloakbrowser')

	def boom(*a, **k):
		raise RuntimeError('no network')

	fake.ensure_binary = boom
	monkeypatch.setitem(sys.modules, 'cloakbrowser', fake)

	assert sandbox.ensure_chromium() is None
	assert 'CLOAKBROWSER_BINARY_PATH' not in os.environ


def test_a_successful_download_is_published_to_the_env(tmp_path, clean_env, monkeypatch):
	binary = _make_chromium(tmp_path)
	fake = type(sys)('cloakbrowser')
	fake.ensure_binary = lambda *a, **k: str(binary)
	monkeypatch.setitem(sys.modules, 'cloakbrowser', fake)

	assert sandbox.ensure_chromium() == str(binary)
	assert os.environ['CLOAKBROWSER_BINARY_PATH'] == str(binary)
