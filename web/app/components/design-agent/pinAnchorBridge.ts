/** Resolve the specific element at a viewport point inside the prototype iframe.
 *  Returns null for cross-origin iframes or out-of-bounds points. */
export function getElementAtIframePoint(
  iframe: HTMLIFrameElement | null,
  clientX: number,
  clientY: number,
): Element | null {
  try {
    const doc = iframe?.contentDocument
    if (!doc) return null
    const r = iframe!.getBoundingClientRect()
    const ix = clientX - r.left
    const iy = clientY - r.top
    if (ix < 0 || iy < 0 || ix > r.width || iy > r.height) return null
    return doc.elementFromPoint(ix, iy)
  } catch { return null }
}

/** Generate a stable anchor for an element: prefers data-anchor-id, falls back to XPath. */
export function getElementAnchor(
  el: Element,
): { type: 'anchor-id' | 'xpath'; value: string } | null {
  const withId = el.closest('[data-anchor-id]')
  if (withId) return { type: 'anchor-id', value: withId.getAttribute('data-anchor-id')! }
  const xpath = buildXPath(el)
  return xpath ? { type: 'xpath', value: xpath } : null
}

function buildXPath(el: Element): string {
  const parts: string[] = []
  let node: Element | null = el
  // 1 === Node.ELEMENT_NODE — use literal so this is safe in node-env tests.
  while (node && node.nodeType === 1) {
    let idx = 1
    let sib = node.previousElementSibling
    while (sib) { if (sib.tagName === node.tagName) idx++; sib = sib.previousElementSibling }
    parts.unshift(idx > 1 ? `${node.tagName.toLowerCase()}[${idx}]` : node.tagName.toLowerCase())
    node = node.parentElement
  }
  return parts.length ? '/' + parts.join('/') : ''
}

/** Find an element in the iframe by anchor (anchor-id or xpath). */
export function findByAnchor(
  iframe: HTMLIFrameElement | null,
  anchor: { type: 'anchor-id' | 'xpath'; value: string },
): Element | null {
  try {
    const doc = iframe?.contentDocument
    if (!doc) return null
    if (anchor.type === 'anchor-id') {
      return doc.querySelector(`[data-anchor-id="${CSS.escape(anchor.value)}"]`)
    }
    return document.evaluate(anchor.value, doc, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null)
      .singleNodeValue as Element | null
  } catch { return null }
}

/** Get pin position as iframe percentages from an anchor. */
export function getAnchorPosition(
  iframe: HTMLIFrameElement | null,
  anchor: { type: 'anchor-id' | 'xpath'; value: string },
): { xPct: number; yPct: number } | null {
  const el = findByAnchor(iframe, anchor)
  if (!el) return null
  try {
    const ir = iframe!.getBoundingClientRect()
    const er = el.getBoundingClientRect()
    return {
      xPct: Math.max(0, Math.min(100, ((er.left - ir.left + er.width / 2) / ir.width) * 100)),
      yPct: Math.max(0, Math.min(100, ((er.top - ir.top + er.height / 2) / ir.height) * 100)),
    }
  } catch { return null }
}

/** Parse a stored resolved_anchor_id string into a typed anchor object. */
export function parseStoredAnchor(
  raw: string | null | undefined,
): { type: 'anchor-id' | 'xpath'; value: string } | null {
  if (!raw) return null
  if (raw.startsWith('xpath:')) return { type: 'xpath', value: raw.slice(6) }
  return { type: 'anchor-id', value: raw }
}

/** Serialize an anchor to a stored resolved_anchor_id string. */
export function serializeAnchor(
  anchor: { type: 'anchor-id' | 'xpath'; value: string } | null,
): string | null {
  if (!anchor) return null
  return anchor.type === 'xpath' ? `xpath:${anchor.value}` : anchor.value
}

let _highlighted: HTMLElement | null = null

/** Highlight the specific element (not an ancestor) with an outline. */
export function setElementHighlight(el: Element | null): void {
  try {
    if (_highlighted && _highlighted !== el) {
      _highlighted.style.outline = ''
      _highlighted.style.outlineOffset = ''
      _highlighted = null
    }
    if (el && el !== _highlighted) {
      ;(el as HTMLElement).style.outline = '2px solid var(--accent, #4a7c6b)'
      ;(el as HTMLElement).style.outlineOffset = '2px'
      _highlighted = el as HTMLElement
    }
  } catch {}
}

export function clearElementHighlight(): void { setElementHighlight(null) }
