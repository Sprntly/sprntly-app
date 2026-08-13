// @vitest-environment jsdom
//
// ChatScreen — the planner is consulted for EVERY message, whatever the
// feature flags say.
//
// This file used to pin the three states of `chat_intent_envelope`, the kill
// switch that chose between the backend decision and a client-side regex
// ladder. Both the flag and the ladder are gone, and the reason is worth
// keeping: a regex in the browser deciding to GENERATE A PRD meant an oddly
// phrased question could spend minutes and real money building a document
// nobody asked for, and no amount of tuning the pattern fixed the class of bug.
//
// So there is no longer a state in which this screen decides anything. What is
// pinned now is that absence — an explicit `false`, a missing key, no flags
// object, and a workspace that has not loaded all reach the planner, because
// there is nothing else left to reach. A kill switch here would no longer
// choose between two routers; it would choose between the planner and nothing.
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

describe("ChatScreen — the planner decides, unconditionally", () => {
  it("explicit true → the planner", async () => {
    workspaceRef.current = { feature_flags: { chat_intent_envelope: true } }
    await expectEnvelopeRouted()
  })

  it("KEY ABSENT → the planner", async () => {
    workspaceRef.current = { feature_flags: { agents: true, top_insights: true } }
    await expectEnvelopeRouted()
  })

  it("no feature_flags object at all → the planner", async () => {
    workspaceRef.current = {}
    await expectEnvelopeRouted()
  })

  it("workspace not loaded yet → the planner", async () => {
    workspaceRef.current = null
    await expectEnvelopeRouted()
  })

  it("explicit FALSE → still the planner: the kill switch no longer exists", async () => {
    // The case this file exists for. `false` used to route to the client's
    // regex ladder; there is no ladder, so the flag is inert and the message
    // goes where every other message goes.
    workspaceRef.current = { feature_flags: { chat_intent_envelope: false } }
    await expectEnvelopeRouted()
  })

  it("a plain question still falls through to the ask agent", async () => {
    // The planner is consulted, says `answer`, and the grounded ask runs. The
    // screen never decided that — it executed a verdict. Note the verdict is
    // stubbed rather than inferred from the words: that is the whole change.
    workspaceRef.current = { feature_flags: { chat_intent_envelope: false } }
    resolveIntent.mockResolvedValue({
      intent: "answer", confidence: 0.9, task: null, instruction: null,
      reason: "a question about users", source: "planner",
      prd_id: null, prd_title: null,
    })
    renderChat()
    await typeAndSend("why are enterprise users asking for this?")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(resolveIntent).toHaveBeenCalledTimes(1)
    expect(generateFromTask).not.toHaveBeenCalled()
  })
})
