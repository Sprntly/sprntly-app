"use client"

import { useEffect, useRef } from "react"
import { usePathname, useRouter } from "next/navigation"
import { useWorkspace } from "../context/WorkspaceContext"
import { postLoginPath } from "../lib/supabase/client"

// Enforces "onboarding must be finished before the app proper" for every entry
// into the protected `(app)` route group — not just the sign-in form and the
// auth callback, the only two places postLoginPath() ran before. A still-valid
// Supabase session reopened on `/`, a bookmarked app route, or a live-session
// reload all skipped those redirects and used to land a half-onboarded user on
// an app shell with no company — useless to them. This guard closes that gap.
//
// It deliberately DELEGATES the routing decision to postLoginPath() rather than
// re-deriving it, because that function is the single source of truth and gets
// every case right from a FRESH read of the DB:
//   - finished onboarding            → "/"            (we render the app)
//   - company exists, unfinished     → resume step    (redirect)
//   - no company, pending invite     → auto-accepted, then "/" or resume
//   - no company, no invite          → onboarding entry (your-name / step 1)
//   - email unverified / signed out  → verify-email / sign-in
// The fresh read also means a user who JUST completed onboarding (whose cached
// WorkspaceContext is momentarily stale) is not bounced back: postLoginPath
// sees the persisted completion and returns "/".
//
// `/onboarding/*` is itself under `(app)`, so this guard wraps it too — there we
// defer entirely to the onboarding layout's own guards and never interfere with
// step navigation (including going back a step).
function OnboardingRequiredGuard({ children }: { children: React.ReactNode }) {
  const { loading, workspace, refresh } = useWorkspace()
  const router = useRouter()
  const pathname = usePathname()
  const onOnboardingRoute = pathname?.startsWith("/onboarding") ?? false

  const completed =
    workspace != null && workspace.onboarding_completed_at != null

  // Resolve a route for any authed user who isn't already cleared into the app.
  // Skipped on `/onboarding/*` (handled there) and once the user is completed.
  const shouldResolve = !onOnboardingRoute && !loading && !completed

  // postLoginPath() does network work and may run while the cached workspace is
  // still settling; a ref makes the resolve fire once per mount rather than on
  // every dependency tick.
  const resolveStartedRef = useRef(false)

  useEffect(() => {
    if (!shouldResolve || resolveStartedRef.current) return
    resolveStartedRef.current = true
    let cancelled = false
    postLoginPath()
      .then((path) => {
        if (cancelled) return
        if (path === "/") {
          // The fresh DB read says this user belongs in the app — onboarding
          // just finished, or a pending invite was auto-accepted — but our
          // cached workspace is stale. Reload it; the guard then renders.
          void refresh()
        } else {
          router.replace(path)
        }
      })
      .catch(() => {
        // A hard failure must never strand the user on a blank shell: send them
        // to the onboarding entry so they can (re)build their workspace.
        if (!cancelled) router.replace("/onboarding/your-name")
      })
    return () => {
      cancelled = true
    }
  }, [shouldResolve, router, refresh])

  // On an onboarding route, defer to the onboarding layout entirely.
  if (onOnboardingRoute) return <>{children}</>

  // The app proper renders ONLY for a fully-onboarded user. Everyone else holds
  // on the loading shell while the effect routes them away — so a workspace-less
  // user never sees an empty app.
  if (loading || !completed) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#0a0a0c",
          color: "#7a7a85",
          fontFamily: "Geist, system-ui, sans-serif",
          fontSize: 14,
        }}
      >
        Loading…
      </div>
    )
  }

  return <>{children}</>
}

export { OnboardingRequiredGuard }
