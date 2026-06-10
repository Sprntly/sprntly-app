"use client"

/**
 * PRD-screen prototype preview card. Shown in the PRD's Design section when the
 * PRD already has a ready prototype (resolved read-only via
 * `designAgentApi.getByPrd`; degrades to nothing — never faking existence, never
 * kicking a generation — when no record exists). The prototype row carries no
 * screenshot today, so the thumbnail is a scaled, click-inert
 * `<iframe src={bundle_url}>`; a real screenshot thumbnail is a future
 * enhancement. `bundle_url=null` falls back to a neutral placeholder. Clicking
 * the card opens the full-screen canvas for that prototype (pushing the
 * refresh-stable canvas route), skipping the loading sequence.
 *
 * Pure leaf (no I/O): it takes the resolved record as a prop, so it renders
 * under node-env vitest without a container or context.
 */

import { useState } from "react"
import type { PrototypeRecord } from "../../lib/api"
import { IconArrowRight } from "../shared/app-icons"

export type PrototypePreviewCardProps = {
  prototype: PrototypeRecord
  /** PRD title used for the card label when the prototype carries no name. */
  prdTitle?: string | null
  /** Open the full-screen canvas for this prototype (skips the loading screen). */
  onOpen: () => void
  onDelete?: () => Promise<void>
}

/** Derive a stable version-ish label for the sub-line. The prototype row has no
 *  explicit version field exposed on the wire, so we surface its id as the
 *  human-stable handle ("v{id}") — when a real version lands it slots in here. */
export function previewVersionLabel(prototype: PrototypeRecord): string {
  return `v${prototype.id}`
}

export function PrototypePreviewCard({
  prototype,
  prdTitle,
  onOpen,
  onDelete,
}: PrototypePreviewCardProps) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const title = `${prdTitle?.trim() || "Prototype"} · prototype`
  const sub = `${previewVersionLabel(prototype)} · click to open the design`

  const handleConfirmDelete = async () => {
    setBusy(true)
    await onDelete?.()
    setConfirming(false)
    setBusy(false)
  }

  return (
    <div className="da-preview-card-wrapper" style={{ position: "relative" }}>
      <button
        type="button"
        className="da-preview-card"
        data-testid="da-prototype-preview-card"
        onClick={() => onOpen()}
        aria-label={`Open the design for ${prdTitle?.trim() || "this PRD"}`}
      >
        <div className="da-preview-thumb" aria-hidden="true">
          {prototype.bundle_url ? (
            // Scaled, click-inert live preview of the bundle. The bundle's scripts
            // must be allowed to run for the preview to render — an empty sandbox
            // blocks all JS and leaves the thumbnail blank. `allow-same-origin`
            // lets the bundle load its own assets from the same origin.
            // Inertness is enforced by `pointer-events:none` (CSS), `tabIndex={-1}`,
            // and `scrolling="no"` — not by restricting the sandbox.
            <iframe
              className="da-preview-iframe"
              src={prototype.bundle_url}
              title=""
              tabIndex={-1}
              scrolling="no"
              sandbox="allow-scripts allow-same-origin"
            />
          ) : (
            <div className="da-preview-thumb-empty" />
          )}
        </div>
        <div className="da-preview-meta">
          <div className="da-preview-title">{title}</div>
          <div className="da-preview-sub">{sub}</div>
        </div>
        <span className="da-preview-open" aria-hidden="true">
          <IconArrowRight size={16} />
        </span>
      </button>
      {onDelete && (
        <button
          type="button"
          className="da-preview-delete-btn"
          aria-label="Delete prototype"
          onClick={(e) => {
            e.stopPropagation()
            setConfirming(true)
          }}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M2 3.5h10M5.5 3.5V2.5a.5.5 0 0 1 .5-.5h2a.5.5 0 0 1 .5.5v1M3 3.5l.7 7.2A.5.5 0 0 0 4.2 11h5.6a.5.5 0 0 0 .5-.3L11 3.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>
      )}
      {confirming && (
        <div className="modal-overlay open" role="dialog" aria-modal="true">
          <div className="modal-box">
            <div className="modal-head">
              <div className="modal-title">Delete prototype?</div>
              <div className="modal-subtitle">This cannot be undone.</div>
            </div>
            <div className="modal-foot">
              <button className="modal-cancel" onClick={() => setConfirming(false)} disabled={busy}>Cancel</button>
              <button className="modal-confirm-danger" onClick={handleConfirmDelete} disabled={busy}>Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
