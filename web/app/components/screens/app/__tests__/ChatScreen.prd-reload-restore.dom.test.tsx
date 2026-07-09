// @vitest-environment jsdom
//
// ChatScreen — restore the PRD panel after a reload.
//
// Tabs persist across reloads (localStorage) but their cached `prd` does not (it's
// stripped from the persisted payload). So a reload that lands back on a PRD-bound
// chat tab must reopen the panel on its own — loading the saved PRD from the DB by
// id (NOT regenerating). A reload onto a plain, non-PRD chat must leave the panel
// closed. These tests seed localStorage (as a reload would leave it), mount the
// REAL ChatScreen, and assert the panel + the DB-load vs regenerate choice.
import * as React from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
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
    },
  }
})

const runPrdGeneration = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 1, title: "Regenerated", metaLine: "", sections: [] },
})
const loadPrdById = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 42, title: "Saved PRD", metaLine: "", sections: [] },
})
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...a: unknown[]) => runPrdGeneration(...a),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromBacklog: vi.fn(),
  loadPrdById: (...a: unknown[]) => loadPrdById(...a),
}))

vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
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
  useWorkspace: () => ({ loading: false, profile: null, workspace: null, refresh: async () => {} }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

// Configurable brief-prototype-map mock: the effect under test only fires once the
// map resolves (loading:false) and reports a DB PRD for the tab's insight. Each
// test sets `mapState` before mounting to model "a PRD exists" vs "none".
let mapState: { entriesByInsight: Map<number, unknown>; loading: boolean } = {
  entriesByInsight: new Map(),
  loading: false,
}
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ ...mapState, refetch: vi.fn() }),
}))

import { NavigationProvider, useNavigation } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

function Harness() {
  const { contentPanelTab } = useNavigation()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement("div", { "data-testid": "panel-probe" }, contentPanelTab ?? "none"),
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

const panelProbe = () => screen.getByTestId("panel-probe").textContent

// Seed localStorage exactly as a reload would leave it: the tab persists (with its
// briefMeta) but `prd` is stripped, and the active tab points at it.
function seedPersistedTab(tab: Record<string, unknown>, activeId: string) {
  localStorage.setItem("sprntly_chat_tabs_acme", JSON.stringify([tab]))
  localStorage.setItem("sprntly_chat_active_tab_acme", activeId)
}

beforeEach(() => {
  localStorage.clear()
  runPrdGeneration.mockClear()
  loadPrdById.mockClear()
  mapState = { entriesByInsight: new Map(), loading: false }
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen — PRD panel restore after reload", () => {
  it("reopens the panel and LOADS the saved PRD from the DB (no regeneration)", async () => {
    // A PRD exists in the DB for insight 0 of this brief.
    mapState = {
      entriesByInsight: new Map([[0, { prd_id: 42, prd_title: "Saved PRD", prototype: null }]]),
      loading: false,
    }
    seedPersistedTab(
      { id: "tab-1", title: "PRD · Saved PRD", thread: [], dbConvId: null, briefMeta: { briefId: 7, insightIndex: 0 }, insightBody: null },
      "tab-1",
    )

    await act(async () => { mountApp() })

    // The panel auto-opens on the PRD tab, and the doc came from loadPrdById(42) —
    // runPrdGeneration was never called.
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(42))
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })

  it("leaves the panel CLOSED when reloading onto a plain (non-PRD) chat", async () => {
    // No brief binding at all → never a candidate for restore.
    seedPersistedTab(
      { id: "tab-plain", title: "New chat", thread: [], dbConvId: null, briefMeta: null, insightBody: null },
      "tab-plain",
    )

    await act(async () => { mountApp() })
    // Give effects a chance to (not) run.
    await act(async () => { await Promise.resolve() })

    expect(panelProbe()).toBe("none")
    expect(loadPrdById).not.toHaveBeenCalled()
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })

  it("leaves the panel CLOSED for a brief-bound tab that has NO PRD in the DB", async () => {
    // briefMeta present, but the map reports no PRD for the insight → no restore,
    // and crucially no speculative regeneration.
    mapState = { entriesByInsight: new Map(), loading: false }
    seedPersistedTab(
      { id: "tab-2", title: "PRD · Nothing yet", thread: [], dbConvId: null, briefMeta: { briefId: 9, insightIndex: 0 }, insightBody: null },
      "tab-2",
    )

    await act(async () => { mountApp() })
    await act(async () => { await Promise.resolve() })

    expect(panelProbe()).toBe("none")
    expect(loadPrdById).not.toHaveBeenCalled()
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })
})
