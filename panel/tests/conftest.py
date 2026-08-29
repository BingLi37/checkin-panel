"""Pytest config — installs the loopback fix before any async test builds a loop.

It used to also put `anyrouter-check-in/` on `sys.path` for the cloakbrowser helpers;
those are vendored in `panel/vendor/utils/` now and import on their own."""

# Every async test needs an event loop, and a loop needs a socketpair — which the stdlib
# cannot build on a machine that relays loopback. Without this the whole suite errors out
# before reaching any assertion. No-op elsewhere. See panel/loopback.py.
from panel import loopback  # noqa: E402

loopback.install()
