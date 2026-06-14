"use client"
// Legacy `/p/<token>` route — client redirect to the canonical
// `/p/<slug>/<token>` form. No real share links were ever minted at the legacy
// shape (verified), so this is hygiene: it keeps any old bookmark working by
// resolving the token to its company_slug and replacing the URL. It renders NO
// viewer chrome — resolution + redirect only.
//
// Prod builds with `output: "export"` (static, no server runtime), so the
// redirect MUST be client-side. The pure target-computation (legacyRedirectTarget)
// is split out so it is node-env unit-testable (no DOM/router), matching the
// public-viewer split convention. Relative imports match the codebase + vitest.
import { useEffect, useRef } from "react"
import { notFound, useParams, useRouter } from "next/navigation"
import { resolveToken, type ResolvedView } from "../resolveToken"

// Pure: given the resolved view (or null) and the token, compute the canonical
// path to replace to — or null when the token did not resolve (caller calls
// notFound()). Mirrors the viewer's notFound() handling for a 404/null view.
export function legacyRedirectTarget(
  view: ResolvedView | null,
  token: string,
): string | null {
  if (!view) return null
  return `/p/${view.company_slug}/${token}`
}

export function LegacyTokenRedirect() {
  const params = useParams<{ token: string | string[] }>()
  const token = Array.isArray(params.token) ? params.token[0] : params.token
  const router = useRouter()
  // Guard against a notFound() throw across a re-render once we've already 404'd.
  const done = useRef(false)

  useEffect(() => {
    if (done.current) return
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
  }, [token, router])

  // Redirect-only surface: nothing visible to render while resolving.
  return null
}
