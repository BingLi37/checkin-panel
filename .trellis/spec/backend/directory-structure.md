# Directory Structure

> One flat package, `panel/`. Modules are layered by dependency direction, not by folders.

---

## Overview

`panel/` is a flat package of eight modules. There are no sub-packages: the layering is expressed
by **which module imports which**, and that graph is acyclic and shallow enough to keep in your
head.

```
app.py        -> service, store, newapi, scheduler   HTTP boundary
scheduler.py  -> service, store                      the daily loop
service.py    -> newapi, store                       one path from account to check-in
browser_login -> newapi                              the OAuth fallback
newapi.py     -> (nothing in panel)                  the protocol engine
store.py      -> (nothing in panel)                  persistence
loopback.py   -> (nothing)                           startup fix, imported before asyncio
```

Nothing imports upward. `newapi` and `store` know nothing about FastAPI or the scheduler, which is
why `test_newapi.py` can swap in a fake site over `httpx.MockTransport` and `test_store.py` can use
a real temp SQLite file.

---

## Directory Layout

```
panel/
├── __init__.py         empty
├── app.py         196  create_app(): routes, pydantic models, _check() validation
├── service.py     430  CheckInService: the decision tree for one check-in
├── newapi.py      599  probe / check_in / whoami / status + ledger reads
├── browser_login  640  the real-browser OAuth hop; imports cloakbrowser helpers
├── store.py       293  AccountStore + Account dataclass + schema/migrations
├── scheduler.py    76  due() / backoff_s() / run_once() / loop()
├── loopback.py    120  socketpair patch; must run before any event loop is built
└── tests/              pytest, testpaths in root pytest.ini, asyncio_mode=auto
    ├── conftest.py     puts anyrouter-check-in/ on sys.path, calls loopback.install()
    └── test_*.py       one file per module
```

Entry point is `run.py` at the repo root, not inside the package: it configures the sandbox
(ADR-0006) and then calls `panel.app.create_app`.

---

## Module Organization

**Business logic lives in `service.py`.** `service._attempt` is the whole decision tree for one
check-in — probe, then the protocol attempt, then a browser only if that failed and the login
method is OAuth. Read that function first when changing check-in behaviour.

**Protocol knowledge lives in `newapi.py`.** Everything a New API fork can do to you — WAF
challenges, route candidates, JWT refresh rotation, Turnstile — is expressed there and nowhere
else. `service` composes it; it does not re-parse responses itself.

**`app.py` stays thin.** Routes validate, delegate, and translate exceptions to status codes. No
route contains check-in logic. `create_app(store, service, enable_scheduler)` takes its
dependencies as arguments, which is what lets `test_app.py` pass a mock service.

**A new module earns its place by owning an external boundary** — a protocol, a browser, a
database, the socket layer. Do not add a `utils.py`; a helper belongs in the module whose boundary
it serves (`newapi.why`, `newapi.parse_session`).

---

## Naming Conventions

- Modules are lowercase, no underscores unless the name genuinely needs two words
  (`browser_login`).
- A leading underscore marks a module-private function, and these are used freely —
  `service._attempt`, `service._reconcile`, `browser_login._logged_in`. The public surface of each
  module is deliberately small.
- Test files mirror the module: `panel/tests/test_<module>.py`.
- Module-level constants are `SCREAMING_SNAKE` (`LOGIN_METHODS`, `CHECKIN_CANDIDATES`, `WINDOW`,
  `AVATAR_SLUG`).

---

## Examples

`store.py` is the model for a boundary module: a dataclass, a schema, an explicit migration list,
and every method taking or returning that dataclass. Nothing outside it opens the database.

`scheduler.py` at 76 lines is the model for keeping policy separate from mechanism: it decides
*when* (`due`, `backoff_s`) and delegates *what* entirely to `service`.
