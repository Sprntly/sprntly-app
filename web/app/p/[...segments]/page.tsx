// Catch-all shell for every /p/... depth (legacy 1-seg bookmark, 2-seg
// canonical, 3-seg canonical, and any future depth) — replaces the three
// separate per-depth route files that previously lived at differently-named
// sibling dynamic folders (/p/[slug]/page.tsx, /p/[slug]/[token]/page.tsx,
// /p/[slug]/[featureSlug]/[token]/page.tsx). Next.js requires every dynamic
// segment at the SAME tree position to share one param name across ALL routes
// that reach it; `[token]` (2-seg) and `[featureSlug]` (3-seg) were siblings
// under /p/[slug]/ with different names, which crashes the ENTIRE app's
// route-tree build ("You cannot use different slug names for the same dynamic
// path"), not just the new route. A single catch-all route has only ONE
// dynamic segment name at that position, by construction, so this class of
// conflict cannot recur regardless of how many segment depths are added later.
//
// Resolution is entirely client-side by REAL path depth (PublicPathRouter
// reads window.location.pathname) — the route params below are build-time
// static-export placeholders only, never read at runtime (same convention as
// every prior /p shell).
import { PublicPathRouter } from "../PublicPathRouter"

// Static export needs explicit params to emit each shell it should serve.
// These three entries preserve the exact static filenames
// backend/deploy/nginx.conf's three /p/... rewrite rules already target,
// UNCHANGED: /p/_.html (1-seg legacy), /p/_/_.html (2-seg canonical),
// /p/_/_/_.html (3-seg canonical). Values are never read at runtime.
export function generateStaticParams() {
  return [
    { segments: ["_"] },
    { segments: ["_", "_"] },
    { segments: ["_", "_", "_"] },
  ]
}

export default function PublicSharePage() {
  return <PublicPathRouter />
}
