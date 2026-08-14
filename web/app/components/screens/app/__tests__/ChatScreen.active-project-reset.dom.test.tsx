// @vitest-environment jsdom
//
// ChatScreen — `content.activeProjectId` is thread-scoped, the same rule as
// the report-focus pointer and the document id it sits beside in the
// thread-change reset effect. A project bound to one thread (the main-chat
// PRD-fork path, see ChatScreen.project-bind.dom.test.tsx) must not survive a
// switch to another thread — otherwise a brand-new/unrelated chat would show
// the PREVIOUS thread's project-menu affordance in the content panel header.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
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

const TAB_A_TITLE = "Dark mode on mobile"

function Harness() {
  const { content, setContent } = useContent()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement("div", { "data-testid": "active-project-probe" },
      content.activeProjectId != null ? String(content.activeProjectId) : "none"),
    React.createElement("div", { "data-testid": "conv-probe" },
      content.conversationId != null ? String(content.conversationId) : "none"),
    // Stands in for the ChatScreen-internal bind (already covered end-to-end
    // by ChatScreen.project-bind.dom.test.tsx) — this suite is ONLY about the
    // reset effect, so it seeds the bound state directly.
    React.createElement("button", {
      "data-testid": "seed-active-project",
      onClick: () => setContent({ activeProjectId: 555 }),
    }, "seed"),
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
const newChatBtn = () =>
  within(screen.getByTestId("chat-tab-bar")).getByLabelText("New chat")
const tabChip = (title: string) =>
  within(screen.getByTestId("chat-tab-bar")).getByText(title)

function seedResume(dbId: number, title: string, projectId: number | null = null) {
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

async function settle() {
  await act(async () => { await new Promise((r) => setTimeout(r, 30)) })
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

describe("ChatScreen — activeProjectId is cleared on a genuine thread change", () => {
  it("a new chat beside a project-bound thread starts with no active project", async () => {
    seedThreadTurns()
    listForConversation.mockResolvedValue([])
    seedResume(77, TAB_A_TITLE)

    await act(async () => { mountApp() })
    await waitFor(() => expect(convProbe()).toBe("77"))

    await act(async () => { fireEvent.click(screen.getByTestId("seed-active-project")) })
    expect(activeProjectProbe()).toBe("555")

    await act(async () => { fireEvent.click(newChatBtn()) })
    await settle()

    // A brand-new tab has no conversation yet, and inherits nothing from the
    // thread it sits beside — the project-menu affordance must not leak in.
    expect(convProbe()).toBe("none")
    expect(activeProjectProbe()).toBe("none")
  })

  it("switching back to a thread with NO recorded project binding does not resurrect a stray fork-bind from a stale render", async () => {
    // The reset effect only ever CLEARS `activeProjectId` on a thread change.
    // There IS a restore path now (see
    // ChatScreen.resume-history-project.dom.test.tsx) — but it only ever
    // re-applies a binding checkResume actually recorded for that DB
    // conversation id at load time. Conversation 77 here was never resumed
    // with a `projectId`, so the button below simulates an ad-hoc fork bind
    // that was never persisted as this thread's actual binding — coming back
    // to tab A after a switch away must still land with no active project.
    seedThreadTurns()
    listForConversation.mockResolvedValue([])
    seedResume(77, TAB_A_TITLE)

    await act(async () => { mountApp() })
    await waitFor(() => expect(convProbe()).toBe("77"))
    await act(async () => { fireEvent.click(screen.getByTestId("seed-active-project")) })
    expect(activeProjectProbe()).toBe("555")

    await act(async () => { fireEvent.click(newChatBtn()) })
    await settle()
    expect(activeProjectProbe()).toBe("none")

    await act(async () => { fireEvent.click(tabChip(TAB_A_TITLE)) })
    await waitFor(() => expect(convProbe()).toBe("77"))

    expect(activeProjectProbe()).toBe("none")
  })
})
