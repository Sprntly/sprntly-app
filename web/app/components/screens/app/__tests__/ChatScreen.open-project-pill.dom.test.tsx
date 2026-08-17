// @vitest-environment jsdom
//
// ChatScreen — the "Open project" pill in the chat tab-strip header. When this
// chat's PRD silently forked a project (`content.activeProjectId` set by the
// main-chat PRD-fork bind), the header shows a pinned affordance that jumps
// straight into that project's individual chat — otherwise the user gets no
// sign a project now exists. When no project is bound the pill must not render
// at all, keeping a plain chat's header byte-identical to before (the golden
// snapshot re-proves that separately).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
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
      listTurns: vi.fn().mockResolvedValue({ turns: [] }),
    },
    reportsApi: { listForConversation: vi.fn().mockResolvedValue([]), get: vi.fn() },
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

const { pushSpy } = vi.hoisted(() => ({ pushSpy: vi.fn() }))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: vi.fn(), prefetch: vi.fn() }),
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

function Harness() {
  const { setContent } = useContent()
  return React.createElement(
    React.Fragment,
    null,
    // Stands in for the ChatScreen-internal fork-bind (covered end-to-end by
    // ChatScreen.project-bind.dom.test.tsx) — this suite is ONLY about the
    // header affordance, so it seeds the bound state directly.
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

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen — Open-project header pill", () => {
  it("does not render when no project is bound to the chat", async () => {
    await act(async () => { mountApp() })
    expect(screen.getByTestId("chat-tab-bar")).toBeTruthy()
    expect(screen.queryByTestId("chat-open-project")).toBeNull()
  })

  it("renders once a project is bound, and navigates to that project's individual chat on click", async () => {
    await act(async () => { mountApp() })
    expect(screen.queryByTestId("chat-open-project")).toBeNull()

    await act(async () => { fireEvent.click(screen.getByTestId("seed-active-project")) })
    const pill = screen.getByTestId("chat-open-project")
    expect(pill.textContent).toContain("Open project")

    await act(async () => { fireEvent.click(pill) })
    expect(pushSpy).toHaveBeenCalledWith("/projects?id=555&chat=individual")
  })
})
