"""The desktop way to run the panel — a window plus a tray icon. One of three (ADR-0016).

Double-click it and the panel opens. Clicking X does **not** quit: it asks once, offers to
remember the answer, and hides to the notification area. Left-click the tray icon to bring
it back, right-click and pick 退出 to really stop.

Threads, and why they are arranged this way:

- **main** — `webview.start()`. pywebview requires the main thread; the window's message
  loop lives here and returning from it ends the process.
- **worker** — uvicorn. The same app `run.py` serves, so a check-in behaves identically.
- **worker** — the tray icon, via `pystray.Icon.run_detached()`. pystray also wants a main
  thread of its own, and only one library can have it.
- **worker, once** — `sandbox.ensure_chromium()` when the browser is missing, so a first
  run shows the UI immediately instead of staring at a 500MB download.

Measured, and the reason there is no command queue between the tray and the window: calling
a pywebview method from pystray's own thread does **not** deadlock (probed on Windows 11 /
Python 3.14.3 before this was written). Quitting still goes through `PostMessageW(WM_CLOSE)`
rather than `window.destroy()` — a posted message cannot block by construction, and quitting
is the one path that must never wedge.

Do not run this at the same time as `start.bat`. They share `data/panel.db` and
`.browser_profiles/`; two panels means a locked database and two browsers fighting over one
profile. The mutex below stops a second *desktop* instance, and a busy port stops the rest.
"""

import os
import socket
import sys
import threading
from pathlib import Path

# The repo root, which is one level up from this package — `roots()` hands it to `prepare()`
# as the writable root, so getting it wrong means the panel opens a second, empty
# data/panel.db beside this file instead of finding the accounts (ADR-0006). A frozen build
# does not use this value at all; `sandbox.roots` explains what it uses instead.
ROOT = Path(__file__).resolve().parent.parent

# Before anything builds an event loop (ADR-0014), and before uvicorn is imported. This is
# the same sequence run.py uses; see panel/sandbox.py for why the order is not negotiable.
# The two roots differ only in a frozen build — `sandbox.roots` explains why.
from panel.sandbox import prepare, roots  # noqa: E402

_ROOT, _ASSETS = roots(ROOT)
_SANDBOX = prepare(_ROOT, assets=_ASSETS)

import uvicorn  # noqa: E402
import webview  # noqa: E402

# Absolute, not relative: PyInstaller runs this module as `__main__` in the frozen build, so
# `from .dialog import ...` has no package to resolve against. These names work both ways.
from desktop import dialog as desktop_dialog  # noqa: E402
from desktop import icon as desktop_icon  # noqa: E402
from desktop import state as desktop_state  # noqa: E402
from panel import sandbox  # noqa: E402
from panel.app import create_app  # noqa: E402
from panel.service import CheckInService  # noqa: E402
from panel.store import AccountStore  # noqa: E402

WINDOW_TITLE = '签到面板'
MUTEX_NAME = 'Global\\AnyRouterCheckInPanel'
STARTUP_TIMEOUT_S = 20.0

# The one shell. Single-window app by construction (see the mutex below), and the tray
# thread, the window thread and the close handler all have to mean the same one.
_SHELL = None

NO_FRONTEND = """<!doctype html><meta charset="utf-8">
<style>body{font:15px/1.7 system-ui,sans-serif;margin:3rem;color:#18181b}
code{background:#f4f4f5;padding:.15rem .4rem;border-radius:4px}</style>
<h2>前端还没构建</h2>
<p>面板的接口已经在跑，但界面文件不在。构建一次就好：</p>
<p><code>cd frontend</code><br><code>npm install</code><br><code>npm run build</code></p>
<p>构建完关掉这个窗口再打开一次即可，不用重新装什么。</p>"""


def _already_running() -> bool:
	"""True when another desktop instance holds the mutex.

	Two panels on one folder is the hazard worth preventing: SQLite locks, and two browsers
	writing the same profile. The handle is deliberately leaked — it must live as long as
	the process, and releasing it early would let a second copy in.
	"""
	if sys.platform != 'win32':
		return False
	import ctypes

	ERROR_ALREADY_EXISTS = 183
	ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
	return ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS


def _raise_existing_window() -> None:
	"""Bring the running instance forward, even if it is hidden in the tray."""
	hwnd = desktop_dialog.window_handle(WINDOW_TITLE)
	if not hwnd or sys.platform != 'win32':
		return
	import ctypes

	SW_SHOW = 5
	ctypes.windll.user32.ShowWindow(hwnd, SW_SHOW)
	ctypes.windll.user32.SetForegroundWindow(hwnd)


def _port_is_free(host: str, port: int) -> bool:
	"""Ask by dialling, not by binding.

	A bind probe cannot answer this on Windows. `SO_REUSEADDR` there does not mean "reuse a
	dead socket" as it does on Linux -- it means "bind anyway, even though someone holds
	this", so the probe succeeded against the panel start.bat already had open, the warning
	below never appeared, and uvicorn went on to fight it for the port (measured). A
	connection that gets answered is proof something is listening; a refusal is proof
	nothing is, because a socket that is bound but not yet listening refuses too, and that
	one uvicorn can still take.

	Only this machine is in scope: 0.0.0.0 is where uvicorn listens, not an address to dial,
	and loopback reaches a listener bound there as well as one bound to loopback itself.
	"""
	target = '127.0.0.1' if host in ('', '0.0.0.0') else host
	try:
		with socket.create_connection((target, port), timeout=0.5):
			return False
	except OSError:
		return True


def _wait_until_serving(host: str, port: int, timeout: float = STARTUP_TIMEOUT_S) -> bool:
	"""Block until uvicorn accepts a connection. Without this the window loads first and
	WebView2 shows its own error page, which looks like the panel is broken."""
	import time

	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		try:
			with socket.create_connection((host, port), timeout=0.5):
				return True
		except OSError:
			time.sleep(0.15)
	return False


class Shell:
	"""Owns the window, the tray icon and the one remembered preference."""

	def __init__(self, url: str, prefs_path: Path):
		self.url = url
		self.prefs_path = prefs_path
		self.prefs = desktop_state.load(prefs_path)
		self.quitting = False
		self.window = None
		self.icon = None

	def build_window(self, serving: bool):
		content = {'url': self.url} if serving else {'html': NO_FRONTEND}
		self.window = webview.create_window(
			WINDOW_TITLE, width=1180, height=820, min_size=(900, 600), **content
		)
		self.window.events.closing += self.on_closing
		return self.window

	def on_closing(self) -> bool:
		"""pywebview asks synchronously and takes the answer literally: False keeps the
		window. Everything worth testing about this lives in desktop_state.decide_close."""
		decision = desktop_state.decide_close(
			quitting=self.quitting,
			hide_without_asking=desktop_state.hide_without_asking(self.prefs),
			ask=lambda: desktop_dialog.ask(desktop_dialog.window_handle(WINDOW_TITLE) or 0),
		)
		if decision.remember:
			self.prefs = desktop_state.remember_hide(self.prefs_path, self.prefs)
		if decision.hide:
			self.window.hide()
		return decision.allow_close

	def open_window(self) -> None:
		"""Tray left-click. Safe from pystray's thread — measured, see the module docstring."""
		if self.window is not None:
			self.window.show()

	def quit(self) -> None:
		"""Tray 退出. Posts the close rather than calling destroy(): a posted message cannot
		block, and `quitting` makes on_closing allow it through without asking."""
		self.quitting = True
		if self.icon is not None:
			self.icon.stop()
		hwnd = desktop_dialog.window_handle(WINDOW_TITLE)
		if hwnd and sys.platform == 'win32':
			import ctypes

			WM_CLOSE = 0x0010
			ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
		elif self.window is not None:
			self.window.destroy()

	def start_tray(self) -> None:
		import pystray

		self.icon = pystray.Icon(
			'anyrouter-checkin',
			desktop_icon.image(64),
			WINDOW_TITLE,
			menu=pystray.Menu(
				pystray.MenuItem('打开面板', lambda *_: self.open_window(), default=True),
				pystray.MenuItem('退出', lambda *_: self.quit()),
			),
		)
		self.icon.run_detached()


def _serve(app, host: str, port: int) -> None:
	uvicorn.run(app, host=host, port=port, log_level='warning')


def main() -> int:
	if _already_running():
		print('[DESK] another panel is already open; bringing it to the front')
		_raise_existing_window()
		return 0

	host = os.getenv('PANEL_HOST', '127.0.0.1')
	port = int(os.getenv('PANEL_PORT', '8000'))
	if not _port_is_free(host, port):
		# Almost always start.bat already running. Sharing the folder would lock the
		# database and put two browsers on one profile, so refuse instead.
		desktop_dialog.warn(
			'端口已被占用',
			f'{host}:{port} 上已经有东西在跑，很可能是用 start.bat 开着的面板。\n\n'
			'两份面板会抢同一个数据库和同一个浏览器配置，所以这次不启动。\n'
			'先关掉那一个，或者设 PANEL_PORT 换个端口。',
		)
		print(f'[DESK] {host}:{port} is busy; not starting')
		return 1

	store = AccountStore(_SANDBOX.db_path)
	scheduler_on = os.getenv('PANEL_SCHEDULER', '1') != '0'
	app = create_app(
		store=store,
		service=CheckInService(store),
		enable_scheduler=scheduler_on,
		dist_dir=_SANDBOX.dist_dir,
	)

	print(f'[DESK] Panel on http://{host}:{port}')
	print(f'[DESK] DB: {_SANDBOX.db_path}')
	print(f'[DESK] Scheduler: {"on (every 30 min)" if scheduler_on else "off"}')
	threading.Thread(target=_serve, args=(app, host, port), daemon=True).start()

	if _SANDBOX.chromium is None:
		# Off the startup path on purpose: the UI is usable while this runs, and every
		# HTTP check-in works without it.
		threading.Thread(target=sandbox.ensure_chromium, daemon=True).start()

	serving = _wait_until_serving(host, port)
	if not serving:
		print('[DESK] the panel did not come up in time; showing the build hint instead')
	elif _SANDBOX.dist_dir is None:
		serving = False  # API is up, but there is no page to show
		print('[DESK] frontend/dist is missing; build it with `npm run build`')

	global _SHELL
	_SHELL = Shell(f'http://{host}:{port}', _SANDBOX.root / 'data' / desktop_state.PREFS_NAME)
	_SHELL.build_window(serving)
	_SHELL.start_tray()
	webview.start()
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
