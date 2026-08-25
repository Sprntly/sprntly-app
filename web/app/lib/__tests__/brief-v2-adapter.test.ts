import { describe, expect, it } from "vitest"
import type { Brief, Insight } from "../api"
import {
  briefToBriefV2State,
  companyLabel,
  humanizeSource,
  orderPoolForTypes,
  selectFindingsForTypes,
} from "../brief-v2-adapter"

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

describe("humanizeSource", () => {
  it("maps known signal source_types to friendly labels", () => {
    expect(humanizeSource("pm_manual")).toBe("PM notes")
    expect(humanizeSource("customer_voice")).toBe("Customer voice")
    expect(humanizeSource("project_mgmt")).toBe("Project mgmt")
    expect(humanizeSource("revenue")).toBe("Revenue")
    expect(humanizeSource("corpus_doc")).toBe("Documents")
  })
  it("de-underscores + sentence-cases unknown tokens", () => {
    expect(humanizeSource("foo_bar")).toBe("Foo bar")
    expect(humanizeSource("ANALYTICS")).toBe("Analytics") // case-insensitive
  })
  it("leaves already-friendly names readable", () => {
    expect(humanizeSource("HubSpot")).toBe("HubSpot")
    expect(humanizeSource("  Zendesk  ")).toBe("Zendesk")
  })
})

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

  it("falls back to the LEAD, not highest confidence, when no headline is flagged", () => {
    // Changed 2026-08-05. The hero is the lead of the composed order; the
    // backend ranks and slices the top 3, and re-sorting by confidence here
    // put the browser out of step with the emailed and Slacked brief, which
    // both render insights[0] first. B has the highest confidence and is
    // deliberately NOT the hero.
    const insights = [
      makeInsight({ tag: "something_broken", title: "A", confidence: 0.4 }),
      makeInsight({ tag: "something_better", title: "B", confidence: 0.9 }),
      makeInsight({ tag: "something_new", title: "C", confidence: 0.6 }),
    ]
    const out = briefToBriefV2State(makeBrief(insights))
    expect(out.hero?.title).toBe("A")
  })

  it("falls back to the LEAD when two insights are flagged", () => {
    // An ambiguous flag is no signal at all, so the composed order decides.
    const insights = [
      makeInsight({ tag: "something_broken", title: "A", confidence: 0.4 }),
      makeInsight({ tag: "something_better", title: "B", confidence: 0.9 }),
    ]
    ;(insights[0] as unknown as { is_headline: boolean }).is_headline = true
    ;(insights[1] as unknown as { is_headline: boolean }).is_headline = true
    const out = briefToBriefV2State(makeBrief(insights))
    expect(out.hero?.title).toBe("A")
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
    // A leads (composed order), so it is the hero and carries fix-1.
    expect(out.hero?.detailKey).toBe("fix-1")
    // The rest keep their own tag-rank keys, unchanged by which one is hero:
    // B is the second `something_broken`, C the first `something_better`.
    expect(out.supporting.map((s) => s.detailKey)).toEqual(["fix-2", "double-1"])
    // Parity with the v1 adapter is a property of the KEY, not of the hero
    // pick: v1's briefToDetailMap assigns `${tagType}-${rankWithinTag}` to
    // every insight independently. Changing which finding is hero re-labels
    // no key, so View-evidence routing is unaffected — assert the full set.
    const allKeys = [out.hero?.detailKey, ...out.supporting.map((s) => s.detailKey)]
    expect(new Set(allKeys)).toEqual(new Set(["fix-1", "fix-2", "double-1"]))
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

  const SKILL_BODY =
    "A checkout failure has been live three weeks. It is costing about $2.2M a " +
    "year across 2.3M monthly users. Drawn from 340 support tickets, three " +
    "interviews, and a public thread."

  it("renders the skill's own card body verbatim", () => {
    expect(bodyOf({ _card: { body: SKILL_BODY } })).toBe(SKILL_BODY)
  })

  it("NEVER appends the recommendation to the body", () => {
    // The brief reports the finding; it does not prescribe the fix. The
    // recommendation stays in the payload (it seeds the PRD goal) but must not
    // reach the card the PM reads.
    const body = bodyOf({
      _card: { body: SKILL_BODY },
      subtitle: "$15k deal stalled at the deductible step.",
      recommendation: "Ship the two-tap deductible fix this sprint.",
    })
    expect(body).toBe(SKILL_BODY)
    expect(body).not.toContain("Ship the two-tap")
    expect(body).not.toContain("$15k deal stalled")
  })

  it("prefers the skill body over the subtitle when both exist", () => {
    const body = bodyOf({ _card: { body: SKILL_BODY }, subtitle: "A shorter teaser." })
    expect(body).toBe(SKILL_BODY)
  })

  it("falls back to the subtitle ALONE on a legacy brief with no _card", () => {
    const body = bodyOf({
      _card: undefined,
      subtitle: "$15k deal stalled at the deductible step.",
      recommendation: "Ship the two-tap fix.",
    })
    expect(body).toBe("$15k deal stalled at the deductible step.")
    expect(body).not.toContain("Ship the two-tap fix")
  })

  it("falls back to the subtitle when _card exists but carries no body", () => {
    const body = bodyOf({
      _card: { type: "reliability", body: "   " },
      subtitle: "Three signals converge on checkout.",
    })
    expect(body).toBe("Three signals converge on checkout.")
  })

  it("renders a long skill body IN FULL without a mid-word cut", () => {
    const long =
      "Checkout abandonment hit 57% at the deductible step, up from 41% last " +
      "quarter, putting an estimated $2.3M of annualized recurring revenue at " +
      "risk across 2.3M monthly active users on the flagship account. Drawn " +
      "from 340 support tickets, three interviews, and one public thread"
    const body = bodyOf({ _card: { body: long } })
    expect(body).toBe(long)
    expect(body.endsWith("…")).toBe(false)
    expect(body.endsWith("one public thread")).toBe(true)
  })

  it("only truncates pathologically long text, and never mid-word", () => {
    // Build a >900-char body out of whole sentences.
    const sentence = "The deductible step is the single biggest drop-off point. "
    const long = sentence.repeat(20).trim() // ~1140 chars, all whole words
    const body = bodyOf({ _card: { body: long } })
    expect(body.length).toBeLessThanOrEqual(902) // cap + trailing " …"
    expect(body.endsWith("…")).toBe(true)
    // Truncation lands on a sentence boundary, not mid-word: drop the trailing
    // " …" and confirm the kept text closes a whole sentence/word.
    const kept = body.replace(/\s*…$/, "")
    expect(kept.endsWith("point.")).toBe(true)
    expect(long.startsWith(kept)).toBe(true)
  })

  it("falls back to headline then title when the body and subtitle are empty", () => {
    expect(
      bodyOf({ _card: { body: "" }, subtitle: "", recommendation: "", headline: "Hero line" }),
    ).toBe("Hero line")
    expect(
      bodyOf({
        _card: undefined,
        subtitle: "",
        recommendation: "",
        headline: "",
        title: "Just a title",
      }),
    ).toBe("Just a title")
  })
})

describe("briefToBriefV2State — top-insights skill taxonomy", () => {
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

  it("carries source chips onto fromSources, humanized, blanks dropped", () => {
    const state = briefToBriefV2State(
      makeBrief([
        makeInsight({
          tag: "something_broken",
          title: "A churn signal",
          // raw signal source_type tokens + a blank
          _card: { type: "retention", sources: ["customer_voice", "pm_manual", "revenue", "  "] },
        }),
        makeInsight({ tag: "something_new", title: "Second finding" }),
      ]),
    )
    // Honest provenance: raw source_types are humanized; blanks dropped.
    expect(state.hero!.fromSources).toEqual(["Customer voice", "PM notes", "Revenue"])
    // Legacy insight with no _card → no source chips (no fabricated convergence).
    expect(state.supporting[0].fromSources).toEqual([])
  })
})

describe("briefToBriefV2State — ledger held-back line + updated chip (phase 2B)", () => {
  it("compresses _backlog into one figure-light line with per-reason counts", () => {
    const brief: Brief = {
      ...makeBrief([makeInsight({ tag: "something_broken" })]),
      _backlog: [
        { theme_id: "t1", theme_label: "Billing outage cluster", reason: "carried" },
        { theme_id: "t2", theme_label: "B", reason: "carried" },
        { theme_id: "t3", theme_label: "C", reason: "deferred", deferred_until: "2026-08-03T06:00:00Z" },
        { theme_id: "t4", theme_label: "D", reason: "in_progress" },
      ],
    }
    const line = briefToBriefV2State(brief).heldBackLine!
    expect(line).toContain("2 unchanged since last surfaced")
    // Local-timezone rendering of 2026-08-03T06:00Z: Aug 2 or 3.
    expect(line).toMatch(/1 deferred \(back Aug [23]\)/)
    expect(line).toContain("1 already in progress")
    // Counts only — no theme labels leak into the line.
    expect(line).not.toContain("Billing outage cluster")
  })

  it("labels sibling hold-backs distinctly from the user's own actions", () => {
    const brief: Brief = {
      ...makeBrief([makeInsight({ tag: "something_broken" })]),
      _backlog: [
        { theme_id: "t1", theme_label: "A", reason: "deferred", deferred_until: "2026-08-03T06:00:00Z" },
        { theme_id: "t2", theme_label: "B", reason: "sibling_deferred" },
        { theme_id: "t3", theme_label: "C", reason: "sibling_dismissed" },
      ],
    }
    const line = briefToBriefV2State(brief).heldBackLine!
    expect(line).toContain("1 held with a deferred finding on the same topic")
    expect(line).toContain("1 held with a dismissed finding on the same topic")
  })

  it("renders no line when _backlog is absent or empty (pre-ledger briefs)", () => {
    expect(briefToBriefV2State(makeBrief([makeInsight({ tag: "something_broken" })])).heldBackLine).toBeNull()
    expect(
      briefToBriefV2State({ ...makeBrief([makeInsight({ tag: "something_broken" })]), _backlog: [] }).heldBackLine,
    ).toBeNull()
  })

  it("threads _card.state onto the finding as skillState ('updated' only)", () => {
    const updated = makeInsight({ tag: "something_broken", _card: { type: "reliability", state: "updated" } })
    const fresh = makeInsight({ tag: "something_better", _card: { type: "growth", state: "new" } })
    const out = briefToBriefV2State(makeBrief([updated, fresh]))
    const all = [out.hero!, ...out.supporting]
    expect(all.some((f) => f.skillState === "updated")).toBe(true)
    expect(all.filter((f) => f.skillState === "updated")).toHaveLength(1)
  })
})

describe("orderPoolForTypes", () => {
  // makeInsight doesn't thread insight_types (the filter tests use a leaner
  // fixture for that); attach it after the fact so these tests still get the
  // full card shape briefToBriefV2State-adjacent helpers expect.
  const withTypes = (insight: Insight, types: string[]): Insight => ({
    ...insight,
    insight_types: types,
  })

  it("stable-partitions matches before non-matches, preserving relative order within each group", () => {
    const a = withTypes(makeInsight({ tag: "something_broken", title: "A" }), ["top_problems"])
    const b = withTypes(makeInsight({ tag: "something_broken", title: "B" }), ["competitor_moves"])
    const c = withTypes(makeInsight({ tag: "something_broken", title: "C" }), ["top_problems"])
    const d = withTypes(makeInsight({ tag: "something_broken", title: "D" }), ["build_priorities"])
    const e = withTypes(makeInsight({ tag: "something_broken", title: "E" }), ["top_problems"])
    const ordered = orderPoolForTypes([a, b, c, d, e], ["top_problems"])
    expect(ordered.map((i) => i.title)).toEqual(["A", "C", "E", "B", "D"])
  })

  it("matches on any intersecting type, not just an exact single-type match", () => {
    const a = withTypes(makeInsight({ tag: "something_broken", title: "A" }), ["competitor_moves"])
    const b = withTypes(makeInsight({ tag: "something_broken", title: "B" }), [
      "top_problems",
      "competitor_moves",
    ])
    const c = withTypes(makeInsight({ tag: "something_broken", title: "C" }), ["top_problems"])
    const ordered = orderPoolForTypes([a, b, c], ["top_problems"])
    expect(ordered.map((i) => i.title)).toEqual(["B", "C", "A"])
  })

  it("returns the input unchanged (same order, same reference) when there is no selection", () => {
    const insights = [
      withTypes(makeInsight({ tag: "something_broken", title: "A" }), ["top_problems"]),
      withTypes(makeInsight({ tag: "something_broken", title: "B" }), ["competitor_moves"]),
    ]
    expect(orderPoolForTypes(insights, [])).toBe(insights)
  })

  it("leaves order unchanged when nothing matches the selection", () => {
    const a = withTypes(makeInsight({ tag: "something_broken", title: "A" }), ["competitor_moves"])
    const b = withTypes(makeInsight({ tag: "something_broken", title: "B" }), ["build_priorities"])
    expect(orderPoolForTypes([a, b], ["top_problems"]).map((i) => i.title)).toEqual(["A", "B"])
  })

  it("is wired into selectFindingsForTypes: matched pool findings render in the reordered sequence", () => {
    // Multiple selected types spread across the pool — selectFindingsForTypes
    // routes the matched-only result through orderPoolForTypes, so the picked
    // findings come out in the same order the reorder would produce (best-first
    // pool order, restricted to matches).
    const a = withTypes(makeInsight({ tag: "something_broken", title: "A", confidence: 0.9 }), [
      "top_problems",
    ])
    const b = withTypes(makeInsight({ tag: "something_broken", title: "B", confidence: 0.85 }), [
      "build_priorities",
    ])
    const c = withTypes(makeInsight({ tag: "something_broken", title: "C", confidence: 0.8 }), [
      "top_problems",
    ])
    const d = withTypes(makeInsight({ tag: "something_broken", title: "D", confidence: 0.7 }), [
      "competitor_moves",
    ])
    const brief: Brief = {
      id: 1,
      company: "acme",
      generated_at: "2026-07-26T00:00:00Z",
      week_label: "w",
      summary_headline: "H",
      insights: [a, b, c],
      _pool: [a, b, c, d],
    }
    const picked = selectFindingsForTypes(brief, ["top_problems", "build_priorities"]).map((i) => i.title)
    // Matches A, B, C (pool order) — D (competitor_moves) never matched, so it
    // never enters the reordered/matched result.
    expect(picked).toEqual(["A", "B", "C"])
  })
})
