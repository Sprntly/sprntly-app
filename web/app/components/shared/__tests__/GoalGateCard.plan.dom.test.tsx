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
  will_produce: ["A ranked list of what is blocking this goal"],
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

  it("shows the reader's own sentence beside the goal it was taken to mean", () => {
    // AC-3: what was asked AND what the metric was taken to mean, both on
    // screen — a count phrased in the sentence, not only in `goal_text`.
    render(
      <GoalGateCard
        gate={{
          kind: "plan",
          runId: 7,
          plan: {
            ...PLAN,
            asked_text: "What are three things I can do to raise net revenue retention?",
          } as unknown as GoalRunPlan,
        }}
        onApprovePlan={vi.fn()}
      />,
    )
    const panel = screen.getByTestId("goal-plan")
    expect(panel.textContent).toContain(
      "What are three things I can do to raise net revenue retention?",
    )
  })

  it("says nothing extra when there is no literal sentence to show", () => {
    // A run with no `asked_text` (the direct API, an older run) renders
    // exactly as it did before this field existed.
    renderPlan()
    const panel = screen.getByTestId("goal-plan")
    expect(panel.querySelector("[data-testid='goal-plan-asked-text']")).toBeNull()
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

  it("keeps the WHOLE plan on screen after approval, not a receipt", () => {
    // THE POINT OF PUTTING THE GATE IN THE CONVERSATION. Collapsing to "Plan
    // approved" plus four counts threw away what a PM has to point at later:
    // which sources were in scope, what each can actually witness, and what
    // the run said up front it would NOT be able to answer. Scrolling back
    // showed THAT a plan was approved, not WHAT was approved.
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 1, plan: PLAN as GoalRunPlan }}
        resolved={{
          kind: "plan", excludedSources: ["customer_voice"],
          hypotheses: ["onboarding is where they drop off"],
          plan: PLAN as GoalRunPlan,
        }}
      />,
    )
    const card = screen.getByTestId("goal-gate-plan-done").textContent ?? ""
    expect(card).toContain("Plan approved")
    // What each source can witness — gone entirely from the receipt.
    expect(card).toMatch(/can witness what customers asked for/i)
    // What it said it could not answer, and what would close it.
    expect(card).toMatch(/will not be able to answer/i)
    // What the reader said they already believed.
    expect(card).toContain("onboarding is where they drop off")
  })

  it("marks the dropped source in the settled plan and drops no control on it", () => {
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 1, plan: PLAN as GoalRunPlan }}
        resolved={{
          kind: "plan", excludedSources: ["customer_voice"], hypotheses: [],
          plan: PLAN as GoalRunPlan,
        }}
      />,
    )
    const card = screen.getByTestId("goal-gate-plan-done")
    expect(card.textContent ?? "").toContain("dropped by you")
    expect(card.querySelector(".ggc-src-struck")).not.toBeNull()
    // A SETTLED PLAN IS A RECORD, NOT A CONTROL: no checkboxes, no button.
    expect(card.querySelectorAll("input[type=checkbox]")).toHaveLength(0)
    expect(card.querySelector("textarea")).toBeNull()
    expect(
      screen.queryByRole("button", { name: /approve and run/i }),
    ).toBeNull()
  })

  it("falls back to the terse record for a turn persisted without the plan", () => {
    // Older turns carried only `sources`. They must still render, and must
    // still state the exclusion — the fallback is why that is not silence.
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 1, plan: PLAN as GoalRunPlan }}
        resolved={{
          kind: "plan", excludedSources: ["customer_voice"], hypotheses: [],
          plan: { sources: PLAN.sources },
        }}
      />,
    )
    const card = screen.getByTestId("goal-gate-plan-done").textContent ?? ""
    expect(card).toContain("dropped by you")
    expect(card).not.toMatch(/will not be able to answer/i)
  })

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

describe("approving says what happens next", () => {
  // Apurva, on the feedback doc: "After I approved the plan, there was no
  // comms to me. Just the artifact section opening." Approve did two things
  // silently — settled the card and swung the panel open — so the thread, the
  // place the reader was actually looking, went quiet at the exact moment the
  // work started.
  it("names the artefact and where it appears, on the full settled card", () => {
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 1, plan: PLAN as GoalRunPlan }}
        resolved={{
          kind: "plan", excludedSources: [], hypotheses: [],
          plan: PLAN as GoalRunPlan,
        }}
      />,
    )
    const next = screen.getByTestId("goal-gate-plan-next").textContent ?? ""
    // WHAT is being made, and WHERE it lands. Either half alone leaves the
    // reader guessing at the other.
    expect(next).toMatch(/Goal Analysis/)
    expect(next).toMatch(/on the right/i)
  })

  it("says it on the terse card too, where there is no plan to fall back on", () => {
    // The record persisted before the plan was carried renders the fallback
    // branch. That reader has LESS on screen, not more, so the handoff line
    // matters there at least as much.
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 1, plan: PLAN as GoalRunPlan }}
        resolved={{ kind: "plan", excludedSources: [], hypotheses: [] }}
      />,
    )
    expect(screen.getByTestId("goal-gate-plan-next").textContent ?? "")
      .toMatch(/Goal Analysis/)
  })

  it("makes no progress claim that the transcript will outlive", () => {
    // TENSE IS LOAD-BEARING. This card is re-read long after the run lands, so
    // "analysing now" / "will appear shortly" is false every time it is read
    // after the first minute. Simple present survives both readings.
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 1, plan: PLAN as GoalRunPlan }}
        resolved={{
          kind: "plan", excludedSources: [], hypotheses: [],
          plan: PLAN as GoalRunPlan,
        }}
      />,
    )
    const next = screen.getByTestId("goal-gate-plan-next").textContent ?? ""
    expect(next).not.toMatch(/analysing now|analyzing now|in progress|shortly|any moment|will appear/i)
  })

  it("sends nobody to a panel when the run died at the gate", () => {
    // `error` on a settled card means the run failed BETWEEN gates. Pointing
    // that reader at the Goal Analysis panel points them at nothing.
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 1, plan: PLAN as GoalRunPlan }}
        resolved={{
          kind: "plan", excludedSources: [], hypotheses: [],
          plan: PLAN as GoalRunPlan,
        }}
        error="That analysis stopped before it could read anything."
      />,
    )
    expect(screen.queryByTestId("goal-gate-plan-next")).toBeNull()
    expect(document.body.textContent).toContain("stopped before it could read")
  })
})

describe("the plan leads with the approach, not the form", () => {
  // Apurva, on the feedback doc: the plan needs to be "more human readable",
  // with "what we do" separated from "what we say". What was on screen was
  // four headed sections and a checkbox list — accurate, and with no sentence
  // anywhere saying what was about to happen.
  it("opens with a numbered account of what will happen", () => {
    renderPlan()
    const approach = screen.getByTestId("goal-plan-approach")
    expect(approach.textContent).toMatch(/This is the approach I am going to use/i)
    const steps = approach.querySelectorAll("li")
    expect(steps.length).toBeGreaterThanOrEqual(4)
    // Step one is what gets read, in the reader's words and with the total.
    expect(steps[0].textContent).toContain("calls and customer tickets")
    expect(steps[0].textContent).toContain("412")
  })

  it("sits above the controls, so the account is read before the form", () => {
    // ORDER IS THE FEATURE. Underneath the checkboxes it is a footnote, and
    // the card is back to being a form with a paragraph attached.
    renderPlan()
    const approach = screen.getByTestId("goal-plan-approach")
    const firstBox = document.querySelector("input[type=checkbox]")!
    expect(approach.compareDocumentPosition(firstBox) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy()
  })

  it("rewrites itself the moment a source is unticked", () => {
    // A narrative that kept describing the tracker after the reader unticked
    // the tracker would be the most confidently wrong sentence on the card.
    renderPlan()
    expect(screen.getByTestId("goal-plan-approach").textContent).toContain("the tracker")
    fireEvent.click(screen.getByLabelText("Read the tracker"))
    const after = screen.getByTestId("goal-plan-approach").textContent ?? ""
    expect(after).not.toContain("the tracker")
    expect(after).toContain("calls and customer tickets")
    expect(after).toContain("260")
  })

  it("speaks in the past on a settled plan, because the decision is made", () => {
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 1, plan: PLAN }}
        resolved={{
          kind: "plan", excludedSources: [], hypotheses: [], plan: PLAN,
        }}
      />,
    )
    const approach = screen.getByTestId("goal-plan-approach").textContent ?? ""
    expect(approach).toMatch(/approach you approved/i)
    expect(approach).not.toMatch(/I am going to use/i)
  })
})


describe("the definition is confirmed in the plan, not one screen earlier", () => {
  // Apurva: "Lets remove the goal clarification and add it to the plan. In the
  // plan we can confirm that." The gate it replaces asked what a metric meant
  // before the reader had seen anything the run intended to do — and collected
  // answers to match: one run recorded its definition as the words "that is
  // accurate", because that is what somebody typed at a question that was not,
  // to them, asking for a definition.
  const PROPOSED = {
    ...PLAN, definition_adopted: false,
    definition_source: "the usual reading of this metric",
    definition_note:
      "I will work to that sentence exactly as you write it: I do not "
      + "recompute it.",
  } as unknown as GoalRunPlan

  const renderProposed = (onApprovePlan = vi.fn()) => {
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 4, plan: PROPOSED }}
        onApprovePlan={onApprovePlan}
      />,
    )
    return onApprovePlan
  }

  it("shows the proposal as an editable field, attributed to what produced it", () => {
    renderProposed()
    const box = screen.getByLabelText("What this goal means") as HTMLTextAreaElement
    expect(box.value).toBe(PLAN.definition_text)
    // A proposal that cannot be attributed is an assertion with a polite tone.
    expect(screen.getByTestId("goal-plan-definition").textContent)
      .toContain("the usual reading of this metric")
  })

  it("carries an edited definition on the approve that adopts it", () => {
    const onApprove = renderProposed()
    fireEvent.change(screen.getByLabelText("What this goal means"), {
      target: { value: "retention counted on seats, not accounts" },
    })
    fireEvent.click(screen.getByRole("button", { name: /start|approve|run/i }))
    expect(onApprove).toHaveBeenCalledTimes(1)
    expect(onApprove.mock.calls[0][0].definition_text)
      .toBe("retention counted on seats, not accounts")
  })

  it("sends no definition at all when the reader did not change it", () => {
    // OMISSION IS THE SIGNAL FOR "as shown". Echoing the proposal back lets a
    // stale card overwrite the stored definition with words the server already
    // has — a round trip that can only lose.
    const onApprove = renderProposed()
    fireEvent.click(screen.getByRole("button", { name: /start|approve|run/i }))
    expect(onApprove.mock.calls[0][0].definition_text).toBeUndefined()
  })

  it("treats retyping the same words as agreement, not as an edit", () => {
    const onApprove = renderProposed()
    fireEvent.change(screen.getByLabelText("What this goal means"), {
      target: { value: `  ${PLAN.definition_text}  ` },
    })
    fireEvent.click(screen.getByRole("button", { name: /start|approve|run/i }))
    expect(onApprove.mock.calls[0][0].definition_text).toBeUndefined()
  })

  it("says what is done with the sentence without restating it", () => {
    // §6's note, from the server so the wording lives in one place. It must
    // say the part the sentence cannot — that it is taken literally — and must
    // NOT repeat the convention, which is already the text in the field above.
    // That repetition is exactly what the feedback asked us to cut.
    renderProposed()
    const note = screen.getByTestId("goal-plan-definition-note").textContent ?? ""
    expect(note).toMatch(/exactly as you write it/i)
    expect(note).not.toContain(PLAN.definition_text)
  })

  it("says each thing once", () => {
    // "let's review the copy of what we show. There are multiple repetitions
    // and LLM reexplaining etc." Three of them were real and are pinned here,
    // because each was introduced by a fix for something else and would come
    // back the same way.
    renderProposed()
    const card = screen.getByTestId("goal-plan").textContent ?? ""
    // 1. The deliverables, once — the narrative's step, not a second section.
    const produce = "A ranked list of what is blocking this goal"
    expect(card.split(produce).length - 1).toBeLessThanOrEqual(1)
    // 2. The signal total, once.
    const total = PLAN.total_signals.toLocaleString()
    expect(card.split(`${total} signal`).length - 1).toBeLessThanOrEqual(1)
    // 3. "I work to this sentence" — the server's note owns it, so the
    //    attribution line above must not say it too.
    expect(card.toLowerCase().split("work to th").length - 1)
      .toBeLessThanOrEqual(1)
  })

  it("stops offering a field once the definition is adopted", () => {
    // A settled plan is a record. An editable box on it invites a change that
    // cannot happen and says nothing about why.
    render(
      <GoalGateCard
        gate={{ kind: "plan", runId: 4, plan: PROPOSED }}
        resolved={{ kind: "plan", excludedSources: [], hypotheses: [], plan: PROPOSED }}
      />,
    )
    expect(screen.queryByLabelText("What this goal means")).toBeNull()
    expect(screen.getByTestId("goal-plan-definition").textContent)
      .toContain(PLAN.definition_text)
  })
})

describe("the gate asks what it cannot know", () => {
  // Apurva: "the plan gate can start asking questions it doesn't know answers
  // to." Until now it asked one thing — what the metric means — and everything
  // else it lacked was reported afterwards as a limit.
  it("asks for the three, and says they are optional", () => {
    renderPlan()
    const box = screen.getByTestId("goal-plan-unknowns").textContent ?? ""
    expect(box).toMatch(/what I cannot know/i)
    expect(box).toMatch(/will not guess/i)
    expect(box).toMatch(/stays stated as missing/i)
    expect(screen.getByLabelText(/one account worth/i)).toBeTruthy()
    expect(screen.getByLabelText(/who decides/i)).toBeTruthy()
    expect(screen.getByLabelText(/when do you need/i)).toBeTruthy()
  })

  it("carries the answers on approve", () => {
    const onApprove = vi.fn()
    renderPlan(onApprove)
    fireEvent.change(screen.getByLabelText(/one account worth/i),
      { target: { value: "12000" } })
    fireEvent.change(screen.getByLabelText(/who decides/i),
      { target: { value: "VP Product" } })
    fireEvent.change(screen.getByLabelText(/when do you need/i),
      { target: { value: "before the Q3 review" } })
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))

    const d = onApprove.mock.calls[0][0]
    expect(d.account_value).toBe(12000)
    expect(d.decision_owner).toBe("VP Product")
    expect(d.needed_by).toBe("before the Q3 review")
  })

  it("sends nothing for a field left blank", () => {
    // EMPTY MEANS UNANSWERED, never zero. A blank account value reaching the
    // arithmetic as 0 would render a stake of nothing and read as a
    // measurement.
    const onApprove = vi.fn()
    renderPlan(onApprove)
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    const d = onApprove.mock.calls[0][0]
    expect(d.account_value).toBeUndefined()
    expect(d.decision_owner).toBeUndefined()
    expect(d.needed_by).toBeUndefined()
  })

  it("ignores a zero or unparseable account value rather than sending it", () => {
    const onApprove = vi.fn()
    renderPlan(onApprove)
    fireEvent.change(screen.getByLabelText(/one account worth/i),
      { target: { value: "0" } })
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    expect(onApprove.mock.calls[0][0].account_value).toBeUndefined()
  })
})

describe("the gate asks only what the CHOSEN framework needs (AC-5)", () => {
  // Replaces the fixed three above with the batch `plan.questions` carries —
  // derived server-side from which framework this run actually picked.
  const withQuestions = (questions: GoalRunPlan["questions"]) =>
    ({ ...PLAN, framework: "moscow", questions } as unknown as GoalRunPlan)

  it("does not ask for a dollar value when MoSCoW ranked the findings", () => {
    // MoSCoW's ranking has no arithmetic that reads account_value — asking
    // for it collects an input nothing downstream uses.
    render(
      <GoalGateCard gate={{
        kind: "plan", runId: 8,
        plan: withQuestions([
          { id: "decision_owner", prompt: "Who decides this?", why: "" },
          { id: "needed_by", prompt: "When do you need the decision?", why: "" },
        ]),
      }} onApprovePlan={vi.fn()} />,
    )
    expect(screen.queryByLabelText(/one account worth/i)).toBeNull()
    expect(screen.getByLabelText(/who decides/i)).toBeTruthy()
    expect(screen.getByLabelText(/when do you need/i)).toBeTruthy()
  })

  it("shows the framework's own reason for asking, when the plan carries one", () => {
    render(
      <GoalGateCard gate={{
        kind: "plan", runId: 9,
        plan: withQuestions([
          {
            id: "decision_owner", prompt: "Who decides this?",
            why: "Named on the decision box so the ranking has an owner.",
          },
        ]),
      }} onApprovePlan={vi.fn()} />,
    )
    expect(screen.getByTestId("goal-plan-unknowns").textContent)
      .toContain("Named on the decision box so the ranking has an owner.")
  })

  it("still asks for a dollar value when RICE ranked the findings", () => {
    render(
      <GoalGateCard gate={{
        kind: "plan", runId: 10,
        plan: withQuestions([
          { id: "account_value", prompt: "What is one account worth to you, per year?", why: "" },
          { id: "decision_owner", prompt: "Who decides this?", why: "" },
          { id: "needed_by", prompt: "When do you need the decision?", why: "" },
        ]),
      }} onApprovePlan={vi.fn()} />,
    )
    expect(screen.getByLabelText(/one account worth/i)).toBeTruthy()
  })

  it("falls back to the old fixed three for a plan stored before this field existed", () => {
    // `PLAN` (used everywhere else in this file) carries no `questions` at
    // all — a run left `awaiting_approval` from before this shipped.
    renderPlan()
    expect(screen.getByLabelText(/one account worth/i)).toBeTruthy()
    expect(screen.getByLabelText(/who decides/i)).toBeTruthy()
    expect(screen.getByLabelText(/when do you need/i)).toBeTruthy()
  })
})
