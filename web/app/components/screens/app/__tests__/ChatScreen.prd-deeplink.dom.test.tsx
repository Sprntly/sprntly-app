// @vitest-environment jsdom
//
// ChatScreen — the `/brief?prd=<id>` deep-link opens that PRD.
//
// The backend's "your PRD is ready" Slack ping links to `/brief?prd=<id>`.
// ChatScreen reads the `prd` search param on mount and opens THAT PRD as a
// chat tab + panel via the same load flow the command palette / brief "View
// PRD" use (openPrdTab → kind:"load" → loadPrdById). This test mounts the REAL
// ChatScreen and asserts the param drives a load of the given prd id.
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}
// openPrdTab calls window.scrollTo (unimplemented in jsdom) — stub it.
window.scrollTo = (() => {}) as typeof window.scrollTo

// ── Boundary mocks (network / router / heavy contexts) ─────────────────────
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
    prdApi: { importDoc: vi.fn() },
  }
})

// The PRD load flow (openPrdTab kind:"load") calls loadPrdById — capture the id
// it's asked to load so we can prove the deep-link routed the right PRD.
const loadPrdById = vi.fn().mockResolvedValue({
  ok: true,
  prd: { prd_id: 515, title: "Checkout redesign", payload_md: "# PRD" },
  meta: null,
})
vi.mock("../../../../lib/runPrdGeneration", () => ({
  loadPrdById: (...args: unknown[]) => loadPrdById(...args),
  runPrdGeneration: vi.fn(),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromBacklog: vi.fn(),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn(),
  }),
}))

let searchString = ""
const pushSpy = vi.fn()
const replaceSpy = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: replaceSpy, prefetch: vi.fn() }),
  usePathname: () => "/brief",
  useSearchParams: () => new URLSearchParams(searchString),
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
  useBriefPrototypeMap: () => ({ entriesByInsight: {}, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

function renderScreen() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  searchString = ""
  pushSpy.mockClear()
  replaceSpy.mockClear()
  loadPrdById.mockClear()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen — /brief?prd=<id> deep-link", () => {
  it("loads the PRD named by the ?prd= param", async () => {
    searchString = "prd=515"
    await act(async () => {
      renderScreen()
    })
    // The param drove a load of that exact PRD…
    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(515))
    // …and openPrdTab routed to `/` (which strips the ?prd= param).
    expect(pushSpy).toHaveBeenCalledWith("/")
  })

  it("does nothing without a ?prd= param", async () => {
    searchString = ""
    await act(async () => {
      renderScreen()
    })
    await Promise.resolve()
    expect(loadPrdById).not.toHaveBeenCalled()
  })

  it("ignores a non-numeric ?prd= value", async () => {
    searchString = "prd=abc"
    await act(async () => {
      renderScreen()
    })
    await Promise.resolve()
    expect(loadPrdById).not.toHaveBeenCalled()
  })
})
