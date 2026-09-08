// @vitest-environment jsdom
//
// The disclosure channel on the plan gate — before approval, in "What I will
// read". `source_scope` is the channel's first producer
// (`routes.crucible._apply_attachments_scope`): an attachments-scope run
// arrives here with `plan.sources` already emptied server-side, so without a
// rendered note a reader has no way to tell an empty connected-source list
// apart from a workspace with nothing connected.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { GoalAnalysisPlan } from "../GoalAnalysisPlan"
import type { GoalRunPlan } from "../../../lib/api"

const CONNECTED = [
  {
    source_type: "revenue", signal_count: 260,
    label: "revenue data", witnesses: "how much something moved",
  },
]

const base = (over: Record<string, unknown> = {}) => ({
  goal_text: "grow revenue this year",
  definition_text: "expansion minus churn",
  currency: "accounts",
  total_signals: 260,
  sources: CONNECTED,
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

describe("the disclosure channel at the plan gate", () => {
  it("renders the source_scope note the backend computed", () => {
    renderPlan(base({
      sources: [],
      notes: [{
        kind: "source_scope",
        text: "Source scope is set to attachments: only the documents attached "
          + "to this run are read. The connected workspace was not read; 4 "
          + "signals across 1 source were set aside.",
      }],
    }))
    expect(screen.getByTestId("goal-plan-note-source_scope").textContent).toBe(
      "Source scope is set to attachments: only the documents attached to "
      + "this run are read. The connected workspace was not read; 4 signals "
      + "across 1 source were set aside.",
    )
  })

  it("renders a note of a kind never wired into this component, same as any other", () => {
    renderPlan(base({
      notes: [{ kind: "invented_for_this_test", text: "A disclosure this file has no case for." }],
    }))
    expect(screen.getByTestId("goal-plan-note-invented_for_this_test").textContent)
      .toBe("A disclosure this file has no case for.")
  })

  it("renders nothing extra for a plan with no notes — the pre-channel shape", () => {
    renderPlan(base())
    expect(screen.queryByTestId(/goal-plan-note-/)).toBeNull()
  })
})
