// @vitest-environment jsdom
//
// ChatScreen — action-envelope dispatch (flag: chat_intent_envelope ON).
// Every message goes to POST /v1/chat/intent first; the backend's verdict —
// not a client regex — decides the executor. The reported failures this
// architecture fixes are KEYWORD-FREE commands ("draft it up") whose meaning
// lives in the conversation: the envelope carries the intent AND the task the
// backend synthesized from the thread. Fail-open: an envelope fetch failure
// falls back to the full legacy regex ladder, so command dispatch never
// degrades below flag-off behavior.
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

const { generateFromTask, classifyCommand, clarifyTask, resolveIntent, runTicketSetGeneration } = vi.hoisted(() => ({
  runTicketSetGeneration: vi.fn().mockResolvedValue({
    ok: true,
    set: { id: 7, title: "Webhook retries", stories: [], conversationId: 1, status: "ready" },
  }),
  generateFromTask: vi.fn().mockResolvedValue({ prd_id: 501, title: "CSV export", status: "generating", variant: "v3" }),
  classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
  clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
  // The envelope call. Default: answer — individual tests override per-case.
  resolveIntent: vi.fn().mockResolvedValue({
    intent: "answer", confidence: 0.9, task: null, instruction: null,
    reason: "plain question", source: "llm", prd_id: null, prd_title: null,
  }),
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
    chatIntentApi: { resolve: resolveIntent },
    // The thread-resume probe reads this on every chat open; it must find a
    // callable here even in a suite that is not about ticket sets.
    ticketSetsApi: {
      byConversation: vi.fn().mockResolvedValue({ ticket_sets: [] }),
      get: vi.fn(),
    },
    artifactsApi: { chatSummary: vi.fn().mockResolvedValue({ summary: null }) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
    },
  }
})

vi.mock("../../../../lib/runTicketSetGeneration", () => ({
  runTicketSetGeneration: (...a: unknown[]) => runTicketSetGeneration(...a),
  loadTicketSet: vi.fn().mockResolvedValue({ ok: true }),
}))

const runPrdGeneration = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 77, title: "Generated PRD", metaLine: "", sections: [] },
})
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...args: unknown[]) => runPrdGeneration(...args),
  resumePrdGeneration: vi.fn().mockResolvedValue({ ok: true, prd: { prd_id: 501, title: "CSV export", metaLine: "", sections: [] } }),
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
// Flag EXPLICITLY on — the whole point of this suite. The other two states of
// the flag (key absent, explicit false) have their own suites:
// ChatScreen.envelope-default.dom.test.tsx. (The sibling command suites now
// mock an explicit `chat_intent_envelope: false`, since a null/flagless
// workspace resolves to ON.)
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

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  protoMap.clear()
  runAskGeneration.mockClear()
  runPrdGeneration.mockClear()
  generateFromTask.mockClear()
  classifyCommand.mockClear()
  clarifyTask.mockClear()
  clarifyTask.mockResolvedValue({ sufficient: true, questions: [], missing: [] })
  resolveIntent.mockReset()
  resolveIntent.mockResolvedValue({
    intent: "answer", confidence: 0.9, task: null, instruction: null,
    reason: "plain question", source: "llm", prd_id: null, prd_title: null,
  })
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — action-envelope dispatch (flag on)", () => {
  it("a KEYWORD-FREE command generates the PRD from the envelope's synthesized task", async () => {
    resolveIntent.mockResolvedValue({
      intent: "generate_prd", confidence: 0.95,
      task: "CSV export of the weekly report: respect report filters, 50k-row cap, scheduled exports",
      instruction: null, reason: "thread converged on the feature",
      source: "llm", prd_id: null, prd_title: null,
    })
    renderChat()
    await typeAndSend("okay, draft it up")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    // The ENVELOPE task (composed from the conversation) drives the PRD — not
    // the deictic message text, which the regex extractor could never parse.
    expect(generateFromTask.mock.calls[0][0]).toMatch(/CSV export .* 50k-row cap/)
    expect(runAskGeneration).not.toHaveBeenCalled()
    // The legacy tier-2 classifier is dead on this path.
    expect(classifyCommand).not.toHaveBeenCalled()
  })

  it("an answer verdict falls through to the grounded ask path", async () => {
    renderChat()
    await typeAndSend("why are enterprise users asking for this?")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(resolveIntent).toHaveBeenCalledTimes(1)
    expect(generateFromTask).not.toHaveBeenCalled()
    expect(classifyCommand).not.toHaveBeenCalled()
  })

  it("a mention of a PRD inside a question is NOT hijacked into generation", async () => {
    // Envelope says answer even though the message names a PRD — the exact
    // mention-trap the regex tier used to mis-route.
    renderChat()
    await typeAndSend("what's the acceptance criteria in the PRD for onboarding?")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(generateFromTask).not.toHaveBeenCalled()
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })

  it("a planner failure ANSWERS the message — it never falls back to guessing", async () => {
    // This used to fall back to the client's regex ladder, so a dead intent
    // endpoint still generated a PRD off a pattern match. That fallback is
    // deliberately gone.
    //
    // The failure mode is now: answer the question. Chosen on purpose — the
    // cost of being wrong here is that a genuine "write me a PRD" gets a reply
    // instead of a document and the user asks again, which is recoverable in
    // one turn. The cost of the old fallback was an oddly-phrased question
    // silently spending minutes and real money generating a document nobody
    // asked for, which is not.
    resolveIntent.mockRejectedValue(new Error("network down"))
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(generateFromTask).not.toHaveBeenCalled()
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })

  it("a /slash message skips the envelope entirely (explicit intent, backend fast-path)", async () => {
    renderChat()
    await typeAndSend("/prioritize rank these features")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(resolveIntent).not.toHaveBeenCalled()
  })

  it("generate_tickets with no PRD on the tab builds a STANDALONE ticket set", async () => {
    resolveIntent.mockResolvedValue({
      intent: "generate_tickets", confidence: 0.9, task: "the webhook retry work",
      instruction: null, reason: "tickets", source: "llm", prd_id: null, prd_title: null,
    })
    renderChat()
    await typeAndSend("break this into work items")

    // No PRD anywhere used to mean the ask agent answered with the
    // user-stories skill's raw markdown — a wall of ticket bodies in a chat
    // bubble that could not be pushed to a tracker, reopened or found again.
    // It now produces a durable `ticket_sets` artifact that reads in the panel
    // (feat/tickets/standalone-from-chat). No PRD is generated either way.
    await waitFor(() => expect(runTicketSetGeneration).toHaveBeenCalledTimes(1))
    expect(runTicketSetGeneration.mock.calls[0][0]).toBe("the webhook retry work")
    expect(runAskGeneration).not.toHaveBeenCalled()
    expect(generateFromTask).not.toHaveBeenCalled()
  })
})
