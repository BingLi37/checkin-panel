# Logging Guidelines

> `print()` with a bracketed prefix. No `logging` module, no levels, no log files written by the
> panel itself.

---

## Overview

The panel is a local single-user tool the owner starts from a terminal and watches. `print()` to
stdout goes straight to that terminal, and `run.py` makes it line-buffered:

```python
sys.stdout.reconfigure(line_buffering=True)  # or the scheduler's prints sit in a buffer for hours
```

That line is not decoration. Without it, a scheduler that only prints every 30 minutes produces
nothing visible until the buffer fills.

`uvicorn` handles request logging at `log_level='info'`. The panel adds only what uvicorn cannot
see: startup facts and what the daily loop did.

Do not introduce `logging` for its own sake. It would buy levels and handlers that nobody
configures, and lose the property that every line is already readable at a glance.

---

## Prefixes instead of levels

There are no levels. Each line starts with the subsystem in brackets, so a terminal that mixes
uvicorn output with panel output is still scannable:

| Prefix | Source | Content |
|---|---|---|
| `[PANEL]` | `run.py` | startup facts: port, DB path, scheduler on/off, loopback patched |
| `[SCHED]` | `scheduler.py` | one line per account per tick, plus tick-level failures |
| `[STORE]` | `store.py` | migration problems that need the owner to act |

Add a new prefix only for a new subsystem, and keep it four to five characters so the lines stay
aligned.

---

## Structured Logging

Not structured, but **consistently shaped**. The scheduler's per-account line is the pattern to
follow — fixed field order, `key=value` for the data, the human reason last:

```python
state = 'OK' if outcome.success else 'FAIL'
print(
	f'[SCHED] #{account_id} {state} checked_in={outcome.checked_in} '
	f'quota={outcome.after_quota} {outcome.error or ""}'.rstrip()
)
```

Two details worth copying: the account is identified by **id**, never by name (see below), and
`.rstrip()` keeps the line clean when there is no error to append.

---

## What to Log

- **Startup facts that change behaviour**: which port, which DB file, whether the scheduler is on,
  whether the loopback patch was needed. These are the first questions asked when the panel
  misbehaves, and they cost nothing to print once.
- **Every scheduled attempt, success or failure.** The daily loop runs unattended; a run that left
  no trace cannot be audited afterwards.
- **A tick that failed as a whole**, with the exception class:
  `print(f'[SCHED] tick failed: {type(e).__name__}: {e}')`. A scheduler that dies silently is
  worse than a noisy one, which is why `loop()` catches broadly and keeps going.
- **A migration that could not complete**, with the fix in the message. `store.py` cannot create
  its unique index when duplicates already exist, so it says so and names what to change.

---

## What NOT to Log

`data/panel.db` holds **plaintext credentials** (ADR-0003) and is the only copy of them. The
`accounts` row is therefore never printed whole. Specifically, never log:

- `password`, `access_token`, `session`, or any `Outcome.session` / `Outcome.access_token`
- a `new_api_refresh` value — it rotates, and a spent one in a log is both useless and misleading
- cookies, `Authorization` headers, or a Turnstile token
- `username` where a stable id would do

Identify an account by `#{id}` in logs. It is shorter, it is unambiguous, and it keeps a site
username out of a terminal that may be screen-shared. `app.py` strips credentials on the way out
of the API for the same reason:

```python
body.pop('session', None)  # never hand a session back to the browser
body.pop('access_token', None)
```

A debug print added while chasing a bug counts as logging. Remove it before committing, or write
it so it could stay.
