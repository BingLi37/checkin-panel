# Directory Structure

> How frontend code is organized. One SPA, one screen, flat layout.

---

## Overview

The panel is a single screen: a list of accounts plus a modal to add or edit one. There is no
router and no feature tree, so every module sits directly in `frontend/src/`. Do not add
`components/`, `hooks/` or `utils/` directories for a handful of files — a flat directory of
eleven well-named modules is easier to navigate than the same eleven split across four folders.

---

## Directory Layout

```
frontend/
├── index.html              inline SVG favicon lives here (see below)
├── tailwind.config.js      the three font stacks + heroui() plugin
├── vite.config.ts          dev server on :5173, proxies /api -> :8000
├── dist/                   build output, gitignored, served by FastAPI
└── src/
    ├── main.tsx            HeroUIProvider + mount
    ├── App.tsx             the list: toolbar, search, table, cards
    ├── AccountForm.tsx     the add/edit modal; exports FIELD (shared input classNames)
    ├── AccountAvatar.tsx   avatar + colour/shape picker
    ├── avatar.ts           palette, initials(), defaultSkins(), slug resolvers
    ├── icons.tsx           inline SVGs + login-method labels
    ├── useStuck.ts         sticky-state hook
    ├── api.ts              every server type and the fetch wrapper
    ├── index.css           Tailwind entry + the cascade overrides
    ├── fonts.css           8 @font-face rules
    └── assets/fonts/       8 woff2, bundled by Vite
```

---

## Module Organization

The split that matters here is **data out of JSX**, not folders:

- Pure derivations go in a `.ts` module. `avatar.ts` owns the palette, the initials rule and the
  default-colour assignment; `AccountAvatar.tsx` only renders. That is what makes the colour
  logic checkable without a DOM.
- Inline SVG and its metadata go in `icons.tsx`, keyed by the union from `api.ts`.
- `api.ts` is the only place that knows the wire format. A component that needs a new server
  field gets it added there first.
- A hook gets its own file when it owns a browser API (`useStuck.ts` and its
  `IntersectionObserver`). Local UI state stays in the component.

---

## Assets: `src/assets/`, never `public/`

`run.py` mounts **only** `/assets`:

```python
app.mount('/assets', StaticFiles(directory=dist_dir / 'assets'), name='assets')
```

Nothing serves `/fonts`, `/favicon.ico`, or anything else from `frontend/public/`, so a file put
there returns 404 in production. This was a live bug: the favicon 404'd for weeks.

So: reference assets from CSS or TS (`url('./assets/fonts/…')`) and let Vite emit them into
`dist/assets/` with a content hash. The favicon is an inline SVG data URI in `index.html` for the
same reason — it needs no route at all.

A rebuild does not need a panel restart: `/assets` is a `StaticFiles` mount and `/` returns
`dist/index.html`, both read from disk per request. A change under `panel/` does need one.

---

## Naming Conventions

- Component files are `PascalCase.tsx` and default-export that component.
- Non-component modules are `camelCase.ts` and export named symbols only.
- Hooks are `useThing.ts`, named export.
- Shared constants are `SCREAMING_SNAKE` (`AVATAR_COLORS`, `ACTION_SLOTS`, `FIELD`).

---

## Examples

`avatar.ts` + `AccountAvatar.tsx` is the pattern to copy: the data, the rules and their
explanations in the `.ts` file; only rendering and event wiring in the `.tsx`.
