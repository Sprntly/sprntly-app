// Legacy public route `/p/<token>` — thin SERVER shell for a CLIENT redirect to
// the canonical `/p/<slug>/<token>` form. The canonical viewer now lives at
// `/p/[slug]/[token]`; this route resolves the token to its company_slug and
// redirects, so old bookmarks keep working (no real legacy links exist — this is
// hygiene).
//
// NOTE: the dynamic segment is named [slug] (not [token]) to satisfy Next's
// same-name rule with the canonical `/p/[slug]/[token]` route — Next forbids two
// differently-named sibling first-position dynamic segments. HERE the [slug]
// value IS the share token; LegacyTokenRedirect reads it from params.slug.
//
// WHY a client redirect: next.config.ts uses `output: "export"` — the static
// SPA has no server runtime, so a request-time server redirect cannot work. The
// token is unbounded (arbitrary UUIDs), so static export cannot prerender a page
// per token; this shell is emitted once under a sentinel param, and
// LegacyTokenRedirect reads the REAL token from the URL (useParams) at runtime,
// resolves it client-side, and router.replace()s to /p/<slug>/<token>. Serving
// arbitrary /p/<uuid> on the static host relies on an nginx SPA-fallback rewrite
// (depth-1: /p/<token> → /p/_.html) — a deploy-config item in
// backend/deploy/nginx.conf.
import { LegacyTokenRedirect } from "./LegacyTokenRedirect"

// Static export needs ≥1 param to emit the shell. The value is a build-time
// placeholder only — never read at runtime (the client reads the URL's token).
export function generateStaticParams() {
  return [{ slug: "_" }]
}

export default function LegacyPublicPrototypePage() {
  return <LegacyTokenRedirect />
}
