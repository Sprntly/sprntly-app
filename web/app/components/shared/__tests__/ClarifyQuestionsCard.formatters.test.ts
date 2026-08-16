// @vitest-environment node
//
// `clarifyQuestionsText`/`clarifyAnswersText` — the clarify gate's durable
// text formatters, relocated into this module (from `ChatScreen.tsx`, which
// now re-exports them) so the private project engine can share them without
// coupling to the 7k-line host. This is a byte-identity guard on the MOVE:
// output for representative inputs must match the pre-relocation strings
// exactly — the golden `.snap` re-running unmodified is the DOM-side proof;
// this is the formatter-output-side proof.
import { describe, expect, it } from "vitest"
import { clarifyAnswersText, clarifyQuestionsText, type ClarifyAnswer, type ClarifyQuestion } from "../ClarifyQuestionsCard"

describe("clarifyQuestionsText — byte-identity (AC6)", () => {
  it("test_clarify_questions_text_output_stable", () => {
    const questions: ClarifyQuestion[] = [
      { prompt: "Who are the target users?", options: ["Admins", "End users"], skip_default: "all end users" },
      { prompt: "How will you measure success?", options: [] },
    ]
    const text = clarifyQuestionsText(questions)
    expect(text).toBe(
      "Before I write this PRD, a few details would make it much stronger. " +
        "Answer what you can in one message — or say \"generate now\" and I'll " +
        "proceed with what I have:\n\n" +
        "1. Who are the target users? (e.g. Admins / End users) — if skipped, I'll assume: all end users" +
        "\n\n" +
        "2. How will you measure success?",
    )
  })

  it("test_clarify_questions_text_no_options_no_skip", () => {
    const questions: ClarifyQuestion[] = [{ prompt: "What's the scope?", options: [] }]
    const text = clarifyQuestionsText(questions)
    expect(text).toContain("1. What's the scope?")
    expect(text).not.toContain("(e.g.")
    expect(text).not.toContain("if skipped")
  })
})

describe("clarifyAnswersText — byte-identity (AC6)", () => {
  it("test_clarify_answers_text_output_stable", () => {
    const answers: ClarifyAnswer[] = [
      { prompt: "Who are the target users?", answer: "Admins" },
      { prompt: "How will you measure success?", answer: "Fewer support tickets" },
    ]
    expect(clarifyAnswersText(answers)).toBe(
      "Who are the target users?\nAdmins\n\nHow will you measure success?\nFewer support tickets",
    )
  })

  it("test_clarify_answers_text_empty_batch", () => {
    expect(clarifyAnswersText([])).toBe("")
  })
})
