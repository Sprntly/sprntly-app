// Pure node-env tests for the @-mention helpers — no jsdom, no React.
import { describe, expect, it } from "vitest"
import { detectMentionQuery, insertMentionChip, isEmailNeedle, parseMentionChips } from "../mentions"

describe("detectMentionQuery — excludes @sprntly, honours caret", () => {
  it("test_detect_mention_query_excludes_sprntly", () => {
    // @For| → "For"
    expect(detectMentionQuery("@For", 4)).toEqual({ query: "For", start: 0, end: 4 })
    // @sprntly| (exact agent word, case-insensitive) → null
    expect(detectMentionQuery("@sprntly", 8)).toBeNull()
    expect(detectMentionQuery("@Sprntly", 8)).toBeNull()
    // caret outside the token → null
    expect(detectMentionQuery("@For hello", 10)).toBeNull() // caret is in "hello"
    expect(detectMentionQuery("@For", 0)).toBeNull() // caret before the "@"
  })

  it("mid-word caret returns only the typed-so-far prefix", () => {
    // "@Fo|rtune" → active token is what precedes the caret
    expect(detectMentionQuery("@Fortune", 3)).toEqual({ query: "Fo", start: 0, end: 3 })
  })

  it("an @ at a word boundary opens; an inline email does NOT", () => {
    expect(detectMentionQuery("hey @For", 8)).toEqual({ query: "For", start: 4, end: 8 })
    // inline email — the "@" is not at a word boundary → null
    expect(detectMentionQuery("email me@acme.com", 17)).toBeNull()
  })

  it("an email typed AS a mention resolves to the whole run", () => {
    expect(detectMentionQuery("@jane@acme.com", 14)).toEqual({
      query: "jane@acme.com",
      start: 0,
      end: 14,
    })
  })

  it("a bare @ is an active empty query (directory browse)", () => {
    expect(detectMentionQuery("@", 1)).toEqual({ query: "", start: 0, end: 1 })
  })
})

describe("insertMentionChip — replaces the active token, preserves surroundings + caret", () => {
  it("test_insert_mention_chip_replaces_token", () => {
    // "hi @Fo|" + label "Fortune" → "hi @Fortune " with caret after the mention
    const res = insertMentionChip("hi @Fo", 6, "Fortune")
    expect(res.text).toBe("hi @Fortune ")
    expect(res.caret).toBe(res.text.length)
  })

  it("preserves text AFTER the token", () => {
    // caret sits at end of "@Fo" inside "@Fo done" (caret index 3)
    const res = insertMentionChip("@Fo done", 3, "Fortune Ade")
    expect(res.text).toBe("@Fortune Ade  done")
    expect(res.caret).toBe("@Fortune Ade ".length)
  })

  it("with no active token, inserts at the caret", () => {
    const res = insertMentionChip("hello world", 5, "Ada")
    expect(res.text).toBe("hello@Ada  world")
    expect(res.caret).toBe("hello@Ada ".length)
  })
})

describe("parseMentionChips — segments content, never chips @sprntly", () => {
  it("test_parse_mention_chips_segments", () => {
    const segs = parseMentionChips("hey @Fortune can you look?")
    expect(segs).toEqual([
      { type: "text", value: "hey " },
      { type: "mention", label: "Fortune" },
      { type: "text", value: " can you look?" },
    ])
  })

  it("plain text is unchanged (single text segment)", () => {
    expect(parseMentionChips("just some text")).toEqual([{ type: "text", value: "just some text" }])
  })

  it("@sprntly is NEVER chipped as a person — it is a distinct AGENT segment", () => {
    // The rewrite gave the agent token its own recognized `agent` segment
    // (rendered as an agent chip, never a people chip). The load-bearing
    // exclusion — @Sprntly is never a PERSON mention — still holds; it is now
    // additionally surfaced as an `agent` segment rather than left as prose.
    const segs = parseMentionChips("@Sprntly please help")
    // Person-chipping exclusion preserved: no people-mention segment.
    expect(segs.some((s) => s.type === "mention")).toBe(false)
    // The agent token is recognized as its own agent segment (the engine seam).
    const agent = segs.find((s) => s.type === "agent")
    expect(agent).toBeTruthy()
    expect(agent && agent.type === "agent" ? agent.label.toLowerCase() : null).toBe("sprntly")
    // The rest of the message survives as trailing text.
    expect(segs.map((s) => (s.type === "text" ? s.value : "")).join("")).toContain("please help")
  })

  it("empty content yields a single empty text segment", () => {
    expect(parseMentionChips("")).toEqual([{ type: "text", value: "" }])
  })
})

describe("isEmailNeedle", () => {
  it("recognises bare emails and rejects names", () => {
    expect(isEmailNeedle("jane@acme.com")).toBe(true)
    expect(isEmailNeedle("Fortune")).toBe(false)
    expect(isEmailNeedle("@Fortune")).toBe(false)
  })
})
