"use client"

import { Suspense, useEffect } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useAuth } from "../lib/auth"
import { ArtifactShareGate } from "../components/shared/ArtifactShareGate"

export function AuthGate({ children }: { children: React.ReactNode }) {
  // useSearchParams() requires a Suspense boundary (Next 15 CSR-bailout rule).
  // Wrapping here (rather than pushing this requirement onto every caller of
  // AuthGate) keeps the `(app)` layout's own shape untouched.
  return (
    <Suspense fallback={<AuthLoading />}>
      <AuthGateInner>{children}</AuthGateInner>
    </Suspense>
  )
}

function AuthGateInner({ children }: { children: React.ReactNode }) {
  const auth = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  // Presence of `?share=` hands ALL routing decisions for this visit to
  // ArtifactShareGate — including the anonymous/unconfigured case, which
  // would otherwise redirect to /sign-in before EntryGateScreen ever renders.
  const shareToken = searchParams.get("share")
  const prdParam = searchParams.get("prd")
  // Set ONLY by postLoginPath()'s pending_prd_id branch on the exact URL it
  // redirects a domain-matched bare-link guest to — never present on an
  // ordinary in-app `?prd=` link (NavigationContext's own URL sync doesn't
  // add it). This is what lets that guest's OWN session keep resolving
  // through the guest pipeline on a page refresh/revisit, without a plain
  // `?prd=` visit from an already-authed REAL member ever being redirected
  // away from their normal working app (see prdOnlyGuestMode below).
  const guestAccess = searchParams.get("access") === "guest"
  // A bare `?prd=` link (no `share=` token) goes through the SAME guest
  // pipeline ArtifactShareGate already provides for share tokens, but ONLY
  // for a visitor with no existing normal path to protect: someone not yet
  // signed in at all, or a signed-in guest whose OWN session was routed
  // here via the `access=guest` marker. An already-authed REAL member's
  // everyday bare `?prd=` navigation (the app's own primary deep-link
  // shape, used constantly by the sidebar/tickets/chat) never carries that
  // marker, so it falls through unchanged to `children` below — this branch
  // must never intercept it, or every normal PRD open in the app would
  // start round-tripping through a guest-resolve check.
  const prdOnlyGuestMode =
    !shareToken && !!prdParam && (auth.kind !== "authed" || guestAccess)

  useEffect(() => {
    if (shareToken) return
    if (prdOnlyGuestMode) return
    if (auth.kind === "anonymous" || auth.kind === "unconfigured") {
      router.replace("/sign-in")
    }
  }, [auth.kind, router, shareToken, prdOnlyGuestMode])

  if (shareToken) {
    return <ArtifactShareGate token={shareToken}>{children}</ArtifactShareGate>
  }

  if (prdOnlyGuestMode) {
    return <ArtifactShareGate publicId={prdParam!}>{children}</ArtifactShareGate>
  }

  if (auth.kind !== "authed") {
    return (
      <AuthLoading />
    )
  }

  return <>{children}</>
}

export function AuthLoading() {
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
