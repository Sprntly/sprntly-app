// The quoted-excerpt protocol. `splitQuotedSuffix` runs over EVERY user turn
// ever sent, including the ones written before quoting existed, so the tests
// that matter most are the ones proving it leaves ordinary messages alone.
import { describe, expect, it } from "vitest"

import {
  QUOTE_MAX_CHARS,
  buildQuotedMessage,
  normalizeQuote,
  splitQuotedSuffix,
} from "../chatQuote"

describe("normalizeQuote", () => {
  it("trims the whitespace a drag selection always picks up", () => {
    expect(normalizeQuote("\n   findings must be documented  \n\n")).toBe(
      "findings must be documented",
    )
  })

  it("normalizes line endings and collapses runs of blank lines", () => {
    expect(normalizeQuote("one\r\n\r\n\r\n\r\ntwo")).toBe("one\n\ntwo")
  })

  it("caps a long excerpt and marks the truncation", () => {
    const out = normalizeQuote("x".repeat(QUOTE_MAX_CHARS + 500))
    expect(out.length).toBe(QUOTE_MAX_CHARS + 1)
    expect(out.endsWith("…")).toBe(true)
  })

  it("returns empty for a selection with no text in it", () => {
    expect(normalizeQuote("   \n  ")).toBe("")
  })
})

describe("buildQuotedMessage", () => {
  it("appends the excerpt as a trailing blockquote", () => {
    expect(buildQuotedMessage("Which manual is that?", "findings must be documented")).toBe(
      "Which manual is that?\n\n> findings must be documented",
    )
  })

  it("keeps a multi-paragraph excerpt as ONE blockquote (blank lines become '>')", () => {
    expect(buildQuotedMessage("why?", "first para\n\nsecond para")).toBe(
      "why?\n\n> first para\n>\n> second para",
    )
  })

  it("leaves the message untouched when there is no quote", () => {
    expect(buildQuotedMessage("plain question", null)).toBe("plain question")
    expect(buildQuotedMessage("plain question", "   ")).toBe("plain question")
  })

  it("keeps a pinned skill's slash trigger as the FIRST token", () => {
    // The whole reason the quote rides at the END: `skillForQuery` and the
    // backend's deterministic fast-path both read the query's first token.
    const sent = buildQuotedMessage("/competitive-intel how do we compare?", "their pricing page")
    expect(sent.startsWith("/competitive-intel ")).toBe(true)
  })
})

describe("splitQuotedSuffix", () => {
  it("round-trips whatever buildQuotedMessage produced", () => {
    for (const [message, quote] of [
      ["Which manual is that?", "findings must be documented"],
      ["why?", "first para\n\nsecond para"],
      ["/competitive-intel how do we compare?", "their pricing page"],
      // A quoted LIST: single newlines between rows, which is the shape
      // `rangeToText` produces and the one the run-on-paragraph bug destroyed.
      ["really?", "All set — assigned:\n\nValidate SPF record → Dana Reyes\nValidate DKIM key → Dana Reyes"],
    ] as const) {
      const { body, quote: back } = splitQuotedSuffix(buildQuotedMessage(message, quote))
      expect(body).toBe(message)
      expect(back).toBe(quote)
    }
  })

  it("leaves an ordinary message whole", () => {
    for (const text of [
      "just a question",
      "",
      "a > b in the comparison",
      "line one\nline two",
      "5 > 3 and 2 > 1",
    ]) {
      expect(splitQuotedSuffix(text)).toEqual({ body: text, quote: null })
    }
  })

  it("does not split a message that is ONLY a blockquote", () => {
    // Nothing would be left to render above it, so splitting would lose the
    // words rather than reposition them.
    const text = "> just a quoted line"
    expect(splitQuotedSuffix(text)).toEqual({ body: text, quote: null })
  })

  it("does not split a blockquote that isn't at the end", () => {
    const text = "before\n\n> quoted\n\nafter"
    expect(splitQuotedSuffix(text)).toEqual({ body: text, quote: null })
  })

  it("requires the blank-line separator it writes", () => {
    const text = "question\n> quoted"
    expect(splitQuotedSuffix(text)).toEqual({ body: text, quote: null })
  })
})
