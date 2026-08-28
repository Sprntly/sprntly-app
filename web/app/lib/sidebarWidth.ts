"use client"

/**
 * How wide the sidebar is, and who decides.
 *
 * The width lives in ONE CSS custom property, `--sidebar-w`, read by the
 * sidebar itself and by the main column's left padding. So resizing is a
 * matter of writing that property — no layout state threaded through React, and
 * no re-render per pointer move, which is what keeps the drag smooth. The
 * chosen width is written to `localStorage` so it survives a reload, the same
 * way the collapsed/expanded choice already does.
 */

/** Narrow enough to be mostly icons and a truncated chat title, and no
 *  narrower — below this the nav labels start eliding into uselessness. */
export const SIDEBAR_MIN_WIDTH = 200

/** Wide enough for a long chat title, and no wider — past this the sidebar
 *  starts competing with the document it exists to navigate. */
export const SIDEBAR_MAX_WIDTH = 420

/** What it is before anyone drags it: the value the design shipped with. */
export const SIDEBAR_DEFAULT_WIDTH = 220

const STORAGE_KEY = "sprntly_sidebar_w"
const CSS_VAR = "--sidebar-w"

export function clampSidebarWidth(px: number): number {
  if (!Number.isFinite(px)) return SIDEBAR_DEFAULT_WIDTH
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(px)))
}

/** The stored width, clamped — a value saved before these bounds changed, or
 *  hand-edited in devtools, must not be able to render an unusable sidebar. */
export function loadSidebarWidth(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return SIDEBAR_DEFAULT_WIDTH
    return clampSidebarWidth(Number(raw))
  } catch {
    return SIDEBAR_DEFAULT_WIDTH
  }
}

export function saveSidebarWidth(px: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(clampSidebarWidth(px)))
  } catch {
    // Private mode, or storage full. The width still applies for this visit;
    // it just will not be remembered, which is not worth failing a drag over.
  }
}

/** Write the width where the CSS can see it. Set on the root element rather
 *  than on the sidebar node, because the main column's padding reads it too. */
export function applySidebarWidth(px: number): void {
  document.documentElement.style.setProperty(CSS_VAR, `${clampSidebarWidth(px)}px`)
}
