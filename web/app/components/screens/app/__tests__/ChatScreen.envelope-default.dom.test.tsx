// @vitest-environment jsdom
//
// ChatScreen — the THREE states of `chat_intent_envelope`, at the ChatScreen
// read site. The flag is DEFAULT ON (2026-08-03): a company that never had the
// key written is on the envelope, and the staff checkbox is a kill switch
// rather than an opt-in.
//
//   * explicit `true`                  → envelope
//   * key absent                       → envelope   ← the default-on behaviour
//   * no feature_flags object at all   → envelope
//   * workspace UNKNOWN (null/loading) → envelope   ← fails OPEN, on purpose
//   * explicit `false`                 → legacy regex ladder
//
// Every case sends the SAME message, one the regex ladder CAN parse, so the
// two routers are distinguishable by their output rather than by a mock spy
// alone: the envelope generates from the task it synthesized off the thread,
// the ladder generates from the substring its regex extracted.
//
// Why UNKNOWN fails open (the opposite of ds_claude_analysis, which fails
// closed — see backend/app/qa_agent.py::_ds_claude_enabled): this flag picks a
// ROUTING STRATEGY, not whether a tenant's data leaves the box. "I can't read
// your flags yet" must resolve to the better router, and the envelope call
// keeps its own fail-open floor back to the ladder if the request fails.
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

const { generateFromTask, classifyCommand, clarifyTask, resolveIntent } = vi.hoisted(() => ({
  generateFromTask: vi.fn().mockResolvedValue({ prd_id: 501, title: "Dark mode", status: "generating", variant: "v3" }),
  classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
  clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
  resolveIntent: vi.fn(),
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
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
    },
  }
})

const runPrdGeneration = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 77, title: "Generated PRD", metaLine: "", sections: [] },
})
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...args: unknown[]) => runPrdGeneration(...args),
  resumePrdGeneration: vi.fn().mockResolvedValue({ ok: true, prd: { prd_id: 501, title: "Dark mode", metaLine: "", sections: [] } }),
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

// The workspace is swapped PER TEST — that's the whole subject of this suite.
const { workspaceRef } = vi.hoisted(() => ({
  workspaceRef: { current: null as Record<string, unknown> | null },
}))
vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    loading: false, profile: null, workspace: workspaceRef.current, refresh: async () => {},
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

// A phrasing BOTH routers claim, so the two are told apart by their output.
const MESSAGE = "generate a PRD for dark mode on mobile"
// What the regex ladder extracts from it…
const LADDER_TASK = "dark mode on mobile"
// …versus what the backend classifier synthesized off the whole thread.
const ENVELOPE_TASK =
  "Dark mode on mobile: honor the OS setting, per-account override, and an AMOLED-true-black variant"

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
    intent: "generate_prd", confidence: 0.95, task: ENVELOPE_TASK, instruction: null,
    reason: "thread converged on the feature", source: "llm", prd_id: null, prd_title: null,
  })
  workspaceRef.current = null
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

async function expectEnvelopeRouted() {
  renderChat()
  await typeAndSend(MESSAGE)
  await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
  expect(resolveIntent).toHaveBeenCalledTimes(1)
  // The ENVELOPE's synthesized task drove generation — proof the backend
  // classifier, not the client regex, decided this turn.
  expect(generateFromTask.mock.calls[0][0]).toBe(ENVELOPE_TASK)
  expect(classifyCommand).not.toHaveBeenCalled()
}

describe("ChatScreen — chat_intent_envelope default-on", () => {
  it("explicit true → envelope dispatch", async () => {
    workspaceRef.current = { feature_flags: { chat_intent_envelope: true } }
    await expectEnvelopeRouted()
  })

  it("KEY ABSENT → envelope dispatch (the default-on behaviour)", async () => {
    // A company whose feature_flags row was never written for this key — 17 of
    // 33 at the time of the flip. No data migration turns these on; this read
    // site does.
    workspaceRef.current = { feature_flags: { agents: true, top_insights: true } }
    await expectEnvelopeRouted()
  })

  it("no feature_flags object at all → envelope dispatch", async () => {
    workspaceRef.current = {}
    await expectEnvelopeRouted()
  })

  it("workspace not loaded yet → envelope dispatch (fails OPEN)", async () => {
    // An unknown flag state must not silently downgrade the router. The
    // envelope call keeps its own fallback if the request itself fails.
    workspaceRef.current = null
    await expectEnvelopeRouted()
  })

  it("explicit false → the legacy regex ladder, and no intent call at all", async () => {
    workspaceRef.current = { feature_flags: { chat_intent_envelope: false } }
    renderChat()
    await typeAndSend(MESSAGE)

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    // The ladder's regex-extracted task — NOT the envelope's synthesized one.
    expect(generateFromTask.mock.calls[0][0]).toBe(LADDER_TASK)
    // The kill switch has to actually save the call: a company that opted out
    // must not be billed for a Sonnet classification per message.
    expect(resolveIntent).not.toHaveBeenCalled()
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("explicit false keeps the ladder's fall-through for a plain question", async () => {
    workspaceRef.current = { feature_flags: { chat_intent_envelope: false } }
    renderChat()
    await typeAndSend("why are enterprise users asking for this?")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(resolveIntent).not.toHaveBeenCalled()
    expect(generateFromTask).not.toHaveBeenCalled()
  })
})
