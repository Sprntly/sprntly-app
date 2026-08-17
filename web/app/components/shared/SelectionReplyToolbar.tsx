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
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { normalizeQuote } from "../../lib/chatQuote"
import styles from "./SelectionReplyToolbar.module.css"

/** Where a quotable selection is allowed to live. The agent's reply body is
 *  the one region worth quoting; `AskReplyBody`, the streamed partial and the
 *  history-wrapped ladder all render inside it on every surface. */
const DEFAULT_BODY_SELECTOR = ".bc-agent-body"

type Anchor = { top: number; left: number; text: string }

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

    const text = normalizeQuote(sel.toString())
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
    setAnchor({
      top: rect ? rect.top : 0,
      left: rect ? rect.left + rect.width / 2 : 0,
      text,
    })
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
      style={{ top: anchor.top, left: anchor.left }}
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
