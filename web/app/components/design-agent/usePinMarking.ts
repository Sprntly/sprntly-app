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
  /** Surface-specific PERSISTED resolve (mirrors the `onCreate` injection seam).
   *  Signed-in injects withAuthRetry(() => resolveComment(prototype.id, id)) so a
   *  pin-resolve writes through the SAME authed PATCH the CommentsPanel card uses;
   *  the public surface passes nothing (anon viewers cannot resolve), keeping
   *  pin-resolve local-only there. Called with the pin's captured `commentId`. */
  onResolve?: (commentId: number) => Promise<unknown>
}

export type UsePinMarkingReturn = {
  markMode: boolean
  setMarkMode: React.Dispatch<React.SetStateAction<boolean>>
  toggleMark: () => void
  pins: PinComment[]
  computedPinPositions: Record<number, { xPct: number; yPct: number }>
  /** Pins whose anchored element is currently HIDDEN behind an in-iframe overlay
   *  (a modal opened over it). The pin layer skips rendering these so the pin
   *  never floats on top of a modal that visually covers its element. Recomputed
   *  on scroll / resize / load AND on in-iframe DOM mutations (modal open/close). */
  occludedPins: Set<number>
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
  onResolve,
}: UsePinMarkingParams): UsePinMarkingReturn {
  // mark-and-comment pin flow state.
  // `markMode` toggles the crosshair overlay; `pins` holds the dropped pins +
  // their (optimistic) comment drafts. Entering mark mode force-opens the right
  // comments sidebar (David's behaviour) so the new comment row is visible.
  const [markMode, setMarkMode] = useState<boolean>(false)
  const [pins, setPins] = useState<PinComment[]>([])
  const pinCounter = useRef<number>(0)
  const [computedPinPositions, setComputedPinPositions] = useState<Record<number, { xPct: number; yPct: number }>>({})
  const [occludedPins, setOccludedPins] = useState<Set<number>>(() => new Set())

  const recomputePinPositions = useCallback(() => {
    const iframe = document.querySelector<HTMLIFrameElement>(".da-prototype-iframe")
    const updates: Record<number, { xPct: number; yPct: number }> = {}
    const occluded = new Set<number>()
    for (const pin of pins) {
      if (pin.anchor) {
        const pos =
          pin.xPctInEl != null && pin.yPctInEl != null
            ? getAnchorPositionWithOffset(iframe, pin.anchor, pin.xPctInEl, pin.yPctInEl)
            : getAnchorPosition(iframe, pin.anchor)
        if (pos) {
          updates[pin.n] = pos
          // Occlusion check: is the pin's anchored element actually the topmost
          // thing at the pin's point, or has an in-iframe overlay/modal been drawn
          // over it? Same-origin only (`allow-same-origin` iframe). ALL access is
          // try/catch-guarded — on ANY failure (cross-origin public/token path,
          // detached doc, missing element) we fall through to treating the pin as
          // VISIBLE so a pin never disappears because the check errored.
          try {
            const doc = iframe?.contentDocument
            if (doc && iframe) {
              const ir = iframe.getBoundingClientRect()
              const xInFrame = (pos.xPct / 100) * ir.width
              const yInFrame = (pos.yPct / 100) * ir.height
              const top = doc.elementFromPoint(xInFrame, yInFrame)
              const anchorEl = findByAnchor(iframe, pin.anchor)
              // Only HIDE when we positively resolved a real topmost element that
              // is neither the anchor nor a descendant of it (i.e. something is
              // drawn OVER the anchor). A null topmost (point off-viewport / not
              // resolvable) falls back to SHOWING the pin — never a false hide.
              if (anchorEl && top && !(top === anchorEl || anchorEl.contains(top))) {
                occluded.add(pin.n)
              }
            }
          } catch {
            // same-origin access failed → leave the pin visible (never throw).
          }
        }
      }
    }
    setComputedPinPositions(updates)
    setOccludedPins(occluded)
  }, [pins])

  // Keep the latest `recomputePinPositions` in a ref so the binding lifecycle below
  // does NOT depend on `[pins]`. This effect also RE-RUNS the recompute whenever
  // `pins` changes (recomputePinPositions changes identity on a pins change) — that
  // is how a freshly-dropped pin gets positioned + occlusion-checked, decoupled from
  // the iframe binding.
  const recomputeRef = useRef(recomputePinPositions)
  useEffect(() => {
    recomputeRef.current = recomputePinPositions
    recomputePinPositions()
  }, [recomputePinPositions])

  // ── Occlusion lifecycle: THREE cleanly-separated concerns, wired linearly ──
  //
  //   iframe appears/changes ─(1)─▶ bind the content observer to the LIVE doc
  //   in-iframe DOM mutation ─(2)─▶ schedule a recompute (ALWAYS, unconditionally)
  //   recompute ─────────────(3)─▶ hide/show pins by occlusion (recomputeRef)
  //
  // Concern 1 (iframe lifecycle binding) is the ONLY thing that (re)binds. Concern 2
  // (the content observer's mutation handler) does ONE thing: schedule a recompute —
  // it MUST NOT call the binding-sync (that conflation is what left a real modal
  // mutation unable to hide a pin). Concern 3 (`recomputePinPositions`, above) is
  // preserved verbatim: it owns the occlusion decision + anchor tracking.
  //
  // ALL contentWindow / contentDocument / observer access is try/catch-guarded: on
  // failure (cross-origin public/token path, detached doc) the observer stays null
  // and pins still render + track — occlusion-hiding is a progressive enhancement.
  useEffect(() => {
    let cancelled = false
    let rafRecompute: number | null = null
    let contentObserver: MutationObserver | null = null // Concern 2 — on the live doc
    let bodyWatcher: MutationObserver | null = null      // Concern 1 — mount/remount signal
    let boundIframe: HTMLIFrameElement | null = null
    let boundWin: Window | null = null
    let boundDoc: Document | null = null

    // Concern 3 driver: a stable recompute that always reads the latest callback via
    // the ref, so listeners/observers bound here never go stale as `pins` changes.
    // It does NOT touch the binding — recompute is pure occlusion work.
    const recompute = () => { recomputeRef.current() }

    // ── Concern 2: the content observer's mutation handler → ALWAYS recompute. ──
    // Dead-simple + unconditional: a modal open/close in the iframe fires the content
    // observer, which schedules exactly one pending frame (coalescing bursts), and the
    // flush runs the occlusion recompute. It NEVER routes through the binding-sync.
    const scheduleRecompute = () => {
      if (rafRecompute != null) return
      rafRecompute = requestAnimationFrame(() => {
        rafRecompute = null
        recompute()
      })
    }

    // ── Concern 1: keep exactly ONE content observer bound to the CURRENT doc. ──

    // Tear down the per-DOCUMENT bindings (contentWindow `scroll` + the content
    // MutationObserver) for whatever document is currently bound. The element-level
    // `load` listener + the window `resize` listener are managed separately — they
    // survive a same-element document swap.
    const unbindDoc = () => {
      try { boundWin?.removeEventListener("scroll", recompute) } catch { /* noop */ }
      boundWin = null
      try { contentObserver?.disconnect() } catch { /* noop */ }
      contentObserver = null
      boundDoc = null
    }

    // Bind the per-DOCUMENT listeners to `boundIframe`'s CURRENT document: a fresh
    // `scroll` listener on its contentWindow (anchor tracking) + a fresh content
    // MutationObserver (Concern 2's signal), recording `boundDoc` so `rebindIfChanged`
    // can detect the NEXT document replacement. Then recompute against the new doc.
    const bindDoc = () => {
      try {
        const win = boundIframe?.contentWindow ?? null
        if (win) {
          win.addEventListener("scroll", recompute, { passive: true })
          boundWin = win
        }
        const doc = boundIframe?.contentDocument ?? null
        if (doc) {
          contentObserver = new MutationObserver(scheduleRecompute)
          contentObserver.observe(doc, { subtree: true, childList: true, attributes: true })
          boundDoc = doc
        }
      } catch {
        // same-origin access failed → observer stays null; pins still render + track.
        contentObserver = null
        boundDoc = null
      }
      recompute()
    }

    // Same element, new document (in-place re-navigation to the same URL): rebind the
    // content listeners onto the replaced document.
    const onLoad = () => {
      unbindDoc()
      bindDoc()
    }

    // The ONLY (re)bind decision — idempotent. If the current `.da-prototype-iframe`
    // element or its `contentDocument` differs from what's bound, tear down the old
    // content bindings and bind fresh to the live doc; otherwise no-op.
    //   • iframe absent → wait for the next body-watcher fire.
    //   • NEW element (late mount or React-key remount) → move the `load` listener to
    //     the new element and bind its document.
    //   • SAME element, document replaced → rebind the content listeners.
    //   • already bound to the live element+doc → no-op.
    const rebindIfChanged = () => {
      // The permanent body observer can outlive an unmount that never ran (e.g. a test
      // env torn down without unmounting) and fire once the document global is gone —
      // bail before touching `document`. In production `document` always exists.
      if (cancelled || typeof document === "undefined" || !document.body) return
      const iframe = document.querySelector<HTMLIFrameElement>(".da-prototype-iframe")
      if (!iframe) return
      if (iframe !== boundIframe) {
        unbindDoc()
        try { boundIframe?.removeEventListener("load", onLoad) } catch { /* noop */ }
        boundIframe = iframe
        iframe.addEventListener("load", onLoad)
        bindDoc()
        return
      }
      let liveDoc: Document | null = boundDoc
      try { liveDoc = iframe.contentDocument } catch { liveDoc = boundDoc }
      if (liveDoc !== boundDoc) {
        unbindDoc()
        bindDoc()
      }
    }

    // window `resize` is element-independent → bind once for the effect's lifetime.
    window.addEventListener("resize", recompute, { passive: true })

    // Concern 1's signal: a PERMANENTLY-connected `document.body` MutationObserver.
    // It fires on the FIRST mount (cold loads included — no fixed budget to exhaust)
    // AND on every subsequent remount of the `<iframe>` (a childList mutation in the
    // body subtree), and is NEVER disconnected until cleanup, so a replaced element
    // always re-heals. The callback runs `rebindIfChanged` SYNCHRONOUSLY (no rAF):
    // MutationObserver callbacks are already microtask-batched, and binding on the
    // mount signal (not a frame tick) is what lets a cold-load bind before any frame.
    try {
      bodyWatcher = new MutationObserver(() => { rebindIfChanged() })
      bodyWatcher.observe(document.body, { childList: true, subtree: true })
    } catch {
      // document.body unavailable (should not happen once effects run) → no watcher;
      // pins still render + track, occlusion-hiding is progressive.
      bodyWatcher = null
    }

    // Synchronous initial check — the iframe may already be present (warm load).
    rebindIfChanged()

    return () => {
      cancelled = true
      try { bodyWatcher?.disconnect() } catch { /* noop */ }
      bodyWatcher = null
      if (rafRecompute != null) cancelAnimationFrame(rafRecompute)
      unbindDoc()
      window.removeEventListener("resize", recompute)
      try { boundIframe?.removeEventListener("load", onLoad) } catch { /* noop */ }
      boundIframe = null
    }
  }, [])

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
                // capture the server comment id (null when the create returned
                // no record) so pin-resolve can persist + the saved-pin↔server
                // dedup can reconcile. A null id never throws — it just means
                // this pin stays local-only for the server write.
                commentId: created?.id ?? null,
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
    // applicability; the client fabricates no change. Then mark the pin resolved
    // (optimistically) and PERSIST the resolve through `onResolve`.
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
    return resolvePinOptimistically(pin)
  }

  // Ignore — mark the pin resolved
  // WITHOUT pre-filling the composer, then PERSIST the resolve through `onResolve`.
  function handlePinIgnore(n: number) {
    const pin = pins.find((p) => p.n === n)
    if (!pin) return
    return resolvePinOptimistically(pin)
  }

  // Optimistically flip the pin to resolved, then — when the pin carries a
  // captured server comment id AND a surface-specific `onResolve` is wired —
  // write the resolve through the SAME authed PATCH the CommentsPanel card uses
  // so a pin-resolve actually PERSISTS (survives reload). On a server failure we
  // ROLL BACK the optimistic flip and surface the error on that pin. A pin with
  // no commentId (optimistic-only) or a surface with no `onResolve` (public anon
  // viewer) keeps the local-only resolve as a safe no-op for the server write.
  const warnedLocalOnlyResolveRef = useRef<boolean>(false)
  async function resolvePinOptimistically(pin: PinComment): Promise<void> {
    setPins((prev) =>
      prev.map((p) => (p.n === pin.n ? { ...p, resolved: true, error: null } : p)),
    )
    if (pin.commentId == null || !onResolve) {
      if (!warnedLocalOnlyResolveRef.current) {
        warnedLocalOnlyResolveRef.current = true
        // local-only resolve: no server write attempted (no captured id, or this
        // surface does not allow resolve). Warned once to avoid console noise.
        console.warn(
          "usePinMarking: pin resolved locally only (no server comment id or no onResolve).",
        )
      }
      return
    }
    try {
      await onResolve(pin.commentId)
    } catch (e) {
      // roll back the optimistic resolve + surface the failure on the pin.
      setPins((prev) =>
        prev.map((p) =>
          p.n === pin.n
            ? {
                ...p,
                resolved: false,
                error: e instanceof Error ? e.message : "Could not resolve comment",
              }
            : p,
        ),
      )
    }
  }

  return {
    markMode,
    setMarkMode,
    toggleMark,
    pins,
    computedPinPositions,
    occludedPins,
    handleStageClick,
    handlePinDraftChange,
    handlePinRemove,
    handlePinSubmit,
    handlePinApply,
    handlePinIgnore,
  }
}
