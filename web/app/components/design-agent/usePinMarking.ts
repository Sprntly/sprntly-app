"use client"

/**
 * C2b — shared mark-and-comment pin engine.
 *
 * Extracted VERBATIM from PostGenerationResult's inlined pin block so BOTH the
 * signed-in editor (PostGenerationResult) and the public viewer
 * (PublicTokenViewer) drive ONE implementation. The only per-surface difference
 * is injected: how a pin's comment is created (`onCreate`) and the surface side
 * effects (open the comments sidebar on enter-mark / pin-drop; the signed-in
 * Apply runner / pre-fill seam).
 *
 *   • signed-in: onCreate = withAuthRetry(() => createComment(prototype.id, …))
 *   • public:    onCreate = createCommentByToken(token, …)
 *
 * Apply / Ignore (handlePinApply) is signed-in only: the public surface mounts
 * PrototypeMarkLayer with editorMode=false, so the Apply / Ignore controls are
 * hidden and onPinIterate / onPinApply are never supplied there.
 *
 * What this hook deliberately does NOT own (NOT pin concerns — they stay in
 * PostGenerationResult): leftPanelRef + the iterate-activity-scroll effect, the
 * fullscreen-Escape effect, and the reseed/baseline effect.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import type { PinComment } from "./PostGenerationResult"
import type { CommentRecord } from "../../lib/api"
import {
  getAnchorPosition,
  getClickOffsetInElement,
  getAnchorPositionWithOffset,
  findByAnchor,
  getElementDescription,
  serializeAnchor,
  clearElementHighlight,
} from "./pinAnchorBridge"

/** The create payload handlePinSubmit builds for a pin's comment. Identical on
 *  both surfaces — only the transport (`onCreate`) differs. */
export type PinCreatePayload = {
  anchor_id: string
  body: string
  pin_x_pct: number
  pin_y_pct: number
  resolved_anchor_id: string | null
}

export type UsePinMarkingParams = {
  /** Surface-specific comment create. Signed-in wraps createComment(prototype.id)
   *  in withAuthRetry; public calls createCommentByToken(token). Returns the
   *  created CommentRecord (or null) so the saved row can mirror author + time. */
  onCreate: (payload: PinCreatePayload) => Promise<CommentRecord | null>
  /** Called when mark mode is ENTERED (signed-in: open the comments sidebar). */
  onEnterMarkMode?: () => void
  /** Called after a pin is dropped (signed-in: open the comments sidebar). */
  onPinDropped?: () => void
  /** Signed-in only — run the iterate immediately with the pin instruction. When
   *  supplied it takes precedence over onPinApply. Public passes neither. */
  onPinIterate?: (instruction: string, x: null) => void
  /** Signed-in only — pre-fill the composer via a synthetic CommentRecord (the
   *  applyTarget seam) when no iterate runner is wired. Public passes neither. */
  onPinApply?: (comment: CommentRecord) => void
  /** Public only — when true a pin comment must NOT post yet because the viewer
   *  has not supplied a name (it would otherwise be attributed "Anonymous"). The
   *  submit aborts and `onRequireName` surfaces the existing name-capture form.
   *  The signed-in surface passes neither, so its submit is unchanged. */
  requireName?: boolean
  /** Public only — called when a submit is blocked for a missing name, to force
   *  the comments/name surface open so the viewer can enter it. */
  onRequireName?: () => void
}

export type UsePinMarkingReturn = {
  markMode: boolean
  setMarkMode: React.Dispatch<React.SetStateAction<boolean>>
  toggleMark: () => void
  pins: PinComment[]
  computedPinPositions: Record<number, { xPct: number; yPct: number }>
  handleStageClick: (
    xPct: number,
    yPct: number,
    viewportX: number,
    viewportY: number,
    anchor: { type: 'anchor-id' | 'xpath'; value: string } | null,
  ) => void
  handlePinDraftChange: (n: number, value: string) => void
  handlePinRemove: (n: number) => void
  handlePinSubmit: (n: number) => Promise<void>
  handlePinApply: (n: number) => void
  handlePinIgnore: (n: number) => void
}

export function usePinMarking({
  onCreate,
  onEnterMarkMode,
  onPinDropped,
  onPinIterate,
  onPinApply,
  requireName = false,
  onRequireName,
}: UsePinMarkingParams): UsePinMarkingReturn {
  // mark-and-comment pin flow state.
  // `markMode` toggles the crosshair overlay; `pins` holds the dropped pins +
  // their (optimistic) comment drafts. Entering mark mode force-opens the right
  // comments sidebar (David's behaviour) so the new comment row is visible.
  const [markMode, setMarkMode] = useState<boolean>(false)
  const [pins, setPins] = useState<PinComment[]>([])
  const pinCounter = useRef<number>(0)
  const [computedPinPositions, setComputedPinPositions] = useState<Record<number, { xPct: number; yPct: number }>>({})

  const recomputePinPositions = useCallback(() => {
    const iframe = document.querySelector<HTMLIFrameElement>(".da-prototype-iframe")
    const updates: Record<number, { xPct: number; yPct: number }> = {}
    for (const pin of pins) {
      if (pin.anchor) {
        const pos =
          pin.xPctInEl != null && pin.yPctInEl != null
            ? getAnchorPositionWithOffset(iframe, pin.anchor, pin.xPctInEl, pin.yPctInEl)
            : getAnchorPosition(iframe, pin.anchor)
        if (pos) updates[pin.n] = pos
      }
    }
    setComputedPinPositions(updates)
  }, [pins])

  useEffect(() => {
    recomputePinPositions()
    const iframe = document.querySelector<HTMLIFrameElement>(".da-prototype-iframe")
    const win = iframe?.contentWindow
    win?.addEventListener("scroll", recomputePinPositions, { passive: true })
    window.addEventListener("resize", recomputePinPositions, { passive: true })
    return () => {
      win?.removeEventListener("scroll", recomputePinPositions)
      window.removeEventListener("resize", recomputePinPositions)
    }
  }, [recomputePinPositions])

  useEffect(() => {
    const iframe = document.querySelector<HTMLIFrameElement>(".da-prototype-iframe")
    if (!iframe) return
    iframe.addEventListener("load", recomputePinPositions)
    return () => iframe.removeEventListener("load", recomputePinPositions)
  }, [recomputePinPositions])

  // Clear any active element highlight whenever mark mode is exited.
  useEffect(() => {
    if (!markMode) clearElementHighlight()
  }, [markMode])

  function toggleMark() {
    setMarkMode((on) => {
      const next = !on
      if (next) onEnterMarkMode?.() // mark mode reveals the comments sidebar
      return next
    })
  }

  // Drop a numbered pin at the clicked stage location + open its comment composer.
  function handleStageClick(xPct: number, yPct: number, viewportX: number, viewportY: number, anchor: { type: 'anchor-id' | 'xpath'; value: string } | null) {
    const iframe = document.querySelector<HTMLIFrameElement>('.da-prototype-iframe')
    let xPctInEl: number | null = null
    let yPctInEl: number | null = null
    let finalXPct = xPct
    let finalYPct = yPct
    let elementFriendly: string | null = null
    let elementTechnical: string | null = null
    if (anchor && iframe) {
      const offset = getClickOffsetInElement(iframe, viewportX, viewportY, anchor)
      if (offset) {
        xPctInEl = offset.xPctInEl
        yPctInEl = offset.yPctInEl
        const pos = getAnchorPositionWithOffset(iframe, anchor, xPctInEl, yPctInEl)
        if (pos) { finalXPct = pos.xPct; finalYPct = pos.yPct }
      }
      const anchorEl = findByAnchor(iframe, anchor)
      const desc = getElementDescription(anchorEl)
      elementFriendly = desc?.friendly ?? null
      elementTechnical = desc?.technical ?? null
    }
    pinCounter.current += 1
    const n = pinCounter.current
    setPins((prev) => [
      ...prev,
      { n, xPct: finalXPct, yPct: finalYPct, xPctInEl, yPctInEl, anchor, elementFriendly, elementTechnical, draft: "", body: "", saved: false, busy: false, error: null },
    ])
    onPinDropped?.()
    setMarkMode(false)
    clearElementHighlight()
  }

  function handlePinDraftChange(n: number, value: string) {
    setPins((prev) => prev.map((p) => (p.n === n ? { ...p, draft: value } : p)))
  }

  function handlePinRemove(n: number) {
    setPins((prev) => prev.filter((p) => p.n !== n))
  }

  // Submit a pin's comment to the create endpoint. The anchor_id carries a
  // synthetic pin marker (`pin-<n>`) — the iframe click cannot resolve a real
  // data-anchor-id across the sandbox boundary. The pin's on-canvas position IS
  // persisted via `pin_x_pct`/`pin_y_pct` alongside the comment body. The row
  // stays optimistic until the create resolves.
  async function handlePinSubmit(n: number) {
    const pin = pins.find((p) => p.n === n)
    if (!pin || !pin.draft.trim()) return
    // Public surface: never post an unnamed pin comment (it would be attributed
    // "Anonymous"). Abort and surface the name-capture form; the draft is kept so
    // the viewer can submit again once a name is set. No-op on the signed-in
    // surface (requireName defaults false there).
    if (requireName) {
      onRequireName?.()
      return
    }
    setPins((prev) =>
      prev.map((p) => (p.n === n ? { ...p, busy: true, error: null } : p)),
    )
    try {
      // the create returns the CommentRecord with the server-attributed author +
      // created_at — mirror them onto the pin so the saved row shows real identity
      // + a relative timestamp. (Signed-in wraps createComment in withAuthRetry so
      // a transient 401 retries once through the refresh instead of silently
      // losing a saved comment; public routes via createCommentByToken.)
      const created = await onCreate({
        anchor_id: `pin-${n}`,
        body: pin.draft.trim(),
        pin_x_pct: pin.xPct,
        pin_y_pct: pin.yPct,
        resolved_anchor_id: serializeAnchor(pin.anchor),
      })
      setPins((prev) =>
        prev.map((p) =>
          p.n === n
            ? {
                ...p,
                body: p.draft.trim(),
                saved: true,
                busy: false,
                error: null,
                author: created?.author ?? "demo",
                createdAt: created?.created_at ?? new Date().toISOString(),
              }
            : p,
        ),
      )
    } catch (e) {
      // Keep the optimistic pin + draft so nothing is lost; surface the error.
      setPins((prev) =>
        prev.map((p) =>
          p.n === n
            ? {
                ...p,
                busy: false,
                error: e instanceof Error ? e.message : "Could not save comment",
              }
            : p,
        ),
      )
    }
  }

  // describe WHERE a pin sits on the
  // canvas so the agent knows where the comment applies. The raw x/y ARE also
  // persisted on the comment; here we compose a human region hint from the
  // LOCAL pin state for the agent instruction.
  function pinRegionHint(xPct: number, yPct: number): string {
    const v = yPct < 33 ? "top" : yPct < 66 ? "middle" : "bottom"
    const h = xPct < 33 ? "left" : xPct < 66 ? "centre" : "right"
    return `${v} ${h}`
  }

  // Apply a saved pin comment —
  // pre-fill the LEFT IterateComposer (via the SAME applyTarget seam CommentsPanel
  // uses; ApproveModal threads `onPinApply → setApplyTarget`) with an instruction
  // that includes the pin number + on-canvas position + the comment text, THEN
  // mark the pin resolved. The synthetic CommentRecord's `body` is the composed
  // instruction; `id` is negative so it never collides with a real comment id and
  // is harmless if forwarded as applied_comment_id (the backend treats unknown ids
  // as "no linked comment").
  function handlePinApply(n: number) {
    const pin = pins.find((p) => p.n === n)
    if (!pin || !pin.saved) return
    const region = pinRegionHint(pin.xPct, pin.yPct)
    const elPart = pin.elementFriendly ? ` on ${pin.elementFriendly}` : ''
    const instruction = pin.elementTechnical
      ? `Re: pin #${pin.n}${elPart} (${region}):\n${pin.body}\n[ref: ${pin.elementTechnical}]`
      : `Re: pin #${pin.n}${elPart} (${region}): ${pin.body}`
    // pin Apply now runs the iterate
    // IMMEDIATELY through the shared runner (pin context + body as the
    // instruction) when `onPinIterate` is supplied — same fixed path as the
    // composer + comment Apply. Falls back to the old applyTarget pre-fill
    // (`onPinApply`) only when no runner is wired. The agent decides
    // applicability; the client fabricates no change. Then mark the pin resolved.
    if (onPinIterate) {
      onPinIterate(instruction, null)
    } else {
      const synthetic: CommentRecord = {
        id: -pin.n,
        anchor_id: `pin-${pin.n}`,
        body: instruction,
        author: pin.author ?? "demo",
        status: "open",
        created_at: pin.createdAt ?? new Date().toISOString(),
        resolved_at: null,
      }
      onPinApply?.(synthetic)
    }
    setPins((prev) => prev.map((p) => (p.n === n ? { ...p, resolved: true } : p)))
  }

  // Ignore — mark the pin resolved
  // WITHOUT pre-filling the composer.
  function handlePinIgnore(n: number) {
    setPins((prev) => prev.map((p) => (p.n === n ? { ...p, resolved: true } : p)))
  }

  return {
    markMode,
    setMarkMode,
    toggleMark,
    pins,
    computedPinPositions,
    handleStageClick,
    handlePinDraftChange,
    handlePinRemove,
    handlePinSubmit,
    handlePinApply,
    handlePinIgnore,
  }
}
