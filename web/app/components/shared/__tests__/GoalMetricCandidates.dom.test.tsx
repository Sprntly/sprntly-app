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

const CAND = {
  key: "interchange_revenue_usd",
  label: "Interchange revenue (usd)",
  source_type: "revenue",
  source_label: "revenue data",
  observations: 11,
  current_value: 2190890,
  current_period: "2025-12",
  first_period: "2025-02",
  last_period: "2025-12",
  consequence:
    "Picking this scopes the run to how much something moved, and in which direction. 11 observations, 2025-02 to 2025-12. Findings are still sized in reach — how many accounts a theme touches — not in this metric's own unit.",
}

const SEARCHED = [
  { label: "revenue data", signal_count: 28, source_type: "revenue" },
  { label: "product analytics", signal_count: 197, source_type: "analytics" },
  { label: "nothing here", signal_count: 0, source_type: "verbal_claim" },
]

describe("1. show the search before the gap", () => {
  it("opens with what was looked at, not with what is missing", () => {
    render(
      <GoalMetricCandidates searched={SEARCHED} candidates={[CAND]} onPick={() => {}} />,
    )
    const looked = screen.getByTestId("goal-ask-searched").textContent || ""
    expect(looked).toContain("What I looked at")
    expect(looked).toContain("197")
    // A source with nothing in it is not evidence of effort.
    expect(looked).not.toContain("nothing here")
  })
})

describe("2. candidates carry live numbers, so the user can point", () => {
  it("shows the current value, its period, the history and the source", () => {
    render(<GoalMetricCandidates searched={SEARCHED} candidates={[CAND]} onPick={() => {}} />)
    const t = screen.getByTestId("goal-ask-candidates").textContent || ""
    expect(t).toContain("Interchange revenue (usd)")
    expect(t).toContain("2,190,890")
    expect(t).toContain("2025-12")
    expect(t).toContain("11 observations")
    expect(t).toContain("revenue data")
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
    expect(t).not.toContain("0 · 2025-12")
    expect(t).toContain("11 observations")
  })

  it("a pick is answerable by pointing — it seeds the box, it does not lock", () => {
    const picked: string[] = []
    render(
      <GoalMetricCandidates searched={SEARCHED} candidates={[CAND]} onPick={(s) => picked.push(s)} />,
    )
    fireEvent.click(screen.getByText("Interchange revenue (usd)"))
    expect(picked).toHaveLength(1)
    expect(picked[0]).toContain("Interchange revenue (usd)")
    expect(picked[0]).toContain("2,190,890")
  })
})

describe("3. name the consequence of the choice", () => {
  it("every candidate states what changes if it is picked", () => {
    render(<GoalMetricCandidates searched={SEARCHED} candidates={[CAND]} onPick={() => {}} />)
    const t = screen.getByTestId("goal-ask-candidates").textContent || ""
    expect(t).toContain("Picking this scopes the run")
    // And it must not promise a point estimate the engine cannot produce.
    expect(t).toContain("sized in reach")
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
    expect(seed).toContain("Interchange revenue (usd)")
    expect(seed).toContain("revenue data")
    expect(seed.trim().endsWith(".")).toBe(true)
    expect(seed.toLowerCase()).not.toContain("means")
    expect(seed.toLowerCase()).not.toContain("defined as")
  })
})
