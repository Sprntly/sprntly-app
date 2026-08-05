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

// The hero is the TOP insight the reader actually sees. With a selection
// active the list reaching the renderer is already preference-ordered (the
// backend stable-partitions the pool at generation time), so the hero must be
// its lead — otherwise the browser re-sorts by confidence and disagrees with
// the emailed and Slacked brief, which both render insights[0] first.
describe("hero pick under an active insight-type filter", () => {
  const lowFirst = finding("low but preferred", ["wins"], 0.4)
  const highSecond = finding("high and preferred", ["wins"], 0.95)

  it("leads with pool order, not the highest-confidence match", () => {
    const brief = briefWithPool([lowFirst, highSecond], [lowFirst, highSecond])
    expect(briefToBriefV2State(brief, ["wins"]).hero?.title).toBe("low but preferred")
    // ...and with NO selection the lead still wins. This is the path the
    // backend deliberately skips (it leaves the model's ranking and does not
    // rewrite is_headline when nothing matched), so a confidence fallback here
    // would reintroduce the browser/email drift on exactly that path.
    expect(briefToBriefV2State(brief, []).hero?.title).toBe("low but preferred")
  })

  it("outranks a stale is_headline flag on a demoted finding", () => {
    // A brief generated before the backend re-pointed is_headline, or one read
    // after the PM changed their selection, can carry the flag on a card the
    // current preference order no longer leads with.
    const stale = { ...highSecond, is_headline: true } as Insight
    const brief = briefWithPool([lowFirst, stale], [lowFirst, stale])
    expect(briefToBriefV2State(brief, ["wins"]).hero?.title).toBe("low but preferred")
  })

  it("keeps the model's own hero when the selection matched nothing", () => {
    // Fallback path: the list is the unfiltered top 3, so there is no
    // preference order to honour and the flagged/strongest card is the honest
    // answer rather than an arbitrary first element.
    const flagged = { ...C, is_headline: true } as Insight
    const brief = briefWithPool([A, B, flagged], [A, B, flagged])
    expect(briefToBriefV2State(brief, ["wins"]).hero?.title).toBe("C problems")
  })

  it("drives the KPI tiles off the same hero", () => {
    const brief = briefWithPool([lowFirst, highSecond], [lowFirst, highSecond])
    const state = briefToBriefV2State(brief, ["wins"])
    expect(state.hero?.title).toBe("low but preferred")
    expect(state.kpiTiles.length).toBeGreaterThan(0)
  })
})

// The pill a reader sees must name the finding in the SAME vocabulary the
// picker offers. Before 2026-08-05 it showed the skill taxonomy's own 8 types
// (Reliability, Growth, Demand, …) — "Growth" isn't even a preference type —
// so a brief gave no way to tell whether a selection had been honoured.
describe("card pill uses the preference vocabulary", () => {
  it("labels a card with its own insight type, not the skill type", () => {
    // tag `something_broken` ⇒ skill type `reliability` ⇒ old pill "Reliability".
    const f = finding("A", ["wins"])
    const state = briefToBriefV2State(briefWithPool([f], [f]), [])
    expect(state.hero?.skillLabel).toBe("Win")
    expect(state.hero?.skillAccent).toBe("#0f7d70")
    // The underlying skill type is still carried for anything that needs it.
    expect(state.hero?.skillType).toBe("reliability")
  })

  it("surfaces the selected type when the finding's PRIMARY wasn't picked", () => {
    // Primary is top_problems, but the reader asked only for user_feedback —
    // the card must say so, otherwise it can't evidence the selection.
    const f = finding("A", ["top_problems", "user_feedback"])
    const brief = briefWithPool([f], [f])
    expect(briefToBriefV2State(brief, ["user_feedback"]).hero?.skillLabel).toBe("User feedback")
    expect(briefToBriefV2State(brief, []).hero?.skillLabel).toBe("Top problem")
  })

  it("keeps the finding's PRIMARY type when both of its types were picked", () => {
    // Walking the SELECTION order instead of the finding's would let the
    // reader's first chip override every card's primary — on a real staging
    // brief that collapsed two distinct findings to the same "Top problem"
    // pill. The finding's own order wins; the selection only breaks the tie.
    const a = finding("A", ["top_problems", "reliability_signals"])
    const b = finding("B", ["build_priorities", "top_problems"])
    const sel = ["top_problems", "build_priorities", "reliability_signals"]
    const state = briefToBriefV2State(briefWithPool([a, b], [a, b]), sel)
    expect(state.hero?.skillLabel).toBe("Top problem")
    expect(state.supporting.map((s) => s.skillLabel)).toEqual(["What to build"])
  })

  it("covers every selectable type with a distinct pill", () => {
    const expected: Record<string, string> = {
      top_problems: "Top problem",
      build_priorities: "What to build",
      user_feedback: "User feedback",
      competitor_moves: "Competitor moves",
      reliability_signals: "Reliability",
      wins: "Win",
    }
    for (const [slug, label] of Object.entries(expected)) {
      const f = finding(slug, [slug])
      expect(briefToBriefV2State(briefWithPool([f], [f]), []).hero?.skillLabel).toBe(label)
    }
    // Distinct labels, so two differently-typed cards never read the same.
    expect(new Set(Object.values(expected)).size).toBe(6)
  })

  it("keeps the skill label on a LEGACY finding with no insight_types", () => {
    // 8 skill types do not map onto 6 preference slugs (retention, demand,
    // engagement, compliance have no faithful counterpart), so rather than
    // invent one we leave pre-classifier briefs exactly as they render today.
    const legacy = { ...finding("old", []), insight_types: undefined } as Insight
    const state = briefToBriefV2State(briefWithPool([legacy], [legacy]), [])
    expect(state.hero?.skillLabel).toBe("Reliability") // from tag → skill type
    expect(state.hero?.skillAccent).toBe("#c0473c")
  })

  it("labels supporting cards the same way as the hero", () => {
    const a = finding("A", ["reliability_signals"])
    const b = finding("B", ["competitor_moves"])
    const state = briefToBriefV2State(briefWithPool([a, b], [a, b]), [])
    expect(state.hero?.skillLabel).toBe("Reliability")
    expect(state.supporting.map((s) => s.skillLabel)).toEqual(["Competitor moves"])
  })
})

// PRODUCTION CALL SHAPE. `briefToContentPatch` (brief-adapter.ts) is the only
// path that builds content.briefV2, and it calls briefToBriefV2State(brief)
// with NO second argument — so every behaviour that depends on a selection has
// to source it from the payload or it is dead code in the app. These tests use
// that exact shape: one argument, selection supplied only via `_insight_prefs`.
describe("selection sourced from the brief payload (production call shape)", () => {
  function briefWithPrefs(insights: Insight[], selected: string[] | null): Brief {
    const b = briefWithPool(insights, insights)
    if (selected) b._insight_prefs = { selected, matched: selected.length }
    return b
  }

  it("names cards from _insight_prefs with no argument passed", () => {
    // Primary is user_feedback, which the reader did NOT pick; reliability_signals
    // is the one they asked for, so that is what the card must say.
    const f = finding("A", ["user_feedback", "reliability_signals"])
    const b = briefWithPrefs([f], ["top_problems", "build_priorities", "reliability_signals"])
    expect(briefToBriefV2State(b).hero?.skillLabel).toBe("Reliability")
  })

  it("matches what the email renders for the same finding", () => {
    // The emailed pill reads the identical field, so both surfaces agree by
    // construction. This is the drift the PR exists to remove.
    const f = finding("A", ["top_problems", "wins"])
    expect(briefToBriefV2State(briefWithPrefs([f], ["wins"])).hero?.skillLabel).toBe("Win")
    expect(briefToBriefV2State(briefWithPrefs([f], [])).hero?.skillLabel).toBe("Top problem")
  })

  it("falls back to the primary type when the brief predates _insight_prefs", () => {
    // Legacy payloads have no `_insight_prefs`; they render primary types until
    // regenerated, which is honest rather than guessed.
    const f = finding("A", ["user_feedback", "reliability_signals"])
    expect(briefToBriefV2State(briefWithPrefs([f], null)).hero?.skillLabel)
      .toBe("User feedback")
  })

  it("picks the hero from _insight_prefs with no argument passed", () => {
    const other = finding("not preferred", ["user_feedback"], 0.99)
    const preferred = finding("preferred", ["wins"], 0.1)
    const b = briefWithPrefs([other, preferred], ["wins"])
    expect(briefToBriefV2State(b).hero?.title).toBe("preferred")
  })

  it("lets an explicit argument override the payload", () => {
    const f = finding("A", ["top_problems", "wins"])
    const b = briefWithPrefs([f], ["wins"])
    expect(briefToBriefV2State(b, ["top_problems"]).hero?.skillLabel).toBe("Top problem")
  })

  it("ignores junk in _insight_prefs.selected", () => {
    const f = finding("A", ["top_problems", "wins"])
    const b = briefWithPool([f], [f])
    b._insight_prefs = { selected: ["drive_metric", "nonsense"], matched: 0 }
    expect(briefToBriefV2State(b).hero?.skillLabel).toBe("Top problem")
  })
})
