"use client"

import { useRef, useState } from "react"
import { stripHtmlCodeFence, stripHypothesisSection } from "../../lib/htmlBrief"

// Re-exported for back-compat with existing import sites.
export { looksLikeHtmlBrief } from "../../lib/htmlBrief"

/**
 * Renders the v3 evidence artifact — the `evidence-brief` skill's self-contained
 * HTML visual brief (inline <style> + hand-authored inline SVG charts) — inside a
 * SANDBOXED iframe.
 *
 * Security: `sandbox="allow-same-origin"` WITHOUT `allow-scripts`. The brief's
 * inline CSS/SVG render, but any <script> in the model-generated HTML cannot
 * execute and inline event handlers never fire — XSS-safe by construction.
 * allow-same-origin lets us read the document height to size the iframe to its
 * content (no inner scrollbar); the brief itself carries no scripts.
 *
 * Shared by the full-page EvidenceScreen and the artifact-panel Evidence tab so
 * both surfaces render the HTML brief identically. Strips a wrapping ```html
 * code fence defensively so a fenced payload (some callers pass the raw row)
 * still renders as a document rather than literal backticks.
 */
export function EvidenceHtmlBrief({ html }: { html: string }) {
  const ref = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(640)
  const doc = stripHypothesisSection(stripHtmlCodeFence(html))

  const resize = () => {
    const cdoc = ref.current?.contentDocument
    if (!cdoc?.body) return
    const h = Math.max(cdoc.body.scrollHeight, cdoc.documentElement?.scrollHeight ?? 0)
    if (h > 0) setHeight(h)
  }

  return (
    <iframe
      ref={ref}
      title="Evidence brief"
      srcDoc={doc}
      onLoad={resize}
      sandbox="allow-same-origin"
      style={{
        width: "100%",
        height,
        border: "1px solid var(--line, #E8E6E0)",
        borderRadius: 10,
        display: "block",
        colorScheme: "light",
        background: "#fbfaf6",
      }}
    />
  )
}
