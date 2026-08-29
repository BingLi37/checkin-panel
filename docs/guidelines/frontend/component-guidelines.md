# Component Guidelines

> How components are built in `frontend/src/`. React 18 + `@heroui/react` 2.8.10 +
> Tailwind 3, TypeScript strict, Vite.

---

## Overview

Flat layout: every module sits directly in `frontend/src/` (`App.tsx`, `AccountForm.tsx`,
`AccountAvatar.tsx`, `avatar.ts`, `icons.tsx`, `useStuck.ts`, `api.ts`). No `components/`
or `hooks/` directories — do not introduce a tree for a handful of files.

Style: 2-space indent, single quotes, no semicolons. All UI copy is Chinese. `api.ts` owns
every server type; components take those types rather than re-declaring payload shapes.

Split rules that this codebase follows:

- Pure data and derivations go in a `.ts` module (`avatar.ts`: palette, `initials()`,
  `defaultSkins()`), not inside the component that renders them.
- Icons are inline SVG in `icons.tsx` using `currentColor`. There is no icon dependency and
  adding one for a handful of glyphs is not worth a package.
- An enum-keyed lookup uses `Record<Union, Meta>` (`LOGIN_METHOD_META`) so a missing member
  is a type error. That is the point — do not loosen it to `Partial<Record<...>>`.

---

## Styling Patterns

### Tailwind class strings must be literal

Tailwind's JIT scans source text. An interpolated class name produces no CSS:

```tsx
// Don't — the stylesheet will not contain this rule
<span className={`bg-${color}-500`} />

// Do — spell every variant out once, in a lookup
export const AVATAR_SKINS = {
  emerald: { letter: 'bg-emerald-500 text-white', dot: 'bg-gradient-to-br from-emerald-400 to-emerald-600' },
} as const
```

### `truncate` inside a flex row needs `min-w-0`

A flex child defaults to `min-width: auto`, which refuses to shrink below its content, so
`truncate` silently does nothing:

```tsx
<div className="flex items-center gap-2 min-w-0">
  <Avatar … />
  <span className="truncate max-w-[168px]">{name}</span>
</div>
```

Pair truncation with a `Tooltip showArrow` carrying the full text; a clipped name with no way
to read it is worse than a wrapped one.

### Sticky elements

Use a zero-height sentinel plus `IntersectionObserver` (`useStuck.ts`) to detect the stuck
state. Do not listen to `scroll` — that sets React state every frame. Check first that no
ancestor establishes an overflow container, or `position: sticky` silently does nothing.

> **Warning**: a sticky bar that **changes height** when it sticks needs `overflow-anchor: none`
> on `html`, or it flickers at the threshold. Chrome's scroll anchoring sees the shrink as content
> moving above the anchor and rewrites `scrollTop` to compensate; that scrolls the sentinel back
> into view, the bar unsticks, grows again, and the loop repeats for as long as the scroll rests
> near the boundary. Measured on this panel: scrolling to y=26 stuck the bar, the bar shrank,
> Chrome snapped `scrollTop` to 0, and it unstuck — every frame. Anchoring has nothing legitimate
> to correct here, because the height change *is* the intended response to the scroll.
>
> To check a change to this: step `window.scrollTo` through the threshold a pixel at a time, and
> after each step read the stuck class twice a few hundred ms apart. The two reads must agree, and
> `window.scrollY` must still equal what you asked for.

### Responsive layout: measure, do not guess the breakpoint

A HeroUI `Table` is a real `<table>` with `table-layout: auto`, so its width is the sum of what
its content needs — hiding a column with `hidden lg:table-cell` is the only way to make it
narrower. Below the width where the essential columns still fit, switch to cards instead: the
alternative is a horizontal scrollbar, and reaching the last action column means hunting for it.

The account list does this at `lg`. The staged columns above it (`xl` adds 登录方式 and 最近运行,
`2xl` adds 网站) were picked by measuring the table against its wrapper at each viewport, not by
taste — at 1280 the eight-column table wanted 1242px against a 1217px wrapper, and 25px of
overflow is still a scrollbar. Re-measure after changing a column's content:

```js
const table = document.querySelector('table')
table.scrollWidth > table.parentElement.clientWidth  // must be false
```

Anything a hidden column carried has to reappear somewhere, or a narrow window silently loses
information. Here the name cell grows a second line with host and mechanism below `2xl`.

Derive once, render twice. The card list and the table share one `rendered` array built from
`visible` (status chip, method meta, busy flag, action list, avatar element), and one `actionRow`
renderer parameterised by layout; that is what stops the two from drifting apart as the row gains
fields.

A row of buttons stays a row: `flex-nowrap` plus `shrink-0`, and fixed-width slots for the actions
only some rows have (`ACTION_SLOTS`), so every row's buttons sit at the same offsets whether or not
it has 浏览器登录. Never give one of them `flex-1` — it then changes width row to row depending on
what else is present, which is exactly the wobble the slots exist to prevent. Where the phone is
too narrow for the desktop labels, shrink the buttons (`min-w-0 px-2 text-tiny`) rather than
letting them wrap; five actions then fit 310px with room to spare, measured.

---

## HeroUI specifics measured in this project

> **Warning**: `Avatar`'s `color` prop has only six semantic values
> (`default`/`primary`/`secondary`/`success`/`warning`/`danger`, verified in
> `@heroui/theme/dist/chunk-BGEKJ4Q5.mjs`). A custom palette must go through `classNames`,
> not `color`.

> **Warning**: `Avatar`'s default `getInitials` is `safeInitials`, which splits on separators
> and takes first characters — so feeding it a pre-computed `'AG'` renders `A`. Pass the full
> name plus your own `getInitials`; the full name also becomes the initials span's label.

> **Warning**: `Avatar`'s `icon` slot is already sized `w-full h-full` by the theme. Use it for
> a non-letter face and the shape inherits the avatar's exact diameter; sizing the child
> yourself (`w-5 h-5`) makes it visibly smaller than the letter variant beside it.

### Focus rings: the white box is Chrome's, not HeroUI's

HeroUI 2.8 is built for **Tailwind v4** and puts `outline-solid outline-transparent` on its
controls. `outline-solid` does not exist in Tailwind 3, so `outline-style` is never set and the UA
rule `:focus-visible { outline: -webkit-focus-ring-color auto 1px }` stands. `outline-style: auto`
makes Chrome paint the *platform* focus ring — a white inner ring plus a dark outer one — and it
ignores `outline-color`, so `outline-transparent` has no effect. That white ring is what a user
sees around a clicked input, and it appears even when HeroUI's own ring does not (react-aria sets
`data-focus-visible` for keyboard interaction only).

Restore the style HeroUI assumed, in `index.css`:

```css
:focus-visible {
  outline-style: solid;
}
```

The outline is already transparent, so this makes it invisible; keyboard focus still gets
HeroUI's `ring-2 ring-focus`. Any hand-rolled focusable element then needs its own
`focus-visible:ring-2 focus-visible:ring-focus`, since it no longer inherits a UA ring.

Separately, that HeroUI ring is `ring-2 ring-focus ring-offset-2 ring-offset-background`: a 2px
gap in the page background colour, which over a filled field reads as a second white frame.
Collapse it:

```css
[data-focus-visible='true'],
[data-focus-visible='true'] * {
  --tw-ring-offset-width: 0px !important;
}
```

`!important` is deliberate. The rule that sets it is
`.group[data-focus-visible="true"] .group-data-\[focus-visible\=true\]\:ring-offset-2`, specificity
(0,3,0) and emitted after this file — and note the flag sits on a *wrapper* while the ring sits on
its child, so a selector matching only the flagged element misses.

Sizes have to be checked the same way: an `Input` at `size="sm"` is `h-8` while a default `Button`
is `h-10`, so a search field beside two buttons looks short unless it also runs at the default size.



### Don't: put an interactive control inside a selectable `TableRow` unguarded

```tsx
// Don't — opening the picker also ticks the row's checkbox
<TableCell>
  <Popover><PopoverTrigger><button …/></PopoverTrigger>…</Popover>
</TableCell>
```

`TableRow` under `selectionMode="multiple"` presses through react-aria, so the row's own press
handler fires from the same event. Stop the bubble on a wrapper **outside** `PopoverTrigger` —
props placed on the trigger's child are overwritten by the props HeroUI clones onto it:

```tsx
const swallow = (e: { stopPropagation: () => void }) => e.stopPropagation()

<span className="shrink-0" onPointerDown={swallow} onMouseDown={swallow} onClick={swallow}>
  <Popover>…</Popover>
</span>
```

The button still handles its own press first, and the row never sees the event.

### Three-valued flags get three branches

`Account.last_checked_in` is `true | false | null` (took it today / site says today is done /
nobody could tell us). A truthiness test renders `null` as the confident 今日已签 chip, which
shows *no evidence* as *evidence*. Branch explicitly on `=== true` / `=== false` / else, and
give the `null` case its own low-confidence label and an explanation. The same care applies to
any nullable field the panel reports as fact.

Whatever explains a status has to survive the card layout too: there is no hover on a touch
screen, so the phone view prints the reason as text where the table shows a `Tooltip`.

### Optimistic writes that must not borrow the row's busy state

`saveAvatar()` deliberately does not go through `run()`: that helper sets `busyId`, which would
put the row's 签到 button into a loading spinner just because someone picked a colour. Roll back
the optimistic update and toast on failure instead.

### Copy that arrived over the network is text, not markup

`PromoCard` renders a card whose every field comes from a remote manifest (`GET /api/promos`).
Those fields go into text nodes and one `href` — never into a class name, a `style`, or
`dangerouslySetInnerHTML`. `panel/promo.py` has already refused a `cta.url` that is not
`https://`; nothing else about the payload is trusted.

Colour is the interesting case, because every 公益站 has to look different and the manifest is
the only thing that knows which site a card is for. The gradients still stay local: `MESHES` is
a table of eight palettes here, and the manifest sends a *name* (`theme`) that is looked up in
it and thrown away when it is not a key — an unknown or absent name falls back to
`THEMES[hash(card.id) % THEMES.length]`, so a site published without one still differs from its
neighbours. Never interpolate a remote string into a class or a `style`; a name that indexes a
local table is not the same thing.

Two measured facts about building those meshes. Every layer must fade to **its own colour at
alpha 0**, not to `transparent` — `transparent` is transparent *black*, and interpolating to it
greys the edge of every blob (`fade()` exists for exactly this). And the **first gradient in the
list paints on top**: a pale wash listed first covers every saturated blob under it, which is
what turned the whole card lavender on the first pass. Geometry is shared by all palettes and
only colour changes, so a new palette is one line.

The sticker's 反光 sweep is a custom animation, and Tailwind 3 has two traps there. Custom
`keyframes`/`animation` must be declared in `tailwind.config.js` — the JIT only sees literal
class strings, so an arbitrary-value animation cannot carry its own keyframes — and Tailwind 3
emits **no `motion-safe` variant for a custom animation**, so honouring
`prefers-reduced-motion` takes one hand-written rule in `index.css` (`.animate-shine {
animation: none }`). Decoration that moves without an off switch is an accessibility bug, not a
detail.

Two measured layout facts about that card, both load-bearing. It is `fixed`, so it renders an
`h-[480px]` spacer that scrolls the last rows' 操作 buttons clear of it — and the spacer must
appear under exactly the same condition as the card, or a panel with no card to show scrolls for
nothing. And it keeps one width (`min(344px,calc(100vw-1.5rem))`) at every breakpoint: going
full-bleed below `sm` put it over 63% of an 844px-tall phone screen, and the 4:3 hero means
width is the only lever on that.

---

## Accessibility

- Every icon-only control needs an `aria-label` naming its subject
  (`设置「${name}」的头像`), and colour swatches need a text label — colour alone is not a name.
- Toasts append into one `#toast-stack` container (`flex flex-col gap-2`). Pinning each toast at
  the same fixed coordinates stacks them on top of each other and only the last is readable.

---

## Common Mistakes

### Common Mistake: verifying UI against a stale bundle

**Symptom**: measurements contradict the source you just changed (e.g. row heights still ragged).
**Cause**: the served `frontend/dist` bundle, or the browser's cached `index.html`, predates the
rebuild.
**Fix**: `npm --prefix frontend run build`, then reload with cache ignored before measuring.
**Prevention**: the `/assets` mount reads from disk per request, so a rebuild needs no panel
restart — but a *backend* change does. See `backend/database-guidelines.md`.

### Common Mistake: driving react-aria widgets from injected JS events

**Symptom**: `element.click()` or a hand-built `PointerEvent` sequence does not open a HeroUI
popover, so a test concludes the component is broken.
**Cause**: react-aria's press handling ignores untrusted synthetic events.
**Fix**: use real CDP input (the browser tool's click/hover), and poll for tooltips — they open
after a delay, so reading the DOM immediately finds nothing.

