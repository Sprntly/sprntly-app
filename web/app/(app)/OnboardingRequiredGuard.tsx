"use client"

import { useEffect, useRef, useState } from "react"
import { usePathname, useRouter } from "next/navigation"
import { useWorkspace } from "../context/WorkspaceContext"
import { slugForStep } from "../lib/onboarding/types"
import { postLoginPath } from "../lib/supabase/client"

// Enforces "onboarding must be finished before the app proper" for every entry
// into the protected `(app)` route group — not just the sign-in form and the
// auth callback, the only two places postLoginPath() ran before. A still-valid
// Supabase session reopened on `/`, a bookmarked app route, or a live-session
// reload all skipped those redirects and used to land a half-onboarded user on
// an app shell with no company — useless to them. This guard closes that gap.
//
// The routing decision splits on what the cached workspace already tells us:
//
//   - NO workspace → delegate to postLoginPath(), the single source of truth
//     for the workspace-less cases, which it gets right from a FRESH read of
//     the DB: pending-invite auto-accept, the your-name profile gate,
//     verify-email, and the onboarding entry for a brand-new user.
//   - workspace exists but onboarding is UNFINISHED → do NOT run postLoginPath
//     (its getUser → fetchWorkspaceForUser → tryAcceptInvite waterfall would
//     just re-fetch data we already hold). Instead refresh() the cached
//     workspace once and re-check: a user who JUST completed onboarding
//     in-session has a momentarily stale cache — the fresh read flips
//     `completed` and the guard renders the app instead of bouncing them back.
//     If the fresh read STILL says unfinished, route straight to the resume
//     step via slugForStep (the same step→slug mapping postLoginPath uses).
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
  // still settling; a ref makes that branch fire once per mount rather than on
  // every dependency tick.
  const resolveStartedRef = useRef(false)
  // The unfinished-workspace branch is a tiny state machine: refresh once
  // (idle → refreshing), then decide on the effect re-run the flip triggers
  // (refreshing → refreshed). Phase is STATE, not a ref, so that flip re-runs
  // the effect even when the re-fetched workspace lands identical to the cached
  // one; redirectedRef pins the resume redirect to exactly once.
  const [resolvePhase, setResolvePhase] = useState<
    "idle" | "refreshing" | "refreshed"
  >("idle")
  const redirectedRef = useRef(false)

  useEffect(() => {
    if (!shouldResolve) return

    if (workspace == null) {
      // Brand-new user or pending invite — postLoginPath owns these cases.
      if (resolveStartedRef.current) return
      resolveStartedRef.current = true
      let cancelled = false
      postLoginPath()
        .then((path) => {
          if (cancelled) return
          if (path === "/") {
            // The fresh DB read says this user belongs in the app — a pending
            // invite was auto-accepted — but our cached workspace is stale.
            // Reload it; the guard then renders.
            void refresh()
          } else {
            router.replace(path)
          }
        })
        .catch(() => {
          // A hard failure must never strand the user on a blank shell: send
          // them to the onboarding entry so they can (re)build their workspace.
          if (!cancelled) router.replace("/onboarding/your-name")
        })
      return () => {
        cancelled = true
      }
    }

    // Workspace exists but onboarding is unfinished. Refresh the cache once —
    // a just-completed user's fresh read flips `completed`, shouldResolve goes
    // false and the app renders — then, if STILL unfinished, resume onboarding
    // locally. No cancelled-flag here: refresh() updates the workspace object
    // mid-flight (re-running this effect while phase is "refreshing"), and a
    // cleanup-cancel on that re-run would wedge the machine before it decides.
    if (resolvePhase === "idle") {
      setResolvePhase("refreshing")
      refresh()
        .catch(() => {
          /* best-effort — the redirect below still resumes onboarding safely */
        })
        .finally(() => setResolvePhase("refreshed"))
      return
    }
    if (resolvePhase === "refreshed" && !redirectedRef.current) {
      redirectedRef.current = true
      router.replace(`/onboarding/${slugForStep(workspace.onboarding_step)}`)
    }
  }, [shouldResolve, workspace, resolvePhase, router, refresh])

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
          background: "#FFFFFF",
          color: "#000000",
          fontFamily: "Geist, system-ui, sans-serif",
          fontSize: 15,
          fontWeight: 500,
        }}
      >
        Loading…
      </div>
    )
  }

  return <>{children}</>
}

export { OnboardingRequiredGuard }
