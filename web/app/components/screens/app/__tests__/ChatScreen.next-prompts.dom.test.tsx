// @vitest-environment jsdom
//
// ChatScreen — next-prompt suggestions, and the silence around them.
//
// The lifecycle contract, all of it observable from the DOM:
//   - the suggestions request is made ONLY after the answer has rendered, never
//     before or during, so it cannot delay or break the answer;
//   - an empty list renders nothing at all (the acceptance criterion);
//   - a rejected request is indistinguishable from an empty one;
//   - chips clear the instant the user sends again, and a response that arrives
//     late — after that next send — is discarded rather than shown under a
//     question it no longer follows from;
//   - clicking a chip sends it as an ordinary message.
import * as React from "react"
import { act, cleanup, fireEvent, waitFor, within, render } from "@testing-library/react"
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

const { resolveIntent, nextSuggestions, addTurn } = vi.hoisted(() => ({
  resolveIntent: vi.fn(),
  nextSuggestions: vi.fn(),
  addTurn: vi.fn(),
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
    prdApi: {
      generateFromTask: vi.fn(),
      classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
      clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
    },
    chatIntentApi: { resolve: resolveIntent },
    chatSuggestionsApi: { next: nextSuggestions },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 11 }),
      addTurn,
    },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: vi.fn(),
}))

const { runAskGeneration } = vi.hoisted(() => ({ runAskGeneration: vi.fn() }))
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
  useWorkspace: () => ({
    loading: false, profile: null,
    workspace: { feature_flags: { chat_intent_envelope: true } },
    refresh: async () => {},
  }),
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
import { ChatScreen } from "../ChatScreen"

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

const ANSWER_ENVELOPE = {
  intent: "answer", confidence: 0.9, task: null, instruction: null,
  reason: "plain question", source: "llm", prd_id: null, prd_title: null,
}

const REPLY = {
  answer: "Promo codes drop the session — 23 tickets this month.",
  sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
}

const strip = () => document.querySelector('[data-testid="next-prompt-suggestions"]')
const chips = () =>
  Array.from(strip()?.querySelectorAll("button") ?? []).map((b) => b.textContent)

/** Deferred ask, released by hand so "during" and "after" are distinguishable. */
function deferAsk() {
  let release!: (reply: typeof REPLY) => void
  runAskGeneration.mockImplementation(
    () => new Promise((res) => { release = res as (r: typeof REPLY) => void }),
  )
  return { release: (reply: typeof REPLY) => act(async () => { release(reply) }) }
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  protoMap.clear()
  runAskGeneration.mockReset()
  resolveIntent.mockReset()
  resolveIntent.mockResolvedValue(ANSWER_ENVELOPE)
  nextSuggestions.mockReset()
  nextSuggestions.mockResolvedValue({ suggestions: [] })
  addTurn.mockReset()
  addTurn.mockResolvedValue({})
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — next-prompt suggestions", () => {
  it("shows suggestion chips only AFTER the answer has rendered", async () => {
    const ask = deferAsk()
    nextSuggestions.mockResolvedValue({
      suggestions: ["Break the promo code bug into tickets", "Draft a PRD for the fix"],
    })
    renderChat()
    await typeAndSend("what are the top complaints about checkout?")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))

    // While the answer generates, nothing has been requested and nothing shows:
    // the suggestion call must never sit in front of the answer.
    expect(nextSuggestions).not.toHaveBeenCalled()
    expect(strip()).toBeNull()

    await ask.release(REPLY)
    await waitFor(() => expect(document.body.textContent).toContain("23 tickets this month"))
    await waitFor(() => expect(strip()).toBeTruthy())
    expect(chips()).toEqual([
      "Break the promo code bug into tickets",
      "Draft a PRD for the fix",
    ])
    // Grounded on the thread the answer belongs to.
    expect(nextSuggestions).toHaveBeenCalledWith(11, { prdId: null })
  })

  it("waits for the assistant turn to be PERSISTED before asking for suggestions", async () => {
    // The backend reads the thread out of the database. Firing before this
    // turn's row lands would ask what comes next about a conversation that is
    // missing the exchange it should continue — and on a first message the
    // thread would look empty and abstain every single time.
    const ask = deferAsk()
    // Only the ASSISTANT write is held. The user turn must still settle, since
    // chatPersistence serializes a tab's appends and the assistant row is
    // queued behind it.
    let finishPersist!: () => void
    addTurn.mockImplementation((_convId: number, role: string) =>
      role === "assistant"
        ? new Promise((res) => { finishPersist = () => res({}) })
        : Promise.resolve({}),
    )
    nextSuggestions.mockResolvedValue({ suggestions: ["Break the promo code bug into tickets"] })
    renderChat()
    await typeAndSend("what are the top complaints about checkout?")
    await ask.release(REPLY)

    // The answer is on screen, but the assistant turn is still being written.
    await waitFor(() => expect(document.body.textContent).toContain("23 tickets this month"))
    await act(async () => { await Promise.resolve() })
    expect(nextSuggestions).not.toHaveBeenCalled()

    await act(async () => { finishPersist() })
    await waitFor(() => expect(nextSuggestions).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(strip()).toBeTruthy())
  })

  it("AN EMPTY LIST RENDERS NOTHING — no container, no placeholder", async () => {
    const ask = deferAsk()
    nextSuggestions.mockResolvedValue({ suggestions: [] })
    renderChat()
    await typeAndSend("thanks!")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))
    await ask.release(REPLY)
    await waitFor(() => expect(nextSuggestions).toHaveBeenCalledTimes(1))

    // Settle every pending microtask so this is "nothing rendered", not
    // "nothing rendered yet".
    await act(async () => { await Promise.resolve() })
    expect(strip()).toBeNull()
    // Nor an empty wrapper hiding inside the composer dock.
    const dock = document.querySelector(".bc-dock") as HTMLElement
    expect(dock).toBeTruthy()
    expect(dock.querySelector('[data-testid="next-prompt-suggestions"]')).toBeNull()
  })

  it("a FAILED suggestions request is silent — same as an empty one", async () => {
    const ask = deferAsk()
    nextSuggestions.mockRejectedValue(new Error("500"))
    renderChat()
    await typeAndSend("what are the top complaints about checkout?")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))
    await ask.release(REPLY)

    // The answer is unaffected and no error surfaces anywhere.
    await waitFor(() => expect(document.body.textContent).toContain("23 tickets this month"))
    await act(async () => { await Promise.resolve() })
    expect(strip()).toBeNull()
    expect(document.body.textContent).not.toContain("500")
  })

  it("clears the chips the moment the user sends again", async () => {
    const first = deferAsk()
    nextSuggestions.mockResolvedValue({ suggestions: ["Break the promo code bug into tickets"] })
    renderChat()
    await typeAndSend("what are the top complaints about checkout?")
    await first.release(REPLY)
    await waitFor(() => expect(strip()).toBeTruthy())

    // The conversation has moved on: chips proposing the previous turn's next
    // step must not sit under the new question while it generates.
    deferAsk()
    await typeAndSend("break that down by week")
    expect(strip()).toBeNull()
  })

  it("discards a suggestions response that arrives AFTER the next send", async () => {
    const first = deferAsk()
    let releaseLate!: (v: { suggestions: string[] }) => void
    nextSuggestions.mockImplementation(
      () => new Promise((res) => { releaseLate = res as (v: { suggestions: string[] }) => void }),
    )
    renderChat()
    await typeAndSend("what are the top complaints about checkout?")
    await first.release(REPLY)
    await waitFor(() => expect(nextSuggestions).toHaveBeenCalledTimes(1))

    // The user sends again while turn 1's suggestions are still in flight.
    deferAsk()
    await typeAndSend("break that down by week")
    expect(strip()).toBeNull()

    // Turn 1's response finally lands. It belongs to a superseded turn — drop it.
    await act(async () => { releaseLate({ suggestions: ["Stale suggestion from before"] }) })
    await act(async () => { await Promise.resolve() })
    expect(strip()).toBeNull()
    expect(document.body.textContent).not.toContain("Stale suggestion from before")
  })

  it("clicking a chip sends it as an ordinary message", async () => {
    const ask = deferAsk()
    nextSuggestions.mockResolvedValue({ suggestions: ["Break the promo code bug into tickets"] })
    renderChat()
    await typeAndSend("what are the top complaints about checkout?")
    await ask.release(REPLY)
    await waitFor(() => expect(strip()).toBeTruthy())

    deferAsk()
    await act(async () => {
      fireEvent.click(
        within(strip() as HTMLElement).getByText("Break the promo code bug into tickets"),
      )
    })
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(2))
    expect(runAskGeneration.mock.calls[1][0]).toBe("Break the promo code bug into tickets")
    // …and the chips are gone the moment it is sent.
    expect(strip()).toBeNull()
  })
})
