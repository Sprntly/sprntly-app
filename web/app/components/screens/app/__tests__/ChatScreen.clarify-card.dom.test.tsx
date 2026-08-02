// @vitest-environment jsdom
//
// ChatScreen — the clarify-first gate's two reported problems.
//
// (1) THE FALSE ACKNOWLEDGMENT. "generate a PRD for X" used to answer instantly
//     with "Generating a PRD for that — it'll open in the panel on the right…",
//     persisted to the conversation, and only THEN discover the task was too
//     thin — leaving that promise sitting in the thread above the questions that
//     contradicted it. The ack is now deferred until the gate settles: it is
//     written only when generation actually starts, and the questions take its
//     place when they don't.
//
// (2) THE QUESTIONS THEMSELVES. The backend returns each question with 2–4
//     candidate `options`; the chat flattened them into a numbered markdown list
//     and made the user re-type prose answering four things at once. They now
//     render as ONE answerable card — options as buttons, one submit for the
//     batch — while answering in the composer keeps working exactly as before.
//
// Also guards the third report: a multi-line ask must keep its line breaks when
// it lands in the thread.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = () => {}
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const { generateFromTask, classifyCommand, clarifyTask } = vi.hoisted(() => ({
  generateFromTask: vi.fn().mockResolvedValue({ prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3" }),
  classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
  clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
}))
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 7, insights: [{ title: "x" }] }) },
    prdApi: { generateFromTask, classifyCommand, clarifyTask },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
      update: vi.fn().mockResolvedValue({}),
    },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn().mockResolvedValue({ ok: true, prd: { prd_id: 77, title: "Generated PRD", metaLine: "", sections: [] } }),
  resumePrdGeneration: vi.fn().mockResolvedValue({ ok: true, prd: { prd_id: 501, title: "Dark mode on mobile", metaLine: "", sections: [] } }),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: vi.fn(),
}))

const runAskGeneration = vi.fn().mockResolvedValue({
  answer: "canned", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
})
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: (...args: unknown[]) => runAskGeneration(...args),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams("new=1"),
}))
vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({ loading: false, profile: null, workspace: null, refresh: async () => {} }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

const { protoMap } = vi.hoisted(() => ({ protoMap: new Map<number, unknown>() }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: protoMap, loading: false, error: false, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen, clarifyQuestionsText, clarifyAnswersText } from "../ChatScreen"

const QUESTIONS = {
  sufficient: false,
  missing: ["Target users", "Success criteria"],
  questions: [
    { prompt: "Who are the target users?", options: ["Admins", "End users"], skip_default: "all end users" },
    { prompt: "How will you measure success?", options: [] },
  ],
}

function renderChat() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

async function typeAndSend(text: string) {
  const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
  expect(textarea).toBeTruthy()
  await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
  await act(async () => { fireEvent.click(sendBtn) })
}

const questionCards = () => Array.from(document.querySelectorAll('[data-testid="clarify-question"]'))

/** After a simulated reload the harness's ?new=1 activates a fresh landing tab;
 *  the restored thread tab sits first in the strip — click it. */
async function openRestoredTab() {
  await waitFor(() => expect(screen.queryAllByTitle("Close tab").length).toBeGreaterThan(0))
  const tabEl = screen.queryAllByTitle("Close tab")[0].parentElement as HTMLElement
  await act(async () => { fireEvent.click(tabEl) })
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  protoMap.clear()
  runAskGeneration.mockClear()
  generateFromTask.mockClear()
  classifyCommand.mockClear()
  clarifyTask.mockClear()
  clarifyTask.mockResolvedValue({ sufficient: true, questions: [], missing: [] })
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — the PRD command's acknowledgment is deferred until the gate settles", () => {
  // The persistence half of this — that the ack is not WRITTEN either, and the
  // questions take its place as the command turn's reply — is asserted in
  // ChatScreen.prd-command.dom.test.tsx, whose conversationsApi mock reaches the
  // screen's dynamic import.
  it("shows no acknowledgment above the questions", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(document.body.textContent).toContain("Who are the target users?"))
    expect(document.body.textContent).not.toContain("Generating a PRD for that")
    expect(document.body.textContent).not.toContain("View PRD button")
  })

  it("keeps the command turn's own message and identity intact", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())
    // The questions answer the COMMAND turn rather than arriving as a second,
    // author-less message — one user bubble, one agent body.
    const bubbles = Array.from(document.querySelectorAll(".bc-user-bubble"))
    expect(bubbles).toHaveLength(1)
    expect(bubbles[0].textContent).toBe("generate a PRD for dark mode on mobile")
  })
})

describe("ChatScreen — clarifying questions as an answerable card", () => {
  it("renders the backend's options as buttons instead of flattening them into prose", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())
    const cards = questionCards()
    expect(cards).toHaveLength(2)
    const choices = within(cards[0] as HTMLElement).getAllByTestId("clarify-choice")
    expect(choices.map((b) => b.textContent)).toEqual(["Admins", "End users"])
    // The option-less question gets a free-text box outright.
    expect(within(cards[1] as HTMLElement).getByTestId("clarify-input")).toBeTruthy()
    expect(generateFromTask).not.toHaveBeenCalled()
  })

  it("submits the whole batch at once and generates with the answers folded in", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())

    await act(async () => {
      fireEvent.click(within(questionCards()[0] as HTMLElement).getAllByTestId("clarify-choice")[0])
    })
    await act(async () => {
      fireEvent.change(within(questionCards()[1] as HTMLElement).getByTestId("clarify-input"), {
        target: { value: "30% fewer support tickets" },
      })
    })
    // Nothing has been sent yet — the batch goes on one submit.
    expect(generateFromTask).not.toHaveBeenCalled()

    await act(async () => { fireEvent.click(screen.getByTestId("clarify-submit")) })

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    const combined = generateFromTask.mock.calls[0][0] as string
    expect(combined).toContain("dark mode on mobile")
    expect(combined).toContain("Additional details from the user:")
    // Q/A pairs, so the generator can tell which question each answer settles.
    expect(combined).toContain("Who are the target users?\nAdmins")
    expect(combined).toContain("How will you measure success?\n30% fewer support tickets")
    // The answers were NOT misrouted to the ask agent.
    expect(runAskGeneration).not.toHaveBeenCalled()
    // The input form is spent, so a second click can't kick another generation…
    await waitFor(() => expect(screen.queryByTestId("clarify-questions")).toBeNull())
    // …but the batch does NOT revert to the flattened wall of text. It stays a
    // formatted record of what was decided.
    const record = screen.getByTestId("clarify-questions-resolved")
    expect(within(record).getAllByTestId("clarify-question")).toHaveLength(2)
    expect(within(record).getAllByTestId("clarify-answer").map((n) => n.textContent))
      .toEqual(["✓Admins", "✓30% fewer support tickets"])
    // Read-only: nothing left to click or type.
    expect(within(record).queryByTestId("clarify-choice")).toBeNull()
    expect(within(record).queryByTestId("clarify-input")).toBeNull()
    expect(within(record).queryByTestId("clarify-submit")).toBeNull()
  })

  it("a question left blank shows the assumption it fell back to, not a gap", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())

    // Answer only Q1; Q2 (no options, no skip_default) is left alone.
    await act(async () => {
      fireEvent.click(within(questionCards()[0] as HTMLElement).getAllByTestId("clarify-choice")[1])
    })
    await act(async () => { fireEvent.click(screen.getByTestId("clarify-submit")) })

    await waitFor(() => expect(screen.getByTestId("clarify-questions-resolved")).toBeTruthy())
    const record = screen.getByTestId("clarify-questions-resolved")
    expect(within(record).getByTestId("clarify-answer").textContent).toBe("✓End users")
    // The blank one didn't vanish — it resolved to its stated default, and the
    // PRD was written on that basis. Saying so is the honest record.
    expect(within(record).getByTestId("clarify-answer-skipped").textContent).toBe("Skipped")
    expect(record.textContent).toContain("1 of 2 answered")
  })

  it("'Generate now' leaves a record of the assumptions it proceeded on", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())

    await act(async () => { fireEvent.click(screen.getByTestId("clarify-skip")) })

    await waitFor(() => expect(screen.getByTestId("clarify-questions-resolved")).toBeTruthy())
    const record = screen.getByTestId("clarify-questions-resolved")
    // Q1 carries a skip_default, so the assumption is named rather than implied.
    expect(record.textContent).toContain("Skipped — assumed: all end users")
    expect(within(record).queryByTestId("clarify-answer")).toBeNull()
  })

  it("answering in the composer keeps the record too, without inventing a mapping", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())

    const threadInput = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => { fireEvent.change(threadInput, { target: { value: "admins only" } }) })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
    await act(async () => { fireEvent.click(sendBtn) })

    await waitFor(() => expect(screen.getByTestId("clarify-questions-resolved")).toBeTruthy())
    const record = screen.getByTestId("clarify-questions-resolved")
    // Free text has no per-question mapping, so the record points at the reply
    // rather than claiming answers or skips that were never made.
    expect(record.textContent).toContain("Answered in your reply below")
    expect(record.textContent).not.toContain("assumed:")
  })

  it("'Generate now' on the card generates from the ORIGINAL task", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())

    await act(async () => { fireEvent.click(screen.getByTestId("clarify-skip")) })

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(generateFromTask.mock.calls[0][0]).toBe("dark mode on mobile")
  })

  it("still accepts a prose answer typed in the composer — the card is an addition, not a replacement", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())

    const threadInput = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => { fireEvent.change(threadInput, { target: { value: "admins only" } }) })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
    await act(async () => { fireEvent.click(sendBtn) })

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(generateFromTask.mock.calls[0][0]).toContain("admins only")
  })
})

describe("ChatScreen — the clarify gate's failure windows", () => {
  it("a reload mid-gate restores an honest 'interrupted' note, not 'No response was generated'", async () => {
    // Hold the gate open so the deferred reply never lands — then simulate a
    // reload by unmounting and re-rendering from sessionStorage.
    clarifyTask.mockImplementationOnce(() => new Promise(() => {}))
    const view = renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() =>
      expect(document.querySelector('[data-testid="prd-command-thinking"]')).toBeTruthy())

    view.unmount()
    renderChat()
    // The harness's ?new=1 lands the remount on a fresh tab; the restored
    // command tab is still in the strip — activate it, as a user would.
    await openRestoredTab()

    // The command is back, with the truth: interrupted, retryable — never the
    // dead-end "No response was generated for this message."
    await waitFor(() =>
      expect(document.body.textContent).toContain("generate a PRD for dark mode on mobile"))
    expect(screen.getByTestId("turn-interrupted").textContent).toContain("send it again")
    expect(document.body.textContent).not.toContain("No response was generated for this message.")
    // And it isn't sticky: the marker lives only in the saved copy of an
    // UNSETTLED gate, so the restored (idle) tab persists back clean.
    view.unmount()
  })

  it("a normal settle leaves no interrupted marker behind in storage", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    const view = renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())

    view.unmount()
    renderChat()
    await openRestoredTab()
    await waitFor(() =>
      expect(document.body.textContent).toContain("Who are the target users?"))
    expect(screen.queryByTestId("turn-interrupted")).toBeNull()
  })

  it("holds a message sent while the gate is deciding — the pair order can't invert", async () => {
    let resolveClarify!: (v: unknown) => void
    clarifyTask.mockImplementationOnce(() => new Promise((res) => { resolveClarify = res }))
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() =>
      expect(document.querySelector('[data-testid="prd-command-thinking"]')).toBeTruthy())

    // A send INSIDE the decision window is held, not dispatched: dispatching it
    // would queue its user turn ahead of the deferred assistant write and
    // invert the persisted user→assistant pairing (and a second PRD command
    // would cross-wire deferredAckRef).
    const threadInput = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => { fireEvent.change(threadInput, { target: { value: "also generate a PRD for exports" } }) })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
    await act(async () => { fireEvent.click(sendBtn) })

    expect(runAskGeneration).not.toHaveBeenCalled()
    expect(generateFromTask).not.toHaveBeenCalled()
    // The message is handed back, not eaten.
    expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value)
      .toBe("also generate a PRD for exports")

    // Gate settles → the held message can go through normally.
    await act(async () => { resolveClarify(QUESTIONS) })
    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())
  })

  it("clamps an over-long combined task to the backend's 4000-char cap", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())

    // A card answer long enough to blow past the cap once the prompts are
    // echoed in — without the clamp this 422s AFTER the ack is on screen.
    await act(async () => {
      fireEvent.change(within(questionCards()[1] as HTMLElement).getByTestId("clarify-input"), {
        target: { value: "x".repeat(5000) },
      })
    })
    await act(async () => { fireEvent.click(screen.getByTestId("clarify-submit")) })

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    const sent = generateFromTask.mock.calls[0][0] as string
    expect(sent.length).toBeLessThanOrEqual(4000)
    expect(sent).toContain("dark mode on mobile") // the head survives
  })
})

describe("ChatScreen — a multi-line ask keeps its line breaks", () => {
  it("carries the newlines into the thread AND the conversation row", async () => {
    renderChat()
    const multiline = "Two things:\n- the build board is slow\n- CSV export fails silently"
    await typeAndSend(multiline)

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    const bubble = Array.from(document.querySelectorAll(".bc-user-bubble"))
      .find((b) => b.textContent?.includes("the build board is slow"))
    expect(bubble).toBeTruthy()
    // The text reaches the DOM with its structure intact — `white-space:
    // pre-wrap` on .bc-user-bubble is what stops the browser collapsing it.
    expect(bubble!.textContent).toBe(multiline)
  })
})

describe("clarify text helpers", () => {
  it("keeps the durable question list blank-line separated, and its matched opening", () => {
    const text = clarifyQuestionsText(QUESTIONS.questions)
    // PRD_ACK_ANSWER_RE / PRD_CLARIFY_ANSWER_RE match on TEXT to keep Sprntly's
    // process chatter out of the PRD's grounding transcript — this opening is
    // load-bearing.
    expect(text.startsWith("Before I write this PRD")).toBe(true)
    // Single newlines are soft-wrapped away by the markdown renderer, which ran
    // the whole list together on one line.
    expect(text).toContain("1. Who are the target users? (e.g. Admins / End users) — if skipped, I'll assume: all end users\n\n2.")
  })

  it("renders card answers as Q/A pairs", () => {
    expect(clarifyAnswersText([
      { prompt: "Who are the target users?", answer: "Admins" },
      { prompt: "Scope for v1?", answer: "The two account-risk items" },
    ])).toBe("Who are the target users?\nAdmins\n\nScope for v1?\nThe two account-risk items")
  })
})
