// @vitest-environment jsdom
//
// ChatScreen — the send's OPTIMISTIC render lands BEFORE the intent-classify
// round-trip (POST /v1/chat/intent), and shows a THINKING state during it — not
// the "No response was generated" failure copy.
//
// submitAsk (useConversation) now resolves the target tab, renders the user's
// message turn, renames the tab, and marks the tab busy — all on the send's own
// commit — and only THEN awaits the classifier. This suite pins that ordering
// and the two things that make it correct:
//   1. message turn + tab title are on screen while the classify promise is held
//      pending (the ~5–9s window that used to be blank).
//   2. that optimistically-rendered turn shows the thinking/generating state, so
//      it never falls through to "No response was generated" during the wait.
//   3. a COMMAND verdict reconciles the optimistic turn away (rollbackOptimistic)
//      so exactly one turn / one tab survives, and the command grounds on a CLEAN
//      thread (the user's own command must not leak into the PRD as a source doc).
//   4. an ANSWER verdict KEEPS the optimistic turn (no rollback).
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
  answer: "Because enterprise admins asked for it.",
  sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
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
// Flag ON — the envelope call is the classify await this optimistic render bridges.
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

/** Every rendered user bubble carrying exactly this text — a duplicate shows as 2. */
function bubblesSaying(text: string): number {
  return Array.from(document.querySelectorAll(".bc-user-bubble"))
    .filter((el) => el.textContent === text).length
}

/** Real (non-pinned) chat tabs — the "Top Insights" pin is excluded. */
function nonPinnedTabCount(): number {
  return document.querySelectorAll(".chat-tab:not(.chat-tab--pinned)").length
}

/** Hold the intent envelope open so the pre-classify window is observable, and
 *  hand back the release fn. This is the multi-second gap in production. */
function deferIntent() {
  let release!: (envelope: Record<string, unknown>) => void
  resolveIntent.mockImplementation(
    () => new Promise((res) => { release = res as (e: Record<string, unknown>) => void }),
  )
  return (envelope: Record<string, unknown>) => act(async () => { release(envelope) })
}

const ANSWER_ENVELOPE = {
  intent: "answer", confidence: 0.9, task: null, instruction: null,
  reason: "plain question", source: "llm", prd_id: null, prd_title: null,
}
const GENERATE_PRD_ENVELOPE = {
  intent: "generate_prd", confidence: 0.95,
  task: "Dark mode for mobile: system-preference aware, per-account override",
  instruction: null, reason: "explicit command", source: "llm",
  prd_id: null, prd_title: null,
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
  resolveIntent.mockResolvedValue(ANSWER_ENVELOPE)
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — optimistic render before the intent classify", () => {
  it("renders the message turn AND the renamed tab title BEFORE the classifier resolves", async () => {
    const release = deferIntent()
    renderChat()
    await typeAndSend("why did enterprise churn spike")

    // The classify promise is still pending — this is the window that used to be
    // frozen behind the round-trip. The user's turn is already a real bubble…
    expect(bubblesSaying("why did enterprise churn spike")).toBe(1)
    // …and the tab has already been renamed from "New chat" to the message.
    expect(
      within(screen.getByTestId("chat-tab-bar")).getByText("why did enterprise churn spike"),
    ).toBeTruthy()
    // The classifier really is in flight, and the ask has NOT started yet.
    expect(resolveIntent).toHaveBeenCalledTimes(1)
    expect(runAskGeneration).not.toHaveBeenCalled()

    await release(ANSWER_ENVELOPE)
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
  })

  it("shows the thinking state (not the failure copy) while the classify is pending", async () => {
    const release = deferIntent()
    renderChat()
    await typeAndSend("why did enterprise churn spike")

    // The turn is marked busy on the SAME commit as the optimistic render, so
    // once past the 400ms rung-0 gate it shows a working skeleton…
    await waitFor(() => expect(document.querySelector(".cw")).toBeTruthy())
    // …and NEVER the "No response was generated" copy it would fall through to
    // with no reply and no in-flight signal. This is the regression that was fixed.
    expect(document.body.textContent).not.toContain("No response was generated")
    expect(runAskGeneration).not.toHaveBeenCalled()

    await release(ANSWER_ENVELOPE)
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
  })

  it("a COMMAND verdict reconciles the optimistic turn away — one turn, one tab, clean grounding", async () => {
    resolveIntent.mockResolvedValue(GENERATE_PRD_ENVELOPE)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))

    // Exactly ONE bubble for the command — the optimistic turn was rolled back
    // and the command flow's own seeded turn is the one that survives (no double).
    expect(bubblesSaying("generate a PRD for dark mode on mobile")).toBe(1)
    // Exactly ONE real tab — the optimistic tab spawned for the send was removed
    // and the command opened its own PRD tab (no stranded/duplicate tab).
    expect(nonPinnedTabCount()).toBe(1)
    // No stranded "thinking with no content" failure copy left on any tab.
    expect(document.body.textContent).not.toContain("No response was generated")
    // The PRD grounds on a CLEAN thread: on this fresh landing there is nothing to
    // ground on, so the command must NOT fold its own optimistic turn in as a
    // "Conversation (this chat)" source doc. (rollbackOptimistic must reconcile
    // the thread before the command flow reads it for grounding.)
    expect(generateFromTask.mock.calls[0][2]).toBeUndefined()
    // Never routed to the ask agent.
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("an ANSWER verdict KEEPS the optimistic turn (no rollback)", async () => {
    resolveIntent.mockResolvedValue(ANSWER_ENVELOPE)
    renderChat()
    await typeAndSend("why did enterprise churn spike")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))

    // The optimistic turn persists as the one-and-only bubble — the answer path
    // reuses it rather than rolling it back and re-seeding.
    expect(bubblesSaying("why did enterprise churn spike")).toBe(1)
    // And it stays on its tab, which keeps its message-derived title.
    expect(
      within(screen.getByTestId("chat-tab-bar")).getByText("why did enterprise churn spike"),
    ).toBeTruthy()
    expect(generateFromTask).not.toHaveBeenCalled()
  })
})
