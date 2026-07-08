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
// Prototype", navigates to that in-tab canvas — all delegated to the shared
// useGeneratePrototype() hook, which owns the existence check, the loading
// overlay lifecycle, and the notify-when-ready side effects.
import { useEffect, useRef, useState } from "react"
import { usePathname } from "next/navigation"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { GenerateModal } from "../design-agent/GenerateModal"
import { GenerationLoadingScreen } from "../design-agent/GenerationLoadingScreen"
import { useGeneratePrototype } from "../design-agent/useGeneratePrototype"
import { IconSparkle } from "./app-icons"

export function ApproveModal() {
  const { activeModal, closeModal, openDrawer, openModal } = useNavigation()
  const { content } = useContent()
  const prd = content.prd

  const gen = useGeneratePrototype(prd?.prd_id ?? null, {
    figmaFileKey: prd?.figma_file_key ?? null,
    // ApproveModal is a singleton tied to the single ContentContext-active PRD,
    // never a per-item list — the ONLY site in Theme A where this flag is safe
    // (see useGeneratePrototype's doc comment on listenForCrossSurfaceGenerating).
    listenForCrossSurfaceGenerating: true,
    open: activeModal === "generate",
    onOpenChange: (open) => (open ? openModal("generate") : closeModal()),
  })

  // Guard for "View Prototype" re-verification: dims the option while the
  // hook's re-verify fetch is in flight. Not part of the hook's own contract —
  // handleCtaClick returns a Promise<void> precisely so a host can wrap it.
  const [viewBusy, setViewBusy] = useState(false)

  // The hook's "view" success path calls `router.push` directly and has no
  // notion of THIS modal's own `activeModal === "approve"` visibility gate —
  // before this migration, ApproveModal always paired its own `closeModal()`
  // call with that same router.push (see git history). Reacting to an actual
  // pathname change (rather than peeking at the hook's internal state right
  // after the click, which is a stale closure once the hook's setState lands)
  // closes the approve modal on a real successful navigation while leaving it
  // open on the stale/failed re-verify path (which never calls router.push, so
  // pathname never changes) — matching pre-migration behavior on both
  // branches without duplicating the hook's own success/failure bookkeeping.
  const pathname = usePathname()
  const prevPathnameRef = useRef(pathname)
  useEffect(() => {
    if (pathname !== prevPathnameRef.current) {
      prevPathnameRef.current = pathname
      if (activeModal === "approve") closeModal()
    }
  }, [pathname, activeModal, closeModal])

  // The hook's existence-check effect only re-runs when `prdId` itself changes
  // (see useGeneratePrototype's deps) — it has no notion of THIS modal's own
  // open/close cycling. Before this migration, ApproveModal's own existence
  // effect depended on `[activeModal, prd?.prd_id]`, so reopening the modal for
  // the SAME PRD re-checked status (the "reopen mid-generation" belt-and-braces
  // behavior: a background generation's `da:generating` event fired while this
  // modal was closed is caught on reopen via a fresh read, not just the window
  // listener). `refetchExisting()` is the hook's own sanctioned escape hatch
  // for exactly this kind of host-specific re-check trigger — call it whenever
  // the union transitions INTO "approve" (skipped on the very first mount,
  // where the hook's own mount-time fetch already covers it). `hasOpenedApproveRef`
  // additionally skips the VERY FIRST transition into "approve" (not just an
  // already-approve initial render) — that first open is already covered by
  // the hook's own unconditional mount-time fetch (fired as soon as `prdId` is
  // set, independent of `activeModal`), so re-triggering here too would just
  // be a redundant duplicate fetch. Only a genuine re-open (leave "approve",
  // come back) re-checks.
  const hasOpenedApproveRef = useRef(false)
  const prevActiveModalRef = useRef(activeModal)
  useEffect(() => {
    const prevActiveModal = prevActiveModalRef.current
    prevActiveModalRef.current = activeModal
    if (activeModal === "approve") {
      if (hasOpenedApproveRef.current && prevActiveModal !== "approve") {
        gen.refetchExisting()
      }
      hasOpenedApproveRef.current = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeModal])

  const generateModal = (
    <>
      <GenerateModal {...gen.generateModalProps} />
      <GenerationLoadingScreen {...gen.loadingScreenProps} />
    </>
  )

  if (activeModal !== "approve") return generateModal

  const isGenerating = gen.cta === "generating"

  // "View Prototype" re-verifies via the hook before navigating (guards
  // against a stale `existing` after a delete); on failure the hook resets its
  // own `existing` state and toasts "Prototype unavailable" — this modal stays
  // open showing the reset "Generate Prototype" option. "Generate Prototype"
  // opens the generate modal in-place (via the controlled open/onOpenChange
  // pair above), switching the navigation modal union from "approve" to
  // "generate" — no navigation on click.
  const handleClaudeClick = async () => {
    if (gen.cta === "generating") return
    if (gen.cta === "view") {
      setViewBusy(true)
      try {
        await gen.handleCtaClick()
      } finally {
        setViewBusy(false)
      }
      return
    }
    gen.handleCtaClick() // "generate" branch — synchronous, opens via onOpenChange
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
          <div style={isGenerating ? { cursor: "not-allowed" } : undefined}>
          <div
            className={`modal-option${viewBusy || isGenerating ? " opacity-50 pointer-events-none" : ""}`}
            onClick={handleClaudeClick}
          >
            <div
              className="modal-option-icon"
              style={isGenerating ? { background: "var(--surface-3)", color: "var(--ink-3)" } : undefined}
            >
              <IconSparkle size={18} />
            </div>
            <div
              className="modal-option-name"
              style={isGenerating ? { color: "var(--ink-3)" } : undefined}
            >
              {isGenerating ? "Generating Prototype" : gen.existing ? "View Prototype" : "Generate Prototype"}
            </div>
            <div
              className="modal-option-desc"
              style={isGenerating ? { color: "var(--ink-3)" } : undefined}
            >
              {gen.existing
                ? "Open the interactive prototype already generated from this PRD."
                : "Full context package → Claude Code scopes, implements, opens a PR against main."}
            </div>
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
