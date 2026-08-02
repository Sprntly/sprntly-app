"use client"

// Thin route wrapper for postLoginPath()'s "blocked" routing target
// (`/not-authorized?share={token}&reason={reason}`). `share` isn't read here
// — the reason alone is enough to render the right copy, and this screen
// deliberately never re-resolves the token (no artifact identity is
// disclosed to a blocked visitor, per AC3).
import { Suspense, useEffect, useState } from "react"
import { useSearchParams } from "next/navigation"
import { AuthShell } from "../components/auth/AuthShell"
import { NotAuthorizedScreen, type NotAuthorizedReason } from "../components/auth/NotAuthorizedScreen"
import { notAuthorizedContinuePath } from "../lib/supabase/client"

const VALID_REASONS: NotAuthorizedReason[] = ["different_company", "invalid_token"]

function NotAuthorizedContent() {
  const searchParams = useSearchParams()
  const reasonParam = searchParams.get("reason")
  const reason: NotAuthorizedReason = VALID_REASONS.includes(reasonParam as NotAuthorizedReason)
    ? (reasonParam as NotAuthorizedReason)
    : "invalid_token"

  // The user's own account state (never the artifact) — computed
  // client-side, best-effort. Defaults to "/" while resolving / on any
  // failure, matching NotAuthorizedScreen's own default.
  const [continueHref, setContinueHref] = useState("/")
  useEffect(() => {
    let cancelled = false
    notAuthorizedContinuePath()
      .then((path) => {
        if (!cancelled) setContinueHref(path)
      })
      .catch(() => {
        /* best-effort — keep the "/" default */
      })
    return () => {
      cancelled = true
    }
  }, [])

  return <NotAuthorizedScreen reason={reason} continueHref={continueHref} />
}

export default function NotAuthorizedPage() {
  return (
    <Suspense
      fallback={
        <AuthShell tag="Not authorized">
          <div className="auth-sub">Loading…</div>
        </AuthShell>
      }
    >
      <NotAuthorizedContent />
    </Suspense>
  )
}
