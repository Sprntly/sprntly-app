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

// With `onOpenReport`, chat surfaces park the report in the right content panel
// (Report tab) instead of rendering the document inline — the thread shows a
// compact card whose button re-opens the panel, and a FRESH reply auto-opens it.
describe("AskReplyBody report-to-panel routing", () => {
  const TITLED =
    '<!DOCTYPE html><html><head><title>Voice of Customer — Q2</title></head>' +
    '<body><div class="page"><h1>VoC</h1></div></body></html>'

  it("renders a compact card (no inline iframe) and the report title", () => {
    const onOpen = vi.fn()
    const { container } = render(
      <AskReplyBody reply={reply(TITLED)} onOpenReport={onOpen} />,
    )
    expect(container.querySelector("iframe")).toBeNull()
    const card = container.querySelector('[data-testid="report-panel-card"]')
    expect(card).not.toBeNull()
    expect(card!.textContent).toContain("Voice of Customer — Q2")
  })

  it("auto-opens the panel for a FRESH reply (animateIn)", () => {
    const onOpen = vi.fn()
    render(<AskReplyBody reply={reply(TITLED)} animateIn onOpenReport={onOpen} />)
    expect(onOpen).toHaveBeenCalledWith({ html: TITLED, title: "Voice of Customer — Q2" })
  })

  it("does NOT auto-open a restored (non-fresh) reply; the card button re-opens it", () => {
    const onOpen = vi.fn()
    const { getByRole } = render(
      <AskReplyBody reply={reply(TITLED)} onOpenReport={onOpen} />,
    )
    expect(onOpen).not.toHaveBeenCalled()
    getByRole("button", { name: /open report/i }).click()
    expect(onOpen).toHaveBeenCalledWith({ html: TITLED, title: "Voice of Customer — Q2" })
  })

  it("leaves plain-markdown answers untouched (no card, no auto-open)", () => {
    const onOpen = vi.fn()
    const { container } = render(
      <AskReplyBody reply={reply("plain answer text here")} onOpenReport={onOpen} />,
    )
    expect(onOpen).not.toHaveBeenCalled()
    expect(container.querySelector('[data-testid="report-panel-card"]')).toBeNull()
  })
})
