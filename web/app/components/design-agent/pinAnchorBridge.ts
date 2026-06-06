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
