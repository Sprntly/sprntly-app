"use client"

import { isValidElement, type ReactNode } from "react"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import type { AskResponse } from "../../lib/api"
import { stripAnswerHtmlChrome } from "../../lib/answerHtmlChrome"
import { looksLikeHtmlBrief } from "../../lib/htmlBrief"
import { reportKindLabel, reportTitleFromHtml } from "../../lib/reportKind"
import {
  columnKinds,
  labelColumnClasses,
  tableRows,
} from "../../lib/tableColumnKinds"
import { useAnswerSimulatedStream } from "../../lib/useAnswerSimulatedStream"
import { HtmlReportView } from "./HtmlReportView"
import { JiraChangeConfirm } from "./JiraChangeConfirm"
import { TicketChangeConfirm } from "./TicketChangeConfirm"
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

/**
 * Report title per skill, for the sandboxed-iframe header. Every skill that
 * answers with a self-contained HTML document needs an entry; anything not
 * listed falls back to the VoC label, which is where this surface started.
 */
const REPORT_TITLES: Record<string, string> = {
  "public-feedback-report": "Public Feedback report",
  "ds-agent": "Data analysis report",
  "competitive-intelligence-review": "Competitive Intelligence report",
  "voice-of-customer-report": "Voice of Customer report",
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
  // A table whose columns are all sized by content alone wraps the SHORT
  // column to spare the long one — "Inputs & Data Sources" stacked over two
  // lines beside prose running the full message width. The browser can't tell
  // a label column from a prose one, so classify them here (see
  // lib/tableColumnKinds) and mark the label ones; globals.css then sizes
  // those to their content instead of wrapping them. Returns the table
  // untouched whenever the heuristic declines, so the existing layout — and
  // the min-width floor that stops the one-letter collapse — stays the
  // fallback rather than being replaced.
  table({ node, className, children, ...rest }) {
    const kinds = columnKinds(tableRows(node))
    const marks = labelColumnClasses(kinds)
    const cls = [className, marks].filter(Boolean).join(" ") || undefined
    return (
      <table className={cls} {...rest}>
        {children}
      </table>
    )
  },
}

/**
 * A report answer, as it appears IN THE THREAD: a card naming the document, with
 * the way to read it. The document itself lives in the panel's Reports tab —
 * that's where every artifact of a thread reads, and printing it here as well
 * meant scrolling past the same report twice to find the conversation.
 */
function ReportAnswerCard({
  html,
  skill,
  onOpen,
}: {
  html: string
  skill?: string | null
  onOpen: (title: string) => void
}) {
  const title = reportTitleFromHtml(html, skill)
  // `reportKindLabel` falls back to a bare "Report" when the turn carries no
  // skill (persisted turns don't), and "Report report" is nonsense — say it once.
  const kind = reportKindLabel(skill)
  const kindLine = kind === "Report" ? "Report" : `${kind} report`
  return (
    <div
      className="ask-report-card"
      data-testid="report-answer-card"
      style={{
        display: "flex", alignItems: "center", gap: 14,
        padding: "14px 16px", borderRadius: 12,
        border: "1px solid var(--line, #E8E6E0)", background: "var(--paper, #fff)",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 38, height: 38, borderRadius: "50%", flexShrink: 0,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: "#EDE9FE", color: "#6D28D9",
        }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <line x1="4" y1="20" x2="20" y2="20" />
          <rect x="6" y="11" width="3.4" height="6" rx="1" />
          <rect x="11.4" y="7" width="3.4" height="10" rx="1" />
          <rect x="16.8" y="13" width="3.4" height="4" rx="1" />
        </svg>
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14.5, fontWeight: 700, color: "var(--ink, #1A1A17)" }}>
          {title}
        </div>
        <div style={{ fontSize: 11.5, color: "var(--ink-3, #8C8A84)", marginTop: 2 }}>
          {kindLine} · opens on the right
        </div>
      </div>
      <button
        type="button"
        className="bc-action-btn"
        data-testid="open-report-btn"
        // The TITLE identifies which document this turn is: it's derived from the
        // report's own <h1>, exactly as the captured artifact's title is (see
        // report_capture.report_title), so the panel can open THIS report rather
        // than dropping the reader on a list of the thread's reports.
        onClick={() => onOpen(title)}
      >
        View report
      </button>
    </div>
  )
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
  /**
   * Where a report answer READS, on surfaces that have the content panel.
   *
   * A report is an artifact, and artifacts open in the panel — so the chat turn
   * becomes a compact card that opens it there, instead of printing the whole
   * document into the thread (which showed the same report twice, once inline
   * and once in the panel, and buried the conversation under it).
   *
   * Receives the report's title, which is how the panel resolves WHICH of the
   * thread's reports this turn is — a thread can hold several, and landing the
   * reader on a list when they clicked one specific document is a step backwards.
   *
   * Omitted on surfaces with no panel to open — the staff transcript viewer, the
   * AI rail — where the inline document is still the only way to read it.
   */
  onOpenReport?: (title: string) => void
}) {
  const { visible, done, isStreaming } = useAnswerSimulatedStream(reply.answer, simulateTyping)

  // A skill answer that IS a self-contained HTML document (e.g. the
  // voice-of-customer-report) renders in a sandboxed iframe — ReactMarkdown would
  // escape the tags. The report is self-contained, so we skip the simulated-typing
  // stream and the citations chrome below it.
  if (looksLikeHtmlBrief(reply.answer)) {
    const reportTitle =
      REPORT_TITLES[reply._skill ?? ""] ?? "Voice of Customer report"
    const report = onOpenReport ? (
      <ReportAnswerCard
        html={reply.answer}
        skill={reply._skill}
        onOpen={onOpenReport}
      />
    ) : (
      <HtmlReportView html={reply.answer} title={reportTitle} />
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
        {/* A markdown answer that carries raw HTML has that HTML PRINTED as
            tag text here — react-markdown runs without rehype-raw, by design.
            The chrome a skill's delivery format describes (an action row of
            "Push to Jira" / "Regenerate" buttons, colored pills) belongs to
            the surface the app renders, not to a chat turn, so it is removed
            rather than escaped. Applied to `visible`, so a partial tag never
            flashes as source while the answer streams. */}
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={askMarkdownComponents}>
          {stripAnswerHtmlChrome(visible)}
        </ReactMarkdown>
      </div>
      {/* A Jira change the agent proposed. Held back until the answer has
          finished revealing, so the confirm button can't be clicked while the
          sentence explaining what it does is still being typed out. */}
      {done && reply._pending_jira_change ? (
        <JiraChangeConfirm change={reply._pending_jira_change} />
      ) : null}
      {/* A PRD-grounded ticket rewrite, held back for the same reason. */}
      {done && reply._pending_ticket_change ? (
        <TicketChangeConfirm change={reply._pending_ticket_change} />
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
    </>
  )
  if (animateIn) {
    return <div className="ask-reply-body ask-reply-body--enter">{inner}</div>
  }
  return inner
}
