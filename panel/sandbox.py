"""The startup sequence every way of running the panel shares (ADR-0016).

Three entry points need the same environment before anything else happens: `run.py`,
the desktop shell, and the container. This module is the single copy of that sequence —
duplicating it would mean two copies of the loopback fix, and one of them would rot.

Order is load-bearing, not stylistic:

1. `sys.stdout` line buffering, or the scheduler's prints sit in a buffer for hours.
2. `loopback.install()` **before anything builds an event loop** (ADR-0014). uvicorn, a
   bare `asyncio.run` and every async test die at startup without it on this machine.
3. `sys.path` gains `anyrouter-check-in/`, whose cloakbrowser helpers the browser login imports.
4. Sandbox paths as env defaults (ADR-0006) — `setdefault`, so a real env var still wins.

Two roots, not one. `root` is where the panel *writes* — `data/panel.db`, `.browser_profiles/`,
the browser cache — and `assets` is where read-only bundled files were unpacked. They are the
same folder in a git checkout and in the container, and differ only in a frozen build, where
`sys._MEIPASS` is a temp directory that is deleted on exit: resolving the database against it
would put every run's accounts somewhere new and then throw them away.

Deliberately imports neither fastapi nor uvicorn: it has to be callable before they are
imported, which is the whole point of step 2.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from panel import loopback

CHROME_NAME = 'chrome.exe' if sys.platform == 'win32' else 'chrome'


@dataclass(frozen=True)
class Prepared:
	"""Where everything lives, resolved once so no caller repeats the layout."""

	root: Path
	assets: Path
	loopback_patched: bool
	db_path: Path
	dist_dir: Optional[Path]  # None when the frontend was never built
	chromium: Optional[str]  # None when no browser binary is on disk yet


def prepare(root: Path, *, assets: Optional[Path] = None, chromium: bool = True) -> Prepared:
	"""Put this process in the sandbox. Idempotent — safe to call twice.

	`assets` is where read-only bundled files live, and defaults to `root`; a frozen build
	passes `sys._MEIPASS` so the SPA and the vendored helpers are found in the unpacked
	bundle while the database stays next to the exe.

	`chromium=False` skips looking for the browser binary; nothing on the HTTP check-in
	path needs it, and a caller that will never launch a browser can skip the directory
	walk. It does not download anything either way — that is `ensure_chromium`.
	"""
	root = Path(root).resolve()
	assets = root if assets is None else Path(assets).resolve()

	# `--windowed` PyInstaller builds have no stdout at all, and reconfigure() would
	# raise AttributeError on None (print() itself is a safe no-op there).
	if sys.stdout is not None:
		sys.stdout.reconfigure(line_buffering=True)

	patched = loopback.install()

	checkin_dir = assets / 'anyrouter-check-in'
	if str(checkin_dir) not in sys.path:
		sys.path.insert(0, str(checkin_dir))

	os.environ.setdefault('CHECKIN_BROWSER_PROFILE_DIR', str(root / '.browser_profiles'))
	os.environ.setdefault('CHECKIN_PROXY_URL', 'http://127.0.0.1:7897')

	found: Optional[str] = None
	if chromium:
		# cloakbrowser's own cache layout is <cache>/chromium-<version>/chrome.exe, which is
		# already what .local/cloakbrowser holds — so pointing the cache here reuses the
		# existing download instead of pulling another copy into ~/.cloakbrowser (ADR-0006).
		cache = root / '.local' / 'cloakbrowser'
		os.environ.setdefault('CLOAKBROWSER_CACHE_DIR', str(cache))
		found = _find_chromium(cache)
		if found:
			os.environ.setdefault('CLOAKBROWSER_BINARY_PATH', str(found))

	dist_dir = assets / 'frontend' / 'dist'
	return Prepared(
		root=root,
		assets=assets,
		loopback_patched=patched,
		db_path=root / 'data' / 'panel.db',
		dist_dir=dist_dir if dist_dir.exists() else None,
		chromium=found,
	)


def roots(source_root: Path) -> tuple[Path, Path]:
	"""`(root, assets)` to hand `prepare`, whether or not this is a frozen build.

	Unfrozen they are both the repo checkout. Frozen, PyInstaller sets `sys._MEIPASS` to a
	temp directory it unpacks into and deletes on exit, so that is the assets root only —
	the writable root is the folder holding the exe, which is where a user who moves the
	build expects their accounts to travel with it (ADR-0006).
	"""
	bundled = getattr(sys, '_MEIPASS', None)
	if bundled is None:
		return Path(source_root).resolve(), Path(source_root).resolve()
	return Path(sys.executable).resolve().parent, Path(bundled).resolve()


def _find_chromium(cache: Path) -> Optional[str]:
	"""Newest binary already in the cache, or None. Never downloads."""
	if not cache.exists():
		return None
	for candidate in sorted(cache.rglob(CHROME_NAME), reverse=True):
		return str(candidate)
	return None


def ensure_chromium() -> Optional[str]:
	"""Download the browser if it is missing. Returns its path, or None on failure.

	Slow (hundreds of MB) and network-bound, so callers run it off the startup path.
	Failure is not fatal: every HTTP check-in still works, only the browser fallback and
	`visit` accounts are affected — so this reports None rather than raising.
	"""
	# A stale CLOAKBROWSER_BINARY_PATH is worse than none: ensure_binary() raises
	# FileNotFoundError on it instead of downloading.
	declared = os.environ.get('CLOAKBROWSER_BINARY_PATH', '').strip()
	if declared:
		if Path(declared).exists():
			return declared
		print(f'[PANEL] CLOAKBROWSER_BINARY_PATH points at a missing file, ignoring it: {declared}')
		del os.environ['CLOAKBROWSER_BINARY_PATH']

	try:
		from cloakbrowser import ensure_binary
	except ImportError as e:
		print(f'[PANEL] cloakbrowser is not installed, browser login unavailable: {e}')
		return None

	print('[PANEL] Downloading the browser (a few hundred MB, once)...')
	try:
		path = ensure_binary()
	except Exception as e:
		print(f'[PANEL] Browser download failed ({type(e).__name__}: {e}); HTTP check-in still works')
		return None

	os.environ['CLOAKBROWSER_BINARY_PATH'] = str(path)
	print(f'[PANEL] Browser ready: {path}')
	return str(path)
