// @vitest-environment jsdom
//
// The SECOND gate — the run says what it will read and what it cannot answer,
// and the user says go — now answered IN THE CHAT THREAD rather than the side
// panel. Retargeted from `GoalAnalysisTab.plan.dom.test.tsx` when the gates
// moved; the guarantees are unchanged and are not about layout:
//
//   1. The approve call must carry the user's ACTUAL decision. An approve that
//      posts an empty body looks identical on screen and silently reads the
//      source they dropped.
//   2. A missing plan must never render an approve button. That click would be
//      agreement to something never shown.
//   3. The engine's name never reaches the screen, in ANY state.
//
// The poller re-arm cases that lived here went with the panel: the tab no
// longer owns a gate, so there is no gate-poll to re-arm. `approveGoalPlan` in
// ChatScreen owns that now, and a refused approve surfacing on the turn rather
// than vanishing is covered in `ChatScreen.goal-restore.dom.test.tsx`.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { GoalGateCard } from "../GoalGateCard"
import type { GoalRunPlan } from "../../../lib/api"

const PLAN = {
  goal_text: "raise net revenue retention",
  definition_text: "expansion minus churn across renewing accounts",
  currency: "accounts",
  total_signals: 412,
  sources: [
    {
      source_type: "customer_voice",
      signal_count: 260,
      label: "calls and customer tickets",
      witnesses: "what customers asked for and reported",
    },
    {
      source_type: "project_mgmt",
      signal_count: 152,
      label: "the tracker",
      witnesses: "what was built, broken, blocked or attempted",
    },
  ],
  cannot_answer: [
    {
      question: "How many points will this move the metric?",
      because: "nothing connected here carries numbers",
      remedy: "connect Amplitude, or upload a cohort export",
    },
  ],
  will_produce: ["Themes ranked by how much of your book they touch"],
  excluded_sources: [],
  hypotheses: [],
} as unknown as GoalRunPlan

const renderPlan = (onApprovePlan = vi.fn()) => {
  render(
    <GoalGateCard
      gate={{ kind: "plan", runId: 7, plan: PLAN }}
      onApprovePlan={onApprovePlan}
    />,
  )
  return onApprovePlan
}

afterEach(cleanup)

describe("the plan is shown before anything is spent", () => {
  it("lists each source with its count and what it can witness", () => {
    renderPlan()
    const panel = screen.getByTestId("goal-plan")
    expect(panel.textContent).toContain("calls and customer tickets")
    expect(panel.textContent).toContain("260")
    expect(panel.textContent).toContain("what customers asked for and reported")
  })

  it("states what the run will NOT be able to answer, with the fix", () => {
    // Said BEFORE the run, this is a decision — connect the source, or accept
    // a qualitative answer knowingly. Said after, it is an apology.
    renderPlan()
    const gaps = screen.getByTestId("goal-plan-gaps")
    expect(gaps.textContent).toContain("How many points will this move the metric?")
    expect(gaps.textContent).toContain("connect Amplitude")
  })
})

describe("approving carries the user's decision", () => {
  it("posts nothing extra when the user changed nothing", () => {
    const onApprove = renderPlan()
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    expect(onApprove).toHaveBeenCalledWith({ excluded_sources: [], hypotheses: [] })
  })

  it("posts the source the user dropped", () => {
    // The whole point of the gate: a dropped source has to reach the server, or
    // the run quietly reads it anyway and the screen looks identical.
    const onApprove = renderPlan()
    fireEvent.click(screen.getByLabelText(/calls and customer tickets/i))
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    expect(onApprove).toHaveBeenCalledWith({
      excluded_sources: ["customer_voice"], hypotheses: [],
    })
  })

  it("unchecking and re-checking a source leaves it read", () => {
    const onApprove = renderPlan()
    const box = screen.getByLabelText(/calls and customer tickets/i)
    fireEvent.click(box)
    fireEvent.click(box)
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    expect(onApprove).toHaveBeenCalledWith({ excluded_sources: [], hypotheses: [] })
  })

  it("posts the hypotheses the user typed, one per line", () => {
    const onApprove = renderPlan()
    fireEvent.change(screen.getByLabelText("What you already believe"), {
      target: { value: "onboarding is the drop-off\n\nenterprise churns on SSO" },
    })
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    expect(onApprove).toHaveBeenCalledWith({
      excluded_sources: [],
      hypotheses: ["onboarding is the drop-off", "enterprise churns on SSO"],
    })
  })

  it("refuses to run with every source excluded", () => {
    // A run with nothing to read produces a confident-looking empty report,
    // which is the worst output this feature has.
    renderPlan()
    fireEvent.click(screen.getByLabelText(/calls and customer tickets/i))
    fireEvent.click(screen.getByLabelText(/the tracker/i))
    expect(
      (screen.getByRole("button", { name: /approve and run/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(true)
  })
})

describe("the engine's name never reaches the screen", () => {
  it("says nothing about Crucible in the plan gate", () => {
    renderPlan()
    expect(document.body.textContent?.toLowerCase()).not.toContain("crucible")
  })

  it("says nothing about Crucible in the settled record either", () => {
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 7, plan: PLAN }}
        resolved={{ kind: "plan", excludedSources: ["customer_voice"], hypotheses: [] }}
      />,
    )
    expect(document.body.textContent?.toLowerCase()).not.toContain("crucible")
  })
})
