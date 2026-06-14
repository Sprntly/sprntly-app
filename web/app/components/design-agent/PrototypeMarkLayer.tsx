"use client"

/**
 * C1 Slice B — reusable mark-and-comment view, extracted from
 * PostGenerationResult's `.da-stage` overlay + `.da-pin-layer` + the `.da-right`
 * pin-comment rows.
 *
 * Three pure, SSR-renderable pieces (the file's existing view-component idiom:
 * dependency-injected props, no I/O, no lifted state) that the
 * PostGenerationResultView places into their respective canvas regions:
 *
 *   <MarkOverlay>  → the transparent stage overlay (`.da-mark-overlay`). Sits
 *                    ABOVE the same-origin prototype iframe; click-inert except
 *                    in mark mode, where it hit-tests the iframe and reports the
 *                    drop point + resolved anchor via `onStageClick`.
 *   <PinLayer>     → the numbered teardrop pins (`.da-pin-layer`), positioned
 *                    from `computedPinPositions` (anchor-tracked) with a static
 *                    `xPct`/`yPct` fallback.
 *   <PrototypeMarkLayer> → the `.da-right` pin-comment rows: the draft composer
 *                    for an unsaved pin and the saved row (author + avatar +
 *                    relative time + Apply / Ignore + the consolidated resolve
 *                    control). The resolve control reuses CommentsPanel's shared
 *                    `.comment-resolve-btn` markup verbatim (Part 2 consolidation
 *                    — the old inline `comment-resolve-indicator` SVGs are gone).
 *
 * Pin-anchor threading is preserved end to end: the overlay click captures
 * `xPctInEl`/`yPctInEl` + the resolved anchor, the container's `handleStageClick`
 * stores them on the pin, and `handlePinSubmit` sends `anchor_id` (`pin-${n}`),
 * `pin_x_pct`, `pin_y_pct`, and `resolved_anchor_id` on create. This view only
 * surfaces the draft + saved rows + the submit callback; it never drops a pin
 * field through the prop boundary.
 *
 * Per BUILD.md §6 this adds NO CSS to the hot `globals.css`; it reuses the
 * component-scoped class names already defined in `design-agent.css`
 * (`da-mark-overlay`, `da-pin-layer`, `pc-pin`, `proto-comment*`, and the shared
 * `comment-resolve-btn`).
 */

// `PinComment` is a type-only import (erased at compile time → no runtime cycle
// with PostGenerationResult, which imports this view). The identity helpers come
// straight from their source module (CommentsPanel) — PostGenerationResult only
// re-exports them — so this view does not depend on PostGenerationResult at runtime.
import type { PinComment } from "./PostGenerationResult"
import { CommentAvatar, shortRelativeTime } from "./CommentsPanel"
import {
  getElementAtIframePoint,
  getElementAnchor,
  setElementHighlight,
  clearElementHighlight,
} from "./pinAnchorBridge"
import { IconCheck, IconPin } from "../shared/app-icons"

/**
 * the transparent mark overlay over the canvas stage. Click-inert normally
 * (pointer-events:none via `.da-mark-overlay`), click-active only in mark mode
 * (`.da-mark-overlay.active`). On click it hit-tests the same-origin prototype
 * iframe, computes the stage-relative x/y percentages, resolves the clicked
 * element's anchor, and reports all of it via `onStageClick`.
 */
export function MarkOverlay({
  markMode,
  onStageClick,
}: {
  markMode: boolean
  onStageClick?: (
    xPct: number,
    yPct: number,
    viewportX: number,
    viewportY: number,
    anchor: { type: 'anchor-id' | 'xpath'; value: string } | null,
  ) => void
}) {
  return (
    <div
      className={`da-mark-overlay${markMode ? " active" : ""}`}
      data-testid="da-mark-overlay"
      aria-hidden={markMode ? "false" : "true"}
      onClick={(e) => {
        if (!markMode) return
        const iframe = document.querySelector<HTMLIFrameElement>('.da-prototype-iframe')
        const ir = iframe?.getBoundingClientRect()
        if (!ir) return
        if (e.clientX < ir.left || e.clientX > ir.left + ir.width ||
            e.clientY < ir.top || e.clientY > ir.top + ir.height) return
        const el = getElementAtIframePoint(iframe, e.clientX, e.clientY)
        const anchor = el ? getElementAnchor(el) : null
        const xPct = Math.max(0, Math.min(100, ((e.clientX - ir.left) / ir.width) * 100))
        const yPct = Math.max(0, Math.min(100, ((e.clientY - ir.top) / ir.height) * 100))
        clearElementHighlight()
        onStageClick?.(xPct, yPct, e.clientX, e.clientY, anchor)
      }}
      onMouseMove={(e) => {
        if (!markMode) return
        const iframe = document.querySelector<HTMLIFrameElement>('.da-prototype-iframe')
        const el = getElementAtIframePoint(iframe, e.clientX, e.clientY)
        setElementHighlight(el)
      }}
      onMouseLeave={() => clearElementHighlight()}
    />
  )
}

/**
 * the numbered teardrop pins, positioned absolutely over the canvas. `placed`
 * triggers the `pinDrop` animation. An anchor-tracked `computedPinPositions`
 * entry (keyed by pin.n) overrides the static `xPct`/`yPct` so pins follow the
 * DOM element they were placed on across scroll + resize.
 */
export function PinLayer({
  pins,
  computedPinPositions = {},
}: {
  pins: PinComment[]
  computedPinPositions?: Record<number, { xPct: number; yPct: number }>
}) {
  if (pins.length === 0) return null
  return (
    <div className="da-pin-layer" data-testid="da-pin-layer" aria-hidden="true">
      {pins.map((pin) => {
        const pos = computedPinPositions[pin.n] ?? { xPct: pin.xPct, yPct: pin.yPct }
        return (
          <span
            key={pin.n}
            className="pc-pin placed"
            data-testid={`da-pin-${pin.n}`}
            style={{ left: `${pos.xPct}%`, top: `${pos.yPct}%` }}
          >
            <span className="pc-pin-num">{pin.n}</span>
          </span>
        )
      })}
    </div>
  )
}

export type PrototypeMarkLayerProps = {
  /** the dropped pins + their (optimistic) comment drafts/saved bodies. */
  pins: PinComment[]
  /** True on the signed-in editable surface — gates the Apply / Ignore actions.
   *  The public/read-only viewer composes its own chrome and does not mount this. */
  editorMode?: boolean
  /** True when the resolve affordance should be clickable (authed editor mount).
   *  When false the resolve control renders display-only (David's `--static`),
   *  mirroring CommentsPanel's `canResolve` capability. */
  canResolve?: boolean
  onPinDraftChange?: (n: number, value: string) => void
  /** Submit a pin's draft → create the comment. The container's `handlePinSubmit`
   *  sends anchor_id (`pin-${n}`) + pin_x_pct + pin_y_pct + resolved_anchor_id. */
  onSubmitComment?: (n: number) => void
  onPinRemove?: (n: number) => void
  /** Apply a saved pin comment (pre-fill composer / immediate-iterate + resolve). */
  onPinApply?: (n: number) => void
  /** Ignore — resolve a saved pin comment WITHOUT pre-filling the composer. */
  onPinIgnore?: (n: number) => void
  /** Resolve a saved pin comment from the consolidated header control. Wired to
   *  the same resolve-only semantic as Ignore on the editable mount. */
  onPinResolve?: (n: number) => void
}

/** the `.da-right` pin-comment rows. Each dropped pin renders here with its
 *  number + either a draft composer (unsaved) or the saved row (author + avatar +
 *  relative time + Apply / Ignore + the consolidated resolve control). Pure →
 *  SSR-renderable in node-env vitest. */
export function PrototypeMarkLayer({
  pins,
  editorMode = true,
  canResolve = true,
  onPinDraftChange,
  onSubmitComment,
  onPinRemove,
  onPinApply,
  onPinIgnore,
  onPinResolve,
}: PrototypeMarkLayerProps) {
  if (pins.length === 0) return null
  return (
    <ul className="proto-comment-list" data-testid="da-pin-comments">
      {pins.map((pin) => (
        <li
          key={pin.n}
          className={`proto-comment${pin.saved ? " saved" : ""}${pin.resolved ? " resolved" : ""}`}
          data-testid={`da-pin-comment-${pin.n}`}
          data-status={pin.resolved ? "resolved" : pin.saved ? "open" : "draft"}
        >
          <div className="proto-comment-main">
            {pin.saved ? (
              <>
                {/* author + avatar + relative time on the saved pin comment, plus
                    the consolidated resolve control (David's `pc-resolve`),
                    reusing CommentsPanel's shared `.comment-resolve-btn` markup. */}
                <div className="proto-comment-au-row">
                  <div className="comment-step-chip">
                    <IconPin size={11} />
                    Step {pin.n}
                  </div>
                  <CommentAvatar author={pin.author ?? "demo"} />
                  <span className="proto-comment-au">{pin.author ?? "demo"}</span>
                  <time
                    className="proto-comment-time"
                    dateTime={pin.createdAt ?? undefined}
                    title={pin.createdAt ?? undefined}
                  >
                    {shortRelativeTime(pin.createdAt)}
                  </time>
                  {canResolve ? (
                    <button
                      type="button"
                      className={`comment-resolve-btn${pin.resolved ? " resolved" : ""}`}
                      data-testid={`da-pin-resolve-${pin.n}`}
                      title={pin.resolved ? "Resolved" : "Resolve"}
                      aria-label={pin.resolved ? "Resolved" : "Resolve comment"}
                      aria-pressed={pin.resolved}
                      onClick={() => onPinResolve?.(pin.n)}
                    >
                      <IconCheck size={13} />
                    </button>
                  ) : (
                    /* Read-only mount: display-only state, no click affordance. */
                    <span
                      className={`comment-resolve-btn comment-resolve-btn--static${pin.resolved ? " resolved" : ""}`}
                      title={pin.resolved ? "Resolved" : undefined}
                      aria-hidden="true"
                    >
                      <IconCheck size={13} />
                    </span>
                  )}
                </div>
                <p className="proto-comment-body">{pin.body}</p>
                {/* Apply / Ignore on a saved, unresolved pin comment. Apply
                    pre-fills the composer with the pin context + marks resolved;
                    Ignore marks resolved only. Editable mount only. */}
                {editorMode && !pin.resolved && (
                  <div className="proto-comment-actions">
                    <button
                      type="button"
                      className="btn btn-accent"
                      data-testid={`da-pin-apply-${pin.n}`}
                      onClick={() => onPinApply?.(pin.n)}
                    >
                      Apply
                    </button>
                    <button
                      type="button"
                      className="btn"
                      data-testid={`da-pin-ignore-${pin.n}`}
                      onClick={() => onPinIgnore?.(pin.n)}
                    >
                      Ignore
                    </button>
                  </div>
                )}
                {pin.resolved && (
                  <p className="proto-comment-resolved-note">Resolved</p>
                )}
              </>
            ) : (
              <form
                className="proto-comment-form"
                onSubmit={(e) => {
                  e.preventDefault()
                  onSubmitComment?.(pin.n)
                }}
              >
                <textarea
                  className="proto-comment-input"
                  data-testid={`da-pin-input-${pin.n}`}
                  value={pin.draft}
                  placeholder="Add a comment, or click a pin on the canvas…"
                  autoFocus
                  onChange={(e) => onPinDraftChange?.(pin.n, e.target.value)}
                />
                <span className="comment-composer-helper">Click anywhere on the canvas to pin a comment</span>
                <div className="proto-comment-actions">
                  <button
                    type="submit"
                    className="comment-composer-send-btn"
                    data-testid={`da-pin-submit-${pin.n}`}
                    disabled={pin.busy || !pin.draft.trim()}
                    aria-label="Send comment"
                  >
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                      <path d="M2 8l10-6-3 6 3 6-10-6z" fill="currentColor"/>
                    </svg>
                  </button>
                  <button
                    type="button"
                    className="btn"
                    data-testid={`da-pin-cancel-${pin.n}`}
                    onClick={() => onPinRemove?.(pin.n)}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
            {pin.error && (
              <p className="proto-comment-error error">{pin.error}</p>
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}
