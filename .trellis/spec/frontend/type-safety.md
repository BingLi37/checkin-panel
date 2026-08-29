# Type Safety

> TypeScript `strict: true`, no runtime validation library. With no tests, the compiler is the
> main safety net — so the types have to be worth trusting.

---

## Overview

`tsconfig.json` sets `strict: true`, `noFallthroughCasesInSwitch: true`, `isolatedModules: true`.
Keep them. There is no Zod, Yup or io-ts: server responses are trusted because the server is the
same project on the same machine (`run.py` binds 127.0.0.1), and the API is validated on the
Python side.

That trust has a limit, and it is worth naming: `api.ts` describes what the server *should*
return. It is a declaration, not a proof. Where a wrong value would break rendering rather than
merely look odd, add a resolver instead of widening the type — see Validation below.

---

## Type Organization

**`api.ts` is the only place that describes the wire format.** Every server shape lives there:
`Account`, `AccountInput`, `Outcome`, `SiteInfo`, plus the unions `LoginMethod` and `Mechanism`.
Components import those types; they never redeclare a payload shape or cast a field locally.

Types that belong to a module's own vocabulary live with that module:

```ts
// avatar.ts — the palette is the source, the type is derived from it
export const AVATAR_COLORS = ['slate', 'blue', 'violet', 'emerald', 'amber', 'rose', 'cyan', 'fuchsia'] as const
export type AvatarColor = (typeof AVATAR_COLORS)[number]
```

Deriving the union from the array with `as const` means the list and the type cannot disagree, and
the array stays iterable for rendering the swatches.

Local shapes that never cross a module boundary stay inline (`ChipState`, `RowAction`).

---

## Validation

The pattern here is **exhaustive lookup plus tolerant resolver**:

```ts
// A missing key is a compile error — that is the point.
const LOGIN_METHOD_META: Record<LoginMethod, { label: string; Icon: FC<IconProps> }> = { … }

// A value the panel has not heard of still has to render.
export const loginMethodMeta = (method: string) =>
  LOGIN_METHOD_META[method as LoginMethod] ?? { label: method, Icon: KeyIcon }
```

`Record<Union, T>` catches the case a developer causes (adding a `LoginMethod` and forgetting the
icon). The resolver catches the case the database causes (a slug written by an older or newer
version). Both are needed; neither substitutes for the other.

`avatar.ts` does the same for stored slugs, and it matters more there because the backend
deliberately validates only their *shape*:

```ts
export const resolveColor = (slug: string | null, fallback: AvatarColor): AvatarColor =>
  (AVATAR_COLORS as readonly string[]).includes(slug ?? '') ? (slug as AvatarColor) : fallback
```

`panel/app.py` accepts any `^[a-z]{2,16}$` for `avatar_color`, so the palette has exactly one
owner (this file) rather than two that can drift. The price of that decision is that the frontend
**must** degrade on an unknown value, and `resolveColor` is where it pays.

---

## Common Patterns

- **Three-valued fields are typed as such and branched as such.** `last_checked_in: boolean | null`
  where null means "nobody could tell us". Branch `=== true` / `=== false` / else; never rely on
  truthiness. Same for `last_quota: number | null`.
- **`as const` on data tables** (`ACTION_SLOTS`, `AVATAR_SHAPES`) so keys and literal widths stay
  in the type rather than widening to `string`.
- **Index into a derived array's element type** rather than exporting a one-off interface:
  `(typeof rendered)[number]` is the parameter type of `actionRow`.
- **A discriminating string parameter over a boolean** when a function serves two layouts:
  `actionRow(row, 'table' | 'card')` reads at the call site; `actionRow(row, true)` does not.

---

## Forbidden Patterns

- No `any`. If a type is genuinely unknown, model it as `unknown` and narrow.
- No non-null assertion (`!`) unless genuinely unavoidable. `main.tsx` has the one legitimate
  case (`getElementById('root')!`), where the element is guaranteed by `index.html`.
- No casting a server field to a narrower type without a resolver behind it. `slug as AvatarColor`
  appears exactly once, inside `resolveColor`, guarded by an `includes` check.
- No `@ts-ignore` / `@ts-expect-error`. There are none in the codebase; a suppression here would
  be hiding the only automated check the frontend has.
- Do not loosen `Record<Union, T>` to `Partial<Record<Union, T>>` to silence a missing key. Add
  the key.
