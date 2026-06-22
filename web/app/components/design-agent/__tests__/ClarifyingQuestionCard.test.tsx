// @vitest-environment jsdom
//
// ClarifyingQuestionCard — the structured clarifying-question CARD that replaced
// the bare InlineClarifyAnswer surface. Interactive flow (selection → enable
// Continue, write-your-own gating, skip path) needs real state + re-render, so it
// runs under jsdom with render + fireEvent rather than an SSR string.
import * as React from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, fireEvent, within } from "@testing-library/react"

// Classic JSX runtime reads globalThis.React for createElement (this config
// transpiles JSX to React.createElement, matching the sibling node-env tests).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ClarifyingQuestionCard } from "../ClarifyingQuestionCard"
import type { PendingQuestion } from "../../../lib/api"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const TWO_OBJECT_CHOICES: PendingQuestion = {
  question: "Where should the CTA live?",
  choices: [
    { label: "Top of the page", description: "Above the fold, first thing seen" },
    { label: "In the footer", description: "After the supporting content" },
  ],
}

describe("ClarifyingQuestionCard", () => {
  it("disabled-until-choice: Continue is disabled with no selection, enabled after picking an option", () => {
    const { getByTestId, getAllByTestId } = render(
      React.createElement(ClarifyingQuestionCard, {
        question: TWO_OBJECT_CHOICES,
        onAnswer: vi.fn(),
        onSkip: vi.fn(),
      }),
    )
    const cont = getByTestId("da-qcard-continue") as HTMLButtonElement
    expect(cont.disabled).toBe(true)

    fireEvent.click(getAllByTestId("da-qcard-option")[0])
    expect((getByTestId("da-qcard-continue") as HTMLButtonElement).disabled).toBe(false)
  })

  it("graceful-missing-desc: only the with-description option renders a desc node; missing/empty + legacy string render label-only", () => {
    const q: PendingQuestion = {
      question: "Pick a style",
      choices: [
        { label: "Bold", description: "High-contrast, attention-grabbing" },
        { label: "Subtle" }, // no description
        "Plain legacy", // legacy bare string → normalizeChoice → {label}
      ],
    }
    const { getAllByTestId } = render(
      React.createElement(ClarifyingQuestionCard, {
        question: q,
        onAnswer: vi.fn(),
        onSkip: vi.fn(),
      }),
    )
    const opts = getAllByTestId("da-qcard-option")
    // with-desc option: one desc node carrying the text
    const withDesc = within(opts[0]).getAllByTestId("da-qcard-option-desc")
    expect(withDesc).toHaveLength(1)
    expect(withDesc[0].textContent).toBe("High-contrast, attention-grabbing")
    // missing-desc option: NO desc node
    expect(within(opts[1]).queryByTestId("da-qcard-option-desc")).toBeNull()
    // legacy string option: NO desc node, label rendered
    expect(within(opts[2]).queryByTestId("da-qcard-option-desc")).toBeNull()
    expect(opts[2].textContent).toContain("Plain legacy")
  })

  it("write-your-own-gating: reveals input, stays disabled on whitespace, enables on real text, submits trimmed text once", () => {
    const onAnswer = vi.fn()
    const { getByTestId } = render(
      React.createElement(ClarifyingQuestionCard, {
        question: TWO_OBJECT_CHOICES,
        onAnswer,
        onSkip: vi.fn(),
      }),
    )
    fireEvent.click(getByTestId("da-qcard-option-own"))
    const input = getByTestId("da-qcard-own-input") as HTMLInputElement
    expect(input).toBeTruthy()
    expect((getByTestId("da-qcard-continue") as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(input, { target: { value: "   " } })
    expect((getByTestId("da-qcard-continue") as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(input, { target: { value: "  make it teal  " } })
    expect((getByTestId("da-qcard-continue") as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(getByTestId("da-qcard-continue"))
    expect(onAnswer).toHaveBeenCalledTimes(1)
    expect(onAnswer).toHaveBeenCalledWith("make it teal")
  })

  it("skip-dismisses: clicking Skip calls onSkip once and never onAnswer", () => {
    const onAnswer = vi.fn()
    const onSkip = vi.fn()
    const { getByTestId } = render(
      React.createElement(ClarifyingQuestionCard, {
        question: TWO_OBJECT_CHOICES,
        onAnswer,
        onSkip,
      }),
    )
    fireEvent.click(getByTestId("da-qcard-skip"))
    expect(onSkip).toHaveBeenCalledTimes(1)
    expect(onAnswer).not.toHaveBeenCalled()
  })

  it("object-option-continue: continuing with an object choice answers with its LABEL, not the description", () => {
    const onAnswer = vi.fn()
    const { getAllByTestId, getByTestId } = render(
      React.createElement(ClarifyingQuestionCard, {
        question: TWO_OBJECT_CHOICES,
        onAnswer,
        onSkip: vi.fn(),
      }),
    )
    fireEvent.click(getAllByTestId("da-qcard-option")[1])
    fireEvent.click(getByTestId("da-qcard-continue"))
    expect(onAnswer).toHaveBeenCalledTimes(1)
    expect(onAnswer).toHaveBeenCalledWith("In the footer")
  })
})
