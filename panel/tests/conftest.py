"""Pytest config — puts anyrouter-check-in/ on sys.path so panel.browser_login can
import the cloakbrowser helpers. Nothing on the daily check-in path needs it."""
import sys
from pathlib import Path

CHECKIN_DIR = Path(__file__).resolve().parent.parent.parent / 'anyrouter-check-in'
if str(CHECKIN_DIR) not in sys.path:
	sys.path.insert(0, str(CHECKIN_DIR))

# Every async test needs an event loop, and a loop needs a socketpair — which the stdlib
# cannot build on a machine that relays loopback. Without this the whole suite errors out
# before reaching any assertion. No-op elsewhere. See panel/loopback.py.
from panel import loopback  # noqa: E402

loopback.install()
