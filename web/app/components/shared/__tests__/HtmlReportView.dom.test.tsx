// @vitest-environment jsdom
//
// HtmlReportView renders a self-contained HTML report (e.g. the
// voice-of-customer-report) inside a SANDBOXED iframe, and AskReplyBody routes an
// HTML answer here instead of through ReactMarkdown (which would escape the tags).
// The security contract is the point: sandbox="allow-same-origin" WITHOUT
// allow-scripts, so model-generated HTML renders its inline CSS but can never
// execute a <script>.
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// Classic JSX runtime needs a global React before the component modules evaluate,
// and AskReplyBody's simulated-stream hook reads window.matchMedia (absent in jsdom).
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
  if (typeof window !== "undefined" && !window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {}, dispatchEvent() { return false },
    })) as unknown as typeof window.matchMedia
  }
})

import { AskReplyBody } from "../AskReplyBody"
import { HtmlReportView } from "../HtmlReportView"

afterEach(cleanup)

const REPORT =
  '<!DOCTYPE html><html><head><style>.page{max-width:900px}</style></head>' +
  '<body><div class="page"><h1>Voice of Customer</h1></div></body></html>'

function reply(answer: string) {
  return { answer, key_points: [], citations: [], confidence: 1, unanswered: "" }
}

describe("HtmlReportView", () => {
  it("renders an iframe carrying the report HTML in srcDoc", () => {
    const { container } = render(<HtmlReportView html={REPORT} />)
    const iframe = container.querySelector("iframe")
    expect(iframe).not.toBeNull()
    expect(iframe!.getAttribute("srcdoc")).toBe(REPORT)
  })

  it("sandboxes the iframe: allow-same-origin but NOT allow-scripts (XSS-safe)", () => {
    const { container } = render(<HtmlReportView html={REPORT} />)
    const sandbox = container.querySelector("iframe")!.getAttribute("sandbox")
    expect(sandbox).toBe("allow-same-origin")
    expect(sandbox).not.toContain("allow-scripts")
  })

  it("leaves the document's own page gutter alone by default", () => {
    // The standalone /r/<token> page has the whole viewport — the report's own
    // layout is right there.
    const { container } = render(<HtmlReportView html={REPORT} />)
    expect(container.querySelector("iframe")!.getAttribute("srcdoc")).toBe(REPORT)
  })

  it("trims the page gutter inside the panel, without touching the document's own styles", () => {
    const { container } = render(<HtmlReportView html={REPORT} fitPanel />)
    const srcdoc = container.querySelector("iframe")!.getAttribute("srcdoc")!
    expect(srcdoc).toContain(".page{padding:0px 0px !important;}")
    // Appended INSIDE head, after the document's own <style>, so it wins on
    // order rather than by editing the skill's template.
    expect(srcdoc.indexOf(".page{padding")).toBeGreaterThan(srcdoc.indexOf(".page{max-width:900px}"))
    expect(srcdoc).toContain("<h1>Voice of Customer</h1>")
  })
})

describe("AskReplyBody HTML routing", () => {
  it("routes an HTML answer to the sandboxed iframe (not ReactMarkdown)", () => {
    const { container } = render(<AskReplyBody reply={reply(REPORT)} />)
    const iframe = container.querySelector("iframe")
    expect(iframe).not.toBeNull()
    expect(iframe!.getAttribute("srcdoc")).toBe(REPORT)
    // The markdown answer container must NOT be present for an HTML report.
    expect(container.querySelector(".ai-bar-reply-answer")).toBeNull()
  })

  it("renders a plain-markdown answer as markdown (no iframe)", () => {
    const { container } = render(
      <AskReplyBody reply={reply("## Hello\n\nplain **markdown** answer")} />,
    )
    expect(container.querySelector("iframe")).toBeNull()
    expect(container.querySelector(".ai-bar-reply-answer")).not.toBeNull()
    expect(container.textContent).toContain("plain")
  })
})

// On a surface WITH the content panel, a report is an artifact like any other:
// it reads in the panel, and the chat turn is the card that opens it. Printing
// the document inline as well showed the same report twice — once in the thread,
// once in the panel — and buried the conversation under it.
describe("AskReplyBody — a report answer on a surface with the panel", () => {
  it("renders a card naming the report instead of the document", () => {
    const onOpenReport = vi.fn()
    const { container } = render(
      <AskReplyBody reply={reply(REPORT)} onOpenReport={onOpenReport} />,
    )

    expect(container.querySelector("iframe")).toBeNull()
    const card = container.querySelector('[data-testid="report-answer-card"]')!
    expect(card).not.toBeNull()
    // Named by the document's own <h1>, the way the captured artifact is.
    expect(card.textContent).toContain("Voice of Customer")
  })

  it("opens the report in the panel on click", () => {
    const onOpenReport = vi.fn()
    const { container } = render(
      <AskReplyBody reply={reply(REPORT)} onOpenReport={onOpenReport} />,
    )

    ;(container.querySelector('[data-testid="open-report-btn"]') as HTMLButtonElement).click()
    expect(onOpenReport).toHaveBeenCalledTimes(1)
  })

  it("still renders the document inline where there is no panel to open", () => {
    // The staff transcript viewer and the AI rail pass no handler — inline is
    // the only way to read it there.
    const { container } = render(<AskReplyBody reply={reply(REPORT)} />)
    expect(container.querySelector("iframe")).not.toBeNull()
    expect(container.querySelector('[data-testid="report-answer-card"]')).toBeNull()
  })
})
