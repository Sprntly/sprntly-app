// Share-token derivation for the public `/p` routes under static export.
//
// WHY THIS EXISTS: prod builds with `output: "export"` (next.config.ts), so the
// catch-all `/p/[...segments]` route (which serves every real depth — legacy
// 1-seg, canonical 2-seg, canonical 3-seg) is prerendered under a handful of
// sentinel params (`generateStaticParams` → `{ segments: ["_"] }`,
// `{ segments: ["_", "_"] }`, `{ segments: ["_", "_", "_"] }`), emitting
// `/p/_.html`, `/p/_/_.html`, `/p/_/_/_.html`. nginx then rewrites EVERY real
// `/p/...` request (by depth) to the matching static file (the SPA shell).
// Consequently `useParams()` on the client returns the prerendered SENTINEL
// segments, NOT the token in the address bar — so resolving against it hits
// `by-token/_` and fails. The live URL is the only source of truth for the
// real token, and it lives on `window.location.pathname`.
//
// These helpers are pure (no `window` access of their own — the caller passes the
// pathname in), so they are node-env unit-testable without a DOM/router, matching
// the resolveToken split convention. The component reads `window.location.pathname`
// client-side and feeds it here.

/**
 * Strip an optional `NEXT_PUBLIC_BASE_PATH` prefix, then return the path segments
 * AFTER the leading `/p` of a public share URL. Trailing-slash tolerant; returns
 * `[]` when the path is not under `/p`. `basePath` defaults to "" (no base path).
 *
 * e.g. ("/p/acme/tok-123")            → ["acme", "tok-123"]
 *      ("/demo/p/tok-9/", "/demo")    → ["tok-9"]
 *      ("/about")                     → []
 */
export function publicPathSegments(pathname: string, basePath = ""): string[] {
  let p = pathname
  const base = basePath.replace(/\/+$/, "")
  if (base && (p === base || p.startsWith(`${base}/`))) {
    p = p.slice(base.length)
  }
  const parts = p.split("/").filter(Boolean)
  if (parts[0] !== "p") return []
  return parts.slice(1)
}

/**
 * Derive the real share token from a public-share pathname. The share token is
 * ALWAYS the LAST `/p` segment — `/p/<slug>/<token>` (canonical viewer) and
 * `/p/<token>` (legacy redirect) both put the token last. Returns null when there
 * is no real token: an empty `/p` path, or the prerender sentinel (`"_"`) that the
 * static export emits and would otherwise resolve to `by-token/_`. The returned
 * token is `decodeURIComponent`-d (the path segment is URL-encoded).
 */
export function shareTokenFromPathname(
  pathname: string,
  basePath = "",
): string | null {
  const seg = publicPathSegments(pathname, basePath)
  const raw = seg[seg.length - 1]
  if (!raw || raw === "_") return null
  try {
    return decodeURIComponent(raw)
  } catch {
    // A malformed %-escape can't be the token we minted; treat as not-found.
    return null
  }
}

/**
 * Read the share token from the live browser URL, base-path aware. Returns null on
 * the server (no `window`) and for the sentinel/empty cases. This is the single
 * client-side entry point the viewer + legacy redirect call inside their effects.
 */
export function shareTokenFromLocation(): string | null {
  if (typeof window === "undefined") return null
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? ""
  return shareTokenFromPathname(window.location.pathname, basePath)
}
