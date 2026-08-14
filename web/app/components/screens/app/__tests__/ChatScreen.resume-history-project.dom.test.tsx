// @vitest-environment jsdom
//
// ChatScreen — reopening a thread from Chat history restores its project-menu
// affordance.
//
// `content.activeProjectId` is set when a main-chat PRD generation forks a
// project (ChatScreen.project-bind.dom.test.tsx) — but that bind is a
// one-shot signal, cleared on every genuine thread change
// (ChatScreen.active-project-reset.dom.test.tsx) with no restore path. So
// reopening a thread that IS bound to a project (from Chat history, or any
// other caller of the `sprntly_resume_conv` hand-off) came back with no
// project-menu at all — the folder-icon affordance only ever showed up right
// after the fork, never again once you navigated away and came back.
//
// The hand-off now carries `projectId` (from ConversationRecord.project_id),
// and checkResume records it so the thread-change effect can re-apply it the
// moment this conversation becomes active — on top of the clear, not racing
// it.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = (() => {}) as typeof window.scrollTo
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const listTurns = vi.fn((..._a: unknown[]) => Promise.resolve<unknown>({ turns: [] }))
const listForConversation = vi.fn((..._a: unknown[]) => Promise.resolve<unknown[]>([]))

vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
      update: vi.fn().mockResolvedValue({}),
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
      listTurns: (...a: unknown[]) => listTurns(...a),
    },
    reportsApi: {
      listForConversation: (...a: unknown[]) => listForConversation(...a),
      get: vi.fn(),
    },
    prdApi: { importDoc: vi.fn() },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: vi.fn((id: number) =>
    Promise.resolve({ ok: true, prd: { prd_id: id, title: `PRD ${id}`, metaLine: "", sections: [] } }),
  ),
}))

vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn(() => null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(""),
}))

vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: { feature_flags: { chat_intent_envelope: false } },
    refresh: async () => {},
  }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: new Map(), loading: false, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

const BOUND_TITLE = "Dark mode on mobile"
const UNBOUND_TITLE = "Onboarding copy tweaks"

function Harness() {
  const { content } = useContent()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement("div", { "data-testid": "active-project-probe" },
      content.activeProjectId != null ? String(content.activeProjectId) : "none"),
    React.createElement("div", { "data-testid": "conv-probe" },
      content.conversationId != null ? String(content.conversationId) : "none"),
    React.createElement(ChatScreen),
  )
}

function mountApp() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(Harness)),
    ),
  )
}

const activeProjectProbe = () => screen.getByTestId("active-project-probe").textContent
const convProbe = () => screen.getByTestId("conv-probe").textContent

function seedResume(dbId: number, title: string, projectId: number | null) {
  localStorage.setItem("sprntly_resume_conv", JSON.stringify({
    dbId, title, fallbackTurns: [], prdId: null, projectId,
  }))
}

function seedThreadTurns() {
  listTurns.mockResolvedValue({
    turns: [
      { role: "user", content: "generate a PRD for dark mode on mobile" },
      { role: "assistant", content: "Here's the draft." },
    ],
  })
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen — activeProjectId is derived from a revisited thread's project binding", () => {
  it("opening a thread whose conversation IS project-bound restores the project-menu affordance", async () => {
    seedThreadTurns()
    listForConversation.mockResolvedValue([])
    seedResume(77, BOUND_TITLE, 555)

    await act(async () => { mountApp() })
    await waitFor(() => expect(convProbe()).toBe("77"))

    expect(activeProjectProbe()).toBe("555")
  })

  it("opening a thread with NO project binding leaves activeProjectId null — the additive-null invariant holds", async () => {
    seedThreadTurns()
    listForConversation.mockResolvedValue([])
    seedResume(78, UNBOUND_TITLE, null)

    await act(async () => { mountApp() })
    await waitFor(() => expect(convProbe()).toBe("78"))

    expect(activeProjectProbe()).toBe("none")
  })
})
