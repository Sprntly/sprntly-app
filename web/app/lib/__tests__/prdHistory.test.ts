import { describe, it, expect } from "vitest"
import { mergeHistory, type PrdSnapshot, type PrdGeneration } from "../prdHistory"

const snap = (id: number, n: number, saved_at: string): PrdSnapshot => ({
  id, prd_id: 100, version_number: n, title: `v${n}`, payload_md: "", saved_by: "a@b.com", saved_at,
})
const gen = (id: number, generated_at: string): PrdGeneration => ({
  id, title: `gen ${id}`, status: "ready", generated_at, insight_index: 0,
})

describe("mergeHistory", () => {
  it("merges snapshots + generations sorted newest first", () => {
    const out = mergeHistory(
      [snap(1, 2, "2026-06-10T10:00:00Z"), snap(2, 1, "2026-06-08T10:00:00Z")],
      [gen(100, "2026-06-11T10:00:00Z"), gen(99, "2026-06-07T10:00:00Z")],
      100,
    )
    expect(out.map((e) => e.kind)).toEqual(["generation", "snapshot", "snapshot", "generation"])
    expect(out[0].ts).toBeGreaterThan(out[3].ts)
  })

  it("flags the current generation, not the others", () => {
    const out = mergeHistory([], [gen(100, "2026-06-11T10:00:00Z"), gen(99, "2026-06-07T10:00:00Z")], 100)
    const cur = out.find((e) => e.kind === "generation" && e.generation.id === 100)
    const old = out.find((e) => e.kind === "generation" && e.generation.id === 99)
    expect(cur && cur.kind === "generation" && cur.isCurrent).toBe(true)
    expect(old && old.kind === "generation" && old.isCurrent).toBe(false)
  })

  it("handles empty inputs", () => {
    expect(mergeHistory([], [], 1)).toEqual([])
  })

  it("tolerates unparseable timestamps (ts=0, sorts last)", () => {
    const out = mergeHistory([snap(1, 1, "not-a-date")], [gen(100, "2026-06-11T10:00:00Z")], 100)
    expect(out[0].kind).toBe("generation")
    expect(out[1].ts).toBe(0)
  })
})
