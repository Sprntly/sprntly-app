// @vitest-environment jsdom
/** The two Goal Analysis gates, answered IN THE THREAD.
 *
 * They moved out of the side panel because both gates are the conversation
 * that lets a PM defend the result — and a question answered somewhere other
 * than the thread leaves no record of what was agreed.
 */
import * as React from "react"
import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { GoalGateCard, type GoalGate } from "../GoalGateCard"

const definitionGate: GoalGate = {
  kind: "definition",
  runId: 7,
  goalText: "increase revenue by 5%",
  ask: "I looked for an existing definition and did not find one to adopt.",
  proposedDefinition: "Net new ARR, all accounts, trailing 90 days",
  proposedSource: "your KPI tree",
  methodNote: "Counted as closed-won ARR minus churned ARR.",
}

describe("GoalGateCard — the definition gate", () => {
  it("shows the engine's ask VERBATIM, not a tidied paraphrase", () => {
    render(<GoalGateCard gate={definitionGate} />)
    // §5 requires the ask to show the search before the gap; a shortened
    // version drops exactly that.
    expect(screen.queryByText(definitionGate.ask)).not.toBeNull()
  })

  it("states the calculation being assumed, in the same step", () => {
    render(<GoalGateCard gate={definitionGate} />)
    expect(screen.getByTestId("goal-gate-method-note").textContent)
      .toContain("closed-won ARR minus churned ARR")
  })

  it("hands back the user's OWN words, including an edit", () => {
    const onConfirm = vi.fn()
    render(<GoalGateCard gate={definitionGate} onConfirmDefinition={onConfirm} />)
    const box = screen.getByLabelText("What this goal means")
    fireEvent.change(box, { target: { value: "Expansion ARR only, 30 days" } })
    fireEvent.click(screen.getByRole("button", { name: /confirm and plan/i }))
    expect(onConfirm).toHaveBeenCalledWith("Expansion ARR only, 30 days")
  })

  it("cannot be confirmed empty — an empty definition is not a definition", () => {
    render(
      <GoalGateCard
        gate={{ ...definitionGate, proposedDefinition: "" }}
        onConfirmDefinition={vi.fn()}
      />,
    )
    expect((screen.getByRole("button", { name: /confirm and plan/i }) as HTMLButtonElement)
      .disabled).toBe(true)
  })

  it("keeps the record of what was agreed once resolved", () => {
    render(
      <GoalGateCard
        gate={definitionGate}
        resolved={{ kind: "definition", definition: "Expansion ARR only" }}
      />,
    )
    expect(screen.getByTestId("goal-gate-definition-done").textContent)
      .toContain("Expansion ARR only")
    // The controls are gone; the answer is not.
    expect(screen.queryByLabelText("What this goal means")).toBeNull()
  })
})

describe("GoalGateCard — the settled plan", () => {
  it("STATES an excluded source rather than merely omitting it", () => {
    // A quietly narrower run is exactly what the coverage notes exist to
    // prevent, so a dropped source has to appear in the record.
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 7, plan: { sources: [] } as never }}
        resolved={{ kind: "plan", excludedSources: ["slack"], hypotheses: [] }}
      />,
    )
    expect(screen.getByTestId("goal-gate-plan-done").textContent).toContain("slack")
  })

  it("says so plainly when nothing was excluded", () => {
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 7, plan: { sources: [] } as never }}
        resolved={{ kind: "plan", excludedSources: [], hypotheses: [] }}
      />,
    )
    expect(screen.getByTestId("goal-gate-plan-done").textContent)
      .toMatch(/every connected source/i)
  })
})


describe("a failure that lands after the gate was answered", () => {
  // `endGoalTurn` writes `goalGateError` alongside an existing settled record —
  // the run dying between gate 1 and gate 2. The settled card is the only thing
  // that renders once a gate is answered, so for a while that reason had
  // nowhere to appear at all and this pair had no test anywhere.
  it("shows the reason beside what was agreed, not instead of it", () => {
    render(
      <GoalGateCard
        gate={definitionGate}
        resolved={{ kind: "definition", definition: "NRR, trailing 90 days" }}
        error="That analysis stopped before it could read anything."
      />,
    )
    expect(screen.getByTestId("goal-gate-definition-done").textContent)
      .toContain("NRR, trailing 90 days")
    expect(document.body.textContent).toContain("stopped before it could read")
  })

  it("states a wholly failed gate's reason ONCE, not twice", () => {
    // `endGoalTurn` used to write the same sentence to both the failed record
    // and the note beside it, so the card printed it twice: "Analysis stopped /
    // That analysis stopped… / That analysis stopped…". It writes one or the
    // other now, and this pins that.
    render(
      <GoalGateCard
        gate={definitionGate}
        resolved={{ kind: "failed", reason: "Goal Analysis is not enabled." }}
      />,
    )
    const card = screen.getByTestId("goal-gate-failed")
    expect(card.textContent).toContain("not enabled")
    expect(card.textContent?.match(/not enabled/g)?.length).toBe(1)
  })
})
