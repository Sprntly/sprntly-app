// @vitest-environment node
//
// Registry + search logic for the public docs site. All content is hardcoded
// (no database), so these tests double as a structural guard: adding a new doc
// with a duplicate slug, a duplicate section id, or an empty body fails here
// before it can ship a broken /docs/<slug> page or a dead table-of-contents
// anchor.
import { describe, it, expect } from "vitest"
import {
  DOCS,
  getDoc,
  docsByCategory,
  stripMarkdown,
  searchDocs,
} from "../index"

describe("docs registry integrity", () => {
  it("ships at least the How-To Guide", () => {
    expect(DOCS.length).toBeGreaterThan(0)
    expect(getDoc("sprntly-how-to-guide")).toBeDefined()
  })

  it("has unique, url-safe slugs", () => {
    const slugs = DOCS.map((d) => d.slug)
    expect(new Set(slugs).size).toBe(slugs.length)
    for (const slug of slugs) {
      expect(slug).toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)*$/)
    }
  })

  it("every doc has a title, description, category, and sections", () => {
    for (const doc of DOCS) {
      expect(doc.title.trim()).not.toBe("")
      expect(doc.description.trim()).not.toBe("")
      expect(doc.category.trim()).not.toBe("")
      expect(doc.sections.length).toBeGreaterThan(0)
    }
  })

  it("section ids are unique within a doc and non-empty bodies", () => {
    for (const doc of DOCS) {
      const ids = doc.sections.map((s) => s.id)
      expect(new Set(ids).size).toBe(ids.length)
      for (const s of doc.sections) {
        expect(s.id).toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)*$/)
        expect(s.title.trim()).not.toBe("")
        expect(s.body.trim()).not.toBe("")
      }
    }
  })
})

describe("getDoc", () => {
  it("resolves a known slug", () => {
    expect(getDoc("sprntly-how-to-guide")?.title).toBe("How-To Guide")
  })

  it("returns undefined for an unknown slug", () => {
    expect(getDoc("does-not-exist")).toBeUndefined()
  })
})

describe("docsByCategory", () => {
  it("groups docs under their category, preserving registry order", () => {
    const groups = docsByCategory()
    const flat = groups.flatMap((g) => g.docs)
    // Every doc appears exactly once across all groups.
    expect(flat.length).toBe(DOCS.length)
    // Categories are distinct.
    const cats = groups.map((g) => g.category)
    expect(new Set(cats).size).toBe(cats.length)
    // The How-To Guide sits under "Guides".
    const guides = groups.find((g) => g.category === "Guides")
    expect(guides?.docs.some((d) => d.slug === "sprntly-how-to-guide")).toBe(true)
  })
})

describe("stripMarkdown", () => {
  it("drops inline code backticks but keeps the token", () => {
    expect(stripMarkdown("call `list_tickets` first")).toContain("list_tickets")
    expect(stripMarkdown("call `list_tickets` first")).not.toContain("`")
  })

  it("keeps link text, drops the URL and brackets", () => {
    const out = stripMarkdown("see [the guide](https://sprntly.ai/docs)")
    expect(out).toContain("the guide")
    expect(out).not.toContain("https://sprntly.ai/docs")
    expect(out).not.toMatch(/[[\]()]/)
  })

  it("flattens table pipes and heading/emphasis punctuation", () => {
    const out = stripMarkdown("| **Tool** | What it does |\n### Step 1")
    expect(out).not.toContain("|")
    expect(out).not.toContain("#")
    expect(out).not.toContain("*")
    expect(out).toContain("Tool")
    expect(out).toContain("Step 1")
  })
})

describe("searchDocs", () => {
  it("returns nothing for an empty query", () => {
    expect(searchDocs("")).toEqual([])
    expect(searchDocs("   ")).toEqual([])
  })

  it("finds a section by a word in its body", () => {
    const results = searchDocs("prototype")
    expect(results.length).toBeGreaterThan(0)
    expect(results.every((r) => r.slug === "sprntly-how-to-guide")).toBe(true)
    expect(results.some((r) => /prototype/i.test(r.sectionTitle))).toBe(true)
  })

  it("matches content inside a code-formatted token (the MCP tools table)", () => {
    const results = searchDocs("list_tickets")
    expect(results.length).toBeGreaterThan(0)
    expect(results.some((r) => r.sectionId === "mcp")).toBe(true)
    // The snippet is plain text (markdown stripped).
    expect(results[0].snippet).not.toContain("`")
  })

  it("is case-insensitive and requires every term (AND semantics)", () => {
    const both = searchDocs("MCP token")
    expect(both.some((r) => r.sectionId === "mcp")).toBe(true)
    // A term that appears nowhere alongside the others yields no match.
    expect(searchDocs("mcp zzzznotarealword")).toEqual([])
  })

  it("returns a bounded, well-formed result shape", () => {
    const results = searchDocs("the", 5)
    expect(results.length).toBeLessThanOrEqual(5)
    for (const r of results) {
      expect(getDoc(r.slug)).toBeDefined()
      expect(getDoc(r.slug)!.sections.some((s) => s.id === r.sectionId)).toBe(true)
      expect(r.docTitle.trim()).not.toBe("")
      expect(r.sectionTitle.trim()).not.toBe("")
    }
  })
})
