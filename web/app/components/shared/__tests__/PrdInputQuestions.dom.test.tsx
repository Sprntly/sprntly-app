// @vitest-environment jsdom
//
// Tests for PrdInputQuestions — the PRD "User input needed" items surfaced as
// chat messages with answer buttons. Drives the REAL container with injected api
// deps (no network): an [ESCALATE] question renders its options as buttons and a
// click routes through answerInputQuestion → onPrdUpdated with the updated PRD; a
// [NEED] question renders a free-text box. Also covers the pure helpers.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
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

// A NEED (data) question that now carries candidate value options → selectable
// buttons, same as an escalate decision.
const needWithOptionsQ: PrdInputQuestion = {
  id: 13,
  prd_id: 1,
  ordinal: 2,
  tag: "need",
  prompt: "Manual follow-up rate today?",
  owner: "Data",
  options: [{ label: "0–20%" }, { label: "20–50%" }, { label: ">50%" }],
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

  it("renders a free-text box for a need question with no options", () => {
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

  it("renders selectable option buttons for a need question that has options", () => {
    render(
      <PrdInputQuestionCard
        question={needWithOptionsQ}
        answerText=""
        onAnswerTextChange={() => {}}
        onChoose={() => {}}
        onSubmitText={() => {}}
      />,
    )
    const choices = screen.getAllByTestId("prd-input-question-choice")
    expect(choices.map((c) => c.textContent)).toEqual(["0–20%", "20–50%", ">50%"])
    // Options lead — the free-text box is hidden behind "Other…" and not shown yet.
    expect(screen.getByTestId("prd-input-question-other")).toBeTruthy()
    expect(screen.queryByTestId("prd-input-question-input")).toBeNull()
  })

  it("reveals the free-text box when 'Other…' is clicked", () => {
    render(
      <PrdInputQuestionCard
        question={needWithOptionsQ}
        answerText=""
        onAnswerTextChange={() => {}}
        onChoose={() => {}}
        onSubmitText={() => {}}
      />,
    )
    expect(screen.queryByTestId("prd-input-question-input")).toBeNull()
    fireEvent.click(screen.getByTestId("prd-input-question-other"))
    expect(screen.getByTestId("prd-input-question-input")).toBeTruthy()
  })

  it("routes an option click through onChoose", () => {
    const onChoose = vi.fn()
    render(
      <PrdInputQuestionCard
        question={needWithOptionsQ}
        answerText=""
        onAnswerTextChange={() => {}}
        onChoose={onChoose}
        onSubmitText={() => {}}
      />,
    )
    fireEvent.click(screen.getAllByTestId("prd-input-question-choice")[1])
    expect(onChoose).toHaveBeenCalledWith("20–50%")
  })

  it("shows an in-progress indicator and marks the picked option while busy", () => {
    render(
      <PrdInputQuestionCard
        question={escalateQ}
        busy
        pendingAnswer="Off"
        answerText=""
        onAnswerTextChange={() => {}}
        onChoose={() => {}}
        onSubmitText={() => {}}
      />,
    )
    // The applying status is announced and every option is disabled.
    expect(screen.getByTestId("prd-input-question-applying")).toBeTruthy()
    const choices = screen.getAllByTestId("prd-input-question-choice")
    expect(choices.every((c) => (c as HTMLButtonElement).disabled)).toBe(true)
    // Only the picked option is marked active/aria-busy.
    const off = choices.find((c) => c.textContent === "Off")!
    expect(off.getAttribute("aria-busy")).toBe("true")
    expect(off.className).toContain("piq-choice--active")
    const on = choices.find((c) => c.textContent === "On")!
    expect(on.getAttribute("aria-busy")).toBe("false")
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

  it("accepts the {questions, extracting} envelope from the real api", async () => {
    const listQuestions = vi
      .fn()
      .mockResolvedValue({ questions: [escalateQ], extracting: false })
    render(<PrdInputQuestions prdId={1} listQuestions={listQuestions} />)
    await waitFor(() => screen.getByTestId("prd-input-question"))
    expect(listQuestions).toHaveBeenCalledTimes(1)
  })

  it("polls while the backend backfills extraction, then renders the questions", async () => {
    // First fetch: a pre-feature PRD opened from Artifacts — no stored rows yet,
    // backend scheduled the extraction. Second fetch: the rows landed.
    vi.useFakeTimers()
    try {
      const listQuestions = vi
        .fn()
        .mockResolvedValueOnce({ questions: [], extracting: true })
        .mockResolvedValueOnce({ questions: [escalateQ], extracting: false })
      const { container } = render(
        <PrdInputQuestions prdId={1} listQuestions={listQuestions} />,
      )

      // Let the first fetch resolve → still nothing rendered, a poll is queued.
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(container.querySelector(".piq-list")).toBeNull()
      expect(listQuestions).toHaveBeenCalledTimes(1)

      // Advance past the poll interval → second fetch lands the questions.
      await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
      expect(listQuestions).toHaveBeenCalledTimes(2)
      expect(screen.getByTestId("prd-input-question")).toBeTruthy()
      // extracting flipped false → polling stops.
      await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
      expect(listQuestions).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it("stops polling on unmount", async () => {
    vi.useFakeTimers()
    try {
      const listQuestions = vi
        .fn()
        .mockResolvedValue({ questions: [], extracting: true })
      const { unmount } = render(
        <PrdInputQuestions prdId={1} listQuestions={listQuestions} />,
      )
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(listQuestions).toHaveBeenCalledTimes(1)
      unmount()
      await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
      expect(listQuestions).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
