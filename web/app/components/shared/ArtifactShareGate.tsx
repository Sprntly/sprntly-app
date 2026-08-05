"use client"

// The orchestrator for a `?share=` OR bare `?prd=` (no token) visit — and the
// component that decides whether the real app renders AT ALL.
//
// Three outcomes now, not two (2026-08-04):
//
//   "member"     — the visitor is a member of the owning company AND can act
//                  in the artifact's workspace. They get the REAL app, by way
//                  of a full page load to the ordinary `?prd=` deep link.
//                  Previously these callers were handed GuestArtifactViewer,
//                  which is read-only: opening a teammate's share link left a
//                  colleague unable to edit the PRD or its tickets, even
//                  though the very same person could edit it by navigating to
//                  it from the sidebar. Edit rights follow company
//                  membership, not who created the artifact or minted the
//                  link.
//   "guest_view" — same company, no access to that workspace yet (a
//                  zero-company domain-matched signup that just gained
//                  company membership, or a member of a different
//                  workspace). Still GuestArtifactViewer, still NOT
//                  `children`: both sub-cases fail somewhere in the real tree
//                  (OnboardingRequiredGuard's infinite loading shell for the
//                  first, require_owned_prd's 404 for the second). Its Join
//                  prompt grants the workspace, after which resolve returns
//                  "member".
//   "blocked"    — NotAuthorizedScreen, as before. An authed user whose
//                  token/access is invalid or expired lands there too.
//
// The hand-off is a full `window.location.assign`, matching JoinConfirmModal's
// post-join reload and for the same reason: the real
// WorkspaceProvider/OnboardingRequiredGuard/AppShell pipeline must initialize
// from scratch rather than have this standalone gate's state hot-swapped into
// it. `presetActiveWorkspace` seeds the artifact's own workspace as the active
// one first, so a member of several workspaces doesn't land in whichever one
// they used last and 404.
//
// Bare-`?prd=` mode (2026-08-02, "full parity" scope): the
// SAME domain-match guest experience, minus a share row — no revocability,
// no sharer attribution. AuthGate is the ONLY caller that decides whether a
// visit reaches this component in prdId mode at all (only when there's no
// existing normal path to protect — see AuthGate's prdOnlyGuestMode), so
// this component itself doesn't need to re-guard against hijacking a real
// member's ordinary navigation.
import { useEffect, useRef, useState } from "react"
import { useAuth } from "../../lib/auth"
import { AuthLoading } from "../../(app)/AuthGate"
import { EntryGateScreen } from "../auth/EntryGateScreen"
import { NotAuthorizedScreen } from "../auth/NotAuthorizedScreen"
import { GuestArtifactViewer } from "./GuestArtifactViewer"
import { artifactShareApi, type ArtifactShareResolveOutcome } from "../../lib/artifactShareApi"
import { prdAccessApi, type PrdAccessResolveOutcome } from "../../lib/prdAccessApi"
import { presetActiveWorkspace } from "../../context/WorkspaceContext"

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
/** Where a `member` outcome hands the visitor over to the real app: the
 *  ordinary `?prd=` deep link, with no `share=` token and no `access=guest`
 *  marker, so AuthGate stops intercepting and renders `children`.
 *
 *  Prefers the opaque `public_id`; the raw sequential `artifact_id` is only
 *  the fallback for a (currently unreachable) PRD row with no public_id, and
 *  is accepted as a legacy `?prd=` form by useArtifactUrlSync — same
 *  defensive shape postLoginPath and JoinConfirmModal already use. */
function memberAppUrl(outcome: {
  public_id?: string | null
  artifact_id: number
}): string {
  const param = outcome.public_id ?? String(outcome.artifact_id)
  return `/?prd=${encodeURIComponent(param)}`
}

export function ArtifactShareGate({ token, publicId }: ArtifactShareGateProps) {
  const auth = useAuth()
  const [resolveState, setResolveState] = useState<ResolveState>({ kind: "idle" })

  // A `member` outcome leaves this component entirely. Done in an effect
  // (not inline in render) because it is a navigation side effect, and
  // guarded by a ref so a re-render mid-navigation can't fire a second
  // assign.
  const handedOffRef = useRef(false)
  useEffect(() => {
    if (resolveState.kind !== "done") return
    if (resolveState.outcome.outcome !== "member") return
    if (handedOffRef.current) return
    handedOffRef.current = true
    if (auth.kind === "authed") {
      presetActiveWorkspace(auth.user.id, resolveState.outcome.owner_workspace_id)
    }
    window.location.assign(memberAppUrl(resolveState.outcome))
  }, [resolveState, auth])

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

  // The effect above is navigating to the real app; hold the loading shell
  // rather than flashing the read-only viewer this member isn't getting.
  if (outcome.outcome === "member") {
    return <AuthLoading />
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
