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
