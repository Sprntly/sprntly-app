// @vitest-environment jsdom
//
// How this goal reads the evidence, on the plan the reader is asked to approve.
//
// THE DEFECT THIS SCREEN NOW ANSWERS. Two runs on one tenant with one set of
// attachments, one about revenue and one about churn, produced correctly
// different definitions and then twenty-five byte-identical steps — and
// nothing anywhere on the card to show that the engine had, or had not,
// looked at the question. The routing is decided server-side in code; it is
// rendered here because it is a decision the reader is being asked to APPROVE,
// and a decision they cannot see is one they cannot argue with.
//
// The set-aside block is the same argument one level down. Hiding a step that
// does not apply makes a shorter plan and a less checkable one: the reader
// cannot tell a considered omission from a check that quietly failed, and they
// are the only person who can say "no, that one does matter here".
import * as React from "react"
import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { GoalAnalysisPlan } from "../GoalAnalysisPlan"
import type { GoalRunPlan } from "../../../lib/api"

const ROUTING = {
  goal_class: "retention",
  goal_class_note: "keeping the accounts you already have",
  business_type: "B2B SaaS",
  sources: [
    {
      source_type: "revenue", label: "revenue data", disposition: "use",
      why: "For keeping the accounts you already have, it can witness how "
        + "much something moved, so it sizes this.",
    },
    {
      source_type: "verbal_claim", label: "verbal claims", disposition: "discount",
      why: "For keeping the accounts you already have, nothing it carries may "
        + "be counted towards a finding.",
    },
  ],
  notes: [
    "Nothing read here carries a per-account value, so every size is stated "
      + "in accounts touched and never in money.",
  ],
}

const SET_ASIDE = [{
  what: "Check whether two funnel stages are really two steps",
  why: "this is a funnel check. This run is about keeping the accounts you "
    + "already have, which it does not bear on.",
  observation: "obs-stage-1",
  source: "product analytics — activation funnel",
}]

const base = (over: Record<string, unknown> = {}) => ({
  goal_text: "how do we reduce churn",
  definition_text: "accounts lost in the period",
  currency: "accounts",
  total_signals: 260,
  sources: [{
    source_type: "revenue", signal_count: 260,
    label: "revenue data", witnesses: "how much something moved",
  }],
  cannot_answer: [],
  will_produce: [],
  excluded_sources: [],
  hypotheses: [],
  steps: [],
  observations: [],
  questions: [],
  coverage: {},
  ...over,
}) as unknown as GoalRunPlan

const renderPlan = (plan: GoalRunPlan) =>
  render(<GoalAnalysisPlan plan={plan} approving={false} onApprove={vi.fn()} />)

afterEach(cleanup)

describe("how this goal reads the evidence", () => {
  it("says which part of the book it read the goal as being about", () => {
    // The classification is a decision made about the reader's question. If
    // it is wrong, they are the only person who can tell — and only if it is
    // on screen before they approve.
    renderPlan(base({ routing: ROUTING }))
    expect(
      screen.getByTestId("goal-plan-goal-class").textContent,
    ).toContain("keeping the accounts you already have")
  })

  it("says what each source is being used FOR, not only that it is there", () => {
    renderPlan(base({ routing: ROUTING }))
    const block = screen.getByTestId("goal-plan-routing")
    const rows = within(block).getAllByTestId("goal-plan-routed-source")
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText("revenue data")).toBeTruthy()
    expect(within(rows[0]).getByText("use")).toBeTruthy()
    expect(within(rows[1]).getByText("discount")).toBeTruthy()
  })

  it("states the unit it can size in, before anything is sized", () => {
    renderPlan(base({ routing: ROUTING }))
    expect(
      screen.getAllByTestId("goal-plan-routing-note")
        .some((n) => /never in money/.test(n.textContent ?? "")),
    ).toBe(true)
  })

  it("names a step it set aside, with the reason and where the finding is", () => {
    renderPlan(base({ routing: ROUTING, set_aside: SET_ASIDE }))
    const block = screen.getByTestId("goal-plan-set-aside")
    expect(within(block).getByText(/two funnel stages/)).toBeTruthy()
    expect(within(block).getByText(/does not bear on/)).toBeTruthy()
    expect(within(block).getByText(/activation funnel/)).toBeTruthy()
  })

  it("shows no set-aside section when the goal set nothing aside", () => {
    // THE NEGATIVE HALF. A heading that renders empty would tell every reader
    // something was withheld from every plan.
    renderPlan(base({ routing: ROUTING, set_aside: [] }))
    expect(screen.queryByTestId("goal-plan-set-aside")).toBeNull()
    expect(screen.getByTestId("goal-plan-routing")).toBeTruthy()
  })

  it("renders a plan stored before routing existed exactly as before", () => {
    // `routing` is absent, not empty, on every plan written before this — and
    // those are re-rendered whenever an old run is opened.
    renderPlan(base({}))
    expect(screen.queryByTestId("goal-plan-routing")).toBeNull()
    expect(screen.queryByTestId("goal-plan-set-aside")).toBeNull()
    expect(screen.getByTestId("goal-plan-sources")).toBeTruthy()
  })
})
