// @vitest-environment jsdom
//
// A GENERIC chat answer (a plain Q&A tab with no PRD context) shows NO
// artifact-action row: no "Generate PRD", no "Generate Prototype". Those live
// only on the chat-PRD window's insight card; to make a PRD from a chat the
// user types the request. This locks in the removal of the post-reply CTA row
// from generic answers while the restored-PRD-tab row (prdId set) is retained —
// see ChatScreen.tickets-cta.dom.test.tsx for that complementary case.
import * as React from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = () => {}
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      // Report reduced-motion so replies render in full immediately (no typing sim).
      matches: /prefers-reduced-motion/.test(query), media: query, onchange: null,
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
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
    },
    storiesApi: { getForPrd: vi.fn() },
    prdApi: { listInputQuestions: vi.fn().mockResolvedValue([]), answerInputQuestion: vi.fn() },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
  loadPrdById: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
}))
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(), resumeAskGeneration: vi.fn(), getPendingAsk: vi.fn().mockReturnValue(null),
}))
vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/", useSearchParams: () => new URLSearchParams(""),
}))
vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({ loading: false, profile: null, workspace: null, refresh: async () => {} }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: new Map(), loading: false, error: false, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

// A plain Q&A tab: an answered thread turn, but NO PRD context — no prdId, no
// briefMeta, no cached prd. This is the state behind a generic chat answer.
function seedGenericAnswerTab() {
  sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
    {
      id: "tab-generic",
      title: "Dovetail introducing a mid-tier",
      dbConvId: null,
      briefMeta: null,
      prdId: null,
      thread: [{
        id: "t1", query: "what did dovetail launch?",
        reply: { answer: "Dovetail introducing a mid-tier.", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" },
      }],
    },
  ]))
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-generic")
}

function renderRestored() {
  return render(
    React.createElement(NavigationProvider, null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen))),
  )
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); localStorage.clear(); sessionStorage.clear() })

describe("ChatScreen — a generic chat answer shows no artifact-action row", () => {
  it("renders the reply but neither Generate PRD nor Generate Prototype", async () => {
    seedGenericAnswerTab()
    await act(async () => { renderRestored() })

    // The answer itself renders…
    await waitFor(() => expect(screen.getByText("Dovetail introducing a mid-tier.")).toBeTruthy())
    // …but there is NO artifact-action row on a generic (no-PRD) answer.
    expect(screen.queryByRole("button", { name: "Generate PRD" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Generate Prototype" })).toBeNull()
    expect(screen.queryByTestId("chat-prototype-cta")).toBeNull()
  })
})
