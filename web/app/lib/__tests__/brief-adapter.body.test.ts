import { describe, expect, it } from "vitest"
import type { Brief, Insight } from "../api"
import { briefToBriefState } from "../brief-adapter"
import { briefPreviewInsight } from "../workspace-brief"

/**
 * The brief reports the finding; it does not prescribe the fix.
 *
 * Both adapters used to build the card body as `subtitle + recommendation`,
 * which ended every card on an imperative ("Ship one MCP integration…"). The
 * body is now the top-insights skill's own `_card.body` — what's happening →
 * what's at stake → what the finding rests on — and `recommendation` never
 * reaches the reader (it seeds the PRD goal in AIBar instead).
 */

const SKILL_BODY =
  "A checkout failure has been live three weeks. It is costing about $2.2M a " +
  "year across 2.3M monthly users. Drawn from 340 support tickets, three " +
  "interviews, and a public thread."

const RECOMMENDATION = "Ship the two-tap deductible fix this sprint."

function makeInsight(overrides: Partial<Insight> = {}): Insight {
  return {
    tag: "something_broken",
    title: "Checkout is failing 1 in 6 iOS payments — $2.2M a year rides on it",
    subtitle: "$15k deal stalled at the deductible step.",
    metrics: [
      { label: "ARR at risk", value: "$143M/yr" },
      { label: "users affected", value: "2.3M/mo" },
      { label: "effort", value: "2-week sprint" },
    ],
    domain: "retention",
    subdomain: "checkout",
    confidence: 0.9,
    headline: "headline",
    why_this_ranks: [],
    why_alternatives_dont_hold: [],
    recommendation: RECOMMENDATION,
    impact_math: [],
    verification_metrics: [],
    convergence: [],
    user_quotes: [],
    chart_hints: [],
    ...overrides,
  } as Insight
}

function makeBrief(insights: Insight[]): Brief {
  return {
    id: 1,
    company: "asurion",
    generated_at: "2026-08-20T00:00:00Z",
    week_label: "Week of August 20, 2026",
    summary_headline: "headline",
    insights,
  } as Brief
}

function descOf(overrides: Partial<Insight> = {}): string {
  const state = briefToBriefState(makeBrief([makeInsight(overrides)]))
  return state.sections[0]?.findings[0]?.desc ?? ""
}

describe("brief-adapter (v1) — finding body", () => {
  it("renders the skill's card body and never the recommendation", () => {
    const desc = descOf({ _card: { body: SKILL_BODY } })
    expect(desc).toBe(SKILL_BODY)
    expect(desc).not.toContain("Ship the two-tap")
  })

  it("falls back to the subtitle ALONE when there is no _card (legacy brief)", () => {
    const desc = descOf({ _card: undefined })
    expect(desc).toBe("$15k deal stalled at the deductible step.")
    expect(desc).not.toContain(RECOMMENDATION)
  })

  it("falls back to the subtitle when _card carries no body", () => {
    expect(descOf({ _card: { type: "reliability" } })).toBe(
      "$15k deal stalled at the deductible step.",
    )
  })

  it("falls back to headline then title when body and subtitle are both empty", () => {
    expect(descOf({ _card: undefined, subtitle: "", headline: "Hero line" })).toBe(
      "Hero line",
    )
    expect(
      descOf({ _card: undefined, subtitle: "", headline: "", title: "Just a title" }),
    ).toBe("Just a title")
  })
})

describe("workspace-brief — preview teaser", () => {
  it("teases the finding, never the prescription", () => {
    const preview = briefPreviewInsight(
      makeBrief([makeInsight({ subtitle: "", _card: { body: SKILL_BODY } })]),
    )
    expect(preview?.subtitle).toBe(SKILL_BODY.slice(0, 160))
    expect(preview?.subtitle).not.toContain("Ship the two-tap")
  })

  it("prefers the subtitle when the insight has one", () => {
    const preview = briefPreviewInsight(
      makeBrief([makeInsight({ _card: { body: SKILL_BODY } })]),
    )
    expect(preview?.subtitle).toBe("$15k deal stalled at the deductible step.")
  })

  it("yields an empty teaser rather than a recommendation when both are absent", () => {
    const preview = briefPreviewInsight(
      makeBrief([makeInsight({ subtitle: "", _card: undefined })]),
    )
    expect(preview?.subtitle).toBe("")
  })
})
