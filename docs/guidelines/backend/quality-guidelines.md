# Quality Guidelines

> `panel/` has no linter and no formatter configured. Tests are the safety net, and they are
> expected to pass before anything is committed.

---

## Overview

Style is by convention, matched to the surrounding file: **tab indentation, single quotes, Python
3.11+, no `from __future__ import`**. Type hints on public functions; `Optional[X]` rather than
`X | None`, matching what is already there.

`panel/vendor/utils/` is exempt from all of the above. It is upstream's code under upstream's
conventions (`from __future__` included), vendored rather than depended on — reformatting it to
match `panel/` would make a diff against upstream unreadable, which is the only way to tell what
was changed. See `panel/vendor/README.md`.

Verification, from the repo root:

```bash
.venv/Scripts/python.exe -m pytest          # 201 tests
```

There is no global `uv` and no project-level Python outside `.venv` (ADR-0006). Always use
`.venv\Scripts\python.exe`.

---

## Forbidden Patterns

### Don't: touch `data/panel.db` outside `AccountStore`

It holds plaintext credentials (ADR-0003) and is the only copy of them. Every read and write goes
through `store.py`. Do not print credential column values, in logs or test output.

### Don't: add a column in fewer than six places

`_SCHEMA`, `_ADDED_COLUMNS`, `_FIELDS`, the `Account` dataclass, `_to_account()`, and
`AccountIn`/`_check()`. Missing `_FIELDS` is the silent one: the column reads fine and is never
written, so a `PUT` returns 200 with the new value echoed from the request model while nothing
lands. See `database-guidelines.md`.

### Don't: change `store.update()` to write `None`

It drops `None` values on purpose, so a caller cannot blank a column by omission. The account edit
form depends on this to avoid wiping fields it does not own.

### Don't: branch on an error string

`Outcome.error` and `accounts.last_error` are prose for a human. Control flow uses `success`,
`checked_in`, and the site's own status or ledger reads.

### Don't: build an event loop without `loopback.install()`

On this machine the stdlib `socketpair` refuses its own connection, and every async entry point
dies before running any panel code (ADR-0014). `run.py` and `panel/tests/conftest.py` both call it;
a new entry point must too.

### Don't: open the network from a third module

`store` owns the database, `newapi` owns HTTP to a New API site, and `promo` is the single
recorded exception: it GETs a static manifest (`panel/promo.py`, argued in its docstring) and
touches no credential. Anything else that needs the network belongs in `newapi`. Two properties
of `promo` are what make the exception defensible, so a change that breaks either is a
regression, not a refactor: the request carries **no query, body or cookie** — every targeting
rule is evaluated locally from `accounts` afterwards — and the manifest supplies **text only**,
with a `cta.url` that is not `https://` dropped rather than repaired.

The one field that is not text is `theme`, and the shape of that exception is the rule to copy
whenever remote data has to influence how something looks: it is a palette **name** matched
against a table the frontend owns (`PromoCard.tsx`'s `MESHES`), discarded when it is not a key
there. An enum lookup, never a value that reaches CSS. `sticker` is not remote at all — 全新 vs
未注册 is derived from `promo_state.first_seen_at`, because how new a site is is a fact about
this install and a manifest flag would have to be remembered and unset.

### Don't: reorder `CHECKIN_CANDIDATES`

It is ordered most-specific-first and first match wins. A fork can register two check-in routes and
answer 401 on both while only the specific one works (ADR-0013).

---

## Required Patterns

- **Dependencies as arguments, keyword-only.**
  `create_app(*, store, service, enable_scheduler=False)` and
  `CheckInService(store, *, concurrency=CONCURRENCY)` take what they need, which is what lets
  `test_app.py` pass a mock service without patching imports. Keep the `*` — a call site that
  spells out `store=` and `service=` cannot silently swap them.
- **Comments state constraints, not history.** The existing ones explain why a route is probed with
  POST, why a `session` cookie proves nothing, why a quota of `0` is a missing number. They do not
  narrate what a line does or record that something was changed.
- **Anything learned from a live site gets an ADR** under `docs/adr/`, and the code comment points
  at it rather than restating it. Eight of the traps in this codebase cost real debugging; the ADR
  is what stops the next person re-earning them.

---

## Testing Requirements

`pytest.ini` at the root sets `testpaths = panel/tests` and `asyncio_mode = auto`, so async tests
need no decorator.

**No test may hit the network or launch a browser.** How each layer is isolated:

| Module | Substitute |
|---|---|
| `newapi` | `httpx.MockTransport` serving a fake New API site |
| `browser_login` | a fake browser context |
| `service` | stubbed `newapi.check_in` / `probe` and a stubbed browser hop |
| `store` | a real SQLite file in a `TemporaryDirectory` |

Requirements for a change:

- A behaviour learned from a live site gets a test with the shape that site actually returned.
- A new column gets a round-trip test **and** a migration test that opens a database lacking it.
- A validation rule gets both the accepted and the rejected case.
- Mentally delete the feature: if the test still passes, it is tautological and needs rewriting.

A test that opens the database directly must `conn.close()` — `with sqlite3.connect(...)` commits
but does not close, and Windows will not delete an open file, so `TemporaryDirectory` cleanup fails
with `PermissionError [WinError 32]`.

---

## Code Review Checklist

- [ ] `.venv\Scripts\python.exe -m pytest` green, count not lower than before
- [ ] A new column appears in all six places
- [ ] `store.update()` still drops `None`
- [ ] No credential value in a log, a print, or test output
- [ ] Tabs, single quotes, no `from __future__`
- [ ] New site behaviour has an ADR and a test using the real response shape
- [ ] No control flow reading an error message
- [ ] No new module opening the database or the network outside `store` / `newapi` / `promo`
