// @vitest-environment jsdom
//
// AskReplyBody renders only the answer body (plus citation cards where allowed).
// The key_points recap list and the "Gap: …" unanswered note still arrive on
// AskResponse but are deliberately NOT rendered — they read as grey boilerplate
// under every answer.
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

afterEach(cleanup)

const REPLY = {
  answer: "Invite-flow friction is the top pain point this week.",
  key_points: ["23% of new users abandon at the invite screen", "$88k ARR at risk"],
  citations: [{ source: "support_themes_weekly", evidence: "17 tickets tagged invite flow" }],
  confidence: 0.9,
  unanswered: "No verbatim quotes for dashboard slowness.",
}

describe("AskReplyBody answer chrome", () => {
  it("renders the answer without the key_points recap or the Gap note", () => {
    const { container } = render(<AskReplyBody reply={REPLY} />)
    expect(container.textContent).toContain("Invite-flow friction")
    expect(container.querySelector(".ai-bar-reply-kp")).toBeNull()
    expect(container.querySelector(".ai-bar-reply-gap")).toBeNull()
    expect(container.textContent).not.toContain("Gap:")
    expect(container.textContent).not.toContain("23% of new users abandon")
  })

  // An answer that IS a self-contained HTML document renders in the sandboxed
  // iframe; its title comes from the skill that produced it. Before the map the
  // DS analysis report was labelled "Voice of Customer report".
  const HTML_REPLY = { ...REPLY, answer: '<!doctype html><html><body><div>report</div></body></html>' }

  it.each([
    ["ds-agent", "Data analysis report"],
    ["public-feedback-report", "Public Feedback report"],
    ["voice-of-customer-report", "Voice of Customer report"],
    [undefined, "Voice of Customer report"],
  ])("titles the HTML report iframe for _skill=%s", (skill, title) => {
    const { container } = render(<AskReplyBody reply={{ ...HTML_REPLY, _skill: skill }} />)
    expect(container.querySelector("iframe")?.getAttribute("title")).toBe(title)
  })

  // react-markdown runs without rehype-raw, so raw HTML in an answer is not
  // drawn — it is PRINTED as tag text. "create a ticket to address this" on a
  // VoC report with no PRD answers through the ask path in markdown, and the
  // user-stories skill's delivery format had the model draw its action row as
  // HTML; the reader saw `<div style="display:flex…"><button…>` in the thread.
  it("never prints raw HTML chrome from a markdown answer", () => {
    const answer =
      "Tickets from *VoC Report*\n\n" +
      '<div style="display:flex;gap:12px;"> <button style="background:#2e8a57;">' +
      "✓ Push to Jira</button> <button>⟳ Regenerate</button> </div>\n\nT-1 · URGENT · 3 AC"
    const { container } = render(<AskReplyBody reply={{ ...REPLY, answer }} />)
    const text = container.textContent ?? ""
    expect(text).not.toContain("<div")
    expect(text).not.toContain("<button")
    expect(text).not.toContain("style=")
    expect(text).not.toContain("Push to Jira")
    expect(text).toContain("Tickets from")
    expect(text).toContain("T-1 · URGENT · 3 AC")
  })

  it("still renders citation cards unless omitCitations", () => {
    const { container } = render(<AskReplyBody reply={REPLY} />)
    expect(container.querySelector(".ai-bar-reply-cite")).not.toBeNull()
    cleanup()
    const { container: omitted } = render(<AskReplyBody reply={REPLY} omitCitations />)
    expect(omitted.querySelector(".ai-bar-reply-cite")).toBeNull()
  })
})

// A skill answer that IS a self-contained HTML document renders in the
// sandboxed, script-less iframe. The iframe's accessible name is the only place
// the reader is told WHICH report they are looking at, so each report skill
// needs its own title.
describe("AskReplyBody HTML report titles", () => {
  const htmlReply = (skill: string) => ({
    ...REPLY,
    answer: "<!DOCTYPE html><html><body><h1>Report</h1></body></html>",
    _skill: skill,
  })

  it("titles a competitive-intelligence report", () => {
    const { container } = render(<AskReplyBody reply={htmlReply("competitive-intelligence-review")} />)
    const frame = container.querySelector("iframe")
    expect(frame).not.toBeNull()
    expect(frame?.getAttribute("title")).toBe("Competitive Intelligence report")
    // still sandboxed without allow-scripts
    expect(frame?.getAttribute("sandbox")).toBe("allow-same-origin")
  })

  it("keeps the existing public-feedback and VoC titles", () => {
    const { container } = render(<AskReplyBody reply={htmlReply("public-feedback-report")} />)
    expect(container.querySelector("iframe")?.getAttribute("title")).toBe("Public Feedback report")
    cleanup()
    const { container: voc } = render(<AskReplyBody reply={htmlReply("voice-of-customer-report")} />)
    expect(voc.querySelector("iframe")?.getAttribute("title")).toBe("Voice of Customer report")
  })

  it("falls back to the VoC label for an unlabelled HTML answer", () => {
    const { container } = render(
      <AskReplyBody reply={{ ...REPLY, answer: "<!DOCTYPE html><html><body>x</body></html>" }} />,
    )
    expect(container.querySelector("iframe")?.getAttribute("title")).toBe("Voice of Customer report")
  })
})

// ── A report answer is an artifact, not a chat message ───────────────────────
// PR #963 made a report answer render as a CARD that opens the document in the
// panel, instead of printing the whole report into the thread — where it showed
// twice, once inline and once in the panel. That card was gated on the HTML
// document sniff; when the pinned templates were removed every pipeline started
// answering in markdown, the sniff stopped firing, and reports went back to
// printing inline. `_report` is the marker the engines stamp on the one return
// that IS the document, and the same one `report_capture` captures on.
const REPORT_MD = "# Voice-of-Customer Report — Sprntly\n\n## Themes\n\n- Onboarding friction\n"

describe("AskReplyBody — a report answer", () => {
  it("renders a card naming the document, not the document", () => {
    const { container, getByTestId } = render(
      <AskReplyBody
        reply={{ ...REPLY, answer: REPORT_MD, _report: true, _skill: "voice-of-customer-report" }}
        onOpenReport={() => {}}
      />,
    )
    expect(getByTestId("report-answer-card")).toBeTruthy()
    expect(container.textContent).toContain("Voice-of-Customer Report — Sprntly")
    expect(container.textContent).toContain("Voice of Customer report")
    // The report BODY stays out of the thread — that is the whole point.
    expect(container.textContent).not.toContain("Onboarding friction")
  })

  it("opens THAT report by its title", () => {
    const opened: string[] = []
    const { getByTestId } = render(
      <AskReplyBody
        reply={{ ...REPLY, answer: REPORT_MD, _report: true, _skill: "voice-of-customer-report" }}
        onOpenReport={(t) => opened.push(t)}
      />,
    )
    getByTestId("open-report-btn").click()
    // Matches `report_capture.report_title`'s markdown rung, which is what
    // `matchReportByTitle` joins on — a thread can hold several reports.
    expect(opened).toEqual(["Voice-of-Customer Report — Sprntly"])
  })

  it("reads inline on a surface with no panel to open it in", () => {
    const { container, queryByTestId } = render(
      <AskReplyBody
        reply={{ ...REPLY, answer: REPORT_MD, _report: true, _skill: "voice-of-customer-report" }}
      />,
    )
    expect(queryByTestId("report-answer-card")).toBeNull()
    expect(container.textContent).toContain("Onboarding friction")
  })

  it("leaves an ordinary answer alone", () => {
    const { container, queryByTestId } = render(
      <AskReplyBody reply={REPLY} onOpenReport={() => {}} />,
    )
    expect(queryByTestId("report-answer-card")).toBeNull()
    expect(container.textContent).toContain("Invite-flow friction")
  })
})

// A navigation answer ("your PRDs are in Artifacts") is only an answer if the
// link it carries is clickable — see backend/app/app_map.py, which is what
// teaches the model these paths. An in-app path routes through next/link (a
// client-side nav, and base-path aware); an external URL keeps the plain
// anchor it has always had.
describe("AskReplyBody links", () => {
  const linked = (answer: string) =>
    render(<AskReplyBody reply={{ ...REPLY, answer }} />).container

  it("renders an in-app path as a real, navigable link", () => {
    const a = linked("Your PRDs are in [Artifacts](/artifacts).").querySelector("a")
    expect(a?.getAttribute("href")).toBe("/artifacts")
    expect(a?.textContent).toBe("Artifacts")
  })

  it("keeps the query param on a settings deep link", () => {
    const a = linked(
      "Connect it in [Settings -> Connectors](/settings?section=connectors).",
    ).querySelector("a")
    expect(a?.getAttribute("href")).toBe("/settings?section=connectors")
  })

  it("leaves an external link as a plain anchor", () => {
    const a = linked("See [the issue](https://acme.atlassian.net/browse/AB-12).")
      .querySelector("a")
    expect(a?.getAttribute("href")).toBe("https://acme.atlassian.net/browse/AB-12")
  })
})

// ── charts render from what the block SAYS, not what the fence is called ─────
// Reported (2026-09-03): a reader asked for an analytical chart and got a wall
// of raw JSON. The spec was perfect — kind, title, subtitle, labelled values —
// and the model had put it in a ```json fence instead of a ```chart one, so a
// renderer keyed on the word printed it as source code.

describe("AskReplyBody charts", () => {
  const SPEC = JSON.stringify({
    kind: "stat",
    title: "173,500 exports silently failed — customers saw HTTP 200 every time",
    subtitle: "Source: revenue | 8 Jun–20 Aug 2026",
    data: [
      { label: 'Jobs returned HTTP 200 ("success")', value: 168200 },
      { label: "Files actually opened by recipient", value: 96300 },
    ],
  }, null, 2)

  const bodied = (answer: string) =>
    render(<AskReplyBody reply={{ ...REPLY, answer }} />).container

  it.each(["chart", "json", ""])("draws the chart from a ```%s fence", (lang) => {
    const c = bodied(`Here is the cut.\n\n\`\`\`${lang}\n${SPEC}\n\`\`\`\n`)
    // The chart is drawn — a stat tile is numbers rather than an <svg>, so the
    // figure the renderer emits is what says so.
    expect(c.querySelector("figure.prd-chart-stat")).not.toBeNull()
    expect(c.textContent).toContain("173,500 exports silently failed")
    // …and the reader never sees the spec that produced it.
    expect(c.textContent).not.toContain('"kind"')
    expect(c.querySelector("code")).toBeNull()
  })

  it("still renders a real code block as code", () => {
    // The guard that keeps this from swallowing everything: `parseChartBody`
    // needs a known kind and labelled values, so ordinary JSON is untouched.
    const c = bodied('```json\n{ "retries": 3, "timeout_ms": 500 }\n```\n')
    expect(c.querySelector("code")).not.toBeNull()
    expect(c.textContent).toContain('"retries"')
    expect(c.querySelector(".prd-chart")).toBeNull()
  })

  it("leaves a code sample alone", () => {
    const c = bodied("```python\nprint('kind', data)\n```\n")
    expect(c.querySelector("code")?.textContent).toContain("print(")
    expect(c.querySelector(".prd-chart")).toBeNull()
  })

  it("leaves inline code alone", () => {
    const c = bodied("Set `kind` to `stat` in the payload.")
    expect(c.textContent).toContain("Set kind to stat in the payload.")
    expect(c.querySelector(".prd-chart")).toBeNull()
  })

  it("ignores a chart-shaped block with no data to plot", () => {
    const c = bodied('```chart\n{ "kind": "bar", "title": "Nothing", "data": [] }\n```\n')
    expect(c.querySelector(".prd-chart")).toBeNull()
    expect(c.querySelector("code")).not.toBeNull()
  })
})
