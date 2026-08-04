// @vitest-environment jsdom
//
// ChatScreen — live answer streaming. While an ask generates, the SSE preview
// (runAskGeneration's onPartial) feeds the turn's `partial` markdown, which
// renders in place of the thinking skeleton so the reply appears word-by-word.
// The poll's final reply then REPLACES the preview — no simulated typewriter
// re-typing text the user already watched stream in.
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

const { resolveIntent } = vi.hoisted(() => ({ resolveIntent: vi.fn() }))
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
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
    },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: vi.fn(),
}))

// Deferred ask whose onPartial is captured, so the test can drive the live
// stream by hand before releasing the final (authoritative) reply.
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

const FINAL_REPLY = {
  answer: "The top churn driver is onboarding friction.",
  sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
}

/** Deferred runAskGeneration: hands back the captured onPartial + a release. */
function deferAsk() {
  let release!: (reply: typeof FINAL_REPLY) => void
  let onPartial: ((md: string) => void) | undefined
  runAskGeneration.mockImplementation((...args: unknown[]) => {
    const opts = args[3] as { onPartial?: (md: string) => void } | undefined
    onPartial = opts?.onPartial
    return new Promise((res) => { release = res as (r: typeof FINAL_REPLY) => void })
  })
  return {
    partial: (md: string) => act(async () => { onPartial?.(md) }),
    release: (reply: typeof FINAL_REPLY) => act(async () => { release(reply) }),
    hasOnPartial: () => onPartial !== undefined,
  }
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  protoMap.clear()
  runAskGeneration.mockReset()
  resolveIntent.mockReset()
  resolveIntent.mockResolvedValue(ANSWER_ENVELOPE)
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — live answer streaming", () => {
  it("renders streamed partial markdown in place of the thinking skeleton, then the final reply replaces it", async () => {
    const ask = deferAsk()
    renderChat()
    await typeAndSend("what is our top churn driver?")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))

    // The ask wires a live-preview callback…
    expect(ask.hasOnPartial()).toBe(true)
    // …and until the first delta lands, the turn shows the waiting state.
    // Rung 0: for the first 400ms there is deliberately NO indicator (an answer
    // that lands in 300ms must not flash a spinner), so this waits for rung 1.
    expect(document.querySelector('[data-testid="ask-streaming-partial"]')).toBeNull()
    await waitFor(() => expect(document.querySelector(".cw")).toBeTruthy())
    // Rung 1's line is the one thing that is always true while a job generates.
    expect(document.querySelector(".cw-phase")?.textContent).toBe("Working on your question")

    // First delta: the skeleton yields to live markdown, the phase line moves to
    // "Writing the answer" (a delta provably arrived), and the status row STAYS
    // — it used to be blown away entirely by the first token.
    await ask.partial("The **top churn driver**")
    const streaming = document.querySelector('[data-testid="ask-streaming-partial"]')
    expect(streaming).toBeTruthy()
    expect(streaming!.textContent).toContain("top churn driver")
    expect(document.querySelector(".cw-phase")?.textContent).toBe("Writing the answer")

    // More text accumulates in place.
    await ask.partial("The **top churn driver** is onboarding")
    expect(
      document.querySelector('[data-testid="ask-streaming-partial"]')!.textContent,
    ).toContain("is onboarding")

    // The poll's final reply replaces the preview — streamed container gone,
    // authoritative answer rendered as the turn's reply.
    await ask.release(FINAL_REPLY)
    await waitFor(() =>
      expect(document.querySelector('[data-testid="ask-streaming-partial"]')).toBeNull(),
    )
    expect(document.body.textContent).toContain("The top churn driver is onboarding friction.")
  })

  it("an ask that streams nothing keeps the skeleton until the reply lands (non-streamable paths)", async () => {
    const ask = deferAsk()
    renderChat()
    await typeAndSend("give me the VoC report")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))

    // No deltas ever arrive (HTML report / script paths publish nothing).
    await waitFor(() => expect(document.querySelector(".cw")).toBeTruthy())
    expect(document.querySelector('[data-testid="ask-streaming-partial"]')).toBeNull()
    // A stream that publishes nothing must NOT be reported as a dropped preview:
    // it is indistinguishable from a skill that simply doesn't stream.
    expect(document.querySelector(".cw-phase")?.textContent).toBe("Working on your question")

    await ask.release(FINAL_REPLY)
    await waitFor(() =>
      expect(document.body.textContent).toContain("The top churn driver is onboarding friction."),
    )
  })
})
