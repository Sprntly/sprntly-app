// @vitest-environment jsdom
//
// ChatScreen — tickets are NOT exposed in the chat at all. Ticket creation moved
// to the PRD panel's footer (Create/View tickets), so the chat's post-reply row
// shows exactly two buttons — the PRD action and the prototype action — and never
// queries the tickets API.
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

const { storiesGetForPrd } = vi.hoisted(() => ({ storiesGetForPrd: vi.fn() }))

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
    storiesApi: { getForPrd: (...a: unknown[]) => storiesGetForPrd(...a) },
    prdApi: { listInputQuestions: vi.fn().mockResolvedValue([]), answerInputQuestion: vi.fn() },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromBacklog: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
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

// A restored PRD tab (prd stripped, prdId + a replied thread turn kept) — the
// reload state that renders the post-reply action row with the ticket CTA.
// briefMeta is null (a backlog/import-style tab): with briefMeta or a cached
// prd, the insight card at the top hosts the actions and the post-reply row
// is suppressed as duplicate noise.
function seedTabWithReply(prdId: number) {
  sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
    {
      id: "tab-reload",
      title: "PRD · Bulk onboarding",
      dbConvId: null,
      briefMeta: null,
      prdId,
      thread: [{
        id: "t1", query: "what's the goal?",
        reply: { answer: "Ship bulk onboarding.", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" },
      }],
    },
  ]))
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-reload")
}

function renderRestored() {
  return render(
    React.createElement(NavigationProvider, null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen))),
  )
}

beforeEach(() => { localStorage.clear(); storiesGetForPrd.mockReset() })
afterEach(() => { cleanup(); localStorage.clear() })

describe("ChatScreen — tickets are not exposed in the chat (they live in the PRD panel)", () => {
  it("the post-reply action row shows the PRD + prototype buttons, never a ticket button", async () => {
    seedTabWithReply(796)
    await act(async () => { renderRestored() })

    // The two-button row: the PRD action (a PRD is loaded on the tab → "View PRD")
    // and the prototype action. Ticket creation lives in the PRD panel now.
    await waitFor(() => expect(screen.getByRole("button", { name: "View PRD" })).toBeTruthy())
    expect(screen.getByTestId("chat-prototype-cta")).toBeTruthy()
    // No ticket affordance anywhere in the chat…
    expect(screen.queryByRole("button", { name: "Create tickets" })).toBeNull()
    expect(screen.queryByRole("button", { name: "View tickets" })).toBeNull()
    // …and the chat never queries the tickets API (that's the PRD panel's job).
    expect(storiesGetForPrd).not.toHaveBeenCalled()
  })
})
