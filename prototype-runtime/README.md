# prototype-runtime

The Vite + React + TypeScript output stack the Design Agent emits prototypes into.

This package is the **empty scaffold**. It does not run prototypes yet — the pieces ship across Phase 0:

- `P0-01` (this commit) — scaffold + tooling
- `P0-02` — JSX anchor-ID Vite plugin
- `P0-03` — smoke fixture app
- `P0-04` — CI stability snapshot test

## Package manager

This subtree uses **pnpm**. The rest of `sprntly-app/web/` uses npm — do not mix managers. See `sprntly/BUILD.md §6` (isolation strategy) for the why.

## Commands

```
pnpm install
pnpm dev     # local dev server
pnpm build   # production bundle in dist/
pnpm test    # vitest sanity tests
```

## See also

- `sprntly/BUILD.md §6` — isolation map and intent for this package
