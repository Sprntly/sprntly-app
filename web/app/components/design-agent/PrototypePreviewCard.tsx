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

import type { PrototypeRecord } from "../../lib/api"
import { IconArrowRight } from "../shared/app-icons"

export type PrototypePreviewCardProps = {
  prototype: PrototypeRecord
  /** PRD title used for the card label when the prototype carries no name. */
  prdTitle?: string | null
  /** Open the full-screen canvas for this prototype (skips the loading screen). */
  onOpen: () => void
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
}: PrototypePreviewCardProps) {
  const title = `${prdTitle?.trim() || "Prototype"} · prototype`
  const sub = `${previewVersionLabel(prototype)} · click to open the design`
  return (
    <button
      type="button"
      className="da-preview-card"
      data-testid="da-prototype-preview-card"
      onClick={() => onOpen()}
      aria-label={`Open the design for ${prdTitle?.trim() || "this PRD"}`}
    >
      <div className="da-preview-thumb" aria-hidden="true">
        {prototype.bundle_url ? (
          // Scaled, click-inert live preview of the bundle. `tabIndex=-1` +
          // `pointer-events:none` (CSS) keep it non-interactive; `sandbox` with no
          // tokens keeps it inert/safe inside the card.
          <iframe
            className="da-preview-iframe"
            src={prototype.bundle_url}
            title=""
            tabIndex={-1}
            scrolling="no"
            sandbox=""
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
  )
}
