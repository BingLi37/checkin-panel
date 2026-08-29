# Quality Guidelines

> Code quality standards for `frontend/`. There is no linter and no test runner, so the checks
> below are the only safety net.

---

## Verification

What exists, both run from the repo root:

```bash
node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/tsconfig.json
npm --prefix frontend run build
```

Do **not** `cd frontend` in a shell whose cwd persists across calls: a `.zcode` hook resolves
its script path from that cwd, and every later command then fails with
`can't open file '.../frontend/.zcode/hooks/...'`.

`tsconfig.json` sets `strict: true`. Keep it — with no tests, the type checker is the only thing
that catches a renamed field crossing from `api.ts` into a component.

Because nothing runs assertions, **a UI change is not verified until it has been measured in a
browser.** `tsc` passing means it compiles, not that the layout works. `component-guidelines.md`
lists what to measure and why each measurement exists.

---

## Forbidden Patterns

### Don't: interpolate a Tailwind class name

```tsx
<span className={`bg-${color}-500`} />   // no CSS is emitted for this
```

Tailwind's JIT scans source text. Spell every variant out in a lookup (see `AVATAR_SKINS`).

### Don't: reach for `!important` before finding out why the cascade lost

Two rules in `index.css` use it, and each carries a comment naming the selector it beats and
that selector's specificity. That is the bar: if you cannot state what you are overriding and
why a normal selector cannot win, the `!important` is hiding a misdiagnosis rather than fixing
one. Here the answer was a HeroUI rule at (0,3,0) emitted after the app's own stylesheet.

### Don't: branch on a nullable field as if it were a boolean

`Account.last_checked_in` and `last_quota` can both be null, and null means "nobody could tell
us" — not "no". A truthiness test on the first reported unknown state as confirmed success.
Branch on `=== true` / `=== false` / else.

### Don't: add a dependency for a small thing

`package.json` carries HeroUI, framer-motion (its peer), React and the build chain — nothing
else. Six SVG glyphs live in `icons.tsx` instead of an icon package; the toast is twelve lines
of `document.createElement` instead of a notification library. Check whether HeroUI already
exports what you need: `Avatar`, `Popover`, `Tooltip`, `Checkbox`, `Input`, `ScrollShadow` and
`Dropdown` were all already there.

### Don't: explain in a comment how something got fixed

Comments state constraints the code cannot show — why `min-w-0` is required, why a palette is
spelled out, why an event bubble is stopped. They do not say "changed to fix X" or "this is now
correct": that is PR commentary, and it is noise the moment the change merges.

---

## Required Patterns

- Server types live in `api.ts` and components consume them; no local casts of a payload field.
- An enum-keyed lookup uses `Record<Union, Meta>` so a missing member is a type error, plus a
  resolver function for values the panel might not know (`loginMethodMeta`, `resolveColor`).
- Optimistic writes roll back on failure and surface the error; they do not borrow another
  control's busy state.

---

## Testing Requirements

No framework is configured, and this project has not adopted one. The bar for a UI change is
therefore: `tsc --noEmit` clean, `npm run build` clean, and browser measurements recorded for
whatever the change claims to fix. If a change is complex enough that measurement cannot
demonstrate it, that is the signal to add a test runner — a decision for the owner, not a
silent addition.

---

## Code Review Checklist

- [ ] No interpolated Tailwind classes
- [ ] Nullable server fields branch on all three states
- [ ] New enum members are present in every `Record` lookup
- [ ] Every `!important` names what it overrides and why
- [ ] Comments state constraints, not history
- [ ] Layout claims backed by a measurement, not by eye
- [ ] Both layouts (table and cards) render from one derived source
