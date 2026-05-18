import { describe, expect, it } from "vitest"
import {
  dedupeFiles,
  GEN_STAGES,
  progressForElapsed,
  stageForElapsed,
  suggestedSlug,
} from "../onboard-helpers"

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

describe("stageForElapsed", () => {
  it("reads at the very start", () => {
    expect(stageForElapsed(0)).toBe("reading")
  })
  it("still reading just under the cutoff", () => {
    expect(stageForElapsed(14_999)).toBe("reading")
  })
  it("drafts at and past 15s", () => {
    expect(stageForElapsed(15_000)).toBe("drafting")
    expect(stageForElapsed(45_000)).toBe("drafting")
  })
  it("polishes at and past 60s", () => {
    expect(stageForElapsed(60_000)).toBe("polishing")
    expect(stageForElapsed(120_000)).toBe("polishing")
  })
  it("GEN_STAGES ordering matches stage progression", () => {
    expect(GEN_STAGES.map((s) => s.id)).toEqual(["reading", "drafting", "polishing"])
  })
})

describe("progressForElapsed", () => {
  it("is 0 at t=0", () => {
    expect(progressForElapsed(0)).toBe(0)
  })
  it("is strictly increasing", () => {
    expect(progressForElapsed(5_000)).toBeLessThan(progressForElapsed(15_000))
    expect(progressForElapsed(15_000)).toBeLessThan(progressForElapsed(60_000))
  })
  it("is capped at 0.97 — never claims done before backend says so", () => {
    expect(progressForElapsed(10 * 60 * 1000)).toBeLessThanOrEqual(0.97)
    expect(progressForElapsed(60 * 60 * 1000)).toBeLessThanOrEqual(0.97)
  })
  it("is past 50% by 30s (one half-life)", () => {
    expect(progressForElapsed(30_000)).toBeGreaterThanOrEqual(0.5)
  })
})
