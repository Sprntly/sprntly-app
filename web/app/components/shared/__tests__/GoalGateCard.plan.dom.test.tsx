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
import { MAX_HYPOTHESIS_CHARS } from "../GoalAnalysisPlan"
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

describe("a hypothesis longer than the API accepts", () => {
  // Caught HERE, where the offending line can be named, rather than as a 422
  // the reader has to decode. Carried over from the retired panel file.
  it("is caught before approve, and names the problem", () => {
    const onApprove = renderPlan()
    fireEvent.change(screen.getByLabelText("What you already believe"), {
      target: { value: "x".repeat(MAX_HYPOTHESIS_CHARS + 1) },
    })
    expect(screen.getByTestId("goal-plan-hypothesis-too-long")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    expect(onApprove).not.toHaveBeenCalled()
  })

  it("lets an ordinary hypothesis through untouched", () => {
    const onApprove = renderPlan()
    fireEvent.change(screen.getByLabelText("What you already believe"), {
      target: { value: "onboarding is where they drop" },
    })
    expect(screen.queryByTestId("goal-plan-hypothesis-too-long")).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    expect(onApprove).toHaveBeenCalledWith({
      excluded_sources: [], hypotheses: ["onboarding is where they drop"],
    })
  })
})

describe("a refusal leaves the gate answerable", () => {
  it("shows the reason WITHOUT destroying the controls", () => {
    // A 422 means the server refused the body before claiming anything, so the
    // run is still sitting at its gate. Replacing the card with the error is
    // what turns a retryable refusal into a dead end.
    renderPlan()
    cleanup()
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 7, plan: PLAN }}
        error="That was not accepted. Shorten what you wrote and try again."
        onApprovePlan={vi.fn()}
      />,
    )
    expect(document.body.textContent).toContain("Shorten what you wrote")
    expect(screen.getByRole("button", { name: /approve and run/i })).toBeTruthy()
  })
})

describe("the window before the first question", () => {
  it("says it is working, not that nothing was generated", () => {
    // A run is born `resolving_goal`. With no gate on the turn for that window
    // the thread ran its ordinary no-reply ladder and printed "No response was
    // generated for this message." over a run that was working perfectly.
    render(<GoalGateCard gate={{ kind: "pending", goalText: "raise NRR" }} />)
    expect(screen.getByTestId("goal-gate-pending")).toBeTruthy()
    expect(document.body.textContent).not.toContain("No response was generated")
  })
})


describe("the settled plan is a record, not a receipt", () => {
  // Apurva, looking at an approved run: "I cannot see the plan here." The
  // settled card collapsed to "Reading every connected source", so scrolling
  // back showed THAT a plan was approved and not WHAT was — which is the whole
  // reason the gate is in the thread rather than a panel.
  const settled = (excluded: string[] = []) =>
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 7, plan: PLAN }}
        resolved={{
          kind: "plan", excludedSources: excluded, hypotheses: [],
          plan: { sources: PLAN.sources },
        }}
      />,
    )

  it("still names every source and its count after approval", () => {
    settled()
    const card = screen.getByTestId("goal-gate-plan-done-sources")
    expect(card.textContent).toContain("calls and customer tickets")
    expect(card.textContent).toContain("260")
    expect(card.textContent).toContain("the tracker")
    expect(card.textContent).toContain("152")
  })

  it("keeps a dropped source VISIBLE, named, and marked as dropped", () => {
    // Removing it would make the record agree with a narrower run instead of
    // showing that the run was narrowed — the exact thing this gate exists to
    // make impossible.
    //
    // The first version of this test asserted only that SOME source was still
    // listed, which stayed green when the render was changed to delete dropped
    // ones outright — the precise defect its name claims to guard. It now
    // names the dropped source and requires the marking that distinguishes it
    // from one that was read.
    settled(["customer_voice"])
    const list = screen.getByTestId("goal-gate-plan-done-sources")
    expect(list.textContent).toContain("calls and customer tickets")
    expect(list.textContent).toContain("dropped by you")
    const struck = list.querySelector(".ggc-src-out")
    expect(struck).not.toBeNull()
    expect(struck?.textContent).toContain("calls and customer tickets")
    // And the source that was KEPT carries no such marking.
    const kept = Array.from(list.querySelectorAll("li"))
      .find((li) => li.textContent?.includes("the tracker"))
    expect(kept?.className || "").not.toContain("ggc-src-out")
  })

  it("states the scope net of what was dropped, without claiming it was read", () => {
    settled(["customer_voice"])
    const card = screen.getByTestId("goal-gate-plan-done").textContent ?? ""
    // 412 total, 260 dropped -> 152 across 1 source.
    expect(card).toContain("152 signals across 1 source")
    // NOT "were read". This card renders the instant the plan is approved,
    // before anything has been read, and `signal_count` is an inventory the
    // plan step takes without reading content. Past tense here is the same
    // class of overclaim the report's closing section exists to remove.
    expect(card).not.toMatch(/were read/i)
    expect(card).toMatch(/not a record of what has been read/i)
  })

  it("states an exclusion the source list cannot show", () => {
    // THE INVARIANT, at its last gap. The list can only strike through a
    // source it renders, so an excluded slug that is absent from
    // `plan.sources` had nowhere to appear — and the raw-slug fallback was
    // suppressed the moment ANY sources existed. Stated nowhere is the single
    // outcome this card exists to prevent, so it does not get to depend on the
    // two lists agreeing.
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 1, plan: PLAN as GoalRunPlan }}
        resolved={{
          kind: "plan",
          excludedSources: ["customer_voice", "a_source_the_plan_forgot"],
          hypotheses: [],
          plan: { sources: PLAN.sources },
        }}
      />,
    )
    const card = screen.getByTestId("goal-gate-plan-done").textContent ?? ""
    // The one the list CAN show is struck through there.
    expect(card).toContain("calls and customer tickets")
    expect(card).toContain("dropped by you")
    // The one it cannot show is named anyway, slug and all.
    expect(card).toContain("a_source_the_plan_forgot")
    // And the sources it CAN show are not repeated as slugs alongside it.
    expect(card).not.toContain("customer_voice")
  })

  it("does not bury the dropped disclosure in the struck-out text", () => {
    // The strike marks the SOURCE. Striking and dimming the row put the words
    // that carry the fact — "dropped by you" — behind a line and at 0.55
    // opacity, which is under AA for 13px text and gone entirely for a reader
    // who cannot resolve a strike-through. Those words are the fallback, so
    // they cannot be the thing that is hardest to read.
    settled(["customer_voice"])
    const list = screen.getByTestId("goal-gate-plan-done-sources")
    const struck = list.querySelector(".ggc-src-struck")
    expect(struck).not.toBeNull()
    expect(struck?.textContent ?? "").not.toMatch(/dropped by you/i)
    const note = list.querySelector(".ggc-src-note")
    expect(note?.textContent ?? "").toMatch(/dropped by you/i)
  })
})
