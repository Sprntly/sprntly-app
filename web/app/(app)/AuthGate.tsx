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

  useEffect(() => {
    if (shareToken) return
    if (auth.kind === "anonymous" || auth.kind === "unconfigured") {
      router.replace("/sign-in")
    }
  }, [auth.kind, router, shareToken])

  if (shareToken) {
    return <ArtifactShareGate token={shareToken}>{children}</ArtifactShareGate>
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
