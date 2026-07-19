"use client"

import { isValidElement, useEffect, useRef, type ReactNode } from "react"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import type { AskResponse } from "../../lib/api"
import { decodeHtmlEntities, looksLikeHtmlBrief, stripHtmlCodeFence } from "../../lib/htmlBrief"
import { useAnswerSimulatedStream } from "../../lib/useAnswerSimulatedStream"
import { HtmlReportView } from "./HtmlReportView"
import { InlineChart, parseChartBody } from "./InlineChart"
import { IconReportAnalytics } from "@tabler/icons-react"

/** Title for a report card/panel, read from the document's own <title>. */
function reportTitle(html: string): string {
  const m = /<title>([\s\S]*?)<\/title>/i.exec(stripHtmlCodeFence(html))
  const t = m ? decodeHtmlEntities(m[1]).trim() : ""
  return t || "Report"
}

/** Pull a plain-text body out of react-markdown's `code` children prop. */
function flattenText(node: ReactNode): string {
  if (node == null || node === false) return ""
  if (typeof node === "string") return node
  if (typeof node === "number") return String(node)
  if (Array.isArray(node)) return node.map(flattenText).join("")
  if (isValidElement(node)) {
    const props = node.props as { children?: ReactNode }
    return flattenText(props.children)
  }
  return ""
}

const askMarkdownComponents: Components = {
  // Fenced ```chart blocks render as inline SVG infographics. Other fenced
  // blocks fall through to the default <code><pre> rendering.
  code({ className, children, ...rest }) {
    const lang = /language-([\w-]+)/.exec(className || "")?.[1]
    if (lang === "chart") {
      const spec = parseChartBody(flattenText(children))
      if (spec) {
        return (
          <InlineChart
            kind={spec.kind}
            title={spec.title}
            subtitle={spec.subtitle}
            data={spec.data}
          />
        )
      }
    }
    return (
      <code className={className} {...rest}>
        {children}
      </code>
    )
  },
}

export function AskReplyBody({
  reply,
  animateIn,
  simulateTyping = false,
  omitCitations = false,
  onOpenReport,
}: {
  reply: AskResponse
  /** Short fade/slide when the reply block first mounts. */
  animateIn?: boolean
  /** Reveal the answer in cumulative chunks so it feels streamed (POST still returns full JSON). */
  simulateTyping?: boolean
  /** Hide citation/source cards (e.g. right AI rail). */
  omitCitations?: boolean
  /** When set, an HTML-report answer opens in the right content panel's Report
   *  tab (via this callback) instead of rendering the whole document inline —
   *  the chat shows a compact card with an Open-report button, so the user
   *  keeps chatting on the left while reading on the right. A FRESH reply
   *  (animateIn) auto-opens the panel. Kept as a callback so this component
   *  stays free of the navigation/content contexts (it also renders in
   *  provider-less spots like tests). */
  onOpenReport?: (report: { html: string; title: string }) => void
}) {
  const { visible, done, isStreaming } = useAnswerSimulatedStream(reply.answer, simulateTyping)

  const isReport = looksLikeHtmlBrief(reply.answer)
  const openReport = useRef(() => {})
  openReport.current = () => {
    onOpenReport?.({ html: reply.answer, title: reportTitle(reply.answer) })
  }
  // Auto-open ONLY when the reply just landed (fresh turn) — a restored
  // conversation renders the card quietly and the user reopens on click.
  const autoOpen = !!onOpenReport && isReport && !!animateIn
  useEffect(() => {
    if (autoOpen) openReport.current()
    // Mount-time decision — deliberately not re-run on later prop identity churn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // A skill answer that IS a self-contained HTML document (e.g. the
  // voice-of-customer-report) renders in a sandboxed iframe — ReactMarkdown would
  // escape the tags. The report is self-contained, so we skip the simulated-typing
  // stream and the key_points/citations chrome below it.
  if (isReport) {
    const report = onOpenReport ? (
      <div className="ask-reply-report-card" data-testid="report-panel-card" style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "12px 14px", borderRadius: 10,
        border: "1px solid var(--line, #E8E6E0)", background: "var(--surface-2, #F4F1EA)",
      }}>
        <IconReportAnalytics size={18} style={{ flexShrink: 0, color: "var(--accent, #179463)" }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 13 }}>{reportTitle(reply.answer)}</div>
          <div style={{ fontSize: 11.5, color: "var(--ink-3, #8C8A84)" }}>
            Open in the panel on the right — keep chatting here about it.
          </div>
        </div>
        <button
          type="button"
          className="bc-action-btn bc-action-btn--primary"
          onClick={() => openReport.current()}
        >
          Open report
        </button>
      </div>
    ) : (
      <HtmlReportView html={reply.answer} title="Voice of Customer report" />
    )
    return animateIn ? (
      <div className="ask-reply-body ask-reply-body--enter">{report}</div>
    ) : (
      report
    )
  }

  const inner = (
    <>
      <div
        className={`ai-bar-reply-answer${isStreaming ? " ai-bar-reply-answer--streaming" : ""}`}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={askMarkdownComponents}>
          {visible}
        </ReactMarkdown>
      </div>
      {done && reply.key_points?.length ? (
        <ul className="ai-bar-reply-kp ai-bar-reply-kp--stream-reveal">
          {reply.key_points.map((kp, i) => (
            <li key={i} style={{ animationDelay: `${0.05 * i}s` }}>
              {kp}
            </li>
          ))}
        </ul>
      ) : null}
      {done && !omitCitations && reply.citations?.length ? (
        <div className="ai-bar-reply-cites ai-bar-reply-cites--stream-reveal">
          {reply.citations.map((c, i) => (
            <div key={i} className="ai-bar-reply-cite">
              <div className="ai-bar-reply-cite-src">{c.source}</div>
              <div className="ai-bar-reply-cite-ev">{c.evidence}</div>
            </div>
          ))}
        </div>
      ) : null}
      {done && reply.unanswered ? <div className="ai-bar-reply-gap">Gap: {reply.unanswered}</div> : null}
    </>
  )
  if (animateIn) {
    return <div className="ask-reply-body ask-reply-body--enter">{inner}</div>
  }
  return inner
}
