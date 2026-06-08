import { describe, expect, it } from "vitest"
import {
  dedupeFiles,
  generateSlug,
  GEN_STAGES,
  progressForElapsed,
  stageForElapsed,
  suggestedSlug,
} from "../onboard-helpers"

const SLUG_FORMAT = /^[a-z0-9][a-z0-9_-]{1,62}$/

describe("generateSlug", () => {
  it("always matches the backend slug format over many iterations", () => {
    for (let i = 0; i < 5000; i++) {
      const slug = generateSlug()
      expect(slug).toMatch(SLUG_FORMAT)
    }
  })

  it("produces tokens within the 2-63 char bound", () => {
    for (let i = 0; i < 1000; i++) {
      const slug = generateSlug()
      expect(slug.length).toBeGreaterThanOrEqual(2)
      expect(slug.length).toBeLessThanOrEqual(63)
    }
  })

  it("starts with an alphanumeric character", () => {
    for (let i = 0; i < 1000; i++) {
      expect(generateSlug()[0]).toMatch(/[a-z0-9]/)
    }
  })

  it("is unique across many calls (collision resistant)", () => {
    const seen = new Set<string>()
    const N = 10000
    for (let i = 0; i < N; i++) seen.add(generateSlug())
    expect(seen.size).toBe(N)
  })

  it("varies per call (not name-derived, not constant)", () => {
    expect(generateSlug()).not.toBe(generateSlug())
  })
})

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
