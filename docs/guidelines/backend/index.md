# Backend Development Guidelines

> Best practices for backend development in this project.

---

## Overview

`panel/` is a flat FastAPI package: `newapi.py` is the protocol engine, `service.py` the one path
from a stored account to a check-in, `store.py` the SQLite layer, `scheduler.py` the daily loop,
`browser_login.py` the OAuth fallback, `loopback.py` a startup fix. No ORM, no migration tool, no
logging framework, no linter — each of those absences is deliberate and explained in the file that
covers it.

Three things to know before changing anything here:

- **`data/panel.db` holds plaintext credentials and is the only copy** (ADR-0003). Never print its
  values; back it up before touching the schema.
- **A schema or API-model change needs a panel restart.** A rebuilt frontend does not.
- **What the forks taught us lives in `docs/adr/`**, and those eight documents cost real live
  debugging. Read the relevant one before re-solving a problem.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Flat package, dependency direction, where logic lives | Filled |
| [Database Guidelines](./database-guidelines.md) | SQLite conventions, the six-place add-a-column checklist, avatar field code-spec | Filled |
| [Error Handling](./error-handling.md) | `Outcome` vs raise, `why()`, status codes, what not to overwrite | Filled |
| [Quality Guidelines](./quality-guidelines.md) | Style, forbidden patterns, test isolation, review checklist | Filled |
| [Logging Guidelines](./logging-guidelines.md) | Bracketed `print()` prefixes, what to log, what never to log | Filled |

---

## Keeping These Current

These describe **actual conventions in this codebase**, not ideals. When a live site teaches you
something, write the ADR and add the executable contract here — with the response shape you actually
saw. A rule without its evidence gets deleted by the next person who finds it inconvenient.

---

**Language**: All documentation should be written in **English**.
