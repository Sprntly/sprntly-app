import { describe, expect, it } from "vitest"
import {
  buildKpiTreePayload,
  canSaveKpiTree,
  evenWeights,
  MAX_PRIMARY_METRICS,
} from "../onboarding/kpiTreeApi"

describe("evenWeights", () => {
  it("returns weights that sum to exactly 1.0", () => {
    for (const n of [1, 2, 3, 4, 5, 6]) {
      const w = evenWeights(n)
      expect(w.length).toBe(n)
      expect(Math.abs(w.reduce((a, b) => a + b, 0) - 1)).toBeLessThanOrEqual(0.01)
    }
  })
  it("returns [] for non-positive n", () => {
    expect(evenWeights(0)).toEqual([])
  })
})

describe("buildKpiTreePayload", () => {
  it("maps the first 4 supporting metrics to weighted primary_metrics", () => {
    const tree = buildKpiTreePayload("Weekly active clinicians", [
      "Shift-handoff completion rate",
      "Care plans co-authored / week",
      "Time-to-first-handoff",
      "EHR session depth",
      "Cross-location context views",
    ])
    expect(tree.north_star.metric).toBe("Weekly active clinicians")
    expect(tree.primary_metrics).toHaveLength(MAX_PRIMARY_METRICS)
    // remainder becomes secondary signals
    expect(tree.secondary_signals.map((s) => s.metric)).toEqual([
      "Cross-location context views",
    ])
    const sum = tree.primary_metrics.reduce((a, m) => a + m.weight, 0)
    expect(Math.abs(sum - 1)).toBeLessThanOrEqual(0.01)
  })

  it("dedupes supporting metrics and drops any equal to the North Star", () => {
    const tree = buildKpiTreePayload("Retention", [
      "Activation",
      "activation", // dup (case-insensitive)
      "retention", // same as North Star
      "Referral",
    ])
    expect(tree.primary_metrics.map((m) => m.metric)).toEqual([
      "Activation",
      "Referral",
    ])
  })

  it("trims whitespace on names", () => {
    const tree = buildKpiTreePayload("  NRR  ", ["  Expansion  "])
    expect(tree.north_star.metric).toBe("NRR")
    expect(tree.primary_metrics[0].metric).toBe("Expansion")
    expect(tree.primary_metrics[0].weight).toBe(1)
  })

  it("secondary signals default to higher_is_better", () => {
    const tree = buildKpiTreePayload("NS", ["a", "b", "c", "d", "e"])
    expect(tree.secondary_signals[0].direction).toBe("higher_is_better")
  })
})

describe("canSaveKpiTree", () => {
  it("requires a North Star and at least one supporting metric", () => {
    expect(canSaveKpiTree("", ["a"])).toBe(false)
    expect(canSaveKpiTree("NS", [])).toBe(false)
    expect(canSaveKpiTree("NS", ["  "])).toBe(false)
    expect(canSaveKpiTree("NS", ["a"])).toBe(true)
  })
})
