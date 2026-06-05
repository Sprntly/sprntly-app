"use client"

/**
 * UX-EXPLORE (throwaway — REVERT, CHANGE 3): PRD-screen prototype preview card.
 *
 * Shown in the PRD's Design section WHEN the PRD already has a ready prototype
 * (resolved via the read-only `designAgentApi.getByPrd` — see api.ts; degrades to
 * nothing when no read-only endpoint/record exists, never faking existence and
 * NEVER kicking a generation). Modelled on a brief-insight card: a small
 * thumbnail of the prototype + a title + a sub-line + an open affordance.
 *
 * THUMBNAIL: the prototype record carries no screenshot, so the thumbnail is a
 * scaled-down, NON-interactive `<iframe src={bundle_url}>` — `pointer-events:none`
 * + `transform: scale(...)` to a small box (a static screenshot would be ideal but
 * the backend doesn't store one — flagged in the RETURN). When `bundle_url` is
 * null (older/odd rows) the thumbnail falls back to a neutral placeholder.
 *
 * Clicking the card calls `onOpen()` — the launcher reveals the full-screen canvas
 * for that prototype (skipping the loading sequence, CHANGE 4).
 *
 * Pure leaf (no I/O) → the SSR-renderable view + a tiny container is unnecessary;
 * it takes the resolved record as a prop, so it renders under node-env vitest.
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
