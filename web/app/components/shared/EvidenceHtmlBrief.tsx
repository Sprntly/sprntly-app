"use client"

import { useRef, useState } from "react"

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
 * both surfaces render the HTML brief identically.
 */
export function EvidenceHtmlBrief({ html }: { html: string }) {
  const ref = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(640)

  const resize = () => {
    const doc = ref.current?.contentDocument
    if (!doc?.body) return
    const h = Math.max(doc.body.scrollHeight, doc.documentElement?.scrollHeight ?? 0)
    if (h > 0) setHeight(h)
  }

  return (
    <iframe
      ref={ref}
      title="Evidence brief"
      srcDoc={html}
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

/**
 * Heuristic: does this evidence payload look like the self-contained HTML brief
 * (variant v3) rather than the legacy `:::block` markdown? Used where the storage
 * variant isn't in hand (the markdown adapter) — a content sniff for the brief's
 * opening tags.
 */
export function looksLikeHtmlBrief(payload: string | null | undefined): boolean {
  return /^\s*<(?:!doctype|meta|html|div|style)\b/i.test(payload ?? "")
}
