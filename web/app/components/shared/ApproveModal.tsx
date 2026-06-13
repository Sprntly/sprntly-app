"use client"

// "Generate Prototype" opens the GenerateModal (instead of the legacy
// ClaudeDrawer); "Create a ticket" is untouched. GenerateModal is mounted as a
// sibling here so it can read the current PRD from ContentContext and survive
// ApproveModal closing. Its open/close state lives in the shared navigation
// modal union (`activeModal === "generate"`), not local component state.
//
// The prototype canvas itself is NOT owned here: it lives in-tab at
// `/prototype?prd=<id>` (PrototypeRoute). This component only kicks the
// generation flow (loading overlay + modal) and, on success or on "View
// Prototype", navigates to that in-tab canvas.
import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useWorkspace } from "../../context/WorkspaceContext"
import { updateWorkspace } from "../../lib/onboarding/store"
import type { DesignSourcePreference } from "../../lib/onboarding/types"
import { GenerateModal } from "../design-agent/GenerateModal"
import { GenerationLoadingScreen } from "../design-agent/GenerationLoadingScreen"
import { reasonCopy } from "../design-agent/GenerationErrorBanner"
import { designAgentApi, type PrototypeRecord } from "../../lib/api"
import { prototypePath } from "../../lib/routes"
import type { DesignAgentGenResult } from "../../lib/runDesignAgentGeneration"
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

/** Read the PRD id off a prototype record (carried at runtime, typed via cast),
 *  or null when absent. The prototype canvas is opened by PRD context
 *  (`/prototype?prd=<id>`), so a record without a prd_id degrades gracefully to
 *  the bare `/prototype` (which shows the choose-a-PRD empty state). */
function prdIdOf(proto: PrototypeRecord): number | null {
  return (proto as PrototypeRecord & { prd_id?: number }).prd_id ?? null
}

export function ApproveModal() {
  const { activeModal, closeModal, openDrawer, openModal, showToast } =
    useNavigation()
  const router = useRouter()
  const { content } = useContent()
  const { workspace, refresh } = useWorkspace()
  const savedPref = workspace?.design_source ?? null

  const handleSavePreference = useCallback(async (pref: DesignSourcePreference) => {
    if (!workspace) return
    await updateWorkspace(workspace.id, { design_source: pref })
    await refresh()
  }, [workspace, refresh])
  // Full-screen loading-overlay visibility.
  const [genLoading, setGenLoading] = useState(false)
  // Context captured at generation-start for the loading screen's source-aware steps.
  const [genFigmaKey, setGenFigmaKey] = useState<string | null>(null)
  const [genGithubRepo, setGenGithubRepo] = useState<string | null>(null)
  // Prototype id known once the generate POST returns — lets the loading screen
  // subscribe to the real SSE step stream immediately after kickoff.
  const [genProtoId, setGenProtoId] = useState<number | null>(null)
  // The PRD's existing ready prototype (resolved read-only via getByPrd), or
  // null. When set, the modal's primary option becomes "View Prototype" and
  // navigates to the in-tab canvas directly (no loading screen). The lookup is
  // read-only — it never kicks a generation — and degrades to null (label stays
  // "Generate Prototype") when no ready prototype exists for this PRD.
  const [existing, setExisting] = useState<PrototypeRecord | null>(null)

  // Min-duration bookkeeping: track when the overlay was shown and whether
  // generation has resolved, so dismissal waits for the later of the two.
  const shownAtRef = useRef(0)
  const resolvedRef = useRef(false)
  const safetyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const minTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // The prototype to reveal once the loading overlay actually dismisses (after
  // the min-visible delay). Held in a ref so the deferred dismissal closure reads
  // the latest value without re-binding. Null on failure/timeout → no reveal.
  const pendingCanvasRef = useRef<PrototypeRecord | null>(null)
  // Live mirror of the generate modal's open state for the kickoff-failure
  // guard's deferred timeout (avoids a stale closure). Sourced from the shared
  // navigation modal union.
  const generateActiveRef = useRef(false)
  generateActiveRef.current = activeModal === "generate"
  // Set to true when the user clicks "Notify me when ready". Read in
  // handleGenDone to skip auto-navigate and fire a persistent toast instead.
  const notifyModeRef = useRef(false)

  const clearTimers = useCallback(() => {
    if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current)
    if (minTimerRef.current) clearTimeout(minTimerRef.current)
    safetyTimerRef.current = null
    minTimerRef.current = null
  }, [])

  const hideLoading = useCallback(() => {
    clearTimers()
    setGenLoading(false)
    // When the dismissal was triggered by a SUCCESSFUL generation, navigate to
    // the in-tab canvas for the new prototype's PRD (`/prototype?prd=<id>`) as the
    // loading overlay goes away. On failure/timeout pendingCanvasRef stays null
    // → no navigation (failure surfacing is the existing flow's toast/banner,
    // left untouched).
    if (pendingCanvasRef.current) {
      const revealed = pendingCanvasRef.current
      pendingCanvasRef.current = null
      router.push(prototypePath(prdIdOf(revealed)))
    }
  }, [clearTimers, router])

  // Fired when Generate is clicked: show the overlay and arm the safety ceiling.
  // Receives optional figmaFileKey and githubRepo so the loading screen can show
  // source-aware step labels.
  const handleGenStart = useCallback((ctx?: { figmaFileKey?: string | null; githubRepo?: string | null }) => {
    setGenFigmaKey(ctx?.figmaFileKey ?? null)
    setGenGithubRepo(ctx?.githubRepo ?? null)
    setGenProtoId(null)
    shownAtRef.current = Date.now()
    resolvedRef.current = false
    notifyModeRef.current = false
    // Clear any prototype-to-reveal from a prior run before this generation
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
  // prototype in pendingCanvasRef so hideLoading navigates to the in-tab canvas
  // as the loading overlay dismisses. On FAILURE / no result we leave
  // pendingCanvasRef null → the loading overlay just dismisses and the existing
  // failure surfacing (runGenerateFlow's toast) stands; no navigation.
  const handleGenDone = useCallback(
    (result?: DesignAgentGenResult) => {
      if (resolvedRef.current) return
      resolvedRef.current = true
      if (notifyModeRef.current) {
        // User chose "notify me" — overlay already closed. Fire a persistent
        // toast with an action link; skip auto-navigate.
        if (result?.ok && result.prototype) {
          const protoForToast = result.prototype
          showToast(
            "Prototype ready",
            "Your prototype finished generating.",
            "Open",
            {
              persist: true,
              onAction: () => router.push(prototypePath(prdIdOf(protoForToast))),
            },
          )
        } else if (result && !result.ok) {
          showToast(
            "Generation failed",
            reasonCopy(result.message),
            undefined,
            { persist: true },
          )
        }
        clearTimers()
        return
      }
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
    [hideLoading, clearTimers, showToast, router],
  )

  // Fired when the user clicks "Notify me when ready" in the loading overlay.
  // Arms notify mode (so handleGenDone fires a toast instead of navigating) and
  // closes the overlay immediately.
  const handleNotifyWhenReady = useCallback(() => {
    notifyModeRef.current = true
    setGenLoading(false)
  }, [])

  // Guard for "View Prototype" re-verification: prevents navigating to a stale
  // canvas.
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

  const generateModal = (
    <>
      <GenerateModal
        open={activeModal === "generate"}
        onClose={closeModal}
        prdId={prd?.prd_id ?? null}
        figmaFileKey={prd?.figma_file_key ?? null}
        onGenStart={handleGenStart}
        onKickoff={(id) => setGenProtoId(id)}
        onGenDone={handleGenDone}
        savedPreference={savedPref}
        onSavePreference={handleSavePreference}
      />
      <GenerationLoadingScreen
        open={genLoading}
        figmaFileKey={genFigmaKey}
        githubRepo={genGithubRepo}
        prototypeId={genProtoId}
        onNotifyWhenReady={handleNotifyWhenReady}
      />
    </>
  )

  if (activeModal !== "approve") return generateModal

  // When the PRD already has a ready prototype, "View Prototype" re-verifies that
  // the prototype still exists before navigating to the in-tab canvas (guard
  // against stale `existing` after a delete). On null → switch the label back to
  // "Generate Prototype" and surface a toast. Otherwise falls through to
  // GenerateModal.
  const handleClaudeClick = async () => {
    if (existing) {
      const prdId = prd?.prd_id
      if (prdId == null) return
      setViewBusy(true)
      try {
        const fresh = await designAgentApi.getByPrd(prdId)
        if (fresh && fresh.status === "ready" && fresh.bundle_url) {
          closeModal()
          // Navigate to the in-tab canvas for this PRD; PrototypeRoute resolves
          // and renders the ready prototype from the `?prd=` param.
          router.push(prototypePath(prdIdOf(fresh)))
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
    // Open the generate modal in-place over the PRD screen. Switching the
    // navigation modal union from "approve" to "generate" causes the approve
    // content to unmount (the guard `if (activeModal !== "approve")` above) and
    // the GenerateModal to mount in its place — no navigation on click. The
    // redirect to the in-tab canvas (/prototype?prd=<id>) happens only after the
    // user submits the form and the generate kickoff resolves, wired via the
    // hideLoading callback above.
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
    {/* generate-modal subtree (modal + loading overlay) */}
    {generateModal}
    </>
  )
}
