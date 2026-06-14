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
// C2b: the public surface drives the SAME pin engine as the signed-in editor via
// the shared usePinMarking hook + the extracted MarkOverlay / PinLayer /
// PrototypeMarkLayer leaves. The only per-surface difference is the create-fn:
// the public viewer routes via createCommentByToken (no prototypeId / auth).
import { usePinMarking } from "../components/design-agent/usePinMarking"
import { MarkOverlay, PinLayer, PrototypeMarkLayer } from "../components/design-agent/PrototypeMarkLayer"
import { designAgentApi } from "../lib/api"
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
  // CommentsPanel.
  const [commentsOpen, setCommentsOpen] = useState(false)
  // C2b: real marking, driven by the shared usePinMarking hook. The create-fn is
  // the public createCommentByToken (no prototypeId / auth) — distinct from the
  // signed-in editor's withAuthRetry(createComment(prototype.id)). No
  // onPinIterate / onPinApply on this surface, so PrototypeMarkLayer's Apply /
  // Ignore stay hidden (editorMode=false). Entering mark mode + dropping a pin
  // both reveal the comments sidebar so the new pin row is visible.
  const pin = usePinMarking({
    onCreate: (payload) => designAgentApi.createCommentByToken(token as string, payload),
    onEnterMarkMode: () => setCommentsOpen(true),
    onPinDropped: () => setCommentsOpen(true),
  })

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
        // C2b: Mark now drives the real pin/mark overlay via the shared hook
        // (pin.toggleMark / pin.markMode), mounted in the stageOverlay below.
        headControls={
          <div
            className="platform-toggle proto-head-controls-group"
            role="group"
            aria-label="Prototype tools"
          >
            <button
              type="button"
              className={pin.markMode ? "active" : ""}
              aria-pressed={pin.markMode}
              data-testid="public-mark-toggle"
              onClick={() => pin.toggleMark()}
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
        // C2b: the marking overlay renders INSIDE `.proto-stage`, layered over the
        // iframe. MarkOverlay is click-inert except in mark mode (where it
        // hit-tests the iframe + drops a pin); PinLayer renders the numbered pins.
        stageOverlay={
          <>
            <MarkOverlay markMode={pin.markMode} onStageClick={pin.handleStageClick} />
            <PinLayer pins={pin.pins} computedPinPositions={pin.computedPinPositions} />
          </>
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
            <>
              {/* C2b: the dropped-pin comment rows (draft composer + saved rows).
                  editorMode=false + canResolve=false → Apply / Ignore / resolve
                  are hidden on the public surface; only the draft → submit (via
                  createCommentByToken) + saved display remain. */}
              <PrototypeMarkLayer
                pins={pin.pins}
                editorMode={false}
                canResolve={false}
                onPinDraftChange={pin.handlePinDraftChange}
                onSubmitComment={pin.handlePinSubmit}
                onPinRemove={pin.handlePinRemove}
              />
              <CommentsPanel
                token={token as string}
                canComment
              />
            </>
          )}
        </>
      }
      />
    </div>
  )
}
