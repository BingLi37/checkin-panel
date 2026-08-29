# Error Handling

> `panel/` has no custom exception classes. Failure is carried either as a returned `Outcome` or
> as a stdlib exception, and which one depends on the layer.

---

## Overview

Three mechanisms, deliberately separate:

| Layer | Failure shape | Why |
|---|---|---|
| `newapi` / `service` — one check-in | `Outcome(success=False, error='…')` **returned** | a failed check-in is a normal result the owner must read, not an exception |
| preparatory work — no session, no such account, no login entry point | `raise RuntimeError('中文原因')` | there is no result to report *about* |
| `app.py` — the HTTP boundary | `raise HTTPException(status_code, detail='中文原因')` | the only layer that speaks status codes |

---

## Error Types

There are **none defined**. `RuntimeError` with a written-out Chinese reason has been enough, and
a hierarchy nobody catches by type would be ceremony. `ValueError` appears only for genuine
argument errors (`loopback.py` rejecting an unsupported socket family, `browser_login` rejecting a
non-OAuth provider).

Before adding an exception class, ask what would `except` it specifically. If nothing would, a
`RuntimeError` carrying a good message is the better answer.

---

## Error Handling Patterns

### `Outcome` is the result type for a check-in

`service.check_in` does not raise for a site-side failure. It catches everything around one
account and converts:

```python
except Exception as e:  # network/browser/anything — one account must not kill a batch
    outcome = newapi.Outcome(False, error=newapi.why(e))
```

That is load-bearing for `check_in_many`: one site being down must not abort the other thirteen
accounts. `Outcome.error` is a **string for humans** in the owner's words (`凭据无效: …`,
`登录失败: …`). Never branch on its text.

### `newapi.why(e)` — always name the class

```python
def why(e: BaseException) -> str:
	text = str(e).strip()
	return f'{type(e).__name__}: {text}' if text else type(e).__name__
```

Use this anywhere a reason reaches a human. httpx raises some errors with an empty message, and
`ConnectError: ` in a log says strictly nothing.

### Do not let one bad reading destroy good data

Error handling here includes *what not to write*. A failed read is not a zero:

- `store.record_result` keeps the previous `last_quota` via `COALESCE` — blanking a known balance
  because a WAF ate one response is worse than a stale number (ADR-0010).
- `checked_in=None` means "nobody could tell us" and is stored as SQL `NULL`, not collapsed to
  false. Three states, three values, all the way to the UI.

---

## API Error Responses

FastAPI's default `{"detail": …}`; `api.ts` reads `.detail`. Codes in use:

| Code | When |
|---|---|
| 201 / 204 | account created / deleted |
| 400 | `bootstrap` or `browser-login` could not complete |
| 404 | no such account (`_require`) |
| 409 | duplicate `(name, base_url)` — from `sqlite3.IntegrityError` |
| 422 | field validation in `_check()` |
| 502 | `probe` could not reach the site |

Two rules the existing handlers follow:

- **`detail` is Chinese, and says what to do about it.** Compare the duplicate-name message: not
  "duplicate key" but an explanation that the name also keys the browser profile, so a clash would
  silently check in the same identity twice.
- **Translate the exception, never leak it.**
  `raise HTTPException(status_code=502, detail=newapi.why(e)) from e` — `from e` keeps the chain
  for the log while the SPA gets something readable.

---

## Common Mistakes

### Common Mistake: raising where the caller needs a result

**Symptom**: a batch check-in aborts partway and reports nothing for the remaining accounts.
**Cause**: letting an exception escape the per-account boundary.
**Fix**: catch around the single account, convert with `newapi.why`, return an `Outcome`.
**Prevention**: ask whether the caller must keep going. If yes it needs a value, not a raise.

### Common Mistake: reporting an exception without its class

`str(e)` on a bare `httpx.ConnectError` is the empty string. Always `newapi.why(e)`.

### Common Mistake: branching on an error message

`Outcome.error` and `accounts.last_error` are prose for a human. Control flow reads `success`,
`checked_in`, and the status/ledger reads — never the text.
