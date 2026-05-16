import { describe, expect, it } from "vitest"
import { dedupeFiles, suggestedSlug } from "../onboard-helpers"

describe("suggestedSlug", () => {
  it("lowercases and replaces spaces with underscores", () => {
    expect(suggestedSlug("Acme Corp")).toBe("acme_corp")
  })
  it("collapses runs of underscores", () => {
    expect(suggestedSlug("Acme   Corp")).toBe("acme_corp")
  })
  it("strips leading and trailing underscores", () => {
    expect(suggestedSlug("  !! Acme !!  ")).toBe("acme")
  })
  it("keeps hyphens, digits, and existing underscores", () => {
    expect(suggestedSlug("ACME-Corp_2")).toBe("acme-corp_2")
  })
  it("clamps to 63 chars", () => {
    expect(suggestedSlug("a".repeat(100)).length).toBe(63)
  })
  it("returns empty string for purely-symbol input", () => {
    // Backend treats empty/short as InvalidSlug — the wizard's required
    // attribute + pattern catches it client-side first.
    expect(suggestedSlug("!!!")).toBe("")
  })
})

describe("dedupeFiles", () => {
  // dedupeFiles only reads .name and .size, so a lightweight mock is enough.
  // Avoids a jsdom dependency just to run two tests.
  function mkFile(name: string, size: number): File {
    return { name, size } as unknown as File
  }

  it("removes duplicates by (name, size)", () => {
    const a = mkFile("notes.txt", 10)
    const a2 = mkFile("notes.txt", 10)
    const b = mkFile("other.txt", 10)
    expect(dedupeFiles([a, a2, b]).map((f) => f.name)).toEqual(["notes.txt", "other.txt"])
  })

  it("treats same name but different size as distinct", () => {
    expect(dedupeFiles([mkFile("a.txt", 10), mkFile("a.txt", 20)]).length).toBe(2)
  })

  it("preserves order on first occurrence", () => {
    const a = mkFile("a.txt", 1)
    const b = mkFile("b.txt", 1)
    const a2 = mkFile("a.txt", 1)
    expect(dedupeFiles([b, a, a2]).map((f) => f.name)).toEqual(["b.txt", "a.txt"])
  })
})
