import { describe, expect, it } from "vitest"
import {
  buildKpiTreePayload,
  buildKpiTreePayloadFromPicks,
  canSaveKpiTree,
  canSavePickedMetrics,
  MAX_PRIMARY_METRICS,
  REQUIRED_METRIC_PICKS,
  type SupportingMetric,
} from "../onboarding/kpiTreeApi"

function m(name: string, description = ""): SupportingMetric {
  return { name, description }
}

describe("buildKpiTreePayload", () => {
  it("carries the North Star metric + description", () => {
    const tree = buildKpiTreePayload("Weekly active clinicians", "7-day active clinicians", [
      m("Shift-handoff completion rate", "Share of handoffs completed in-app."),
    ])
    expect(tree.north_star).toEqual({
      metric: "Weekly active clinicians",
      description: "7-day active clinicians",
    })
    expect(tree.primary_metrics[0]).toEqual({
      metric: "Shift-handoff completion rate",
      description: "Share of handoffs completed in-app.",
    })
  })

  it("maps the first 4 supporting metrics to primary_metrics, remainder to secondary", () => {
    const tree = buildKpiTreePayload("Weekly active clinicians", "", [
      m("Shift-handoff completion rate"),
      m("Care plans co-authored / week"),
      m("Time-to-first-handoff"),
      m("EHR session depth"),
      m("Cross-location context views"),
    ])
    expect(tree.primary_metrics).toHaveLength(MAX_PRIMARY_METRICS)
    expect(tree.secondary_signals.map((s) => s.metric)).toEqual([
      "Cross-location context views",
    ])
  })

  it("dedupes supporting metrics and drops any equal to the North Star", () => {
    const tree = buildKpiTreePayload("Retention", "", [
      m("Activation"),
      m("activation"), // dup (case-insensitive)
      m("retention"), // same as North Star
      m("Referral"),
    ])
    expect(tree.primary_metrics.map((x) => x.metric)).toEqual(["Activation", "Referral"])
  })

  it("trims whitespace on names and descriptions; no weights/values are emitted", () => {
    const tree = buildKpiTreePayload("  NRR  ", "  net rev retention  ", [
      m("  Expansion  ", "  upsell  "),
    ])
    expect(tree.north_star).toEqual({ metric: "NRR", description: "net rev retention" })
    expect(tree.primary_metrics[0]).toEqual({ metric: "Expansion", description: "upsell" })
    // The payload only carries metric + description — no numeric fields.
    expect(Object.keys(tree.primary_metrics[0]).sort()).toEqual(["description", "metric"])
    expect("weight" in tree.primary_metrics[0]).toBe(false)
  })
})

describe("canSaveKpiTree", () => {
  it("requires a North Star and at least one named supporting metric", () => {
    expect(canSaveKpiTree("", [m("a")])).toBe(false)
    expect(canSaveKpiTree("NS", [])).toBe(false)
    expect(canSaveKpiTree("NS", [m("  ")])).toBe(false)
    expect(canSaveKpiTree("NS", [m("a")])).toBe(true)
  })
})

describe("canSavePickedMetrics — onboarding pick-exactly-3", () => {
  it("the constant is 3", () => {
    expect(REQUIRED_METRIC_PICKS).toBe(3)
  })

  it("is satisfiable ONLY with exactly 3 named picks", () => {
    expect(canSavePickedMetrics([])).toBe(false)
    expect(canSavePickedMetrics([m("a"), m("b")])).toBe(false)
    expect(canSavePickedMetrics([m("a"), m("b"), m("c")])).toBe(true)
    // a 4th pick over-fills → not satisfiable
    expect(canSavePickedMetrics([m("a"), m("b"), m("c"), m("d")])).toBe(false)
    // blanks don't count toward the 3
    expect(canSavePickedMetrics([m("a"), m("  "), m("b")])).toBe(false)
  })
})

describe("buildKpiTreePayloadFromPicks", () => {
  it("sends all 3 picks; north_star is a placeholder = the FIRST pick (server infers the real one)", () => {
    const tree = buildKpiTreePayloadFromPicks([
      m("Weekly active users", "WAU."),
      m("Day-30 retention"),
      m("Incremental revenue"),
    ])
    // north_star = first pick, NOT deduped out of primary_metrics
    expect(tree.north_star).toEqual({ metric: "Weekly active users", description: "WAU." })
    const all = [...tree.primary_metrics, ...tree.secondary_signals].map((x) => x.metric)
    expect(all).toEqual([
      "Weekly active users",
      "Day-30 retention",
      "Incremental revenue",
    ])
  })

  it("trims + dedupes (case-insensitive) and drops blanks, preserving order", () => {
    const tree = buildKpiTreePayloadFromPicks([
      m("  Retention  ", "  keep  "),
      m("retention"), // dup
      m("  "), // blank
      m("Activation"),
    ])
    const all = [...tree.primary_metrics, ...tree.secondary_signals].map((x) => x.metric)
    expect(all).toEqual(["Retention", "Activation"])
    expect(tree.primary_metrics[0]).toEqual({ metric: "Retention", description: "keep" })
  })

  it("emits an empty north_star for an empty pick list (no crash)", () => {
    const tree = buildKpiTreePayloadFromPicks([])
    expect(tree.north_star).toEqual({ metric: "", description: "" })
    expect(tree.primary_metrics).toEqual([])
  })
})
