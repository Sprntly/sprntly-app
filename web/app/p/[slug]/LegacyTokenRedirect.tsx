"use client"
// Legacy `/p/<token>` route — client redirect to the canonical
// `/p/<company>/<feature>/<token>` form. No real share links were ever minted at
// the legacy shape (verified), so this is hygiene: it keeps any old bookmark
// working by resolving the token to its human-readable company + feature
// segments and replacing the URL. It renders NO viewer chrome — resolution +
// redirect only.
//
// Prod builds with `output: "export"` (static, no server runtime), so the
// redirect MUST be client-side. The pure target-computation (legacyRedirectTarget)
// is split out so it is node-env unit-testable (no DOM/router), matching the
// public-viewer split convention. Relative imports match the codebase + vitest.
//
// NOTE: this route is /p/<token> (legacy 1-segment). The shared dynamic segment
// is named [slug] to satisfy Next's same-name rule with the canonical
// /p/[slug]/[token] route, but HERE the value IS the share token.
import { useEffect, useRef } from "react"
import { notFound, useRouter } from "next/navigation"
import { resolveToken, type ResolvedView } from "../resolveToken"
import { shareTokenFromLocation } from "../shareTokenFromPathname"

// Pure: given the resolved view (or null) and the token, compute the canonical
// path to replace to — or null when the token did not resolve (caller calls
// notFound()). Mirrors the viewer's notFound() handling for a 404/null view.
export function legacyRedirectTarget(
  view: ResolvedView | null,
  token: string,
): string | null {
  if (!view) return null
  const company = view.company_display_slug || "company"
  const feature = view.feature_slug || "prototype"
  return `/p/${company}/${feature}/${token}`
}

export function LegacyTokenRedirect() {
  const router = useRouter()
  // Guard against a notFound() throw across a re-render once we've already 404'd.
  const done = useRef(false)

  useEffect(() => {
    if (done.current) return
    // The real token comes from the live URL, not useParams() — under
    // output:"export" the route is prerendered under the "_" sentinel, so
    // useParams().slug returns "_". The legacy /p/<token> route is 1-segment, so
    // the token is the last/only `/p` segment — exactly what this returns.
    const token = shareTokenFromLocation()
    if (!token) {
      done.current = true
      notFound()
      return
    }
    let active = true
    resolveToken(token)
      .then((view) => {
        if (!active) return
        const target = legacyRedirectTarget(view, token)
        done.current = true
        if (target === null) {
          notFound()
          return
        }
        router.replace(target)
      })
      .catch(() => {
        // A real backend error (non-404) — treat as not-found rather than
        // looping the redirect; the legacy route has no error chrome of its own.
        if (!active) return
        done.current = true
        notFound()
      })
    return () => {
      active = false
    }
  }, [router])

  // Redirect-only surface: nothing visible to render while resolving.
  return null
}
