"use client"
// Depth-dispatch shell for every real `/p/...` path, mounted by the catch-all
// route shell (./[...segments]/page.tsx). Next.js requires every dynamic
// segment at the SAME tree position to share one param name across every
// route that reaches it; a fixed per-depth folder tree (a `[token]` folder for
// the 2-segment canonical form and a differently-named `[featureSlug]` folder
// for the 3-segment form, both direct siblings under the same parent) violates
// that rule the moment a third depth is added — Next's route-tree build fails
// outright ("You cannot use different slug names for the same dynamic path"),
// breaking every route in the app, not just the new one. A single catch-all
// route has exactly one dynamic segment name at that position by construction,
// so this class of conflict cannot recur regardless of how many segment depths
// are ever added.
//
// Dispatch is on the REAL request depth, read the same way every /p resolver
// already does — window.location.pathname via publicPathSegments — never
// Next's route params, which are build-time static-export placeholders only
// (see shareTokenFromPathname.ts's header). depth === 1 is the legacy
// `/p/<token>` bookmark shape (redirect-only, no chrome); any other depth (2,
// 3, or a future addition) is a canonical share link, rendered inline by the
// same PublicTokenViewer regardless of depth — resolution is by TOKEN (always
// the last segment) alone, so the number of leading cosmetic segments never
// affects which prototype loads.
import { useEffect, useState } from "react"
import { LegacyTokenRedirect } from "./LegacyTokenRedirect"
import { PublicTokenViewer } from "./PublicTokenViewer"
import { publicPathSegments } from "./shareTokenFromPathname"

export type PathDepthKind = "legacy" | "canonical"

// Pure: node-env unit-testable (no DOM/router), matching the split convention
// of every other /p resolver helper.
export function pathDepthKind(pathname: string, basePath = ""): PathDepthKind {
  return publicPathSegments(pathname, basePath).length === 1 ? "legacy" : "canonical"
}

export function PublicPathRouter() {
  // Deferred to a client-mount effect (never read during render) so this never
  // touches `window` during an SSR pass (DISABLE_STATIC_EXPORT=1 dev mode) —
  // same guard convention as PublicTokenViewer's token read.
  const [kind, setKind] = useState<PathDepthKind | undefined>(undefined)
  useEffect(() => {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? ""
    setKind(pathDepthKind(window.location.pathname, basePath))
  }, [])

  if (kind === undefined) return null // not yet read from the URL — avoid a flash
  return kind === "legacy" ? <LegacyTokenRedirect /> : <PublicTokenViewer />
}
