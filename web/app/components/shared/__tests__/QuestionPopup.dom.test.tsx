// @vitest-environment jsdom
//
// QuestionPopup — the chat's question stepper (one question at a time, options
// as buttons, ‹ 1/2 › pagination, Skip). BATCH-ONLY by owner directive after
// testing a submit-per-click version ("let me finish all the questions before
// you submit"): clicks are local, the user can page back and change any
// answer, and nothing leaves the popup until the LAST question settles — then
// onComplete fires exactly once with the whole record, skips included. Every
// host (clarify gate, PRD input items, assignment) submits from that record.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { QuestionPopup, type PopupQuestion } from "../QuestionPopup"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

afterEach(() => cleanup())

const TWO: PopupQuestion[] = [
  {
    header: "Target users",
    prompt: "Who is this for?",
    options: [{ label: "Admins", description: "Workspace admins" }, { label: "End users" }],
    skipDefault: "all end users",
  },
  { prompt: "How will you measure success?", options: [] },
]

describe("QuestionPopup — batch mode", () => {
  it("steps with the header chip, count, and per-option descriptions", () => {
    render(<QuestionPopup questions={TWO} onComplete={vi.fn()} />)
    expect(screen.getByTestId("question-popup-chip").textContent).toBe("Target users")
    expect(screen.getByTestId("question-popup-count").textContent).toBe("1/2")
    const opts = screen.getAllByTestId("question-popup-option")
    expect(opts[0].textContent).toBe("AdminsWorkspace admins")
    // The stated fallback is visible before the user decides to skip.
    expect(screen.getByText(/all end users/).textContent).toContain("if skipped")
  })

  it("clicking an option advances; the last settle fires onComplete with the whole record", () => {
    const onComplete = vi.fn()
    render(<QuestionPopup questions={TWO} onComplete={onComplete} />)

    fireEvent.click(screen.getAllByTestId("question-popup-option")[0])
    expect(onComplete).not.toHaveBeenCalled()
    expect(screen.getByTestId("question-popup-count").textContent).toBe("2/2")
    // Option-less question → the free-text box shows outright.
    fireEvent.change(screen.getByTestId("question-popup-input"), {
      target: { value: "fewer tickets" },
    })
    fireEvent.click(screen.getByTestId("question-popup-submit-text"))

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0][0]).toEqual([
      { prompt: "Who is this for?", answer: "Admins", skipped: false, value: undefined },
      { prompt: "How will you measure success?", answer: "fewer tickets", skipped: false, value: undefined },
    ])
    // Spent — the popup unmounts itself rather than taking a second batch.
    expect(screen.queryByTestId("question-popup")).toBeNull()
  })

  it("skips ride the record; skipping everything still completes", () => {
    // No `skipAllPrompt` → no confirmation. The gate is opt-in, for batches
    // whose skip CREATES something; see the "nothing answered" describe.
    const onComplete = vi.fn()
    render(<QuestionPopup questions={TWO} onComplete={onComplete} />)
    fireEvent.click(screen.getByTestId("question-popup-skip"))
    fireEvent.click(screen.getByTestId("question-popup-skip"))
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0][0].map((a: { skipped: boolean }) => a.skipped)).toEqual([true, true])
  })

  it("Back lets an answer be revisited and changed before the batch goes", () => {
    const onComplete = vi.fn()
    render(<QuestionPopup questions={TWO} onComplete={onComplete} />)
    fireEvent.click(screen.getAllByTestId("question-popup-option")[0]) // Admins → q2
    fireEvent.click(screen.getByLabelText("Previous question"))
    expect(screen.getByTestId("question-popup-count").textContent).toBe("1/2")
    // Change the pick; the stepper moves on to the still-open question.
    fireEvent.click(screen.getAllByTestId("question-popup-option")[1]) // End users
    fireEvent.change(screen.getByTestId("question-popup-input"), { target: { value: "x" } })
    fireEvent.click(screen.getByTestId("question-popup-submit-text"))
    expect(onComplete.mock.calls[0][0][0].answer).toBe("End users")
  })

  it("dismiss is the host's affordance, rendered only when given", () => {
    const onDismiss = vi.fn()
    const { rerender } = render(
      <QuestionPopup questions={TWO} onComplete={vi.fn()} onDismiss={onDismiss} />,
    )
    fireEvent.click(screen.getByTestId("question-popup-dismiss"))
    expect(onDismiss).toHaveBeenCalled()
    rerender(<QuestionPopup questions={TWO} onComplete={vi.fn()} />)
    expect(screen.queryByTestId("question-popup-dismiss")).toBeNull()
  })

  it("offers no skip-them-all shortcut — Skip in the head is the only way past a question", () => {
    render(<QuestionPopup questions={TWO} onComplete={vi.fn()} onDismiss={vi.fn()} />)
    expect(screen.queryByTestId("question-popup-skip-all")).toBeNull()
    expect(screen.getByTestId("question-popup-skip")).toBeTruthy()
  })
})

describe("QuestionPopup — multi-select questions", () => {
  const MULTI: PopupQuestion[] = [
    {
      header: "Which tickets",
      prompt: "Which tickets should Dave get?",
      multiSelect: true,
      allowOther: false,
      options: [
        { label: "Login flow", value: "prd-7-s1" },
        { label: "Settings page", value: "prd-7-s2" },
        { label: "Billing export", value: "prd-7-s3" },
      ],
    },
  ]

  it("options toggle like checkboxes; Confirm settles with every pick", () => {
    const onComplete = vi.fn()
    render(<QuestionPopup questions={MULTI} onComplete={onComplete} />)

    // Zero picks → the confirm is disabled and says why; a click settles
    // nothing (ticking is browsing, confirming is answering).
    const confirm = screen.getByTestId("question-popup-confirm-picks") as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    expect(confirm.textContent).toContain("Pick one or more")

    const options = screen.getAllByTestId("question-popup-option")
    fireEvent.click(options[0])
    fireEvent.click(options[2])
    expect(onComplete).not.toHaveBeenCalled()
    expect(options[0].getAttribute("aria-checked")).toBe("true")
    expect(options[1].getAttribute("aria-checked")).toBe("false")
    expect(confirm.textContent).toContain("Confirm 2 selected")

    // Toggling OFF works — a mis-tick is one click to undo.
    fireEvent.click(options[2])
    expect(options[2].getAttribute("aria-checked")).toBe("false")
    fireEvent.click(options[2])

    fireEvent.click(confirm)
    expect(onComplete).toHaveBeenCalledTimes(1)
    const [answers] = onComplete.mock.calls[0]
    expect(answers[0].skipped).toBe(false)
    expect(answers[0].answer).toBe("Login flow, Billing export")
    expect(answers[0].picks).toEqual([
      { label: "Login flow", value: "prd-7-s1" },
      { label: "Billing export", value: "prd-7-s3" },
    ])
  })

  it("multi hides the typed escape hatch and Skip still works", () => {
    const onComplete = vi.fn()
    render(<QuestionPopup questions={MULTI} onComplete={onComplete} />)
    expect(screen.queryByTestId("question-popup-other")).toBeNull()
    expect(screen.queryByTestId("question-popup-input")).toBeNull()
    fireEvent.click(screen.getByTestId("question-popup-skip"))
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0][0][0].skipped).toBe(true)
  })

  it("a batch mixes single and multi questions without either changing", () => {
    const onComplete = vi.fn()
    const mixed: PopupQuestion[] = [
      { prompt: "Who is this for?", options: [{ label: "Admins" }, { label: "End users" }] },
      ...MULTI,
    ]
    render(<QuestionPopup questions={mixed} onComplete={onComplete} />)

    // Q1 is single: one click settles and advances.
    fireEvent.click(screen.getAllByTestId("question-popup-option")[0])
    expect(screen.getByTestId("question-popup-count").textContent).toBe("2/2")

    // Q2 is multi: two ticks + confirm complete the batch.
    const options = screen.getAllByTestId("question-popup-option")
    fireEvent.click(options[0])
    fireEvent.click(options[1])
    fireEvent.click(screen.getByTestId("question-popup-confirm-picks"))
    const [answers] = onComplete.mock.calls[0]
    expect(answers[0].answer).toBe("Admins")
    expect(answers[0].picks).toBeUndefined()
    expect(answers[1].picks).toHaveLength(2)
  })

  it("restored picks (a resumed draft) show as ticked and confirm intact", () => {
    const onComplete = vi.fn()
    render(
      <QuestionPopup
        questions={MULTI}
        initialAnswers={{}}
        onComplete={onComplete}
      />,
    )
    // No restored answer → fresh. Now with a draft carrying picks: the boxes
    // come back ticked, and one Confirm completes with them.
    cleanup()
    render(
      <QuestionPopup
        questions={[{ ...MULTI[0] }, { prompt: "And?", options: [{ label: "Done" }] }]}
        initialAnswers={{
          0: {
            prompt: MULTI[0].prompt, answer: "Login flow, Settings page",
            skipped: false,
            picks: [
              { label: "Login flow", value: "prd-7-s1" },
              { label: "Settings page", value: "prd-7-s2" },
            ],
          },
        }}
        onComplete={onComplete}
      />,
    )
    // Starts at the first OPEN question (Q2) — the multi answer was restored.
    expect(screen.getByTestId("question-popup-count").textContent).toBe("2/2")
    fireEvent.click(screen.getAllByTestId("question-popup-option")[0])
    const [answers] = onComplete.mock.calls[0]
    expect(answers[0].picks).toHaveLength(2)
    expect(answers[1].answer).toBe("Done")
  })
})

describe("QuestionPopup — host-shaped questions (assignment)", () => {
  const ASSIGN: PopupQuestion[] = [
    {
      header: "Assignee",
      prompt: "Who should “Login flow” go to?",
      options: [
        { label: "Dave Okafor", value: "u-dave" },
        { label: "Priya Nair", value: "u-priya" },
      ],
      allowOther: false,
    },
  ]

  it("the picked option's stable value rides the completion record", () => {
    // Labels can collide (two tickets sharing a title); `value` is what the
    // host writes with, so it must survive the trip.
    const onComplete = vi.fn()
    render(<QuestionPopup questions={ASSIGN} onComplete={onComplete} />)
    fireEvent.click(screen.getAllByTestId("question-popup-option")[1])
    expect(onComplete).toHaveBeenCalledWith([
      { prompt: "Who should “Login flow” go to?", answer: "Priya Nair", skipped: false, value: "u-priya" },
    ])
  })

  it("allowOther: false renders no typed escape hatch", () => {
    render(<QuestionPopup questions={ASSIGN} onComplete={vi.fn()} />)
    expect(screen.queryByTestId("question-popup-other")).toBeNull()
    expect(screen.queryByTestId("question-popup-input")).toBeNull()
  })
})

// Reported from a live session: "assign some of the tickets to me" listed all
// ten of a PRD's tickets, and the popup grew tall enough to bury the composer.
// Past five options the list scrolls at five rows. The pixel cap is measured
// from the laid-out rows, which jsdom never produces — so what is asserted here
// is the DECISION (does this question's list scroll?) and the fact that
// scrolling caps rather than truncates. The height itself is browser-only.
describe("QuestionPopup — long option lists scroll", () => {
  const many = (n: number, prompt = "Which tickets would you like assigned to you?"): PopupQuestion => ({
    prompt,
    multiSelect: true,
    allowOther: false,
    options: Array.from({ length: n }, (_, i) => ({
      label: `Ticket ${i + 1}`,
      description: `prd-2967-${i}`,
    })),
  })

  it("past five options the list scrolls, with every option still rendered", () => {
    render(<QuestionPopup questions={[many(10)]} onComplete={vi.fn()} />)
    expect(screen.getByTestId("question-popup-options").className).toContain("qpu-options--scroll")
    // Capped, not truncated — all ten stay reachable by scrolling.
    expect(screen.getAllByTestId("question-popup-option")).toHaveLength(10)
  })

  it("five or fewer render exactly as before", () => {
    render(<QuestionPopup questions={[many(5)]} onComplete={vi.fn()} />)
    const list = screen.getByTestId("question-popup-options")
    expect(list.className).not.toContain("qpu-options--scroll")
    expect(list.getAttribute("style")).toBeNull()
  })

  it("the decision is per question, and a new question starts at the top", () => {
    render(
      <QuestionPopup
        questions={[many(10), { prompt: "Anything else?", options: [{ label: "No" }] }]}
        onComplete={vi.fn()}
      />,
    )
    const list = screen.getByTestId("question-popup-options")
    expect(list.className).toContain("qpu-options--scroll")
    Object.defineProperty(list, "scrollTop", { value: 140, writable: true, configurable: true })

    fireEvent.click(screen.getByLabelText("Next question"))
    const next = screen.getByTestId("question-popup-options")
    expect(next.className).not.toContain("qpu-options--scroll")
    // Same reused node — the short question must not inherit the long one's
    // scroll offset and open on blank space.
    expect(next.scrollTop).toBe(0)
  })

  it("the bottom fade lifts once the last option is in view", () => {
    render(<QuestionPopup questions={[many(10)]} onComplete={vi.fn()} />)
    const list = screen.getByTestId("question-popup-options")
    // jsdom lays nothing out; stand in for the geometry a browser would give.
    Object.defineProperty(list, "clientHeight", { value: 200, configurable: true })
    Object.defineProperty(list, "scrollHeight", { value: 400, configurable: true })
    Object.defineProperty(list, "scrollTop", { value: 40, writable: true, configurable: true })

    fireEvent.scroll(list)
    expect(list.getAttribute("data-scroll-end")).toBeNull()

    list.scrollTop = 200
    fireEvent.scroll(list)
    expect(list.getAttribute("data-scroll-end")).toBe("1")
  })
})

describe("QuestionPopup — nothing answered", () => {
  const PROMPT = "You skipped all 2 questions. Generate the PRD anyway?"
  // THE BUG. Five Skips read to the code exactly like five answers:
  // `onComplete` fired either way and the host went ahead. A PRD was written
  // about a call whose transcript had never been supplied — the questions
  // asking for it were all skipped, and nothing distinguished that from a user
  // who had answered them. Only this component can tell a skip from an answer,
  // so it is the only place the difference can be acted on.
  const TWO_Q = [
    { prompt: "Who are the target users?", options: [{ label: "Admins" }] },
    { prompt: "What is in scope?", options: [{ label: "Everything" }] },
  ]

  const skipAll = () => {
    fireEvent.click(screen.getByTestId("question-popup-skip"))
    fireEvent.click(screen.getByTestId("question-popup-skip"))
  }

  it("holds the batch back and asks instead of completing", () => {
    const onComplete = vi.fn()
    render(<QuestionPopup questions={TWO_Q} onComplete={onComplete} skipAllPrompt={PROMPT} />)
    skipAll()
    expect(screen.getByTestId("question-popup-skip-all-confirm")).toBeTruthy()
    // The host has been told NOTHING. Generation cannot have started.
    expect(onComplete).not.toHaveBeenCalled()
  })

  it("names the artifact when the host says what it is", () => {
    render(
      <QuestionPopup
        questions={TWO_Q}
        onComplete={vi.fn()}
        skipAllPrompt="You skipped all 2 questions. Generate the PRD anyway?"
      />,
    )
    skipAll()
    expect(screen.getByText(/Generate the PRD anyway\?/)).toBeTruthy()
  })

  it("goes ahead on yes, handing over the full skipped record", () => {
    const onComplete = vi.fn()
    render(<QuestionPopup questions={TWO_Q} onComplete={onComplete} skipAllPrompt={PROMPT} />)
    skipAll()
    fireEvent.click(screen.getByTestId("question-popup-skip-all-yes"))
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0][0].map((a: { skipped: boolean }) => a.skipped)).toEqual([
      true,
      true,
    ])
  })

  it("returns to a CLEAN first question on no", () => {
    // Every settled entry is a skip, so leaving them would send the stepper
    // straight back to the confirmation — making it the only reachable screen.
    const onComplete = vi.fn()
    render(<QuestionPopup questions={TWO_Q} onComplete={onComplete} skipAllPrompt={PROMPT} />)
    skipAll()
    fireEvent.click(screen.getByTestId("question-popup-skip-all-no"))
    expect(screen.queryByTestId("question-popup-skip-all-confirm")).toBeNull()
    expect(screen.getByTestId("question-popup-count").textContent).toBe("1/2")
    expect(onComplete).not.toHaveBeenCalled()
  })

  it("lets the second pass through once something is answered", () => {
    const onComplete = vi.fn()
    render(<QuestionPopup questions={TWO_Q} onComplete={onComplete} skipAllPrompt={PROMPT} />)
    skipAll()
    fireEvent.click(screen.getByTestId("question-popup-skip-all-no"))
    fireEvent.click(screen.getAllByTestId("question-popup-option")[0]!)
    fireEvent.click(screen.getByTestId("question-popup-skip"))
    // One real answer — no confirmation, straight through.
    expect(screen.queryByTestId("question-popup-skip-all-confirm")).toBeNull()
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it("never asks when even one question was answered", () => {
    const onComplete = vi.fn()
    render(<QuestionPopup questions={TWO_Q} onComplete={onComplete} skipAllPrompt={PROMPT} />)
    fireEvent.click(screen.getAllByTestId("question-popup-option")[0]!)
    fireEvent.click(screen.getByTestId("question-popup-skip"))
    expect(screen.queryByTestId("question-popup-skip-all-confirm")).toBeNull()
    expect(onComplete).toHaveBeenCalledTimes(1)
  })
})
