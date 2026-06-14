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
import { PrototypeViewer } from "../components/design-agent/PrototypeViewer"
import { ManualEditOverlay } from "../components/design-agent/ManualEditOverlay"
import { CommentsPanel } from "../components/design-agent/CommentsPanel"
import { PasscodeGate } from "./PasscodeGate"
import { resolveToken, type ResolvedView } from "./resolveToken"
import { IconMessage, IconPin } from "../components/shared/app-icons"

export type { ResolvedView }

export type ViewerState =
  | { kind: "loading" }
  | { kind: "notfound" }
  | { kind: "error" }
  | { kind: "passcode" }
  | { kind: "ready"; bundleUrl: string; isComplete: boolean }

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
  // C2a public-viewer chrome state. `commentsOpen` toggles the writable-anon
  // CommentsPanel; `markMode` is a placeholder toggle — C2b wires the actual
  // pin/mark overlay to it (no overlay yet, by design for this slice).
  const [commentsOpen, setCommentsOpen] = useState(false)
  const [markMode, setMarkMode] = useState(false)

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
        // C2a: Mark + Comment controls in the browser-frame head. Styled like the
        // platform toggle (.platform-toggle group look). aria-pressed reflects the
        // toggle state. Comment opens the writable-anon CommentsPanel below.
        // Mark just flips markMode for now — C2b wires the actual pin/mark overlay
        // to markMode (no overlay yet, by design for this slice).
        headControls={
          <div
            className="platform-toggle proto-head-controls-group"
            role="group"
            aria-label="Prototype tools"
          >
            <button
              type="button"
              className={markMode ? "active" : ""}
              aria-pressed={markMode}
              data-testid="public-mark-toggle"
              onClick={() => setMarkMode((v) => !v)}
              title="Mark"
            >
              <IconPin size={14} />
            </button>
            <button
              type="button"
              className={commentsOpen ? "active" : ""}
              aria-pressed={commentsOpen}
              data-testid="public-comments-toggle"
              onClick={() => setCommentsOpen((v) => !v)}
              title="Comments"
            >
              <IconMessage size={14} />
            </button>
          </div>
        }
      chrome={
        <>
          {/* F13 manual edit (P4-01) is INTERNAL-ONLY: it renders its toggle only
              when a prototypeId is supplied. The public resolver is minimum-
              disclosure and exposes no prototypeId / signed-in primitive on this
              surface, so the overlay mounts with prototypeId undefined → renders
              nothing (AC10, non-breaking). The signed-in surface mounts it with a
              real prototypeId + isComplete to enable edit mode. */}
          <ManualEditOverlay isComplete={state.isComplete} />
          {/* C2a writable-anon comments. No prototypeId on this surface (minimum-
              disclosure), so create routes via createCommentByToken(token);
              canComment enables create while resolve/apply/ignore/delete stay
              hidden (all gated on prototypeId). The head Comment toggle collapses
              the panel by flipping commentsOpen. */}
          {commentsOpen && (
            <CommentsPanel
              token={token as string}
              canComment
            />
          )}
        </>
      }
      />
    </div>
  )
}
