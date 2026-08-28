// @vitest-environment jsdom
//
// ChatScreen — the message is on screen the INSTANT the user sends it.
//
// Every send now opens with an awaited backend decision (POST /v1/chat/intent —
// a full LLM round-trip) before any branch knows whether the message becomes a
// chat turn or a command that seeds its own turn. That await sat in front of
// every render, so the composer cleared into an empty screen for seconds and the
// send read as dropped. The fix renders the user's message on the send's own
// commit: a PENDING-SEND placeholder bridges the first microtasks, then the REAL
// optimistic thread turn (message + thinking skeleton) supersedes it before the
// classify await, so the classify window is never blank.
//
// The hazard this suite exists to pin is the DUPLICATE: the command flows
// (openPrdInTab's seedTurn / seedCommandTurn) render and persist their own turn,
// so a placeholder that outlives the dispatch would show the user's message
// twice. Every test below therefore counts bubbles, not just presence.
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
// Flag ON — the envelope call is the await this placeholder exists to bridge.
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

/** Every rendered user bubble carrying exactly this text. The duplicate-turn
 *  regression shows up here as 2. */
function bubblesSaying(text: string): number {
  return Array.from(document.querySelectorAll(".bc-user-bubble"))
    .filter((el) => el.textContent === text).length
}

/** Hold the intent envelope open so the pre-dispatch window is observable, and
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

describe("ChatScreen — the send renders before the dispatch decision", () => {
  it("shows the user's message + a thinking skeleton while the intent call is still in flight", async () => {
    const release = deferIntent()
    renderChat()
    await typeAndSend("why are enterprise users asking for this?")

    // The envelope has NOT resolved — this is the window that used to be blank.
    // The optimistic message now renders as the REAL thread turn on the send's own
    // commit (superseding the earlier pending-send placeholder in this window), so
    // observe the user bubble directly.
    const bubble = Array.from(document.querySelectorAll(".bc-user-bubble")).find(
      (el) => el.textContent === "why are enterprise users asking for this?",
    )
    expect(bubble).toBeTruthy()
    // …and, once past the 400ms rung-0 gate, something is visibly working so the
    // wait reads as progress — and specifically NOT the "No response was generated"
    // failure copy the turn would otherwise fall through to with no reply yet.
    // (Below 400ms nothing renders on purpose — a spinner that appears and vanishes
    // inside half a second reads as a glitch.)
    await waitFor(() => expect(document.querySelector(".cw")).toBeTruthy())
    expect(document.body.textContent).not.toContain("No response was generated")
    // The dispatch really is still pending (not a resolved-fast false positive).
    expect(runAskGeneration).not.toHaveBeenCalled()

    await release(ANSWER_ENVELOPE)
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
  })

  it("clears the composer the moment it renders the message (never both, never neither)", async () => {
    deferIntent()
    renderChat()
    await typeAndSend("what changed in onboarding last week?")

    // The text left the composer AND landed on screen in the same commit — the
    // old bug was the first half happening without the second. The message now
    // renders as the real optimistic turn rather than the pending-send placeholder.
    const rendered = Array.from(document.querySelectorAll(".bc-user-bubble")).some(
      (el) => el.textContent === "what changed in onboarding last week?",
    )
    expect(rendered).toBe(true)
    const composer = document.querySelector(
      ".cx-input",
    ) as HTMLTextAreaElement
    expect(composer.value).toBe("")
  })

  it("hands off to the ask turn without duplicating the message", async () => {
    const release = deferIntent()
    renderChat()
    await typeAndSend("why are enterprise users asking for this?")
    expect(bubblesSaying("why are enterprise users asking for this?")).toBe(1)

    await release(ANSWER_ENVELOPE)
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())

    // The placeholder retired and the real turn took its place — exactly one
    // bubble throughout, and the answer renders under it.
    await waitFor(() =>
      expect(document.querySelector('[data-testid="pending-send"]')).toBeNull(),
    )
    expect(bubblesSaying("why are enterprise users asking for this?")).toBe(1)
    await waitFor(() =>
      expect(document.body.textContent).toContain("Because enterprise admins asked for it."),
    )
  })

  it("hands off to a PRD command's OWN seeded turn without duplicating the message", async () => {
    // The regression guard. prdCommandFlow → openPrdInTab seeds its own turn and
    // seedCommandTurn persists it; a placeholder that outlived the dispatch would
    // leave the command on screen twice.
    const release = deferIntent()
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    expect(bubblesSaying("generate a PRD for dark mode on mobile")).toBe(1)

    await release(GENERATE_PRD_ENVELOPE)
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))

    await waitFor(() =>
      expect(document.querySelector('[data-testid="pending-send"]')).toBeNull(),
    )
    expect(bubblesSaying("generate a PRD for dark mode on mobile")).toBe(1)
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("does not double-send when the user hits send twice during the dispatch window", async () => {
    // The busy/asking markers aren't set until the ask itself starts, so this
    // window had no guard of its own before the placeholder existed.
    const release = deferIntent()
    renderChat()
    await typeAndSend("why are enterprise users asking for this?")

    const dock = document.querySelector(".cx") as HTMLElement
    if (dock) {
      const send = within(dock).queryByLabelText("Send")
      if (send) await act(async () => { fireEvent.click(send) })
    }
    expect(resolveIntent).toHaveBeenCalledTimes(1)

    await release(ANSWER_ENVELOPE)
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))
    expect(bubblesSaying("why are enterprise users asking for this?")).toBe(1)
  })
})
