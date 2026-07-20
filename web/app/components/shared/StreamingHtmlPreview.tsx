"use client"

import { useEffect, useRef, useState } from "react"

// Mid-stream the doc may open with a ```html fence line whose closing fence
// hasn't arrived yet — stripHtmlCodeFence requires BOTH fences, so shave a
// dangling leading fence line before handing the partial to the iframe.
export function stripLeadingFence(s: string): string {
  return s.replace(/^\s*```[a-zA-Z]*\r?\n?/, "")
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
