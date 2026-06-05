"use client"

// "Generate Prototype" opens the GenerateModal (instead of the legacy
// ClaudeDrawer); "Create a ticket" is untouched. GenerateModal is mounted as a
// sibling here so it can read the current PRD from ContentContext and survive
// ApproveModal closing. Its open/close state lives in the shared navigation
// modal union (`activeModal === "generate"`), not local component state.
import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useWorkspace } from "../../context/WorkspaceContext"
import { canvasResolveTarget } from "../../lib/routes"
import { GenerateModal } from "../design-agent/GenerateModal"
import { GenerationLoadingScreen } from "../design-agent/GenerationLoadingScreen"
import { PostGenerationResult } from "../design-agent/PostGenerationResult"
import { CommentsPanel } from "../design-agent/CommentsPanel"
import { IterateComposer } from "../design-agent/IterateComposer"
import { designAgentApi, type CommentRecord, type PrototypeRecord } from "../../lib/api"
import type { DesignAgentGenResult } from "../../lib/runDesignAgentGeneration"
import { useIterateRun } from "../design-agent/useIterateRun"
import { IconCheck, IconSparkle } from "./app-icons"

// UX-EXPLORE (throwaway — REVERT): the loading overlay flashes if generation
// dedup-returns an existing prototype almost instantly (e.g. prd 60 → ready in
// ~1s). Keep it visible at least this long so it's actually seen, then dismiss
// promptly once BOTH (generation resolved AND min elapsed).
const MIN_VISIBLE_MS = 2500
// Hard ceiling so the overlay can never hang if neither callback fires (e.g. a
// swallowed kickoff failure). runGenerateFlow's own poll caps at 6 min; this is
// a slightly-longer belt-and-braces backstop.
const SAFETY_MAX_MS = 6.5 * 60 * 1000

export function ApproveModal() {
  const { activeModal, openModal, closeModal, openDrawer, goTo, canvasPrototypeId, goToCanvas } =
    useNavigation()
  const { content } = useContent()
  // The workspace hydration gate for the canvas resolver.
  const { loading: workspaceLoading } = useWorkspace()
  // UX-EXPLORE (throwaway — REVERT): full-screen loading-overlay visibility.
  const [genLoading, setGenLoading] = useState(false)
  // UX-EXPLORE (throwaway — REVERT): the prototype to show in the FULL-SCREEN
  // post-generation canvas (David's flow: loading takeover → reveals the canvas),
  // or null when no canvas is shown. Set on a successful generation once the
  // loading overlay dismisses; cleared by the canvas's Close/Done affordance
  // (returns to the PRD). The canvas is a full-screen overlay — NOT embedded in
  // the PRD screen.
  const [canvasResult, setCanvasResult] = useState<PrototypeRecord | null>(null)
  // UX-EXPLORE (throwaway — REVERT): minimal state to mount the canvas's
  // comments/iterate slots the same way DesignAgentLauncher does. applyTarget is
  // the comment lifted from CommentsPanel's Apply into IterateComposer's pre-fill.
  const [applyTarget, setApplyTarget] = useState<CommentRecord | null>(null)
  // UX-EXPLORE (throwaway — REVERT, CHANGE 4): the PRD's existing ready prototype
  // (read-only getByPrd), or null. When set, the modal's primary option becomes
  // "View Prototype" and opens the canvas DIRECTLY (no loading screen). Resolved
  // read-only → NEVER kicks a generation; degrades to null (label stays "Generate
  // Prototype") when no read-only endpoint/record exists.
  const [existing, setExisting] = useState<PrototypeRecord | null>(null)

  // Min-duration bookkeeping: track when the overlay was shown and whether
  // generation has resolved, so dismissal waits for the later of the two.
  const shownAtRef = useRef(0)
  const resolvedRef = useRef(false)
  const safetyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const minTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // UX-EXPLORE (throwaway — REVERT): the prototype to reveal in the full-screen
  // canvas once the loading overlay actually dismisses (after the min-visible
  // delay). Held in a ref so the deferred dismissal closure reads the latest
  // value without re-binding. Null on failure/timeout → no canvas revealed.
  const pendingCanvasRef = useRef<PrototypeRecord | null>(null)
  // Live mirror of the generate modal's open state for the kickoff-failure
  // guard's deferred timeout (avoids a stale closure). Sourced from the shared
  // navigation modal union.
  const generateActiveRef = useRef(false)
  generateActiveRef.current = activeModal === "generate"

  const clearTimers = useCallback(() => {
    if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current)
    if (minTimerRef.current) clearTimeout(minTimerRef.current)
    safetyTimerRef.current = null
    minTimerRef.current = null
  }, [])

  const hideLoading = useCallback(() => {
    clearTimers()
    setGenLoading(false)
    // UX-EXPLORE (throwaway — REVERT): when the dismissal was triggered by a
    // SUCCESSFUL generation, reveal the full-screen post-generation canvas as
    // the loading overlay goes away (loading takeover → canvas). On failure/
    // timeout pendingCanvasRef stays null → no canvas (failure surfacing is the
    // existing flow's toast/banner, left untouched).
    if (pendingCanvasRef.current) {
      const revealed = pendingCanvasRef.current
      setCanvasResult(revealed)
      pendingCanvasRef.current = null
      // Push the refresh-stable canvas route as the canvas reveals, so a refresh
      // re-resolves this prototype instead of dropping to the PRD.
      goToCanvas(revealed.id)
    }
  }, [clearTimers, goToCanvas])

  // Fired when Generate is clicked: show the overlay and arm the safety ceiling.
  const handleGenStart = useCallback(() => {
    shownAtRef.current = Date.now()
    resolvedRef.current = false
    // UX-EXPLORE (throwaway — REVERT): clear any canvas-to-reveal from a prior
    // run before this generation resolves.
    pendingCanvasRef.current = null
    setGenLoading(true)
    if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current)
    safetyTimerRef.current = setTimeout(hideLoading, SAFETY_MAX_MS)
    // Kickoff-failure guard. runGenerateFlow swallows a kickoff error (toasts
    // "Generate failed", leaves the modal OPEN, never fires onGenerated) — so on
    // success it ALWAYS calls onClose, which closes the generate modal (clears
    // it from the navigation modal union). If the modal is still open a beat
    // after start, the kickoff failed: dismiss the overlay so it doesn't hang to
    // the safety ceiling.
    setTimeout(() => {
      if (!resolvedRef.current && generateActiveRef.current) hideLoading()
    }, 1500)
  }, [hideLoading])

  // Fired on the terminal generation outcome (ready/failed/timeout). Dismiss
  // once the min-visible duration has also elapsed.
  // UX-EXPLORE (throwaway — REVERT): now receives the terminal RESULT. On
  // SUCCESS (`result.ok` with a ready prototype) we stash the prototype in
  // pendingCanvasRef so hideLoading reveals the full-screen post-generation
  // canvas as the loading overlay dismisses (David's flow: loading takeover →
  // canvas). On FAILURE / no result we leave pendingCanvasRef null → the loading
  // overlay just dismisses and the existing failure surfacing (runGenerateFlow's
  // toast) stands; no canvas is shown.
  const handleGenDone = useCallback(
    (result?: DesignAgentGenResult) => {
      if (resolvedRef.current) return
      resolvedRef.current = true
      pendingCanvasRef.current =
        result?.ok && result.prototype ? result.prototype : null
      const remaining = MIN_VISIBLE_MS - (Date.now() - shownAtRef.current)
      if (remaining <= 0) {
        hideLoading()
      } else {
        if (minTimerRef.current) clearTimeout(minTimerRef.current)
        minTimerRef.current = setTimeout(hideLoading, remaining)
      }
    },
    [hideLoading],
  )

  // UX-EXPLORE (throwaway — REVERT): Close/Done — clear the full-screen canvas
  // (returns to the PRD) and drop any lifted apply target.
  const closeCanvas = useCallback(() => {
    setCanvasResult(null)
    setApplyTarget(null)
    // Leave the canvas route so the URL and view stay consistent and the
    // resolver does not immediately re-open the canvas. The canvas opens from
    // the approved PRD, so the PRD is its logical parent.
    if (canvasPrototypeId != null) goTo("prd")
  }, [canvasPrototypeId, goTo])

  // UX-EXPLORE (throwaway — REVERT): after a Share or an iterate advances the
  // SAME prototype, re-fetch the record so the in-canvas share-gated CommentsPanel
  // / viewer reflect it. Minimal single-shot refresh (the launcher's race-safe
  // pollUntilAdvanced is overkill for this throwaway full-screen path).
  const refreshCanvas = useCallback(async () => {
    const id = canvasResult?.id
    if (id == null) return
    try {
      const fresh = await designAgentApi.get(id)
      if (fresh) setCanvasResult(fresh)
    } catch {
      /* degrade silently — the local ShareMenu token already shows the link */
    }
  }, [canvasResult?.id])

  // UX-EXPLORE (throwaway — REVERT, CHANGE A): a reload nonce bumped on every
  // completed iterate. The center iframe reads `bundle_url`; if the backend
  // OVERWRITES the bundle at the SAME url (rather than a new path), the iframe
  // src is unchanged and the browser never reloads the new version. Threading
  // this nonce into the viewer's src (as `?v=<nonce>`) forces a fresh src → the
  // iframe reloads the rebuilt bundle even when the url string is identical.
  const [bundleReloadNonce, setBundleReloadNonce] = useState(0)

  // UX-EXPLORE (throwaway — REVERT, CHANGE A): the SHARED iterate runner. Lives
  // here (the level that owns canvasResult + constructs BOTH the IterateComposer
  // and the CommentsPanel) so the left composer Submit, a comment's Apply, and a
  // pin's Apply all drive ONE fixed iterate path: POST → poll-to-completion →
  // left-panel activity → reload the canvas. onComplete swaps in the fresh row
  // (the new bundle_url) AND bumps the reload nonce so the iframe reloads.
  const iterateRun = useIterateRun({
    prototypeId: canvasResult?.id ?? -1,
    onComplete: (fresh) => {
      setCanvasResult(fresh)
      setBundleReloadNonce((n) => n + 1)
    },
  })

  // UX-EXPLORE (throwaway — REVERT, CHANGE A/B): the single fixed entry the
  // composer + both Apply paths call. No-ops if no canvas is mounted.
  const runCanvasIterate = useCallback(
    (instruction: string, appliedCommentId?: number | null) => {
      if (canvasResult?.id == null) return
      void iterateRun.runIterate(instruction, appliedCommentId)
    },
    [canvasResult?.id, iterateRun],
  )

  // UX-EXPLORE (throwaway — REVERT, CHANGE B): a comment's Apply → run its body
  // through the iterate runner immediately, linking the comment id. The agent
  // decides applicability (prompt-driven); the client fabricates no change.
  const runCommentIterate = useCallback(
    (comment: CommentRecord) => {
      runCanvasIterate(comment.body, comment.id)
    },
    [runCanvasIterate],
  )

  const prd = content.prd

  // UX-EXPLORE (throwaway — REVERT, CHANGE 4): resolve the PRD's existing ready
  // prototype read-only when the approve modal is open. `getByPrd` swallows the
  // 404 (no read-only endpoint yet) → null, so this NEVER kicks a generation and
  // the label simply stays "Generate Prototype" until the endpoint exists.
  useEffect(() => {
    const prdId = prd?.prd_id
    if (activeModal !== "approve" || prdId == null) {
      setExisting(null)
      return
    }
    let cancelled = false
    designAgentApi
      .getByPrd(prdId)
      .then((proto) => {
        if (cancelled) return
        setExisting(
          proto && proto.status === "ready" && proto.bundle_url ? proto : null,
        )
      })
      .catch(() => {
        if (!cancelled) setExisting(null)
      })
    return () => {
      cancelled = true
    }
  }, [activeModal, prd?.prd_id])

  // Refresh re-resolution. When the URL is the canvas route (`/design/{id}`) —
  // e.g. after a page refresh while editing — re-open the canvas by fetching the
  // prototype, instead of dropping to the empty PRD screen. Gated on workspace
  // hydration (never resolve against an un-hydrated workspace). Records the id it
  // resolved from the URL so the transient render during closeCanvas (route not
  // yet updated) cannot refetch and reopen the canvas the user just closed.
  const urlResolvedIdRef = useRef<number | null>(null)
  useEffect(() => {
    const target = canvasResolveTarget(
      canvasPrototypeId,
      !workspaceLoading,
      canvasResult?.id ?? null,
    )
    if (target == null) return
    if (urlResolvedIdRef.current === target) return
    urlResolvedIdRef.current = target
    let cancelled = false
    designAgentApi
      .get(target)
      .then((proto) => {
        if (!cancelled && proto) setCanvasResult(proto)
      })
      .catch(() => {
        // Degrade silently — a bad/stale id just leaves the canvas closed; the
        // PRD screen (base) still renders behind.
      })
    return () => {
      cancelled = true
    }
  }, [canvasPrototypeId, workspaceLoading, canvasResult?.id])

  // Render the generate-modal subtree regardless of which modal is active, so
  // the loading overlay (a top-level sibling) covers the whole viewport (incl.
  // the sidebar) regardless of modal state.
  // UX-EXPLORE (throwaway — REVERT): the full-screen post-generation canvas.
  // Revealed only after a SUCCESSFUL generation (David's flow: loading takeover →
  // canvas), NOT embedded in the PRD screen. Fixed inset:0 above the app chrome
  // (same footprint as the loading screen / proto-fullscreen), with a top-right
  // Done affordance that clears canvasResult and returns to the PRD. The
  // comments/iterate slots are mounted the SAME way DesignAgentLauncher does:
  // CommentsPanel gated on share_token (onApply → setApplyTarget); IterateComposer
  // with prototypeId/isComplete/applyTarget/onClearApply/onIterated. `key` off the
  // prototype id forces a clean remount per prototype.
  const canvasOverlay = canvasResult ? (
    <div
      className="da-canvas-fullscreen design-agent-surface"
      role="dialog"
      aria-modal="true"
      aria-label="Generated prototype"
      data-testid="da-canvas-fullscreen"
    >
      {/* UX-EXPLORE (throwaway — REVERT): the standalone top-right Done button is
          GONE — "Done" now lives in the new top control bar (threaded via
          `onDone={closeCanvas}` below), so the canvas has a single Done affordance
          per the reworked spec. The overlay shell itself is unchanged. */}
      <div className="da-canvas-fullscreen-body">
        <PostGenerationResult
          key={canvasResult.id}
          prototype={canvasResult}
          prdSections={prd?.sections}
          prdTitle={prd?.title ?? null}
          // UX-EXPLORE (throwaway — REVERT, CHANGE A): one-line PRD meta for the
          // condensed left context panel.
          prdMetaLine={prd?.metaLine ?? null}
          // UX-EXPLORE (throwaway — REVERT, CHANGE B): a pin comment's Apply now
          // runs the iterate IMMEDIATELY through the shared runner (body+pin
          // context as the instruction) instead of pre-filling the composer.
          onPinIterate={runCanvasIterate}
          onDone={closeCanvas}
          // UX-EXPLORE (throwaway — REVERT, CHANGE A): live agent-flow activity +
          // clarifying-question continuation for the LEFT panel, all driven by the
          // shared runner (poll-only → cosmetic steps; SSE-ready seam inside it).
          iterateActivity={iterateRun.activity}
          iterateRunning={iterateRun.running}
          iterateError={iterateRun.error}
          iteratePendingQuestion={iterateRun.pendingQuestion}
          onAnswerQuestion={iterateRun.answerQuestion}
          // UX-EXPLORE (throwaway — REVERT, CHANGE A): bumped on each completed
          // iterate so the center iframe reloads the rebuilt bundle even if the
          // backend overwrites the bundle at the same url.
          bundleReloadNonce={bundleReloadNonce}
          comments={
            canvasResult.share_token ? (
              <CommentsPanel
                key={`comments-${canvasResult.id}`}
                token={canvasResult.share_token}
                prototypeId={canvasResult.id}
                // UX-EXPLORE (throwaway — REVERT, CHANGE B): Apply → immediate
                // iterate via the shared runner + resolve (inside CommentsPanel).
                onIterateComment={runCommentIterate}
                iterateBusy={iterateRun.running}
              />
            ) : null
          }
          iterate={
            // UX-EXPLORE (throwaway — REVERT, CHANGE 1): the left composer now
            // reflects the prototype's REAL lock state. When the prototype is
            // LOCKED (`is_complete`) the composer disables itself and shows an
            // "Unlock" button (wired to the resume/unlock path inside
            // IterateComposer); after unlocking, the composer becomes active.
            <IterateComposer
              key={`iterate-${canvasResult.id}`}
              prototypeId={canvasResult.id}
              isComplete={canvasResult.is_complete ?? false}
              applyTarget={applyTarget}
              onClearApply={() => setApplyTarget(null)}
              onIterated={refreshCanvas}
              // UX-EXPLORE (throwaway — REVERT, CHANGE F): Submit runs the
              // iteration directly — skips the AD14 cost-estimate/confirm modal.
              // Intentional product decision (flagged for an AD14 reconsideration
              // ticket, NOT a silent removal of the cost gate).
              skipCostConfirm
              // UX-EXPLORE (throwaway — REVERT, CHANGE A): Submit DELEGATES to the
              // shared runner (single fixed iterate path with left-panel activity
              // + poll-to-completion + canvas reload).
              runIterateExternal={runCanvasIterate}
              externalBusy={iterateRun.running}
            />
          }
          onShared={refreshCanvas}
        />
      </div>
    </div>
  ) : null

  const generateModal = (
    <>
      <GenerateModal
        open={activeModal === "generate"}
        onClose={closeModal}
        prdId={prd?.prd_id ?? null}
        figmaFileKey={prd?.figma_file_key ?? null}
        onGenStart={handleGenStart}
        onGenDone={handleGenDone}
      />
      <GenerationLoadingScreen open={genLoading} />
      {canvasOverlay}
    </>
  )

  if (activeModal !== "approve") return generateModal

  // UX-EXPLORE (throwaway — REVERT, CHANGE 4): if the PRD already has a ready
  // prototype, "View Prototype" opens the canvas DIRECTLY with the existing
  // prototype, SKIPPING the loading sequence (no GenerationLoadingScreen). Else
  // "Generate Prototype" → GenerateModal → loading screen → canvas (unchanged).
  const handleClaudeClick = () => {
    if (existing) {
      closeModal()
      setCanvasResult(existing)
      // Push the refresh-stable canvas route for the existing prototype too, so
      // "View Prototype" → refresh re-opens the canvas.
      goToCanvas(existing.id)
      return
    }
    // Hand the generate modal's visibility to the navigation modal union; this
    // swaps the "approve" modal out for "generate".
    openModal("generate")
  }

  const handleTicketClick = () => {
    closeModal()
    openDrawer("ticket")
  }

  return (
    <>
    <div
      className="modal-overlay open"
      onClick={(e) => e.target === e.currentTarget && closeModal()}
    >
      <div className="modal">
        <div className="modal-head">
          <div className="modal-badge">
            <IconCheck size={12} />
            PRD Approved
          </div>
          <h2 className="modal-title">Where should this go next?</h2>
          <p className="modal-sub">
            Pick how you want to move from spec to code. You can change your mind
            later.
          </p>
        </div>
        <div className="modal-options">
          <div className="modal-option" onClick={handleClaudeClick}>
            <div className="modal-option-icon">
              <IconSparkle size={18} />
            </div>
            <div className="modal-option-name">
              {existing ? "View Prototype" : "Generate Prototype"}
            </div>
            <div className="modal-option-desc">
              {existing
                ? "Open the interactive prototype already generated from this PRD."
                : "Full context package → Claude Code scopes, implements, opens a PR against main."}
            </div>
          </div>
          <div className="modal-option" onClick={handleTicketClick}>
            <div className="modal-option-icon">J</div>
            <div className="modal-option-name">Create a ticket</div>
            <div className="modal-option-desc">
              Push to Linear, Jira, or Asana with evidence attached. Track it to
              merge.
            </div>
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={closeModal}>
            Cancel
          </button>
        </div>
      </div>
    </div>
    {/* generate-modal subtree (modal + loading overlay + canvas) */}
    {generateModal}
    </>
  )
}
