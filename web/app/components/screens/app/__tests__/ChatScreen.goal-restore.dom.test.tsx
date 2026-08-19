// @vitest-environment jsdom
//
// ChatScreen — the Goal Analysis run's lifetime against a chat thread.
//
// Both behaviours here shipped with ZERO coverage, and both are the kind that
// only bite in a browser:
//
//   - `goalRunId` lives in the shared content slot, which is memory only. A
//     reload made a running analysis UNREACHABLE while it carried on finishing
//     on the server.
//   - The same slot is shared across chat tabs, so a run left set showed thread
//     A's analysis — with a LIVE Confirm button — on thread B, where confirming
//     would lock a goal definition against a conversation the reader was not
//     even looking at.
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
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

const listRuns = vi.fn()

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
    },
    goalAnalysisApi: {
      list: (...a: unknown[]) => listRuns(...a),
      start: vi.fn(),
      get: vi.fn(),
      confirm: vi.fn(),
    },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(), resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(), loadPrdById: vi.fn(),
}))
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(), resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
}))
vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn(),
  }),
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(""),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({
    entriesByInsight: new Map(), loading: false, refetch: vi.fn(),
  }),
}))

// The flag is the gate. Flipped per test, because "does nothing for an
// unenrolled company" is as much a requirement as the restore itself.
let crucible = true
vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: { feature_flags: { crucible } },
    refresh: async () => {},
  }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

function Harness() {
  const { content } = useContent()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "div",
      { "data-testid": "goal-probe" },
      content.goalRunId != null ? String(content.goalRunId) : "none",
    ),
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

const goalProbe = () => screen.getByTestId("goal-probe").textContent

function seedPersistedTab(tab: Record<string, unknown>, activeId: string) {
  sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([tab]))
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", activeId)
}

beforeEach(() => {
  crucible = true
  listRuns.mockReset()
  listRuns.mockResolvedValue({ runs: [] })
  sessionStorage.clear()
  localStorage.clear()
})
afterEach(() => {
  cleanup()
  sessionStorage.clear()
  localStorage.clear()
})

describe("restoring a run after a reload", () => {
  it("reopens this thread's most recent run", async () => {
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "running" }],
    })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(goalProbe()).toBe("42"))
  })

  it("ignores a run belonging to a different thread", async () => {
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 999, status: "running" }],
    })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())
    expect(goalProbe()).toBe("none")
  })

  it("does NOT reopen a failed run", async () => {
    // It would pin an undismissable red tab to that thread for as long as the
    // row exists, with nothing the reader could do about it.
    listRuns.mockResolvedValue({
      runs: [{ id: 42, conversation_id: 7, status: "failed" }],
    })
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())
    expect(goalProbe()).toBe("none")
  })

  it("does not even ASK for an unenrolled company", async () => {
    // A request per thread switch, and a 403 in the console, for a feature
    // that company cannot use.
    crucible = false
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await new Promise((r) => setTimeout(r, 50))
    expect(listRuns).not.toHaveBeenCalled()
    expect(goalProbe()).toBe("none")
  })

  it("a failing listing leaves the chat working", async () => {
    listRuns.mockRejectedValue(new Error("down"))
    seedPersistedTab({ id: "t1", title: "chat", dbConvId: 7, messages: [] }, "t1")
    mountApp()
    await waitFor(() => expect(listRuns).toHaveBeenCalled())
    expect(goalProbe()).toBe("none")
    expect(screen.getByTestId("chat-tab-bar")).toBeTruthy()
  })
})
