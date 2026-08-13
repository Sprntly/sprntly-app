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

  it("skip-all and dismiss are the host's affordances, rendered only when given", () => {
    const onSkipAll = vi.fn()
    const onDismiss = vi.fn()
    const { rerender } = render(
      <QuestionPopup questions={TWO} onComplete={vi.fn()} onSkipAll={onSkipAll} skipAllLabel="Generate now" onDismiss={onDismiss} />,
    )
    expect(screen.getByTestId("question-popup-skip-all").textContent).toBe("Generate now")
    fireEvent.click(screen.getByTestId("question-popup-skip-all"))
    expect(onSkipAll).toHaveBeenCalled()
    fireEvent.click(screen.getByTestId("question-popup-dismiss"))
    expect(onDismiss).toHaveBeenCalled()
    rerender(<QuestionPopup questions={TWO} onComplete={vi.fn()} />)
    expect(screen.queryByTestId("question-popup-skip-all")).toBeNull()
    expect(screen.queryByTestId("question-popup-dismiss")).toBeNull()
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
