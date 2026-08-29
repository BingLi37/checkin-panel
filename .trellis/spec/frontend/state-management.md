# State Management

> No store, no context, no cache library. All state lives in `App.tsx` and flows down as props.

---

## Overview

One screen, one owner. `App.tsx` holds every piece of state and passes what children need:

```ts
accounts   Account[]              the server list, the single source of truth
loading    boolean                first load / refresh in flight
error      string | null          last failure, rendered as a banner
busyId     number | null          which row has an action running
batching   boolean                a batch check-in is running
editing    Account | null         which account the modal is editing
selected   Set<string>            checked rows, keyed by String(id)
search     string                 the raw query
```

`selected` is `Set<string>` because that is what HeroUI's `Table` hands back and expects. Convert
to numbers only at the call boundary (`Array.from(selected, Number)`), so the two representations
never drift.

---

## State Categories

**Server state** is `accounts`, refetched wholesale. There is no per-row cache and no
normalisation: the list is fourteen rows on one machine, and a full `GET /api/accounts` after any
mutation is simpler and always correct.

**Local UI state** is everything else above. None of it is persisted; a reload starts clean. The
one thing that *is* persisted — a per-account avatar colour and shape — went into SQLite rather
than `localStorage`, because it is a property of the account, not of the browser looking at it.

**Derived state is computed inline, not stored.** No `useState` mirrors a value that can be
calculated:

```ts
const query = search.trim().toLowerCase()
const visible = query ? accounts.filter(…) : accounts
const enabledVisibleIds = visible.filter((a) => a.enabled).map((a) => a.id)
const batchTargets = selectedIds.length ? selectedIds : enabledVisibleIds
const rendered = visible.map((acc) => ({ … }))   // one row model, two layouts
```

`useMemo` is used only where the derivation is order-dependent and must not jitter
(`defaultSkins`). Everything else is cheap enough to recompute per render.

---

## When to Use Global State

Not yet, and probably not ever at this size. Promote only when a second screen appears and needs
the same server list. Until then, props from `App.tsx` are the mechanism, and a prop drilled two
levels is not a reason to add a context.

---

## Server State

- Mutate, then refetch. `run()`'s `finally` always calls `refresh()`, so the list reflects the
  server rather than an assumption about what the server did.
- `refresh()` never clears `error`, on purpose. It runs inside that same `finally`; clearing there
  erased every failure before React could render it.
- One exception to mutate-then-refetch: `saveAvatar` updates `accounts` optimistically and rolls
  the previous value back on failure, because a colour pick should feel instant and cannot
  meaningfully fail halfway.
- A filtered view must not change what a bulk action touches without saying so. When a query is
  active, the batch button targets only visible enabled rows **and** its label says
  `签到筛选结果 (n)`; the header checkbox likewise selects from `visible`, not `accounts`.

---

## Common Mistakes

### Common Mistake: a bulk action whose scope does not match what is on screen

**Symptom**: with a search active, "全部签到" would have checked in all fourteen accounts.
**Cause**: the `'all'` branch of `onSelectionChange` and the batch target both read `accounts`.
**Fix**: both read `visible`; the label changes so the narrower scope is visible.
**Prevention**: any action derived from a list has to be derived from the *filtered* list, and say
which one it used.

### Common Mistake: storing what can be derived

An early sketch kept a `filtered` state updated in an effect. It went stale for one render every
time `accounts` refreshed. Derive during render instead.

### Common Mistake: rendering two layouts from two sources

The phone card list and the desktop table started as separate JSX with their own inline
derivations, and immediately disagreed about the busy flag. They now share one `rendered` array
and one `actionRow(row, variant)` renderer.
