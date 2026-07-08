// @vitest-environment jsdom
//
// ChatScreen — "a PRD opens as a NEW CHAT TAB with the content panel over it".
//
// Every "view/generate PRD" affordance (brief finding cards, brief composer,
// backlog item) hands the PRD off via NavigationContext.openPrdTab, which stores
// a pending request and routes to `/`. ChatScreen consumes it once (openPrdInTab),
// spawning a fresh chat tab, driving the (generate | ready | load) source into
// the shared ContentContext, and flagging the content panel (Evidence / PRD /
// Tickets) to slide open over that tab. These tests mount the REAL ChatScreen
// inside the real Navigation + Content providers and drive openPrdTab through a
// tiny in-tree harness, asserting the tab is created/activated, the source is
// honoured, and the panel is opened.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// openPrdTab calls window.scrollTo (unimplemented in jsdom) — stub it to keep
// the test output clean.
if (typeof window !== "undefined") window.scrollTo = () => {}

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

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
    },
    // A PRD tab mounts PrdInputQuestions, which loads its questions from prdApi;
    // stub it to an empty set so the panel behaviour under test is unaffected.
    prdApi: {
      listInputQuestions: vi.fn().mockResolvedValue([]),
      answerInputQuestion: vi.fn(),
    },
  }
})

const runPrdGeneration = vi.fn().mockResolvedValue({
  ok: true,
  prd: { prd_id: 77, title: "Generated PRD", metaLine: "", sections: [] },
})
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...args: unknown[]) => runPrdGeneration(...args),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromBacklog: vi.fn().mockResolvedValue({
    ok: true, prd: { prd_id: 88, title: "Backlog PRD", metaLine: "", sections: [] },
  }),
  loadPrdById: vi.fn().mockResolvedValue({
    ok: true, prd: { prd_id: 99, title: "Loaded PRD", metaLine: "", sections: [] },
  }),
}))

const runAskGeneration = vi.fn().mockResolvedValue({
  answer: "canned", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
})
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: (...args: unknown[]) => runAskGeneration(...args),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))

let pathname = "/"
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => pathname,
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

vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: new Map(), refetch: vi.fn() }),
}))

import { NavigationProvider, useNavigation, type PrdTabRequest } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

// Harness: openPrdTab as a button (the real handoff entry point any surface uses)
// + a probe that surfaces the current content-panel tab, so tests can observe the
// panel opening without mounting the heavy ContentPanel/PrdPanelContent tree.
function Harness({ request }: { request: PrdTabRequest }) {
  const { openPrdTab, contentPanelTab } = useNavigation()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement("button", { onClick: () => openPrdTab(request) }, "open-prd"),
    React.createElement("div", { "data-testid": "panel-probe" }, contentPanelTab ?? "none"),
    React.createElement(ChatScreen),
  )
}

function renderWith(request: PrdTabRequest) {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(Harness, { request })),
    ),
  )
}

const tabBar = () => within(screen.getByTestId("chat-tab-bar"))
const briefSection = () => document.querySelector("section.briefx")
const panelProbe = () => screen.getByTestId("panel-probe").textContent

async function clickOpenPrd() {
  await act(async () => { fireEvent.click(screen.getByText("open-prd")) })
}

beforeEach(() => {
  localStorage.clear()
  pathname = "/"
  runPrdGeneration.mockClear()
  runAskGeneration.mockClear()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen — PRD opens as a new chat tab with the panel", () => {
  const READY: PrdTabRequest = {
    title: "PRD · Ready doc",
    source: { kind: "ready", prd: { prd_id: 5, title: "Ready doc", metaLine: "", sections: [] } as never, meta: null },
  }

  it("spawns a new, active chat tab and slides the content panel (PRD) over it", async () => {
    renderWith(READY)
    await clickOpenPrd()

    // A new chat tab chip appears alongside the pinned brief tab, and it's active.
    await waitFor(() => expect(tabBar().getByText("PRD · Ready doc")).toBeTruthy())
    expect(tabBar().getByText("Weekly brief")).toBeTruthy()
    expect(briefSection()).toBeNull()
    // The right-side panel opened on the PRD tab.
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    // A ready PRD needs no generation.
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })

  it("drives generation for a `generate` source into its tab + opens the panel", async () => {
    renderWith({
      title: "PRD · Retention",
      source: { kind: "generate", meta: { briefId: 7, insightIndex: 0 } },
    })
    await clickOpenPrd()

    await waitFor(() => expect(tabBar().getByText("PRD · Retention")).toBeTruthy())
    // ChatScreen (not the caller) runs the generation for the new PRD tab.
    await waitFor(() => expect(runPrdGeneration).toHaveBeenCalledWith({ briefId: 7, insightIndex: 0 }))
    await waitFor(() => expect(panelProbe()).toBe("prd"))
  })

  it("reuses the same tab (by title) instead of stacking duplicates", async () => {
    renderWith(READY)
    await clickOpenPrd()
    await waitFor(() => expect(tabBar().getByText("PRD · Ready doc")).toBeTruthy())
    // Switch to the brief tab, then re-open the same PRD.
    await act(async () => { fireEvent.click(tabBar().getByText("Weekly brief")) })
    await clickOpenPrd()

    expect(tabBar().getAllByText("PRD · Ready doc")).toHaveLength(1)
  })

  it("closes the panel when switching back to the brief tab (no bleed over the brief)", async () => {
    renderWith(READY)
    await clickOpenPrd()
    // Panel is open over the new PRD tab.
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    // Switch back to the pinned brief tab → the global panel must not linger.
    await act(async () => { fireEvent.click(tabBar().getByText("Weekly brief")) })
    await waitFor(() => expect(panelProbe()).toBe("none"))
    // Brief surface is showing, panel is gone.
    expect(briefSection()).toBeTruthy()
  })
})
