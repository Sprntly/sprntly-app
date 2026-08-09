"use client"

import { useEffect, useRef, useState } from "react"

// Mid-stream the doc may open with a ```html fence line whose closing fence
// hasn't arrived yet — stripHtmlCodeFence requires BOTH fences, so shave a
// dangling leading fence line before handing the partial to the iframe.
export function stripLeadingFence(s: string): string {
  return s.replace(/^\s*```[a-zA-Z]*\r?\n?/, "")
}

// How close to the bottom the panel must be for us to keep following the stream.
// Scrolling further up than this reads as "I'm looking at something" and stops
// the auto-follow until the user returns to the bottom.
const FOLLOW_THRESHOLD_PX = 120

// The panel's scrolling viewport is an ancestor (.prd-scroll / .ev-doc / the
// chat panel body), not the iframe — the iframe grows to its full content
// height and never scrolls itself. Walk up to the first scrollable box.
function findScroller(el: HTMLElement | null): HTMLElement | null {
  let node = el?.parentElement ?? null
  while (node) {
    const oy = getComputedStyle(node).overflowY
    if (oy === "auto" || oy === "scroll" || oy === "overlay") return node
    node = node.parentElement
  }
  return null
}

/**
 * Read-only live preview of a generation's HTML while it streams in — shared by
 * the PRD panel and the Evidence tab. The accumulating (possibly mid-tag)
 * document is fed to a sandboxed iframe via incremental document.write — only
 * the NEW suffix is written on each update, so the browser parses progressively
 * and the user's scroll position survives (a srcDoc swap would reload + jump to
 * top every tick). Scripts never execute (sandbox without allow-scripts). A
 * restart (the accumulated doc no longer extends what we wrote — a backend
 * retry re-emitted from zero) reopens the document and rewrites from scratch.
 * Updates are already throttled upstream (runPrdGeneration /
 * runEvidenceGeneration), so writes land at most every ~400ms.
 *
 * As the document grows past the fold the panel follows it, so the newest
 * section stays in view instead of being generated below the bottom edge. The
 * follow is conditional on the reader still being near the bottom (see
 * FOLLOW_THRESHOLD_PX) — scrolling up to re-read an earlier section is never
 * yanked back down, and returning to the bottom resumes the follow.
 */
export function StreamingHtmlPreview({
  html,
  title,
  testId,
}: {
  html: string
  title: string
  testId: string
}) {
  const ref = useRef<HTMLIFrameElement>(null)
  const writtenRef = useRef("")
  const [height, setHeight] = useState(480)
  const scrollerRef = useRef<HTMLElement | null>(null)
  const pinnedRef = useRef(true)

  // Resolve the scrolling ancestor once and track whether the reader is pinned
  // near its bottom. Our own follow-scroll fires this listener too, which just
  // re-confirms the pin (distance ~0), so it stays sticky until the user moves.
  useEffect(() => {
    const scroller = findScroller(ref.current)
    scrollerRef.current = scroller
    if (!scroller) return
    const measure = () => {
      pinnedRef.current =
        scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < FOLLOW_THRESHOLD_PX
    }
    measure()
    scroller.addEventListener("scroll", measure, { passive: true })
    return () => scroller.removeEventListener("scroll", measure)
  }, [])

  // Follow the growth. Keyed on `height` (not `html`) so it runs after React has
  // committed the taller iframe — measuring the scroller any earlier would use
  // the pre-growth scrollHeight and land short of the bottom.
  useEffect(() => {
    const scroller = scrollerRef.current
    if (!scroller || !pinnedRef.current) return
    scroller.scrollTop = scroller.scrollHeight
  }, [height])

  useEffect(() => {
    const cdoc = ref.current?.contentDocument
    if (!cdoc) return
    const written = writtenRef.current
    try {
      if (!written || !html.startsWith(written)) {
        cdoc.open()
        cdoc.write(html)
      } else if (html.length > written.length) {
        cdoc.write(html.slice(written.length))
      }
      writtenRef.current = html
      const h = Math.max(cdoc.body?.scrollHeight ?? 0, cdoc.documentElement?.scrollHeight ?? 0)
      if (h > 0) setHeight(h)
    } catch {
      /* a mid-write parser hiccup only affects the preview; the poll result wins */
    }
  }, [html])

  return (
    <iframe
      ref={ref}
      title={title}
      data-testid={testId}
      sandbox="allow-same-origin"
      style={{
        width: "100%",
        height,
        border: "1px solid var(--line, #E8E6E0)",
        borderRadius: 0,
        display: "block",
        colorScheme: "light",
        background: "#ffffff",
      }}
    />
  )
}
