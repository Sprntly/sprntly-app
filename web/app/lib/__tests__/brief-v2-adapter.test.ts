import { describe, expect, it } from "vitest"
import type { Brief, Insight } from "../api"
import { briefToBriefV2State, companyLabel } from "../brief-v2-adapter"

function makeInsight(overrides: Partial<Insight> & { tag: Insight["tag"] }): Insight {
  return {
    tag: overrides.tag,
    type: overrides.type,
    accent: overrides.accent,
    _card: overrides._card,
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

describe("companyLabel", () => {
  it("prefers the backend display name over the dataset slug", () => {
    expect(companyLabel({ company: "cgfwwhyn3bfl", company_name: "Acme Corp" })).toBe(
      "Acme Corp",
    )
  })

  it("falls back to a prettified slug when no display name exists (demo datasets)", () => {
    expect(companyLabel({ company: "asurion", company_name: null })).toBe("Asurion")
    expect(companyLabel({ company: "asurion" })).toBe("Asurion")
  })

  it("ignores a blank display name", () => {
    expect(companyLabel({ company: "asurion", company_name: "  " })).toBe("Asurion")
  })
})

describe("briefToBriefV2State", () => {
  it("uses company_name for the rendered company label when present", () => {
    const brief = { ...makeBrief([]), company: "cgfwwhyn3bfl", company_name: "Acme Corp" }
    expect(briefToBriefV2State(brief).company).toBe("Acme Corp")
  })

  it("returns the empty state when there are no insights", () => {
    const out = briefToBriefV2State(makeBrief([]))
    expect(out.hero).toBeNull()
    expect(out.supporting).toEqual([])
    expect(out.kpiTiles).toEqual([])
    expect(out.company).toBe("Asurion")
  })

  it("threads _insufficient_evidence / _empty_reason onto the empty state", () => {
    const brief = {
      ...makeBrief([]),
      _insufficient_evidence: true,
      _empty_reason: "Only 1 connected source",
    }
    const out = briefToBriefV2State(brief)
    expect(out.insufficientEvidence).toBe(true)
    expect(out.emptyReason).toBe("Only 1 connected source")
  })

  it("defaults the evidence-gate fields to false/null for a normal brief", () => {
    const out = briefToBriefV2State(makeBrief([makeInsight({ tag: "something_broken" })]))
    expect(out.insufficientEvidence).toBe(false)
    expect(out.emptyReason).toBeNull()
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

  it("builds a KPI strip from the hero's first two metrics — no source count tile", () => {
    const out = briefToBriefV2State(
      makeBrief([makeInsight({ tag: "something_broken", title: "X" })]),
    )
    expect(out.kpiTiles.length).toBeGreaterThanOrEqual(1)
    expect(out.kpiTiles.length).toBeLessThanOrEqual(2)
    expect(out.kpiTiles[0].value).toBe("$143M/yr")
    expect(out.kpiTiles.map((t) => t.label)).not.toContain("Sources this week")
  })

  it("surfaces summary_headline as the headline string", () => {
    const out = briefToBriefV2State(
      makeBrief([makeInsight({ tag: "something_broken", title: "X" })]),
    )
    expect(out.headline).toBe("Three findings this week")
  })
})

describe("briefToBriefV2State — card body (bodyFor)", () => {
  function bodyOf(overrides: Partial<Insight>): string {
    const out = briefToBriefV2State(
      makeBrief([
        makeInsight({ tag: "something_broken", title: "X", ...overrides }),
      ]),
    )
    return out.hero?.body ?? ""
  }

  it("joins a non-terminated subtitle to the recommendation with an em-dash", () => {
    const body = bodyOf({
      subtitle: "$15k deal stalled, root cause deductible step",
      recommendation: "Ship the two-tap deductible fix this sprint",
    })
    expect(body).toBe(
      "$15k deal stalled, root cause deductible step — " +
        "Ship the two-tap deductible fix this sprint",
    )
    // no bare-space run-on between the teaser and the imperative
    expect(body).not.toContain("step Ship")
  })

  it("uses a plain space when the subtitle already ends a sentence", () => {
    const body = bodyOf({
      subtitle: "$15k deal stalled at the deductible step.",
      recommendation: "Ship the two-tap fix.",
    })
    expect(body).toBe(
      "$15k deal stalled at the deductible step. Ship the two-tap fix.",
    )
    expect(body).not.toContain(" — ")
  })

  it("treats a colon-terminated subtitle as already-punctuated (plain space)", () => {
    const body = bodyOf({
      subtitle: "Three signals converge on checkout:",
      recommendation: "Fix the deductible step.",
    })
    expect(body).toBe("Three signals converge on checkout: Fix the deductible step.")
  })

  it("renders a long subtitle+recommendation IN FULL without a mid-word cut", () => {
    const subtitle =
      "Checkout abandonment hit 57% at the deductible step, up from 41% last " +
      "quarter, putting an estimated $2.3M of annualized recurring revenue at " +
      "risk across 2.3M monthly active users on the flagship account"
    const recommendation =
      "Ship the redesigned two-tap deductible flow this sprint and instrument " +
      "step-level drop-off so the team can confirm recovery within two weeks"
    const body = bodyOf({ subtitle, recommendation })
    // Full text present — nothing truncated, no ellipsis in the common case.
    expect(body).toContain(subtitle)
    expect(body).toContain(recommendation)
    expect(body.endsWith("…")).toBe(false)
    // Does not end on a chopped partial word.
    expect(body.endsWith("within two weeks")).toBe(true)
  })

  it("only truncates pathologically long text, and never mid-word", () => {
    // Build a >900-char body out of whole sentences.
    const sentence = "The deductible step is the single biggest drop-off point. "
    const subtitle = sentence.repeat(20).trim() // ~1140 chars, all whole words
    const body = bodyOf({ subtitle, recommendation: "" })
    expect(body.length).toBeLessThanOrEqual(902) // cap + trailing " …"
    expect(body.endsWith("…")).toBe(true)
    // Truncation lands on a sentence boundary, not mid-word: drop the trailing
    // " …" and confirm the kept text closes a whole sentence/word.
    const kept = body.replace(/\s*…$/, "")
    expect(kept.endsWith("point.")).toBe(true)
    // And every retained sentence is a complete copy of the source sentence.
    expect(subtitle.startsWith(kept.replace(/\.$/, "."))).toBe(true)
  })

  it("falls back to headline then title when subtitle and recommendation are empty", () => {
    expect(bodyOf({ subtitle: "", recommendation: "", headline: "Hero line" })).toBe(
      "Hero line",
    )
    expect(
      bodyOf({ subtitle: "", recommendation: "", headline: "", title: "Just a title" }),
    ).toBe("Just a title")
  })
})

describe("briefToBriefV2State — weekly-brief skill taxonomy", () => {
  it("maps each card's skill type/label and derives accent from TYPE (not the card's accent)", () => {
    // _card.accent is deliberately the wrong (retention rose) hex for a
    // competitive card — the adapter must derive the ochre from the type.
    const state = briefToBriefV2State(
      makeBrief([
        makeInsight({
          tag: "something_broken",
          title: "Rival shipped NL search — 3 deals lost",
          _card: {
            type: "competitive",
            accent: "#b23b52",
            ctas: [
              { label: "Draft PRD", style: "primary" },
              { label: "Generate prototype", style: "ghost" },
            ],
          },
        }),
        makeInsight({ tag: "something_new", title: "Second finding" }),
      ]),
    )
    const hero = state.hero!
    expect(hero.skillType).toBe("competitive")
    expect(hero.skillLabel).toBe("Competitive")
    expect(hero.skillAccent).toBe("#b07a2e") // ochre from type, NOT the rose accent
    expect(hero.skillAccent).not.toBe("#b23b52")
    expect(hero.ctas.map((c) => c.label)).toEqual(["Draft PRD", "Generate prototype"])
  })

  it("falls back to a tag-derived type/accent for legacy briefs with no _card", () => {
    const state = briefToBriefV2State(makeBrief([makeInsight({ tag: "something_better" })]))
    const hero = state.hero!
    expect(hero.skillType).toBe("growth")
    expect(hero.skillAccent).toBe("#1a8a52")
    expect(hero.ctas).toEqual([]) // no skill card → caller falls back to default CTAs
  })
})
