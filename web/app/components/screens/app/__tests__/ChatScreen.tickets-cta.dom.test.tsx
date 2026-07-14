// @vitest-environment jsdom
//
// ChatScreen — the post-reply action row's ticket CTA relabels to "View tickets"
// when the PRD already has persisted tickets in the DB (storiesApi.getForPrd →
// status "ready" with stories), else stays "Create tickets".
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
  localStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
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
  localStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-reload")
}

function renderRestored() {
  return render(
    React.createElement(NavigationProvider, null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen))),
  )
}

beforeEach(() => { localStorage.clear(); storiesGetForPrd.mockReset() })
afterEach(() => { cleanup(); localStorage.clear() })

describe("ChatScreen — ticket CTA reflects whether the PRD already has tickets", () => {
  it("labels the action 'View tickets' when the DB has tickets for the PRD", async () => {
    storiesGetForPrd.mockResolvedValue({ status: "ready", fresh: true, stories: [{ id: "s1" }] })
    seedTabWithReply(796)
    await act(async () => { renderRestored() })

    await waitFor(() => expect(storiesGetForPrd).toHaveBeenCalledWith(796))
    await waitFor(() => expect(screen.getByRole("button", { name: "View tickets" })).toBeTruthy())
    expect(screen.queryByRole("button", { name: "Create tickets" })).toBeNull()
  })

  it("labels the action 'Create tickets' when the PRD has no tickets yet", async () => {
    storiesGetForPrd.mockResolvedValue({ status: "none", fresh: false, stories: [] })
    seedTabWithReply(796)
    await act(async () => { renderRestored() })

    await waitFor(() => expect(screen.getByRole("button", { name: "Create tickets" })).toBeTruthy())
    expect(screen.queryByRole("button", { name: "View tickets" })).toBeNull()
  })
})
