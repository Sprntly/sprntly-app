"use client"

// An in-app confirmation, replacing window.confirm for actions that need one.
//
// The native dialog was wrong here for reasons beyond looks: it renders
// unstyled Chrome chrome with a "localhost:3000 says" header, it cannot show
// that the action is IN FLIGHT (it returns the instant you click, so a slow
// delete looked like nothing happened), and it blocks the whole page while
// open. This owns the pending state instead — the confirm button reports
// progress and both buttons lock, so a destructive action can never be
// double-fired by an impatient second click.
import { useEffect, useRef } from "react"
import { IconLoader2 } from "@tabler/icons-react"

export function ConfirmDialog({
  open, title, body, confirmLabel, busyLabel, tone = "default", busy = false,
  elevated = false,
  onConfirm, onCancel,
}: {
  open: boolean
  title: string
  /** The consequence, in plain words. Keep it specific — this is the last
   *  thing the user reads before an action they may not be able to undo. */
  body?: React.ReactNode
  confirmLabel: string
  /** Shown on the confirm button while `busy`. Defaults to "Working…". */
  busyLabel?: string
  /** "danger" for destructive actions (red confirm). */
  tone?: "default" | "danger"
  /** The action is in flight: buttons lock and the confirm reports progress. */
  busy?: boolean
  /** Lift this overlay ABOVE the standard modal layer. Set when the confirm is
   *  opened from inside another `modal-overlay` (e.g. the project settings
   *  modal's Members tab): both share the base `modal-overlay` z-index, so a
   *  same-layer confirm rendered earlier in the DOM would paint BEHIND its
   *  parent modal. Default false — a standalone confirm keeps the base layer. */
  elevated?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const confirmRef = useRef<HTMLButtonElement>(null)

  // Focus the confirm button on open so the dialog is keyboard-operable
  // immediately (Enter confirms, Escape cancels) without a mouse.
  useEffect(() => {
    if (open) confirmRef.current?.focus()
  }, [open])

  // Escape cancels — but never mid-flight: the request is already gone, and
  // closing would leave the user believing they had backed out of it.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) { e.stopPropagation(); onCancel() }
    }
    window.addEventListener("keydown", onKey, true)
    return () => window.removeEventListener("keydown", onKey, true)
  }, [open, busy, onCancel])

  if (!open) return null

  return (
    <div
      className="modal-overlay open"
      // `modal-overlay` is z-index 200; a confirm opened from within another
      // modal must sit above it (300) so it is never buried. Standalone
      // confirms leave the class's own z-index untouched.
      style={elevated ? { zIndex: 300 } : undefined}
      // Same rule as Escape: a click-away must not dismiss an in-flight action.
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onCancel() }}
    >
      <div className="modal cfm" role="dialog" aria-modal="true" aria-labelledby="cfm-title">
        {/* modal-head is a flex ROW; the title/sub stack goes in the sanctioned
            modal-head-text column or the two render side by side. */}
        <div className="modal-head">
          <div className="modal-head-text">
            <h2 className="modal-title" id="cfm-title">{title}</h2>
            {body ? <p className="modal-sub">{body}</p> : null}
          </div>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={`btn ${tone === "danger" ? "btn-danger" : "btn-primary"}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? (
              <>
                <IconLoader2 size={14} className="icon-spin" aria-hidden />
                {busyLabel || "Working…"}
              </>
            ) : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
