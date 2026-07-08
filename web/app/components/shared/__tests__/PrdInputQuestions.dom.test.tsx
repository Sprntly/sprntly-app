// @vitest-environment jsdom
//
// Tests for PrdInputQuestions — the PRD "User input needed" items surfaced as
// chat messages with answer buttons. Drives the REAL container with injected api
// deps (no network): an [ESCALATE] question renders its options as buttons and a
// click routes through answerInputQuestion → onPrdUpdated with the updated PRD; a
// [NEED] question renders a free-text box. Also covers the pure helpers.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import {
  PrdInputQuestions,
  PrdInputQuestionCard,
  changedSectionsLine,
  prdStateFromRecord,
} from "../PrdInputQuestions"
import type { PrdInputQuestion, PrdRecord } from "../../../lib/api"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const escalateQ: PrdInputQuestion = {
  id: 11,
  prd_id: 1,
  ordinal: 0,
  tag: "escalate",
  prompt: "Reminders on by default?",
  owner: "PM",
  options: [{ label: "On" }, { label: "Off", description: "less noise" }],
  status: "pending",
}

const needQ: PrdInputQuestion = {
  id: 12,
  prd_id: 1,
  ordinal: 1,
  tag: "need",
  prompt: "Manual follow-up rate today?",
  owner: "Data",
  options: [],
  status: "pending",
}

const answeredRecord: PrdRecord = {
  id: 1,
  brief_id: 5,
  insight_index: 0,
  generated_at: "2026-07-08T00:00:00Z",
  title: "PRD",
  payload_md: "<!DOCTYPE html><html><body>GATED</body></html>",
  status: "ready",
}

describe("changedSectionsLine", () => {
  it("formats zero / one / many sections", () => {
    expect(changedSectionsLine([])).toBe("Updated the PRD.")
    expect(changedSectionsLine(["Requirements"])).toBe("Updated Requirements.")
    expect(changedSectionsLine(["Requirements", "Goal"])).toBe(
      "Updated Requirements and Goal.",
    )
    expect(changedSectionsLine(["A", "B", "C"])).toBe("Updated A, B and C.")
  })
})

describe("prdStateFromRecord", () => {
  it("carries the ids + detects the HTML document", () => {
    const prd = prdStateFromRecord(answeredRecord)
    expect(prd.prd_id).toBe(1)
    expect(prd.briefId).toBe(5)
    expect(prd.insightIndex).toBe(0)
    // markdownToPrdState routes a full HTML doc to `html` (iframe-rendered).
    expect(prd.html).toContain("GATED")
  })
})

describe("PrdInputQuestionCard", () => {
  it("renders option buttons for an escalate question", () => {
    render(
      <PrdInputQuestionCard
        question={escalateQ}
        answerText=""
        onAnswerTextChange={() => {}}
        onChoose={() => {}}
        onSubmitText={() => {}}
      />,
    )
    const choices = screen.getAllByTestId("prd-input-question-choice")
    expect(choices.map((c) => c.textContent)).toEqual(["On", "Off"])
    expect(screen.queryByTestId("prd-input-question-input")).toBeNull()
  })

  it("renders a free-text box for a need question", () => {
    render(
      <PrdInputQuestionCard
        question={needQ}
        answerText=""
        onAnswerTextChange={() => {}}
        onChoose={() => {}}
        onSubmitText={() => {}}
      />,
    )
    expect(screen.getByTestId("prd-input-question-input")).toBeTruthy()
    expect(screen.queryByTestId("prd-input-question-choice")).toBeNull()
  })

  it("shows a resolved line once answered", () => {
    render(
      <PrdInputQuestionCard
        question={{ ...escalateQ, status: "answered", answer: "On" }}
        resolvedLine="Updated Requirements."
        answerText=""
        onAnswerTextChange={() => {}}
        onChoose={() => {}}
        onSubmitText={() => {}}
      />,
    )
    const resolved = screen.getByTestId("prd-input-question-resolved")
    expect(resolved.textContent).toContain("On")
    expect(resolved.textContent).toContain("Updated Requirements.")
    expect(screen.queryByTestId("prd-input-question-choice")).toBeNull()
  })
})

describe("PrdInputQuestions container", () => {
  it("loads questions and routes a choice click to the answer api + onPrdUpdated", async () => {
    const listQuestions = vi.fn().mockResolvedValue([escalateQ])
    const answerQuestion = vi.fn().mockResolvedValue({
      prd: answeredRecord,
      question: { ...escalateQ, status: "answered", answer: "On" },
      sections_changed: ["Requirements", "Goal"],
      summary: "Reminders default on.",
    })
    const onPrdUpdated = vi.fn()

    render(
      <PrdInputQuestions
        prdId={1}
        onPrdUpdated={onPrdUpdated}
        listQuestions={listQuestions}
        answerQuestion={answerQuestion}
      />,
    )

    // The question renders once the injected loader resolves.
    await waitFor(() => screen.getByTestId("prd-input-question"))
    fireEvent.click(screen.getAllByTestId("prd-input-question-choice")[0])

    await waitFor(() => expect(answerQuestion).toHaveBeenCalledWith(1, 11, "On"))
    await waitFor(() => expect(onPrdUpdated).toHaveBeenCalledTimes(1))
    const passed = onPrdUpdated.mock.calls[0][0]
    expect(passed.prd_id).toBe(1)
    expect(passed.html).toContain("GATED")

    // The question flips to a resolved line with the changed-sections summary.
    await waitFor(() => screen.getByTestId("prd-input-question-resolved"))
    expect(screen.getByTestId("prd-input-question-resolved").textContent).toContain(
      "Updated Requirements and Goal.",
    )
  })

  it("renders nothing when there are no questions", async () => {
    const { container } = render(
      <PrdInputQuestions prdId={1} listQuestions={vi.fn().mockResolvedValue([])} />,
    )
    await waitFor(() => expect(container.querySelector(".piq-list")).toBeNull())
  })
})
