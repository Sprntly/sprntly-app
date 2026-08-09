import { describe, expect, it } from "vitest"

import { stripAnswerHtmlChrome } from "../answerHtmlChrome"

// The exact fragment that shipped into a chat thread: "create a ticket to
// address this" on a VoC report with no PRD falls to the ask path, which
// answers in markdown — and the user-stories skill's delivery format made the
// model draw its action row as raw HTML, which react-markdown prints verbatim.
const ACTION_ROW =
  '<div style="display:flex;gap:12px;margin-bottom:8px;"> ' +
  '<button style="background:#2e8a57;color:#fff;border:none;padding:6px 14px;' +
  'border-radius:6px;font-size:13px;cursor:pointer;">✓ Push to Jira</button> ' +
  '<button style="background:#f1f1ef;color:#1c1e21;border:none;padding:6px 14px;' +
  'border-radius:6px;font-size:13px;cursor:pointer;">⟳ Regenerate</button> </div>'

describe("stripAnswerHtmlChrome", () => {
  it("removes the ticket action row, labels and all", () => {
    const out = stripAnswerHtmlChrome(
      `## Tickets from *VoC Report*\n\n2 tickets · No Part B detected\n\n${ACTION_ROW}\n\nT-1 · URGENT · 3 AC`,
    )
    expect(out).not.toContain("<div")
    expect(out).not.toContain("<button")
    expect(out).not.toContain("style=")
    // A button label is an affordance — leaving "✓ Push to Jira" as prose
    // offers something the reader cannot click.
    expect(out).not.toContain("Push to Jira")
    expect(out).not.toContain("Regenerate")
    // The answer itself is untouched.
    expect(out).toContain("## Tickets from *VoC Report*")
    expect(out).toContain("2 tickets · No Part B detected")
    expect(out).toContain("T-1 · URGENT · 3 AC")
  })

  it("unwraps a non-chrome tag instead of eating the text inside it", () => {
    expect(stripAnswerHtmlChrome("Churn is <strong>up 12%</strong> this week")).toBe(
      "Churn is up 12% this week",
    )
  })

  it("leaves fenced blocks alone, chart schema included", () => {
    const md = [
      "## Finding",
      "",
      "```chart",
      '{"kind": "bar", "title": "Mobile crashes lead", "data": [{"label": "a", "value": 1}]}',
      "```",
      "",
      "```html",
      '<div class="demo">markup the reader asked to SEE</div>',
      "```",
    ].join("\n")
    expect(stripAnswerHtmlChrome(md)).toBe(md)
  })

  it("leaves inline code spans alone", () => {
    const md = "Wrap it in a `<div>` and the export breaks"
    expect(stripAnswerHtmlChrome(md)).toBe(md)
  })

  it("keeps angle-bracket text that is not HTML", () => {
    const md = "Return `List<int>` when count <threshold> and 3 < 5"
    expect(stripAnswerHtmlChrome(md)).toBe(md)
  })

  it("drops a tag that is still streaming in", () => {
    expect(stripAnswerHtmlChrome("Two tickets generated.\n\n<div sty")).toBe(
      "Two tickets generated.",
    )
    expect(
      stripAnswerHtmlChrome('Two tickets generated.\n\n<button style="background:#2e8a57">✓ Push to J'),
    ).toBe("Two tickets generated.")
  })

  it("returns markdown with no HTML unchanged", () => {
    const md = "## Finding\n\nInvite-flow friction is the top pain point.\n"
    expect(stripAnswerHtmlChrome(md)).toBe(md)
  })

  it("degrades an all-markup answer to readable lines, never to a blank turn", () => {
    // A document answer normally renders through HtmlReportView (looksLikeHtmlBrief
    // catches it first). One that slips past — no doctype, straight into a
    // heading — still has to read as something.
    expect(stripAnswerHtmlChrome("<h1>Report</h1><p>body</p>")).toBe("Report\nbody")
  })

  it("keeps the answer when stripping would empty it", () => {
    expect(stripAnswerHtmlChrome('<div style="height:4px"></div>')).toBe(
      '<div style="height:4px"></div>',
    )
  })
})
