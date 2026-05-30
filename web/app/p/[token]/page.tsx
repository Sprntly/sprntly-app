// Public prototype viewer (P2-05) — thin SERVER shell. Mirrors the
// web/app/(app)/onboarding/[step]/page.tsx pattern: a server component that
// satisfies static export, delegating all behaviour to a co-located client
// component.
//
// WHY a client component (not the SSR fetch the ticket sketched): next.config.ts
// uses `output: "export"` — the web app is a static SPA with no server runtime,
// so a request-time server fetch cannot work. The token is unbounded (arbitrary
// UUIDs), so static export cannot prerender a page per token; this shell is
// emitted once under a sentinel param, and PublicTokenViewer reads the REAL
// token from the URL (useParams) at runtime and resolves it client-side. Serving
// arbitrary /p/<uuid> on the static host relies on an nginx SPA-fallback rewrite
// (/p/* → this shell) — a deploy-config item, recorded in the PR body.
import { PublicTokenViewer } from "./PublicTokenViewer"

// Static export needs ≥1 param to emit the shell. The value is a build-time
// placeholder only — never read at runtime (the client reads the URL's token).
export function generateStaticParams() {
  return [{ token: "_" }]
}

export default function PublicPrototypePage() {
  return <PublicTokenViewer />
}
