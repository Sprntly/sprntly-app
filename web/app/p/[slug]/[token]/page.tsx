// Canonical public viewer route `/p/<slug>/<token>` — thin SERVER shell. The
// slug is COSMETIC (human-readable company segment); resolution is by TOKEN
// alone. Mirrors the legacy single-segment shell: a server component that
// satisfies static export, delegating runtime behaviour to the co-located
// client viewer.
//
// WHY a client component (not an SSR fetch): next.config.ts uses
// `output: "export"` — the web app is a static SPA with no server runtime, so a
// request-time server fetch cannot work. Both slug and token are unbounded, so
// static export cannot prerender a page per (slug, token); this shell is emitted
// once under a 2-seg sentinel param, and PublicTokenViewer reads the REAL token
// from the URL (useParams) at runtime and resolves it client-side (the slug is
// ignored for resolution). Serving arbitrary /p/<slug>/<token> on the static
// host relies on an nginx SPA-fallback rewrite (depth-2: /p/<slug>/<token> →
// /p/_/_.html) — a deploy-config item in backend/deploy/nginx.conf.
import { PublicTokenViewer } from "../../[token]/PublicTokenViewer"

// Static export needs ≥1 param to emit the shell. Both values are build-time
// placeholders only — never read at runtime (the client reads the URL's token;
// the slug is cosmetic).
export function generateStaticParams() {
  return [{ slug: "_", token: "_" }]
}

export default function CanonicalPublicPrototypePage() {
  return <PublicTokenViewer />
}
