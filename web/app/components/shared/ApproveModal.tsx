"use client"

// "Generate Prototype" opens the GenerateModal (instead of the legacy
// ClaudeDrawer); "Create a ticket" is untouched. GenerateModal is mounted as a
// sibling here so it can read the current PRD from ContentContext and survive
// ApproveModal closing. Its open/close state lives in the shared navigation
// modal union (`activeModal === "generate"`), not local component state.
import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useWorkspace } from "../../context/WorkspaceContext"
import { canvasResolveTarget } from "../../lib/routes"
import { GenerateModal } from "../design-agent/GenerateModal"
import { GenerationLoadingScreen } from "../design-agent/GenerationLoadingScreen"
import { PostGenerationResult } from "../design-agent/PostGenerationResult"
import { CommentsPanel } from "../design-agent/CommentsPanel"
import { IterateComposer } from "../design-agent/IterateComposer"
import { designAgentApi, prdApi, type CommentRecord, type PrototypeRecord } from "../../lib/api"
import { markdownToPrdState } from "../../lib/prd-adapter"
import type { PrdSection } from "../../types/content"
import type { DesignAgentGenResult } from "../../lib/runDesignAgentGeneration"
import { useIterateRun } from "../design-agent/useIterateRun"
import { IconSparkle } from "./app-icons"

// Min-visible duration. If generation dedup-returns an existing prototype almost
// instantly, the overlay would otherwise flash; keep it visible at least this
// long so it actually registers, then dismiss promptly once BOTH conditions hold
// (generation resolved AND this minimum elapsed).
const MIN_VISIBLE_MS = 2500
// Hard ceiling so the overlay can never hang if neither callback fires (e.g. a
// swallowed kickoff failure). runGenerateFlow's own poll caps at 6 min; this is
// a slightly-longer belt-and-braces backstop.
const SAFETY_MAX_MS = 6.5 * 60 * 1000

export function ApproveModal() {
  const router = useRouter()
  const { activeModal, openModal, closeModal, openDrawer, goTo, canvasPrototypeId, goToCanvas, showToast } =
    useNavigation()
  const { content } = useContent()
  // The workspace hydration gate for the canvas resolver.
  const { loading: workspaceLoading } = useWorkspace()
  // Full-screen loading-overlay visibility.
  const [genLoading, setGenLoading] = useState(false)
  // Context captured at generation-start for the loading screen's source-aware steps.
  const [genFigmaKey, setGenFigmaKey] = useState<string | null>(null)
  const [genGithubRepo, setGenGithubRepo] = useState<string | null>(null)
  // The prototype to show in the full-screen post-generation canvas (the loading
  // takeover reveals the canvas), or null when no canvas is shown. Set on a
  // successful generation once the loading overlay dismisses; cleared by the
  // canvas's Close/Done affordance (returns to the PRD). The canvas is a
  // full-screen overlay — NOT embedded in the PRD screen.
  const [canvasResult, setCanvasResult] = useState<PrototypeRecord | null>(null)
  // Minimal state to mount the canvas's comments/iterate slots the same way
  // DesignAgentLauncher does. applyTarget is the comment lifted from
  // CommentsPanel's Apply into IterateComposer's pre-fill.
  const [applyTarget, setApplyTarget] = useState<CommentRecord | null>(null)
  // The PRD's existing ready prototype (resolved read-only via getByPrd), or
  // null. When set, the modal's primary option becomes "View Prototype" and
  // opens the canvas directly (no loading screen). The lookup is read-only — it
  // never kicks a generation — and degrades to null (label stays "Generate
  // Prototype") when no ready prototype exists for this PRD.
  const [existing, setExisting] = useState<PrototypeRecord | null>(null)
  const [urlPrdSections, setUrlPrdSections] = useState<PrdSection[] | undefined>(undefined)
  const [urlPrdTitle, setUrlPrdTitle] = useState<string | null>(null)

  // Min-duration bookkeeping: track when the overlay was shown and whether
  // generation has resolved, so dismissal waits for the later of the two.
  const shownAtRef = useRef(0)
  const resolvedRef = useRef(false)
  const safetyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const minTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // The prototype to reveal in the full-screen canvas once the loading overlay
  // actually dismisses (after the min-visible delay). Held in a ref so the
  // deferred dismissal closure reads the latest value without re-binding. Null on
  // failure/timeout → no canvas revealed.
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
    // When the dismissal was triggered by a SUCCESSFUL generation, reveal the
    // full-screen post-generation canvas as the loading overlay goes away
    // (loading takeover → canvas). On failure/timeout pendingCanvasRef stays null
    // → no canvas (failure surfacing is the existing flow's toast/banner, left
    // untouched).
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
  // Receives optional figmaFileKey and githubRepo so the loading screen can show
  // source-aware step labels.
  const handleGenStart = useCallback((ctx?: { figmaFileKey?: string | null; githubRepo?: string | null }) => {
    setGenFigmaKey(ctx?.figmaFileKey ?? null)
    setGenGithubRepo(ctx?.githubRepo ?? null)
    shownAtRef.current = Date.now()
    resolvedRef.current = false
    // Clear any canvas-to-reveal from a prior run before this generation
    // resolves.
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
  // once the min-visible duration has also elapsed. Receives the terminal
  // RESULT. On SUCCESS (`result.ok` with a ready prototype) we stash the
  // prototype in pendingCanvasRef so hideLoading reveals the full-screen
  // post-generation canvas as the loading overlay dismisses (loading takeover →
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

  // Close/Done — clear the full-screen canvas (returns to the PRD) and drop any
  // lifted apply target.
  const closeCanvas = useCallback(() => {
    setCanvasResult(null)
    setApplyTarget(null)
    setUrlPrdSections(undefined)
    setUrlPrdTitle(null)
    // Keep the resolved-id sentinel at its current value (the URL prototype id)
    // rather than clearing it to null. Clearing it caused a re-resolution race:
    // canvasResult → null triggered the resolver effect which, seeing
    // urlResolvedIdRef.current = null while canvasPrototypeId was still the old
    // id (Next.js router.push is async), would re-fetch and re-open the canvas
    // before the /prd navigation completed, making the breadcrumb appear to do
    // nothing on the standalone /design/[id] route.
    urlResolvedIdRef.current = canvasPrototypeId
    // Leave the canvas route so the URL and view stay consistent and the
    // resolver does not immediately re-open the canvas. The canvas opens from
    // the approved PRD, so the PRD is its logical parent.
    if (canvasPrototypeId != null) {
      goTo("prd")
      router.push("/prd")
    }
  }, [canvasPrototypeId, goTo, router])

  // After a Share or an iterate advances the
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

  // A reload nonce bumped on every
  // completed iterate. The center iframe reads `bundle_url`; if the backend
  // OVERWRITES the bundle at the SAME url (rather than a new path), the iframe
  // src is unchanged and the browser never reloads the new version. Threading
  // this nonce into the viewer's src (as `?v=<nonce>`) forces a fresh src → the
  // iframe reloads the rebuilt bundle even when the url string is identical.
  const [bundleReloadNonce, setBundleReloadNonce] = useState(0)

  // Shared iterate runner. Lives here (the level that owns canvasResult +
  // constructs both the IterateComposer and the CommentsPanel) so the left
  // composer Submit, a comment's Apply, and a pin's Apply all drive one fixed
  // iterate path: POST → poll-to-completion → left-panel activity → reload the
  // canvas. onComplete swaps in the fresh row (the new bundle_url) and bumps
  // the reload nonce so the iframe reloads.
  const iterateRun = useIterateRun({
    prototypeId: canvasResult?.id ?? -1,
    onComplete: (fresh) => {
      setCanvasResult(fresh)
      setBundleReloadNonce((n) => n + 1)
    },
  })

  // The single fixed entry the composer and both Apply paths call.
  // No-ops if no canvas is mounted.
  const runCanvasIterate = useCallback(
    (instruction: string, appliedCommentId?: number | null) => {
      if (canvasResult?.id == null) return
      void iterateRun.runIterate(instruction, appliedCommentId)
    },
    [canvasResult?.id, iterateRun],
  )

  // A comment's Apply → run its body through the iterate runner, linking
  // the comment id. The agent decides applicability; the client fabricates no change.
  const runCommentIterate = useCallback(
    (comment: CommentRecord) => {
      runCanvasIterate(comment.body, comment.id)
    },
    [runCanvasIterate],
  )

  // Guard for "View Prototype" re-verification: prevents opening a stale canvas.
  const [viewBusy, setViewBusy] = useState(false)

  const prd = content.prd

  // Resolve the PRD's existing ready prototype read-only while the approve modal
  // is open. `getByPrd` swallows a 404 → null, so this never kicks a generation;
  // the label simply stays "Generate Prototype" when no ready prototype exists.
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

  // When canvasResult is set by the URL resolver, fetch the PRD so the left
  // panel has sections/title. Best-effort — swallows errors, no loading state.
  const canvasResultPrdId = (canvasResult as (PrototypeRecord & { prd_id?: number }) | null)?.prd_id ?? null
  useEffect(() => {
    if (!canvasResultPrdId || urlPrdSections !== undefined) return
    prdApi.get(canvasResultPrdId).then((prd) => {
      const parsed = markdownToPrdState(prd.payload_md)
      setUrlPrdSections(parsed.sections)
      setUrlPrdTitle(prd.title ?? null)
    }).catch(() => {/* best-effort */})
  }, [canvasResultPrdId, urlPrdSections])

  // Render the generate-modal subtree regardless of which modal is active, so
  // the loading overlay (a top-level sibling) covers the whole viewport (incl.
  // the sidebar) regardless of modal state.
  // The full-screen post-generation canvas.
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
      {/* The standalone top-right Done button is
          GONE — "Done" now lives in the new top control bar (threaded via
          `onDone={closeCanvas}` below), so the canvas has a single Done affordance
          per the reworked spec. The overlay shell itself is unchanged. */}
      <div className="da-canvas-fullscreen-body">
        <PostGenerationResult
          key={canvasResult.id}
          prototype={canvasResult}
          onStateChange={(state) =>
            setCanvasResult((prev) =>
              prev ? { ...prev, is_complete: state.isComplete } : prev,
            )
          }
          prdSections={prd?.sections ?? urlPrdSections}
          prdTitle={prd?.title ?? urlPrdTitle}
          // One-line PRD meta for the
          // condensed left context panel.
          prdMetaLine={prd?.metaLine ?? null}
          // A pin comment's Apply now
          // runs the iterate IMMEDIATELY through the shared runner (body+pin
          // context as the instruction) instead of pre-filling the composer.
          onPinIterate={runCanvasIterate}
          onDone={closeCanvas}
          // Live agent-flow activity and clarifying-question continuation for
          // the left panel, all driven by the shared runner.
          iterateActivity={iterateRun.activity}
          iterateRunning={iterateRun.running}
          iterateError={iterateRun.error}
          iteratePendingQuestion={iterateRun.pendingQuestion}
          onAnswerQuestion={iterateRun.answerQuestion}
          // Bumped on each completed iterate so the center iframe reloads the
          // rebuilt bundle even if the backend overwrites at the same url.
          bundleReloadNonce={bundleReloadNonce}
          comments={
            canvasResult.share_token ? (
              <CommentsPanel
                key={`comments-${canvasResult.id}`}
                token={canvasResult.share_token}
                prototypeId={canvasResult.id}
                // Apply → immediate
                // iterate via the shared runner + resolve (inside CommentsPanel).
                onIterateComment={runCommentIterate}
                iterateBusy={iterateRun.running}
              />
            ) : null
          }
          iterate={
            // The left composer now
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
              // The iterate path intentionally skips the pre-flight cost-estimate
              // confirmation modal. The per-generation soft/hard spend caps remain
              // the guardrail, and the generate-path estimate is unchanged. The
              // default (`skipCostConfirm = false`) preserves the confirmation
              // modal for any non-iterate caller.
              skipCostConfirm
              // Submit delegates to the shared runner (fixed iterate path with
              // left-panel activity, poll-to-completion, and canvas reload).
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
      <GenerationLoadingScreen
        open={genLoading}
        figmaFileKey={genFigmaKey}
        githubRepo={genGithubRepo}
      />
      {canvasOverlay}
    </>
  )

  if (activeModal !== "approve") return generateModal

  // When the PRD already has a ready prototype, "View Prototype" re-verifies that
  // the prototype still exists before opening the canvas (guard against stale
  // `existing` after a delete). On null → switch the label back to "Generate
  // Prototype" and surface a toast. Otherwise falls through to GenerateModal.
  const handleClaudeClick = async () => {
    if (existing) {
      const prdId = prd?.prd_id
      if (prdId == null) return
      setViewBusy(true)
      try {
        const fresh = await designAgentApi.getByPrd(prdId)
        if (fresh && fresh.status === "ready" && fresh.bundle_url) {
          closeModal()
          setCanvasResult(fresh)
          // Push the refresh-stable canvas route for the existing prototype too, so
          // "View Prototype" → refresh re-opens the canvas.
          goToCanvas(fresh.id)
        } else {
          // Prototype was deleted or is no longer ready — reset so the button
          // switches back to "Generate Prototype".
          setExisting(null)
          showToast("Prototype unavailable", "The prototype was removed. Generate a new one.")
        }
      } catch {
        setExisting(null)
        showToast("Prototype unavailable", "The prototype was removed. Generate a new one.")
      } finally {
        setViewBusy(false)
      }
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
          <h2 className="modal-title">Where should this go next?</h2>
          <p className="modal-sub">
            Pick how you want to move from spec to code. You can change your mind
            later.
          </p>
        </div>
        <div className="modal-options">
          <div
            className={`modal-option${viewBusy ? " opacity-50 pointer-events-none" : ""}`}
            onClick={handleClaudeClick}
          >
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
