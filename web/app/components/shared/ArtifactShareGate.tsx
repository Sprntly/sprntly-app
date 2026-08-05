"use client"

// The orchestrator for a `?share=` OR bare `?prd=` (no token) visit — and the
// component that decides whether `children` (the real WorkspaceProvider >
// OnboardingRequiredGuard > ... > AppShell tree) renders AT ALL. Both
// guest_view sub-cases (a zero-company domain-matched signup, and a
// same-company-different-workspace member) render GuestArtifactViewer —
// NEITHER renders `children` — because both sub-cases fail somewhere in that
// tree: OnboardingRequiredGuard's infinite loading shell for the zero-company
// case, and require_owned_prd's 404 for the cross-workspace case. Only a
// "blocked" resolve outcome, or an authed user whose token/access is
// invalid/expired, ever renders NotAuthorizedScreen.
//
// Bare-`?prd=` mode (2026-08-02, "full parity" scope): the
// SAME domain-match guest experience, minus a share row — no revocability,
// no sharer attribution. AuthGate is the ONLY caller that decides whether a
// visit reaches this component in prdId mode at all (only when there's no
// existing normal path to protect — see AuthGate's prdOnlyGuestMode), so
// this component itself doesn't need to re-guard against hijacking a real
// member's ordinary navigation.
import { useEffect, useState } from "react"
import { useAuth } from "../../lib/auth"
import { AuthLoading } from "../../(app)/AuthGate"
import { EntryGateScreen } from "../auth/EntryGateScreen"
import { NotAuthorizedScreen } from "../auth/NotAuthorizedScreen"
import { GuestArtifactViewer } from "./GuestArtifactViewer"
import { artifactShareApi, type ArtifactShareResolveOutcome } from "../../lib/artifactShareApi"
import { prdAccessApi, type PrdAccessResolveOutcome } from "../../lib/prdAccessApi"

type ResolveOutcome = ArtifactShareResolveOutcome | PrdAccessResolveOutcome

type ResolveState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "done"; outcome: ResolveOutcome }
  // resolve() 404s/throws for an ALREADY-AUTHENTICATED caller — e.g. an
  // invalid/expired/revoked token. Distinct from a server-returned "blocked"
  // outcome (which IS a successful resolve), so it renders through the same
  // `reason: "invalid_token"` NotAuthorizedScreen already supports for the
  // signed-out EntryGateScreen path, rather than stranding the user on a
  // perpetual loading shell.
  | { kind: "error" }

type ArtifactShareGateProps =
  | { token: string; publicId?: undefined; children: React.ReactNode }
  | { token?: undefined; publicId: string; children: React.ReactNode }

// `children` (the real app tree) is part of this component's contract — it is
// the thing being deliberately withheld — but it is NEVER rendered by any
// branch below: every visit resolves to EntryGateScreen, AuthLoading,
// NotAuthorizedScreen, or GuestArtifactViewer. AuthGate passes it through
// only so this stays a drop-in wrapper around the real tree.
export function ArtifactShareGate({ token, publicId }: ArtifactShareGateProps) {
  const auth = useAuth()
  const [resolveState, setResolveState] = useState<ResolveState>({ kind: "idle" })

  useEffect(() => {
    if (auth.kind !== "authed") return
    let cancelled = false
    setResolveState({ kind: "loading" })
    const resolvePromise = token
      ? artifactShareApi.resolve(token)
      : prdAccessApi.resolve(publicId!)
    resolvePromise
      .then((outcome) => {
        if (!cancelled) setResolveState({ kind: "done", outcome })
      })
      .catch(() => {
        if (!cancelled) setResolveState({ kind: "error" })
      })
    return () => {
      cancelled = true
    }
  }, [auth.kind, token, publicId])

  if (auth.kind === "loading") {
    return <AuthLoading />
  }

  if (auth.kind === "anonymous" || auth.kind === "unconfigured") {
    return token ? <EntryGateScreen token={token} /> : <EntryGateScreen publicId={publicId!} />
  }

  // authed
  if (resolveState.kind === "idle" || resolveState.kind === "loading") {
    return <AuthLoading />
  }

  if (resolveState.kind === "error") {
    return <NotAuthorizedScreen reason="invalid_token" />
  }

  const { outcome } = resolveState
  if (outcome.outcome === "blocked") {
    return <NotAuthorizedScreen reason={outcome.reason} />
  }

  return (
    <GuestArtifactViewer
      token={token ?? null}
      artifactId={outcome.artifact_id}
      publicId={token ? null : publicId!}
      sharerName={"sharer_name" in outcome ? outcome.sharer_name : null}
      owningCompanyName={outcome.owning_company_name}
    />
  )
}
