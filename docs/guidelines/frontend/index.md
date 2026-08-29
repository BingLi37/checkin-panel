# Frontend Development Guidelines

> Best practices for frontend development in this project.

---

## Overview

`frontend/` is a React 18 + HeroUI 2.8 + Tailwind 3 SPA built by Vite, served as static assets by
FastAPI. One screen: the account list plus an add/edit modal. Flat `src/`, no router, no store, no
data-fetching library.

Two facts shape most of the conventions below, so they are worth knowing before reading further:

- **There is no linter and no test runner.** `tsc --noEmit` and a browser measurement are the whole
  verification story.
- **HeroUI 2.8 emits Tailwind v4 class names** while this project runs Tailwind 3, so some of its
  utilities silently do nothing. That mismatch is behind more than one measured bug.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Flat `src/`, module split, why assets live in `src/assets/` | Filled |
| [Component Guidelines](./component-guidelines.md) | Component patterns, responsive layout, HeroUI + Tailwind gotchas | Filled |
| [Hook Guidelines](./hook-guidelines.md) | `useStuck`, the `run()` / `refresh()` fetch pattern | Filled |
| [State Management](./state-management.md) | All state in `App.tsx`, derive-don't-store, bulk-action scope | Filled |
| [Quality Guidelines](./quality-guidelines.md) | Verification commands, forbidden patterns, review checklist | Filled |
| [Type Safety](./type-safety.md) | `api.ts` as the only wire format, exhaustive lookup + resolver | Filled |

---

## Keeping These Current

These describe **actual conventions measured in this codebase**, not ideals. When you learn
something new here, add it with the evidence — the number you measured, the response shape you saw,
the selector that won the cascade. A rule without its reason gets deleted by the next person who
finds it inconvenient.

---

**Language**: All documentation should be written in **English**.
