import { describe, expect, it } from "vitest"
import type { Brief, Insight } from "../api"
import { briefToBriefV2State, selectFindingsForTypes } from "../brief-v2-adapter"

// Minimal finding: only the fields the pool filter reads (title + insight_types)
// plus the shape briefToBriefV2State needs to build a card.
function finding(title: string, insight_types: string[], confidence = 0.8): Insight {
  return {
    tag: "something_broken",
    title,
    subtitle: `${title} sub`,
    metrics: [{ label: "x", value: "1" }],
    domain: "retention",
    subdomain: "",
    confidence,
    headline: title,
    why_this_ranks: [],
    why_alternatives_dont_hold: [],
    recommendation: `Do ${title}`,
    impact_math: [],
    verification_metrics: [],
    convergence: [],
    user_quotes: [],
    chart_hints: [],
    insight_types,
  } as Insight
}

// A brief whose _pool is a superset of the canonical top-3 insights.
function briefWithPool(top: Insight[], pool: Insight[]): Brief {
  return {
    id: 1,
    company: "acme",
    generated_at: "2026-07-23T00:00:00Z",
    week_label: "Week of July 23, 2026",
    summary_headline: "H",
    insights: top,
    _pool: pool,
  }
}

const A = finding("A reliability", ["reliability_signals"], 0.9)
const B = finding("B feedback", ["user_feedback"], 0.85)
const C = finding("C problems", ["top_problems"], 0.8)
const D = finding("D competitive", ["competitor_moves"], 0.7)
const E = finding("E wins", ["wins"], 0.6)
const F = finding("F build", ["build_priorities"], 0.55)

describe("selectFindingsForTypes", () => {
  const brief = briefWithPool([A, B, C], [A, B, C, D, E, F])

  it("no filter → the canonical top 3, untouched", () => {
    expect(selectFindingsForTypes(brief, []).map((i) => i.title)).toEqual([
      "A reliability",
      "B feedback",
      "C problems",
    ])
  })

  it("pulls a match up from below the top 3 (rank 4) into view", () => {
    // 'competitor_moves' is only in D, which sits at rank 4 in the pool.
    expect(selectFindingsForTypes(brief, ["competitor_moves"]).map((i) => i.title)).toEqual([
      "D competitive",
    ])
  })

  it("keeps pool order (best-first) and caps at 3 across multiple types", () => {
    const picked = selectFindingsForTypes(
      brief,
      ["wins", "build_priorities", "reliability_signals", "top_problems"],
    ).map((i) => i.title)
    // Matches A, C, E, F in pool order → capped to the first 3.
    expect(picked).toEqual(["A reliability", "C problems", "E wins"])
  })

  it("matches when a finding carries the type as one of two", () => {
    const multi = finding("multi", ["reliability_signals", "competitor_moves"])
    const b = briefWithPool([multi], [multi])
    expect(selectFindingsForTypes(b, ["competitor_moves"]).map((i) => i.title)).toEqual(["multi"])
  })

  it("falls back to the top 3 when a filter matches nothing this week", () => {
    const noComp = briefWithPool([A, B, C], [A, B, C]) // no competitor finding at all
    expect(selectFindingsForTypes(noComp, ["competitor_moves"]).map((i) => i.title)).toEqual([
      "A reliability",
      "B feedback",
      "C problems",
    ])
  })

  it("legacy brief with no _pool filters the top-3 insights directly", () => {
    const legacy: Brief = {
      id: 2,
      company: "acme",
      generated_at: "2026-07-23T00:00:00Z",
      week_label: "w",
      summary_headline: "H",
      insights: [A, B, C], // no _pool
    }
    expect(selectFindingsForTypes(legacy, ["user_feedback"]).map((i) => i.title)).toEqual([
      "B feedback",
    ])
  })
})

describe("briefToBriefV2State with a filter", () => {
  it("renders the filtered finding as the hero", () => {
    const brief = briefWithPool([A, B, C], [A, B, C, D, E, F])
    const state = briefToBriefV2State(brief, ["competitor_moves"])
    expect(state.hero?.title).toBe("D competitive")
    expect(state.supporting).toHaveLength(0)
  })

  it("no filter renders the top-3 hero as before", () => {
    const brief = briefWithPool([A, B, C], [A, B, C, D, E, F])
    const state = briefToBriefV2State(brief)
    // A has the highest confidence (0.9) so it's the hero by fallback.
    expect(state.hero?.title).toBe("A reliability")
    expect(state.supporting.map((s) => s.title)).toEqual(["B feedback", "C problems"])
  })
})
