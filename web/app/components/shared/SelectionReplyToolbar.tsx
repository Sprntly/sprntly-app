"use client"

/**
 * The highlight-to-reply toolbar — select any passage of an answer and a small
 * "Reply" button appears over the selection; clicking it drops that passage
 * into the composer as a quote.
 *
 * It is a shared LEAF with no chat knowledge: it is handed a container ref and
 * a callback, and it reports normalized text. Every surface-specific decision
 * (what the quote does next, where the chip renders) belongs to the caller.
 *
 * Two rules keep it from firing where it shouldn't:
 *
 *  * The selection must live inside `containerRef` AND inside an element
 *    matching `bodySelector` (the agent's reply body). Highlighting your own
 *    message, a wait state, a header or an artifact chip offers nothing —
 *    quoting exists to point at something the agent said.
 *  * The button suppresses `mousedown`. Without that, pressing it collapses the
 *    very selection it is about to read, and the handler quotes an empty
 *    string — the classic selection-toolbar bug.
 *
 * Positioning is `position: fixed` off the selection's own client rect, so it
 * needs no layout relationship to the transcript and cannot be clipped by the
 * scroll viewport's overflow. It hides on scroll rather than tracking, because
 * a button chasing text down the page reads as a glitch.
 *
 * `position: fixed` is set INLINE as well as in the stylesheet, deliberately.
 * It is not decoration here — it is the difference between a button over the
 * words and a button in the document flow. Reported as a "Reply" sitting at the
 * bottom-left of the screen, nowhere near the highlight and wearing the
 * browser's default button chrome: with the class missing, the coordinates
 * below are inert (a static box ignores `top`/`left`) and the toolbar lands
 * wherever the flex column happens to put it — just above the composer, at the
 * column's left edge. The look degrades; the placement must not.
 *
 * Coordinates are CLAMPED to the viewport. Unclamped, a selection on the first
 * visible line anchors the button at a negative `top` (off the top of the
 * screen) and one near an edge pushes half of it out of frame — the same
 * failure `DocumentTab`'s own quote CTA already guards against.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { normalizeQuote } from "../../lib/chatQuote"
import styles from "./SelectionReplyToolbar.module.css"

/** Where a quotable selection is allowed to live. The agent's reply body is
 *  the one region worth quoting; `AskReplyBody`, the streamed partial and the
 *  history-wrapped ladder all render inside it on every surface. */
const DEFAULT_BODY_SELECTOR = ".bc-agent-body"

/** Elements separated by a BLANK line — one paragraph from the next. */
const PARA_TAGS = new Set([
  "ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "DIV", "DL", "FIELDSET",
  "FIGCAPTION", "FIGURE", "FOOTER", "FORM", "H1", "H2", "H3", "H4", "H5", "H6",
  "HEADER", "HR", "MAIN", "NAV", "OL", "P", "PRE", "SECTION", "TABLE", "UL",
])

/** Elements separated by a SINGLE newline — the rows of one block. Keeping
 *  these distinct from `PARA_TAGS` is what makes a quoted list read as a tight
 *  list rather than as one blank-line-separated paragraph per bullet. */
const LINE_TAGS = new Set(["LI", "TR", "DT", "DD"])

/** Table cells separate on the SAME line, not onto new ones. */
const CELL_TAGS = new Set(["TD", "TH"])

/** Extend `out` to end in exactly `want` newlines — never more, and never any
 *  at all while it is still empty (no leading blank lines). Adjacent blocks
 *  each ask for their own separator, so without this every `</li><li>` pair
 *  would contribute two. */
function padNewlines(out: string, want: number): string {
  if (!out) return out
  let have = 0
  for (let i = out.length - 1; i >= 0 && out[i] === "\n"; i--) have++
  return have >= want ? out : out + "\n".repeat(want - have)
}

/**
 * A selection's text WITH its line structure intact.
 *
 * `Selection.toString()` is not good enough here and the difference is visible
 * in the product: an answer's bulleted list, or a run of one-per-line
 * assignments, comes back as a single run-on paragraph, and the quote then
 * reads as a wall of text that no longer resembles the passage the reader
 * pointed at. So the selected DOM is cloned and walked instead, emitting a
 * newline at every block boundary and at every `<br>`.
 *
 * `cloneContents()` on a partial selection yields partial elements (half a
 * list, the tail of a paragraph), which is exactly what should be quoted —
 * the walk makes no assumption that it is looking at whole nodes.
 */
export function rangeToText(range: Range): string {
  let out = ""
  const walk = (node: Node) => {
    if (node.nodeType === 3 /* TEXT_NODE */) {
      out += node.nodeValue ?? ""
      return
    }
    if (node.nodeType !== 1 /* ELEMENT_NODE */) return
    const tag = (node as Element).tagName.toUpperCase()
    if (tag === "BR") {
      out += "\n"
      return
    }
    const want = PARA_TAGS.has(tag) ? 2 : LINE_TAGS.has(tag) ? 1 : 0
    if (want) out = padNewlines(out, want)
    node.childNodes.forEach(walk)
    if (want) out = padNewlines(out, want)
    else if (CELL_TAGS.has(tag)) out += "  "
  }
  try {
    range.cloneContents().childNodes.forEach(walk)
  } catch {
    // A detached or cross-document range: fall back rather than lose the quote.
    return range.toString()
  }
  return out
}

type Anchor = { top: number; left: number; text: string }

/** Half the button's width, and its height plus the gap above the selection.
 *  Approximate on purpose: the clamp only has to keep the pill on screen, and
 *  measuring it would mean rendering it somewhere first. */
const HALF_W = 52
const LIFT = 40

/**
 * The selection's anchor, kept inside the viewport.
 *
 * A passage on the first visible line has `rect.top` near 0, and the button is
 * drawn a row ABOVE that — so unclamped it renders off the top of the screen
 * and the reader sees nothing at all after highlighting. The same happens
 * horizontally at either edge. When there is no room above, the button flips
 * BELOW the selection rather than covering it.
 */
function clampToViewport(rect: DOMRect | null, text: string): Anchor {
  if (!rect) return { top: LIFT, left: HALF_W + 8, text }
  const vw = typeof window !== "undefined" ? window.innerWidth : 0
  const vh = typeof window !== "undefined" ? window.innerHeight : 0
  const centre = rect.left + rect.width / 2
  return {
    // Below the passage when the row above it is off-screen — the toolbar's
    // own transform lifts it, so this is the anchor it lifts from.
    top: rect.top < LIFT ? Math.min(rect.bottom + LIFT, vh || rect.bottom + LIFT) : rect.top,
    left: vw ? Math.min(Math.max(centre, HALF_W + 8), vw - HALF_W - 8) : centre,
    text,
  }
}

export function SelectionReplyToolbar({
  containerRef,
  onReply,
  bodySelector = DEFAULT_BODY_SELECTOR,
  label = "Reply",
}: {
  /** The transcript region selections are read from. A null/unset ref simply
   *  never matches, so the toolbar stays dormant rather than throwing. */
  containerRef?: React.RefObject<HTMLElement | null> | null
  onReply: (text: string) => void
  bodySelector?: string
  label?: string
}) {
  const [anchor, setAnchor] = useState<Anchor | null>(null)
  const toolbarRef = useRef<HTMLDivElement>(null)

  const clear = useCallback(() => setAnchor(null), [])

  const readSelection = useCallback(() => {
    const container = containerRef?.current
    if (!container) return clear()
    const sel = typeof window !== "undefined" ? window.getSelection() : null
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return clear()

    const range = sel.getRangeAt(0)
    // A text node has no `closest`; climb to its element first.
    const node = range.commonAncestorContainer
    const el = (node.nodeType === 1 ? node : node.parentNode) as Element | null
    if (!el || !container.contains(el)) return clear()
    if (bodySelector && !el.closest(bodySelector)) return clear()

    // `rangeToText`, not `sel.toString()` — see its note: the difference is a
    // quoted list arriving as a list rather than as one run-on line.
    const text = normalizeQuote(rangeToText(range))
    if (!text) return clear()

    // jsdom (and any engine mid-layout) can hand back nothing here; a toolbar
    // pinned at 0,0 is worse than one that just doesn't appear, but the TEXT is
    // what the feature is about — so fall back to the container's own rect
    // rather than dropping the selection on the floor.
    let rect: DOMRect | null = null
    try {
      rect = range.getBoundingClientRect?.() ?? null
    } catch {
      rect = null
    }
    if (!rect || (rect.width === 0 && rect.height === 0 && rect.top === 0)) {
      try {
        rect = container.getBoundingClientRect?.() ?? null
      } catch {
        rect = null
      }
    }
    setAnchor(clampToViewport(rect, text))
  }, [containerRef, bodySelector, clear])

  // `mouseup` settles a drag selection; `keyup` covers Shift+Arrow and
  // Ctrl/Cmd+A. Both are read on the document because a drag routinely ends
  // outside the element it started in.
  useEffect(() => {
    const onMouseUp = (e: MouseEvent) => {
      // A click on the toolbar itself is the ACTION, not a new selection.
      if (toolbarRef.current?.contains(e.target as Node)) return
      readSelection()
    }
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === "Escape") return clear()
      readSelection()
    }
    document.addEventListener("mouseup", onMouseUp)
    document.addEventListener("keyup", onKeyUp)
    return () => {
      document.removeEventListener("mouseup", onMouseUp)
      document.removeEventListener("keyup", onKeyUp)
    }
  }, [readSelection, clear])

  // Anything that invalidates the anchor's coordinates hides it: a new
  // mousedown (starting a fresh selection, or clicking away), and any scroll
  // anywhere — captured, because the transcript scrolls in its own viewport,
  // not on the window.
  useEffect(() => {
    if (!anchor) return
    const onMouseDown = (e: MouseEvent) => {
      if (toolbarRef.current?.contains(e.target as Node)) return
      clear()
    }
    document.addEventListener("mousedown", onMouseDown)
    window.addEventListener("scroll", clear, true)
    window.addEventListener("resize", clear)
    return () => {
      document.removeEventListener("mousedown", onMouseDown)
      window.removeEventListener("scroll", clear, true)
      window.removeEventListener("resize", clear)
    }
  }, [anchor, clear])

  if (!anchor) return null

  return (
    <div
      ref={toolbarRef}
      className={styles.toolbar}
      // `position` inline beside the coordinates it governs — see the header
      // note. The stylesheet declares it too; this is what makes the button
      // land on the highlight even if the class never arrives.
      style={{ position: "fixed", top: anchor.top, left: anchor.left }}
      data-testid="selection-reply-toolbar"
      role="toolbar"
      aria-label="Selected text"
    >
      <button
        type="button"
        className={styles.button}
        data-testid="selection-reply-button"
        // Keeps the selection alive long enough for the click handler to have
        // already read it (see the header note).
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => {
          onReply(anchor.text)
          // The quote now lives in the composer; leaving the passage
          // highlighted (and the button hovering over it) reads as though
          // nothing happened.
          try {
            window.getSelection()?.removeAllRanges()
          } catch {
            /* older engines: the selection simply stays; harmless */
          }
          clear()
        }}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <polyline points="9 17 4 12 9 7" />
          <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
        </svg>
        {label}
      </button>
    </div>
  )
}
