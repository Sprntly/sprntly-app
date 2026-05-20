import { describe, expect, it } from "vitest"
import type { Brief, Insight } from "../api"
import { briefToBriefV2State } from "../brief-v2-adapter"

function makeInsight(overrides: Partial<Insight> & { tag: Insight["tag"] }): Insight {
  return {
    tag: overrides.tag,
    title: overrides.title ?? "title",
    subtitle: overrides.subtitle ?? "subtitle",
    metrics: overrides.metrics ?? [
      { label: "ARR at risk", value: "$143M/yr" },
      { label: "users affected", value: "2.3M/mo" },
      { label: "effort", value: "2-week sprint" },
    ],
    domain: overrides.domain ?? "retention",
    subdomain: overrides.subdomain ?? "checkout",
    confidence: overrides.confidence ?? 0.7,
    headline: overrides.headline ?? "headline",
    why_this_ranks: [],
    why_alternatives_dont_hold: [],
    recommendation: overrides.recommendation ?? "Ship the fix",
    impact_math: [],
    verification_metrics: [],
    convergence: overrides.convergence ?? [
      { source: "Asurion analytics", signal: "57% abandon", strength: "Strong" },
      { source: "Zendesk", signal: "Top 3 reason", strength: "Moderate" },
      { source: "Reddit", signal: "Surfaced in threads", strength: "Weak" },
    ],
    user_quotes: overrides.user_quotes ?? [
      { quote: "I quit at the deductible step.", source: "Helpscout" },
    ],
    chart_hints: overrides.chart_hints ?? [
      {
        kind: "bar",
        title: "Abandonment by step",
        data: [
          { label: "Intro", value: 5 },
          { label: "Deductible", value: 57 },
          { label: "Pay", value: 12 },
        ],
      },
    ],
  } as Insight
}

function makeBrief(insights: Insight[]): Brief {
  return {
    id: 1,
    company: "asurion",
    generated_at: "2026-05-20T00:00:00Z",
    week_label: "Week of May 19, 2026",
    summary_headline: "Three findings this week",
    insights,
  }
}

describe("briefToBriefV2State", () => {
  it("returns the empty state when there are no insights", () => {
    const out = briefToBriefV2State(makeBrief([]))
    expect(out.hero).toBeNull()
    expect(out.supporting).toEqual([])
    expect(out.kpiTiles).toEqual([])
    expect(out.company).toBe("Asurion")
  })

  it("picks the LLM-flagged is_headline insight as the hero", () => {
    const insights = [
      makeInsight({ tag: "something_broken", title: "Broken A", confidence: 0.9 }),
      makeInsight({
        tag: "something_new",
        title: "New B",
        confidence: 0.5,
        // typed lookup tolerates the optional v4 field
      }),
    ]
    ;(insights[1] as unknown as { is_headline: boolean }).is_headline = true
    const out = briefToBriefV2State(makeBrief(insights))
    expect(out.hero?.title).toBe("New B")
  })

  it("falls back to highest confidence when no headline is flagged", () => {
    const insights = [
      makeInsight({ tag: "something_broken", title: "A", confidence: 0.4 }),
      makeInsight({ tag: "something_better", title: "B", confidence: 0.9 }),
      makeInsight({ tag: "something_new", title: "C", confidence: 0.6 }),
    ]
    const out = briefToBriefV2State(makeBrief(insights))
    expect(out.hero?.title).toBe("B")
  })

  it("falls back to highest confidence when two insights are flagged", () => {
    const insights = [
      makeInsight({ tag: "something_broken", title: "A", confidence: 0.4 }),
      makeInsight({ tag: "something_better", title: "B", confidence: 0.9 }),
    ]
    ;(insights[0] as unknown as { is_headline: boolean }).is_headline = true
    ;(insights[1] as unknown as { is_headline: boolean }).is_headline = true
    const out = briefToBriefV2State(makeBrief(insights))
    expect(out.hero?.title).toBe("B")
  })

  it("attaches an inline chart and quote to the hero when present", () => {
    const out = briefToBriefV2State(
      makeBrief([makeInsight({ tag: "something_broken", title: "X" })]),
    )
    expect(out.hero?.chart?.kind).toBe("bar")
    expect(out.hero?.quote?.body).toContain("deductible")
  })

  it("drops the hero quote block when no quote exists", () => {
    const out = briefToBriefV2State(
      makeBrief([
        makeInsight({
          tag: "something_broken",
          title: "X",
          user_quotes: [],
        }),
      ]),
    )
    expect(out.hero?.quote).toBeNull()
  })

  it("caps compact-card chips at 2 and surfaces the +N more pill count", () => {
    const insights = [
      makeInsight({ tag: "something_broken", title: "Hero", confidence: 0.9 }),
      makeInsight({
        tag: "something_better",
        title: "Compact",
        confidence: 0.5,
        convergence: [
          { source: "S1", signal: "x", strength: "Strong" },
          { source: "S2", signal: "x", strength: "Moderate" },
          { source: "S3", signal: "x", strength: "Weak" },
          { source: "S4", signal: "x", strength: "Weak" },
        ],
      }),
    ]
    const out = briefToBriefV2State(makeBrief(insights))
    const compact = out.supporting[0]
    expect(compact.convergence).toHaveLength(2)
    expect(compact.extraConvergenceCount).toBe(2)
  })

  it("uses the same detail key shape as the v1 adapter (tag-rank)", () => {
    const insights = [
      makeInsight({ tag: "something_broken", title: "A" }),
      makeInsight({ tag: "something_broken", title: "B" }),
      makeInsight({ tag: "something_better", title: "C", confidence: 0.99 }),
    ]
    const out = briefToBriefV2State(makeBrief(insights))
    // C has highest confidence → hero
    expect(out.hero?.detailKey).toBe("double-1")
    // The two broken insights become supporting with rank 1 and 2.
    expect(out.supporting.map((s) => s.detailKey)).toEqual(["fix-1", "fix-2"])
  })

  it("builds a KPI strip with up to three tiles drawn from real metrics", () => {
    const out = briefToBriefV2State(
      makeBrief([makeInsight({ tag: "something_broken", title: "X" })]),
    )
    expect(out.kpiTiles.length).toBeGreaterThanOrEqual(1)
    expect(out.kpiTiles.length).toBeLessThanOrEqual(3)
    expect(out.kpiTiles[0].value).toBe("$143M/yr")
  })

  it("surfaces summary_headline as the headline string", () => {
    const out = briefToBriefV2State(
      makeBrief([makeInsight({ tag: "something_broken", title: "X" })]),
    )
    expect(out.headline).toBe("Three findings this week")
  })
})
