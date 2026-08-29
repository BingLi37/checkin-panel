# Hook Guidelines

> Hooks in `frontend/`. There is no data-fetching library — `useState` plus `fetch` is the whole
> mechanism.

---

## Overview

Two custom hooks exist: `useStuck` (own file, owns an `IntersectionObserver`) and HeroUI's
`useDisclosure` for the modal. Everything else is `useState` / `useEffect` / `useMemo` inside
`App.tsx`.

Extract a hook when it **owns a browser API or a subscription** that needs cleanup. Do not
extract one just to shorten a component — `App.tsx` keeps its list state, busy state and search
state inline, and that is easier to follow than five one-line hooks.

---

## Custom Hook Patterns

A hook that observes the DOM returns the ref to attach plus the derived value, and disconnects in
cleanup:

```ts
export function useStuck(): { sentinel: RefObject<HTMLDivElement>; stuck: boolean } {
  const sentinel = useRef<HTMLDivElement>(null)
  const [stuck, setStuck] = useState(false)

  useEffect(() => {
    const node = sentinel.current
    if (!node) return
    const observer = new IntersectionObserver(([entry]) => setStuck(!entry.isIntersecting), { threshold: 1 })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  return { sentinel, stuck }
}
```

Prefer an observer over a `scroll` / `resize` listener. A scroll listener that calls `setState`
runs on every frame; an `IntersectionObserver` fires only when the answer changes.

---

## Data Fetching

`api.ts` wraps `fetch` and throws `Error` with the server's `detail` when the response is not ok.
There is no React Query and no SWR. The pattern in `App.tsx`:

- `refresh()` is a `useCallback` that reloads the list and sets `error` on failure. It
  deliberately does **not** clear `error` — it runs inside `run()`'s `finally`, and clearing there
  wiped every failure before it could render.
- `run(id, action, onDone)` wraps a single-account mutation: sets `busyId`, clears `error`, awaits,
  toasts, then always refreshes.
- An optimistic write that should not spin the row's buttons bypasses `run()` entirely
  (`saveAvatar`): it updates local state, awaits the PUT, and restores the previous value on
  failure. Reason: `run()` sets `busyId`, which would put the row's 签到 button into a loading
  state because someone picked a colour.

---

## Naming Conventions

`useThing.ts`, named export, one hook per file. Return an object rather than a tuple when there
is more than one value, so call sites read `{ sentinel, stuck }` instead of positional
destructuring.

---

## Common Mistakes

### Common Mistake: `useMemo` over derived data instead of source data

**Symptom**: avatar colours changed while typing in the search box.
**Cause**: memoising `defaultSkins(visible)` — the filtered list — so the colour assignment was
recomputed against a different group each keystroke.
**Fix**: `useMemo(() => defaultSkins(accounts), [accounts])`, over the full list.
**Prevention**: when a derivation must be stable, feed it the stable input, not the view.

### Common Mistake: a state update that fights the browser

**Symptom**: the sticky toolbar flickered at its threshold.
**Cause**: the bar shrinks when it sticks, Chrome's scroll anchoring "corrected" `scrollTop` for
that, the sentinel re-entered view, and the hook flipped back — a loop.
**Fix**: `html { overflow-anchor: none }`. See `component-guidelines.md` for the full note.
**Prevention**: when an effect changes layout, ask what the browser will do about that change.
