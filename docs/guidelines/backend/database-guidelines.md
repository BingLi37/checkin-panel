# Database Guidelines

> SQLite conventions for `panel/store.py`. There is no ORM and no migration tool.

---

## Overview

One table, `accounts`, in `data/panel.db`. Access goes through `AccountStore` only —
no module outside `panel/store.py` opens the file. Plaintext credentials, single user,
no auth layer (ADR-0003); `data/panel.db` is the only copy of them, so back it up before
touching the schema and never print column values in logs or test output.

`AccountStore._conn()` is a contextmanager that commits on exit. It does **not** close the
handle for you in tests that open the file directly — see Common Mistakes.

---

## Migrations

There is no migration framework. Two mechanisms, both in `_init_db()`:

1. `_SCHEMA` — `CREATE TABLE IF NOT EXISTS`. Only ever consulted for a **fresh** DB.
2. `_ADDED_COLUMNS` — `{column: ddl}`. On every startup, any key missing from
   `PRAGMA table_info(accounts)` is added with `ALTER TABLE ... ADD COLUMN`.

So **adding a column means editing both**, plus four more places. The full checklist:

| Place | File | Why |
|---|---|---|
| `_SCHEMA` | `panel/store.py` | fresh databases |
| `_ADDED_COLUMNS` | `panel/store.py` | existing databases |
| `_FIELDS` | `panel/store.py` | `create()` / `update()` whitelist — a column absent here is silently unwritable |
| `Account` dataclass | `panel/store.py` | the read model |
| `_to_account()` | `panel/store.py` | by-name row → dataclass |
| `AccountIn` (+ `_check()`) | `panel/app.py` | HTTP contract and validation |

Column **order** differs between a fresh DB (`_SCHEMA` position) and a migrated one
(appended). That is harmless because every read is `SELECT *` plus by-name access — do not
introduce positional row access.

A column may only be **added**, never renamed or dropped: the old shape has to keep opening.

---

## Query Patterns

- `create()` and `update()` build their SQL from `_FIELDS` and refuse anything else.
- `update()` drops `None` values, so a caller cannot blank a column by omission. This is
  load-bearing, not an oversight — see the avatar contract below.
- `record_result()` keeps the previous `last_quota` via `COALESCE`: a failed read must not
  blank a known balance (ADR-0010).

---

## Code-Spec: per-account display fields (`avatar_color`, `avatar_shape`)

### 1. Scope / Trigger

Schema change + cross-layer contract change: two nullable columns that the SPA writes on
its own, outside the account edit form.

### 2. Signatures

```sql
ALTER TABLE accounts ADD COLUMN avatar_color TEXT;  -- nullable, no default
ALTER TABLE accounts ADD COLUMN avatar_shape TEXT;  -- nullable, no default
```

```python
# panel/app.py
AVATAR_SLUG = re.compile(r'[a-z]{2,16}')
class AccountIn(BaseModel):
    avatar_color: Optional[str] = None
    avatar_shape: Optional[str] = None
```

`PUT /api/accounts/{id}` accepts either field alone (`AccountPatch` makes `name`,
`base_url`, `login_method` optional).

### 3. Contracts

| Field | Type | Constraint | Meaning of `null` |
|---|---|---|---|
| `avatar_color` | `string \| null` | `^[a-z]{2,16}$` | nobody picked one → client derives a deterministic default |
| `avatar_shape` | `string \| null` | `^[a-z]{2,16}$` | client default (`letter`) |

The backend validates **shape, not membership**. The palette (`slate`, `blue`, `violet`,
`emerald`, `amber`, `rose`, `cyan`, `fuchsia`) and the shape vocabulary (`letter`, `dot`)
live only in `frontend/src/avatar.ts`; an unrecognised slug falls back client-side. This
keeps one list instead of two that can drift. No new env keys.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| `'blue'`, `'dot'` | 200, stored verbatim |
| `'Blue'` (uppercase), `'b'` (too short), 17+ chars, `'blue-500'`, `'鲜红'` | 422 with a Chinese `detail` |
| field omitted | previous value preserved (`update()` drops `None`) |
| field never set | `null` in every response |

### 5. Good / Base / Bad Cases

- Good: `PUT {"avatar_color": "emerald", "avatar_shape": "letter"}` → 200, survives restart.
- Base: full edit-form `PUT` without either field → both keep their stored values.
- Bad: `PUT {"avatar_color": "EMERALD"}` → 422, nothing written.

### 6. Tests Required

- `panel/tests/test_store.py`: round-trip both fields; single-field `update()` leaves the
  other alone; **open a hand-built pre-avatar `accounts` table and assert both columns get
  added** and `list()` still works.
- `panel/tests/test_app.py`: POST/PUT round-trip; the 422 matrix above, parametrised over
  both fields; a full edit `PUT` that omits them does not clear stored values.

### 7. Wrong vs Correct

#### Wrong

```python
_ADDED_COLUMNS = {'avatar_color': 'TEXT'}          # forgot _FIELDS
store.update(account_id, avatar_color='emerald')   # silently does nothing
```

Nothing raises. The endpoint answers 200, the SPA optimistically shows the new colour, and
the value is gone on the next reload.

#### Correct

```python
_ADDED_COLUMNS = {'avatar_color': 'TEXT', 'avatar_shape': 'TEXT'}
_FIELDS = (..., 'avatar_color', 'avatar_shape')
```

---

## Common Mistakes

### Common Mistake: a column that reads but never writes

**Symptom**: `PUT` returns 200 with the new value echoed back, and a reload shows the old one.
**Cause**: the column is in `_SCHEMA` / `Account` / `_to_account` but not in `_FIELDS`, so
`create()`/`update()` filter it out; the echoed response is the *request* model, not a re-read.
**Prevention**: work the six-place table above top to bottom, and assert persistence after a
fresh `AccountStore` on the same file — not just on the return value.

### Common Mistake: a test that opens `data/panel.db` directly leaks the handle

**Symptom**: `PermissionError [WinError 32]` when `TemporaryDirectory` cleans up.
**Cause**: `with sqlite3.connect(...)` commits but does not close. Windows will not delete an
open file.
**Fix**: call `conn.close()` explicitly in tests that bypass `AccountStore`.

### Common Mistake: adding a field and not restarting the panel

`_init_db()` runs at construction, and `AccountIn` is fixed at import. A panel started before
the change keeps the old schema and old model, and FastAPI/pydantic **ignore unknown request
fields** — so the new frontend's write is accepted and dropped. Restart the panel after any
schema or API-model change; a `frontend/dist` rebuild alone does not need one.

