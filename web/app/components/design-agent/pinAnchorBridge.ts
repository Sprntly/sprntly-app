/** Resolve the stable JSX anchor at a viewport point inside the prototype bundle
 *  iframe. The bundle is served same-origin, so contentDocument is reachable.
 *  Returns the deepest enclosing element's data-anchor-id at (viewportX, viewportY)
 *  translated into the iframe's own coordinate space, or null when: the iframe is
 *  cross-origin (contentDocument throws or is null), the point hits no anchored
 *  element, or no ancestor carries data-anchor-id. Never throws — a null result
 *  degrades to position-only persistence (pin_x_pct / pin_y_pct still saved).
 *
 *  Mirrors the captureAnchorId primitive in CommentsPanel.tsx (which uses
 *  closest("[data-anchor-id]") on click targets inside the same document); this
 *  bridge extends the same pattern across the iframe boundary so the mark overlay
 *  — which sits above the iframe in the parent document — can resolve anchors
 *  inside the bundle's contentDocument. */
export function resolveAnchorAtPoint(
  iframe: HTMLIFrameElement | null,
  viewportX: number,
  viewportY: number,
): string | null {
  try {
    const doc = iframe?.contentDocument
    if (!doc) return null
    const rect = iframe!.getBoundingClientRect()
    const innerX = viewportX - rect.left
    const innerY = viewportY - rect.top
    const el = doc.elementFromPoint(innerX, innerY)
    return el?.closest("[data-anchor-id]")?.getAttribute("data-anchor-id") ?? null
  } catch {
    // cross-origin SecurityError, detached document, or any other DOM exception
    return null
  }
}

export function getAnchorPosition(
  iframe: HTMLIFrameElement | null,
  anchorId: string,
): { xPct: number; yPct: number } | null {
  try {
    const doc = iframe?.contentDocument
    if (!doc) return null
    const el = doc.querySelector(`[data-anchor-id="${CSS.escape(anchorId)}"]`)
    if (!el) return null
    const elRect = el.getBoundingClientRect()
    const iRect = iframe.getBoundingClientRect()
    const x = ((elRect.left - iRect.left + elRect.width / 2) / iRect.width) * 100
    const y = ((elRect.top - iRect.top + elRect.height / 2) / iRect.height) * 100
    return { xPct: Math.max(0, Math.min(100, x)), yPct: Math.max(0, Math.min(100, y)) }
  } catch {
    return null
  }
}

let _activeHighlight: HTMLElement | null = null

export function setIframeHighlight(
  iframe: HTMLIFrameElement | null,
  anchorId: string | null,
): void {
  try {
    if (_activeHighlight) {
      _activeHighlight.style.outline = ''
      _activeHighlight.style.outlineOffset = ''
      _activeHighlight.style.borderRadius = ''
      _activeHighlight = null
    }
    if (!anchorId || !iframe?.contentDocument) return
    const el = iframe.contentDocument.querySelector<HTMLElement>(
      `[data-anchor-id="${CSS.escape(anchorId)}"]`
    )
    if (!el) return
    el.style.outline = '2px solid var(--accent, #4a7c6b)'
    el.style.outlineOffset = '3px'
    el.style.borderRadius = '3px'
    _activeHighlight = el
  } catch { /* cross-origin — no-op */ }
}

export function clearIframeHighlight(): void {
  setIframeHighlight(null, null)
}
