// @vitest-environment jsdom
//
// GOAL-RESOLUTION §5 — "the quality of the ask is what makes it feel competent
// rather than helpless". Four requirements, all mandatory, and the shipped ask
// met none of them. Each is pinned here.
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { GoalMetricCandidates, seedFromCandidate } from "../GoalMetricCandidates"

afterEach(cleanup)

// SYNTHETIC, per CONVENTIONS' public-repo hygiene — the repo is public and
// real metric names carrying real figures are a commercial disclosure.
const CAND = {
  key: "weekly_signups_count",
  label: "Weekly signups (count)",
  source: "amplitude",
  source_label: "amplitude",
  points: 11,
  current_value: 4128,
  current_period: "2026-07-06",
  first_period: "2026-02-02",
  last_period: "2026-07-06",
  consequence:
    "Measured from amplitude: 11 points, 2026-02-02 to 2026-07-06. Picking it fixes what the run is steering by. Findings are still sized in reach — how many accounts a theme touches — not in this metric's own unit.",
}

// §5 req 1 reports the LADDER's rungs, and an empty rung is still evidence of
// looking — "no metrics defined" is the sentence that makes the ask read as
// diligence rather than helplessness.
const SEARCHED = [
  { rung: "your KPI tree", found: 0, detail: "no metrics defined" },
  { rung: "your measured metrics", found: 3, detail: "3 metrics, 33 points" },
]

describe("1. show the search before the gap", () => {
  it("opens with what was looked at, not with what is missing", () => {
    render(
      <GoalMetricCandidates searched={SEARCHED} candidates={[CAND]} onPick={() => {}} />,
    )
    const looked = screen.getByTestId("goal-ask-searched").textContent || ""
    expect(looked).toContain("Where I looked for a definition")
    expect(looked).toContain("your KPI tree")
    expect(looked).toContain("3 metrics, 33 points")
    // An EMPTY rung still renders: it is the evidence that it was searched.
    expect(looked).toContain("no metrics defined")
  })
})

describe("2. candidates carry live numbers, so the user can point", () => {
  it("shows the current value, its period, the history and the source", () => {
    render(<GoalMetricCandidates searched={SEARCHED} candidates={[CAND]} onPick={() => {}} />)
    const t = screen.getByTestId("goal-ask-candidates").textContent || ""
    expect(t).toContain("Weekly signups (count)")
    expect(t).toContain("4,128")
    expect(t).toContain("2026-07-06")
    expect(t).toContain("11 points")
    expect(t).toContain("amplitude")
  })

  it("renders no number at all when the metric recorded none", () => {
    // I3 at the ask: "not measured" and "zero" lead to opposite decisions.
    render(
      <GoalMetricCandidates
        searched={SEARCHED}
        candidates={[{ ...CAND, current_value: null }]}
        onPick={() => {}}
      />,
    )
    const t = screen.getByTestId("goal-ask-candidates").textContent || ""
    expect(t).not.toContain("0 · 2026-07-06")
    expect(t).toContain("11 points")
  })

  it("a pick is answerable by pointing — it seeds the box, it does not lock", () => {
    const picked: string[] = []
    render(
      <GoalMetricCandidates searched={SEARCHED} candidates={[CAND]} onPick={(s) => picked.push(s)} />,
    )
    fireEvent.click(screen.getByText("Weekly signups (count)"))
    expect(picked).toHaveLength(1)
    expect(picked[0]).toContain("Weekly signups (count)")
    expect(picked[0]).toContain("4,128")
    // The RAW KEY travels too — writing only the humanised label put a
    // paraphrase of the company's own metric name into the stored definition.
    expect(picked[0]).toContain("weekly_signups_count")
  })
})

describe("3. name the consequence of the choice", () => {
  it("every candidate states what changes if it is picked", () => {
    render(<GoalMetricCandidates searched={SEARCHED} candidates={[CAND]} onPick={() => {}} />)
    const t = screen.getByTestId("goal-ask-candidates").textContent || ""
    expect(t).toContain("Picking it fixes what the run is steering by")
    // And it must not promise a point estimate the engine cannot produce.
    expect(t).toContain("sized in reach")
    // The first version read plan._SOURCE_PROSE and rendered, verbatim,
    // "scopes the run to what nothing — recorded, never counted".
    expect(t).not.toContain("recorded, never counted")
  })
})

describe("4. leave the door open", () => {
  it("renders nothing rather than an empty shell when there is no grounding", () => {
    // The free-text box lives in the parent and is ALWAYS rendered, so this
    // component must simply add nothing when it has nothing to add.
    render(<GoalMetricCandidates onPick={() => {}} />)
    expect(screen.queryByTestId("goal-ask-candidates")).toBeNull()
    expect(screen.queryByTestId("goal-ask-searched")).toBeNull()
  })
})

describe("the seed is deliberately incomplete", () => {
  it("states the metric and its value, and stops short of a definition", () => {
    const seed = seedFromCandidate(CAND)
    // What is counted, over what population, over what window — an observation
    // supplies none of those, so the seed must not pretend to.
    expect(seed).toContain("Weekly signups (count)")
    expect(seed).toContain("weekly_signups_count")
    expect(seed).toContain("amplitude")
    expect(seed.trim().endsWith(".")).toBe(true)
    expect(seed.toLowerCase()).not.toContain("means")
    expect(seed.toLowerCase()).not.toContain("defined as")
  })
})
