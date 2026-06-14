"use client"
// Client viewer for the public /p/<token> route (P2-05). Co-located with the
// page exactly like web/app/(app)/onboarding/[step]/OnboardingStep.tsx — the
// server shell (page.tsx) handles static export; this owns the runtime
// behaviour. Reads the real token from the URL (useParams), resolves it against
// the public backend resolver, and branches: public → iframe; passcode → gate;
// missing/private/not-ready/404 → notFound().
//
// The resolver + branch logic are split into pure functions (resolveToken,
// nextViewerState) so they are unit-testable in the node-env vitest run, which
// has no DOM/router — the same split convention as DesignAgentDrawer's
// runGenerateFlow. Relative imports (not `@/…`) match the codebase + vitest.
import { useEffect, useState } from "react"
import { notFound, useParams } from "next/navigation"
import { API_URL } from "../../lib/api"
import { PrototypeViewer } from "../../components/design-agent/PrototypeViewer"
import { ManualEditOverlay } from "../../components/design-agent/ManualEditOverlay"
import { PasscodeGate } from "./PasscodeGate"

export type ResolvedView = {
  share_mode: "public" | "passcode"
  requires_passcode: boolean
  bundle_url: string | null
  is_complete: boolean
}

export type ViewerState =
  | { kind: "loading" }
  | { kind: "notfound" }
  | { kind: "error" }
  | { kind: "passcode" }
  | { kind: "ready"; bundleUrl: string; isComplete: boolean }

// Returns null for a 404 — the caller maps that to notFound(). A non-404 non-OK
// status is a real backend error and throws (surfaced as the error state).
export async function resolveToken(
  token: string,
  fetchImpl?: typeof fetch,
): Promise<ResolvedView | null> {
  const doFetch = fetchImpl ?? fetch
  const res = await doFetch(
    `${API_URL}/v1/design-agent/by-token/${encodeURIComponent(token)}`,
    // never stale: a Resume Iteration can re-publish a new bundle URL behind the
    // same token.
    { cache: "no-store" },
  )
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`resolver failed: ${res.status}`)
  return (await res.json()) as ResolvedView
}

// Pure reducer over a resolver outcome → the terminal viewer state. Passcode
// mode arrives with bundle_url === null (the bundle is withheld until POST
// /passcode succeeds), so it maps to the gate; any other missing bundle_url is
// treated as not-found rather than rendering an empty iframe.
export function nextViewerState(
  view: ResolvedView | null,
): Extract<ViewerState, { kind: "notfound" | "passcode" | "ready" }> {
  if (!view) return { kind: "notfound" }
  if (view.share_mode === "passcode" && !view.bundle_url) return { kind: "passcode" }
  if (!view.bundle_url) return { kind: "notfound" }
  return { kind: "ready", bundleUrl: view.bundle_url, isComplete: view.is_complete }
}

export function PublicTokenViewer() {
  const params = useParams<{ token: string | string[] }>()
  const token = Array.isArray(params.token) ? params.token[0] : params.token
  const [state, setState] = useState<ViewerState>({ kind: "loading" })

  useEffect(() => {
    if (!token) {
      setState({ kind: "notfound" })
      return
    }
    let active = true
    resolveToken(token)
      .then((view) => {
        if (active) setState(nextViewerState(view))
      })
      .catch(() => {
        if (active) setState({ kind: "error" })
      })
    return () => {
      active = false
    }
  }, [token])

  // notFound() during render is the supported client-component pattern (it
  // throws into the nearest not-found boundary).
  if (state.kind === "notfound") notFound()
  if (state.kind === "loading") {
    return (
      <div className="design-agent-surface da-public-loading">Loading prototype…</div>
    )
  }
  if (state.kind === "error") {
    return (
      <div className="design-agent-surface da-public-error">
        Could not load this prototype. Please try again.
      </div>
    )
  }
  if (state.kind === "passcode") return <PasscodeGate token={token as string} />
  return (
    <div className="design-agent-surface">
      <PrototypeViewer
        bundleUrl={state.bundleUrl}
        isComplete={state.isComplete}
      // Public-viewer chrome: work-status pill (CompletionBar) and read-only
      // CommentsPanel are intentionally omitted from the public surface (Phase 1
      // cleanup). ManualEditOverlay is kept — it renders nothing without a
      // prototypeId (public resolver is minimum-disclosure), so it is non-breaking.
      // Mark/Comment controls + anon-write affordances come in a later phase.
      chrome={
        <>
          {/* F13 manual edit (P4-01) is INTERNAL-ONLY: it renders its toggle only
              when a prototypeId is supplied. The public resolver is minimum-
              disclosure and exposes no prototypeId / signed-in primitive on this
              surface, so the overlay mounts with prototypeId undefined → renders
              nothing (AC10, non-breaking). The signed-in surface mounts it with a
              real prototypeId + isComplete to enable edit mode. */}
          <ManualEditOverlay isComplete={state.isComplete} />
        </>
      }
      />
    </div>
  )
}
