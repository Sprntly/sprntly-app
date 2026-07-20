"use client"

import { isValidElement, type ReactNode } from "react"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import type { AskResponse } from "../../lib/api"
import { looksLikeHtmlBrief } from "../../lib/htmlBrief"
import { useAnswerSimulatedStream } from "../../lib/useAnswerSimulatedStream"
import { HtmlReportView } from "./HtmlReportView"
import { InlineChart, parseChartBody } from "./InlineChart"

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
}: {
  reply: AskResponse
  /** Short fade/slide when the reply block first mounts. */
  animateIn?: boolean
  /** Reveal the answer in cumulative chunks so it feels streamed (POST still returns full JSON). */
  simulateTyping?: boolean
  /** Hide citation/source cards (e.g. right AI rail). */
  omitCitations?: boolean
}) {
  const { visible, done, isStreaming } = useAnswerSimulatedStream(reply.answer, simulateTyping)

  // A skill answer that IS a self-contained HTML document (e.g. the
  // voice-of-customer-report) renders in a sandboxed iframe — ReactMarkdown would
  // escape the tags. The report is self-contained, so we skip the simulated-typing
  // stream and the citations chrome below it.
  if (looksLikeHtmlBrief(reply.answer)) {
    const report = <HtmlReportView html={reply.answer} title="Voice of Customer report" />
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
    </>
  )
  if (animateIn) {
    return <div className="ask-reply-body ask-reply-body--enter">{inner}</div>
  }
  return inner
}
