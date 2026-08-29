"""Panel launcher — the console way to run it. One of three (ADR-0016).

The other two are `desktop/__main__.py` (a window plus a tray icon) and the container image.
All three share `panel.sandbox.prepare()`, which is where the sandbox layout (ADR-0006)
and the loopback fix (ADR-0014) live; this file only adds uvicorn and the console output.

Local-only: check-ins run in this process over HTTP (panel/newapi.py) and the built-in
scheduler keeps them going while the panel is open. No GitHub Actions, no secrets to sync.

Env vars (read at startup):
  PANEL_PORT        — port to listen on (default 8000)
  PANEL_HOST        — bind address (default 0.0.0.0, LAN-reachable; 127.0.0.1 = local-only).
                      There is no auth layer and /api/accounts returns credentials in the
                      clear (ADR-0003), so this is the trust boundary.
  PANEL_SCHEDULER   — "0" to disable the daily auto check-in loop
  CHECKIN_PROXY_URL — proxy for the browser login (default http://127.0.0.1:7897)

Usage:
  .venv\\Scripts\\python.exe run.py
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Before anything builds an event loop: on a machine that relays loopback (a local
# transparent proxy) the stdlib socketpair refuses its own connection, and asyncio needs
# one per loop — so uvicorn dies at startup. Hence prepare() ahead of the uvicorn import,
# and hence this import order, which is not free to rearrange. See panel/sandbox.py.
from panel.sandbox import prepare  # noqa: E402  (must run before uvicorn imports)

_SANDBOX = prepare(PROJECT_ROOT)

import uvicorn  # noqa: E402

from panel.app import create_app  # noqa: E402
from panel.service import CheckInService  # noqa: E402
from panel.store import AccountStore  # noqa: E402


def main():
	store = AccountStore(_SANDBOX.db_path)
	scheduler_on = os.getenv('PANEL_SCHEDULER', '1') != '0'
	app = create_app(
		store=store,
		service=CheckInService(store),
		enable_scheduler=scheduler_on,
		dist_dir=_SANDBOX.dist_dir,
	)

	port = int(os.getenv('PANEL_PORT', '8000'))
	host = os.getenv('PANEL_HOST', '0.0.0.0')
	print(f'[PANEL] Starting on http://{host}:{port}')
	print(f'[PANEL] DB: {_SANDBOX.db_path}')
	print(f'[PANEL] Scheduler: {"on (every 30 min)" if scheduler_on else "off"}')
	if _SANDBOX.loopback_patched:
		print('[PANEL] Loopback is intercepted on this machine; using an authenticated socketpair')
	uvicorn.run(app, host=host, port=port, log_level='info')


if __name__ == '__main__':
	main()
