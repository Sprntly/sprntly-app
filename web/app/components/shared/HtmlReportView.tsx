"use client"

import { useRef, useState } from "react"
import { stripHtmlCodeFence } from "../../lib/htmlBrief"

/**
 * Renders a self-contained HTML report — e.g. the `voice-of-customer-report`
 * skill's fixed-template document — inside a SANDBOXED iframe.
 *
 * Security: `sandbox="allow-same-origin"` WITHOUT `allow-scripts`. The report's
 * inline CSS renders, but any <script> in the HTML cannot execute and inline
 * event handlers never fire — XSS-safe by construction. allow-same-origin lets us
 * read the document height to size the iframe to its content (no inner scrollbar).
 *
 * Read-only sibling of EvidenceHtmlBrief without the evidence-specific hypothesis
 * strip: a chat answer that IS a complete HTML document renders here instead of
 * through ReactMarkdown (which would escape the tags). Strips a wrapping ```html
 * fence defensively so a fenced payload still renders as a document.
 */
export function HtmlReportView({ html, title = "Report" }: { html: string; title?: string }) {
  const ref = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(720)
  const doc = stripHtmlCodeFence(html)

  const resize = () => {
    const cdoc = ref.current?.contentDocument
    if (!cdoc?.body) return
    const h = Math.max(cdoc.body.scrollHeight, cdoc.documentElement?.scrollHeight ?? 0)
    if (h > 0) setHeight(h)
  }

  return (
    <iframe
      ref={ref}
      title={title}
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
        background: "#FFFFFF",
      }}
    />
  )
}
