"use client"

import { useRef, useState } from "react"
import { stripHtmlCodeFence } from "../../lib/htmlBrief"
import { watermarkHtml } from "../../lib/watermark"

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
/**
 * Injected when the document is read inside the side panel.
 *
 * These reports lay themselves out for a full page — `.page` carries a wide
 * reading gutter that makes sense at full width and only squeezes the content
 * when the same document is shown in a ~700px panel. Appended AFTER the
 * document's own styles so it wins without editing the skill's template, and
 * scoped to this one rule so nothing else about the layout changes.
 *
 * Not applied on the standalone `/r/<token>` page, where the document has the
 * whole viewport and its own gutter is exactly right.
 */
const PANEL_STYLE = "<style>.page{padding:0px 0px !important;}</style>"

/** Put the override inside <head> when there is one, else append it. */
function withPanelStyle(doc: string): string {
  if (/<\/head>/i.test(doc)) return doc.replace(/<\/head>/i, `${PANEL_STYLE}</head>`)
  return doc + PANEL_STYLE
}

export function HtmlReportView({
  html,
  title = "Report",
  fitPanel = false,
  watermark = false,
}: {
  html: string
  title?: string
  /** Trim the document's own page gutter — see PANEL_STYLE. Set by the content
   *  panel's Reports tab, which has far less width than a full page. */
  fitPanel?: boolean
  /** Stamp the Sprntly wordmark behind the document. Opt-in, and set only by the
   *  public `/r/<token>` page: this component also renders the in-app Reports
   *  tab, where the reader is a signed-in member reading their own workspace's
   *  work and a mark over it is noise. The mark is for the copy that left. */
  watermark?: boolean
}) {
  const ref = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(720)
  const stripped = stripHtmlCodeFence(html)
  const sized = fitPanel ? withPanelStyle(stripped) : stripped
  const doc = watermark ? watermarkHtml(sized) : sized

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
