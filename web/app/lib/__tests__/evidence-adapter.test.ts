import { describe, expect, it } from "vitest"
import { markdownToEvidenceState } from "../evidence-adapter"

describe("markdownToEvidenceState", () => {
  it("extracts the title from the first H1", () => {
    const out = markdownToEvidenceState("# The Title\n\nbody")
    expect(out.title).toBe("The Title")
  })

  it("falls back to a default title when no H1 is present", () => {
    const out = markdownToEvidenceState("body only")
    expect(out.title).toBe("Evidence")
  })

  describe("v3 HTML brief", () => {
    it("passes a self-contained HTML brief through as `html`, not :::block sections", () => {
      const html =
        '<meta charset="utf-8"><style>.wrap{max-width:820px}</style>' +
        '<div class="wrap"><h1>Beginners Plateau</h1><svg viewBox="0 0 720 250"></svg></div>'
      const out = markdownToEvidenceState(html)
      expect(out.html).toBe(html)
      expect(out.sections).toEqual([])
      // Self-contained brief carries its own title; the panel renders the iframe.
      expect(out.title).toBe("")
    })

    it.each([
      ["<!doctype html><html></html>"],
      ['  <div class="wrap"></div>'],
      ['<style>.x{}</style><div class="wrap"></div>'],
    ])("detects HTML opener %s", (html) => {
      expect(markdownToEvidenceState(html).html).toBe(html)
    })

    it("does NOT treat :::block markdown as HTML", () => {
      const md = ["# T", "", ":::hero", "[]", ":::"].join("\n")
      const out = markdownToEvidenceState(md)
      expect(out.html).toBeUndefined()
    })
  })

  it("parses an H2 + paragraph + bullet list as PRD primitives", () => {
    const out = markdownToEvidenceState(
      ["# T", "", "## Section", "", "Paragraph one.", "", "- item 1", "- item 2"].join("\n"),
    )
    const types = out.sections.map((s) => s.type)
    expect(types).toEqual(["h2", "p", "ul"])
    const ul = out.sections[2]
    if (ul.type !== "ul") throw new Error("expected ul")
    expect(ul.items).toEqual(["item 1", "item 2"])
  })

  it("parses a :::hero block into v2-hero section with cards", () => {
    const md = [
      "# T",
      "",
      ":::hero",
      JSON.stringify([
        { label: "Revenue at risk", value: "$143M / yr", tone: "negative" },
        { label: "Affected users", value: "218k / mo", tone: "negative", delta: "+9%" },
      ]),
      ":::",
    ].join("\n")
    const out = markdownToEvidenceState(md)
    const hero = out.sections[0]
    if (hero.type !== "v2-hero") throw new Error("expected v2-hero")
    expect(hero.cards).toHaveLength(2)
    expect(hero.cards[0].label).toBe("Revenue at risk")
    expect(hero.cards[0].tone).toBe("negative")
    expect(hero.cards[1].delta).toBe("+9%")
  })

  it("defaults tone to 'neutral' when the value is missing or invalid", () => {
    const md = [
      ":::hero",
      JSON.stringify([
        { label: "X", value: "1", tone: "unknown" },
        { label: "Y", value: "2" },
      ]),
      ":::",
    ].join("\n")
    const out = markdownToEvidenceState(md)
    const hero = out.sections[0]
    if (hero.type !== "v2-hero") throw new Error("expected v2-hero")
    expect(hero.cards[0].tone).toBe("neutral")
    expect(hero.cards[1].tone).toBe("neutral")
  })

  it("parses :::context-chip as plain text", () => {
    const md = [":::context-chip", "Claims · Q3 2025 · n=42", ":::"].join("\n")
    const out = markdownToEvidenceState(md)
    const c = out.sections[0]
    expect(c.type).toBe("v2-context-chip")
    if (c.type === "v2-context-chip") expect(c.text).toBe("Claims · Q3 2025 · n=42")
  })

  it("parses :::cuts-index rows and clamps unknown confidence to Medium", () => {
    const md = [
      ":::cuts-index",
      JSON.stringify([
        { n: 1, headline: "first", confidence: "High" },
        { n: 2, headline: "second", confidence: "bogus" },
      ]),
      ":::",
    ].join("\n")
    const out = markdownToEvidenceState(md)
    const c = out.sections[0]
    if (c.type !== "v2-cuts-index") throw new Error("expected v2-cuts-index")
    expect(c.rows[0].confidence).toBe("High")
    expect(c.rows[1].confidence).toBe("Medium")
  })

  it("parses :::source chips", () => {
    const md = [
      ":::source",
      JSON.stringify([
        { kind: "tool", label: "Mixpanel" },
        { kind: "period", label: "Q3 2025" },
        { kind: "confidence", label: "High" },
      ]),
      ":::",
    ].join("\n")
    const out = markdownToEvidenceState(md)
    const c = out.sections[0]
    if (c.type !== "v2-source") throw new Error("expected v2-source")
    expect(c.chips).toHaveLength(3)
    expect(c.chips[2].kind).toBe("confidence")
  })

  it("parses :::callout type=\"rules\" with bold-prefix lines", () => {
    const md = [
      ':::callout type="rules"',
      "**Supports:** It supports X.",
      "**Rules out:** Not Y.",
      ":::",
    ].join("\n")
    const out = markdownToEvidenceState(md)
    const c = out.sections[0]
    if (c.type !== "v2-rules-callout") throw new Error("expected v2-rules-callout")
    expect(c.supports).toBe("It supports X.")
    expect(c.rulesOut).toBe("Not Y.")
  })

  it("parses :::quote with body + channel", () => {
    const md = [
      ":::quote",
      JSON.stringify({
        body: "I left because of the deductible.",
        channel: "Zendesk",
        context: "Aug 2025",
      }),
      ":::",
    ].join("\n")
    const out = markdownToEvidenceState(md)
    const c = out.sections[0]
    if (c.type !== "v2-quote") throw new Error("expected v2-quote")
    expect(c.body).toContain("deductible")
    expect(c.channel).toBe("Zendesk")
    expect(c.context).toBe("Aug 2025")
  })

  it("parses :::forecast omitted=\"...\" as a forecast-omitted section", () => {
    const md = `:::forecast omitted="no trend basis"`
    const out = markdownToEvidenceState(md)
    const c = out.sections[0]
    if (c.type !== "v2-forecast-omitted")
      throw new Error("expected v2-forecast-omitted")
    expect(c.reason).toBe("no trend basis")
  })

  it("falls back to a paragraph for a malformed JSON-bodied block", () => {
    const md = [":::hero", "{this is not json", ":::"].join("\n")
    const out = markdownToEvidenceState(md)
    const c = out.sections[0]
    expect(c.type).toBe("p")
    if (c.type === "p") {
      expect(c.text).toContain("[hero block")
      expect(c.text).toContain("could not parse")
    }
  })

  it("parses a ```chart``` fenced block alongside v2 blocks", () => {
    const md = [
      "# T",
      "",
      ":::hero",
      JSON.stringify([{ label: "X", value: "1", tone: "neutral" }]),
      ":::",
      "",
      "```chart",
      JSON.stringify({
        kind: "bar",
        title: "Title",
        data: [{ label: "a", value: 1 }],
      }),
      "```",
    ].join("\n")
    const out = markdownToEvidenceState(md)
    const types = out.sections.map((s) => s.type)
    expect(types).toEqual(["v2-hero", "chart"])
  })

  it("salvages JSON when the body has surrounding noise", () => {
    const md = [
      ":::cuts-index",
      "noise before",
      JSON.stringify([{ n: 1, headline: "ok", confidence: "High" }]),
      "noise after",
      ":::",
    ].join("\n")
    const out = markdownToEvidenceState(md)
    const c = out.sections[0]
    if (c.type !== "v2-cuts-index") throw new Error("expected v2-cuts-index")
    expect(c.rows[0].headline).toBe("ok")
  })

  it("strips horizontal rules and blank lines between sections", () => {
    const md = [
      "# T",
      "",
      "──────────────",
      "",
      "## Section",
      "",
      "body",
    ].join("\n")
    const out = markdownToEvidenceState(md)
    const types = out.sections.map((s) => s.type)
    expect(types).toEqual(["h2", "p"])
  })
})
