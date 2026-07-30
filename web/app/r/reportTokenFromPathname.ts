// Share-token derivation for the public `/r/<token>` report viewer under static
// export.
//
// WHY THIS EXISTS: prod builds with `output: "export"` (next.config.ts), so
// `/r/[token]` is prerendered under a sentinel param (`generateStaticParams` →
// `{ token: "_" }`), emitting `/r/_.html`, and nginx rewrites every real `/r/...`
// request to that shell. `useParams()` therefore returns the prerendered SENTINEL,
// not the token in the address bar — the live URL is the only source of truth.
//
// Same reasoning and shape as p/shareTokenFromPathname.ts (the prototype viewer);
// kept separate rather than generalised because that module encodes /p's
// multi-depth canonical/legacy path rules, which /r does not have — one depth,
// one segment.
//
// Pure (no `window` access of its own — the caller passes the pathname in), so it
// is node-env unit-testable without a DOM or router.

/**
 * Derive the report share token from a `/r/<token>` pathname, tolerating a
 * `NEXT_PUBLIC_BASE_PATH` prefix and a trailing slash.
 *
 * Returns null when there is no real token: a path that isn't under `/r`, an
 * empty `/r`, or the prerender sentinel (`"_"`) — which would otherwise be sent
 * to the API as a literal token lookup.
 *
 * e.g. ("/r/tok-123")           → "tok-123"
 *      ("/demo/r/tok-9/", "/demo") → "tok-9"
 *      ("/r/_")                  → null
 *      ("/about")               → null
 */
export function reportTokenFromPathname(
  pathname: string,
  basePath = "",
): string | null {
  let p = pathname
  const base = basePath.replace(/\/+$/, "")
  if (base && (p === base || p.startsWith(`${base}/`))) {
    p = p.slice(base.length)
  }
  const parts = p.split("/").filter(Boolean)
  if (parts[0] !== "r") return null
  const raw = parts[1]
  if (!raw || raw === "_") return null
  try {
    return decodeURIComponent(raw)
  } catch {
    // A malformed %-escape cannot be a token we minted; treat as not-found.
    return null
  }
}

/** Read the token from the live browser URL, base-path aware. Null on the server
 *  (no `window`) and for the sentinel/empty cases. */
export function reportTokenFromLocation(): string | null {
  if (typeof window === "undefined") return null
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? ""
  return reportTokenFromPathname(window.location.pathname, basePath)
}
