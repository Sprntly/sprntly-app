// @vitest-environment jsdom
//
// Tests for ClarifyQuestionsCard — the clarify-first gate's question batch as
// ONE answerable card. Covers the affordances that replaced the flattened
// numbered list: options as selectable buttons, an "Other…" free-text escape
// hatch, per-question skip defaults, and a single submit that carries the whole
// batch (Claude-terminal style) rather than one round-trip per question.
import * as React from "react"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { ClarifyQuestionsCard, type ClarifyQuestion } from "../ClarifyQuestionsCard"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const QUESTIONS: ClarifyQuestion[] = [
  {
    prompt: "Who are the target users?",
    options: ["Admins", "End users"],
    skip_default: "all end users",
  },
  { prompt: "How will you measure success?", options: [] },
  {
    prompt: "What is in scope for v1?",
    options: ["Everything", "The two account-risk items"],
    skip_default: "all five surfaces",
  },
]

function renderCard(overrides: Partial<React.ComponentProps<typeof ClarifyQuestionsCard>> = {}) {
  const onSubmit = vi.fn()
  const onSkip = vi.fn()
  render(
    <ClarifyQuestionsCard
      questions={QUESTIONS}
      onSubmit={onSubmit}
      onSkip={onSkip}
      {...overrides}
    />,
  )
  return { onSubmit, onSkip }
}

const questionCards = () => Array.from(document.querySelectorAll('[data-testid="clarify-question"]'))
const submitBtn = () => screen.getByTestId("clarify-submit")

describe("ClarifyQuestionsCard", () => {
  it("renders every question in sequence, with its options as buttons", () => {
    renderCard()

    const cards = questionCards()
    expect(cards).toHaveLength(3)
    expect(cards[0].textContent).toContain("Who are the target users?")
    expect(cards[1].textContent).toContain("How will you measure success?")
    expect(cards[2].textContent).toContain("What is in scope for v1?")

    // The backend's `options` become buttons — the whole point of the card. The
    // flattened list rendered them as "(e.g. Admins / End users)" prose.
    const choices = within(cards[0] as HTMLElement).getAllByTestId("clarify-choice")
    expect(choices.map((b) => b.textContent)).toEqual(["Admins", "End users"])
  })

  it("shows a free-text box outright for a question with NO options", () => {
    renderCard()
    // Q2 has no options → the box leads. Q1/Q3 hide theirs behind "Other…".
    expect(within(questionCards()[1] as HTMLElement).getByTestId("clarify-input")).toBeTruthy()
    expect(within(questionCards()[0] as HTMLElement).queryByTestId("clarify-input")).toBeNull()
  })

  it("states each question's skip assumption, so a skip is informed rather than silent", () => {
    renderCard()
    expect(document.body.textContent).toContain("if skipped, I'll assume: all end users")
    expect(document.body.textContent).toContain("if skipped, I'll assume: all five surfaces")
  })

  it("submits the WHOLE batch in one go — nothing is sent per question", () => {
    const { onSubmit, onSkip } = renderCard()

    fireEvent.click(within(questionCards()[0] as HTMLElement).getAllByTestId("clarify-choice")[0])
    fireEvent.change(within(questionCards()[1] as HTMLElement).getByTestId("clarify-input"), {
      target: { value: "30% fewer support tickets" },
    })
    // Still nothing sent — the user is free to move between questions.
    expect(onSubmit).not.toHaveBeenCalled()

    fireEvent.click(submitBtn())
    expect(onSkip).not.toHaveBeenCalled()
    expect(onSubmit).toHaveBeenCalledTimes(1)
    // Only ANSWERED questions ride along; Q3 was left to its stated default.
    expect(onSubmit.mock.calls[0][0]).toEqual([
      { prompt: "Who are the target users?", answer: "Admins" },
      { prompt: "How will you measure success?", answer: "30% fewer support tickets" },
    ])
  })

  it("tracks how many are answered, so the batch's state is visible before submit", () => {
    renderCard()
    expect(screen.getByTestId("clarify-count").textContent).toBe("0 of 3 answered")

    fireEvent.click(within(questionCards()[0] as HTMLElement).getAllByTestId("clarify-choice")[1])
    expect(screen.getByTestId("clarify-count").textContent).toBe("1 of 3 answered")
  })

  it("deselects a mis-tapped option on a second click", () => {
    const { onSubmit, onSkip } = renderCard()
    const admins = within(questionCards()[0] as HTMLElement).getAllByTestId("clarify-choice")[0]

    fireEvent.click(admins)
    expect(admins.getAttribute("aria-pressed")).toBe("true")
    fireEvent.click(admins)
    expect(admins.getAttribute("aria-pressed")).toBe("false")

    // Back to nothing answered → submitting is a skip, not an empty answer set.
    fireEvent.click(submitBtn())
    expect(onSubmit).not.toHaveBeenCalled()
    expect(onSkip).toHaveBeenCalledTimes(1)
  })

  it("'Other…' reveals a box whose text BEATS a previously picked option", () => {
    const { onSubmit } = renderCard()
    const q1 = () => questionCards()[0] as HTMLElement

    fireEvent.click(within(q1()).getAllByTestId("clarify-choice")[0]) // Admins
    fireEvent.click(within(q1()).getByTestId("clarify-other"))
    fireEvent.change(within(q1()).getByTestId("clarify-input"), {
      target: { value: "workspace owners only" },
    })

    fireEvent.click(submitBtn())
    expect(onSubmit.mock.calls[0][0]).toEqual([
      { prompt: "Who are the target users?", answer: "workspace owners only" },
    ])
  })

  it("'Generate now' skips the batch outright", () => {
    const { onSubmit, onSkip } = renderCard()
    fireEvent.click(screen.getByTestId("clarify-skip"))
    expect(onSkip).toHaveBeenCalledTimes(1)
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it("⌘/Ctrl+Enter in any box submits the batch without reaching for the button", () => {
    const { onSubmit } = renderCard()
    fireEvent.change(within(questionCards()[1] as HTMLElement).getByTestId("clarify-input"), {
      target: { value: "p95 under 2s" },
    })
    fireEvent.keyDown(within(questionCards()[1] as HTMLElement).getByTestId("clarify-input"), {
      key: "Enter",
      metaKey: true,
    })
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it("goes inert while busy — a double submit can't kick a second generation", () => {
    const { onSubmit, onSkip } = renderCard({ busy: true })
    expect(submitBtn().textContent).toContain("Generating PRD…")

    fireEvent.click(submitBtn())
    fireEvent.click(screen.getByTestId("clarify-skip"))
    fireEvent.click(within(questionCards()[0] as HTMLElement).getAllByTestId("clarify-choice")[0])
    expect(onSubmit).not.toHaveBeenCalled()
    expect(onSkip).not.toHaveBeenCalled()
  })

  it("keeps pointing at the composer — answering in prose still works", () => {
    renderCard()
    expect(document.body.textContent).toContain("generate now")
  })
})

// Answering used to collapse the batch back to the flattened numbered list —
// the structure was discarded at the exact moment it became a record of a
// decision. Settled batches keep their shape and gain the answers.
describe("ClarifyQuestionsCard — the settled record", () => {
  const answered = (mode: "card" | "chat" | "skip", answers: { prompt: string; answer: string }[] = []) =>
    renderCard({ resolved: { answers, mode } })

  it("keeps the same questions, in the same order and numbering", () => {
    answered("card", [{ prompt: "Who are the target users?", answer: "Admins" }])
    const cards = questionCards()
    expect(cards).toHaveLength(3)
    expect(cards.map((c) => c.textContent?.slice(0, 2))).toEqual(["1W", "2H", "3W"])
  })

  it("shows each answer against the question it settles", () => {
    answered("card", [
      { prompt: "Who are the target users?", answer: "Admins" },
      { prompt: "What is in scope for v1?", answer: "The two account-risk items" },
    ])
    const shown = screen.getAllByTestId("clarify-answer").map((n) => n.textContent)
    expect(shown).toEqual(["✓Admins", "✓The two account-risk items"])
    expect(screen.getByTestId("clarify-questions-resolved").textContent).toContain("2 of 3 answered")
  })

  it("names the assumption a blank question fell back to, rather than showing a gap", () => {
    answered("card", [{ prompt: "How will you measure success?", answer: "p95 under 2s" }])
    const skipped = screen.getAllByTestId("clarify-answer-skipped").map((n) => n.textContent)
    expect(skipped).toEqual([
      "Skipped — assumed: all end users",
      "Skipped — assumed: all five surfaces",
    ])
  })

  it("a full skip records every assumption the PRD was written on", () => {
    answered("skip")
    expect(screen.queryByTestId("clarify-answer")).toBeNull()
    expect(screen.getAllByTestId("clarify-answer-skipped")).toHaveLength(3)
    expect(screen.getByTestId("clarify-questions-resolved").textContent)
      .toContain("Generated without these")
  })

  it("a prose reply points at the reply instead of inventing per-question outcomes", () => {
    answered("chat")
    const record = screen.getByTestId("clarify-questions-resolved")
    expect(record.textContent).toContain("Answered in your reply below")
    // Free text settled nothing question-by-question, so claiming a skip (and
    // an assumption the user never saw applied) would be a lie.
    expect(record.textContent).not.toContain("assumed:")
    expect(record.textContent).not.toContain("Skipped —")
  })

  it("is inert — no options, no inputs, no submit", () => {
    const { onSubmit, onSkip } = answered("card", [
      { prompt: "Who are the target users?", answer: "Admins" },
    ])
    expect(screen.queryByTestId("clarify-choice")).toBeNull()
    expect(screen.queryByTestId("clarify-input")).toBeNull()
    expect(screen.queryByTestId("clarify-submit")).toBeNull()
    expect(screen.queryByTestId("clarify-skip")).toBeNull()
    expect(screen.queryByTestId("clarify-questions")).toBeNull()
    expect(onSubmit).not.toHaveBeenCalled()
    expect(onSkip).not.toHaveBeenCalled()
  })

  it("matches answers by prompt, so reordered questions still line up", () => {
    // The resolution is keyed on the question text, not on index — a defensive
    // property, since the answers round-trip through state before rendering.
    answered("card", [{ prompt: "What is in scope for v1?", answer: "Everything" }])
    const cards = questionCards()
    expect(within(cards[2] as HTMLElement).getByTestId("clarify-answer").textContent)
      .toBe("✓Everything")
    expect(within(cards[0] as HTMLElement).getByTestId("clarify-answer-skipped")).toBeTruthy()
  })
})
