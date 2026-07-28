// @vitest-environment jsdom
//
// ChatScreen — reopening the artifact panel from the tab strip.
//
// Closing the panel used to be one-way: the only route back was the View PRD
// button on the insight card, which sits at the TOP of the thread (buried by any
// long conversation) or mid-thread on a command-opened tab. The tab strip now
// carries the same action, pinned to its right edge. These tests mount the REAL
// ChatScreen, close the panel the way the user does, and assert the button
// brings it back with THIS tab's document — and that it's absent when there's
// nothing to reopen.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
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
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
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
const loadPrdById = vi.fn((id: number) =>
  Promise.resolve({ ok: true, prd: { prd_id: id, title: `PRD ${id}`, metaLine: "", sections: [] } }),
)
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...a: unknown[]) => runPrdGeneration(...a),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: (id: number) => loadPrdById(id),
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

vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: new Map(), loading: false, refetch: vi.fn() }),
}))

import { NavigationProvider, useNavigation } from "../../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

// The real ContentPanel isn't mounted here (it's a route-level overlay), so the
// harness exposes its close action — the same closeContentPanel the panel's ×
// and overlay call.
function Harness() {
  const { contentPanelTab, closeContentPanel } = useNavigation()
  const { content } = useContent()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement("div", { "data-testid": "panel-probe" }, contentPanelTab ?? "none"),
    React.createElement("div", { "data-testid": "prd-probe" }, content.prd?.prd_id != null ? String(content.prd.prd_id) : "none"),
    React.createElement("button", { "data-testid": "close-panel", onClick: closeContentPanel }, "close"),
    React.createElement(ChatScreen),
  )
}

const appTree = () =>
  React.createElement(
    NavigationProvider,
    null,
    React.createElement(ContentProvider, null, React.createElement(Harness)),
  )

const panelProbe = () => screen.getByTestId("panel-probe").textContent
const prdProbe = () => screen.getByTestId("prd-probe").textContent
const reopenBtn = () => screen.queryByTestId("chat-reopen-artifact")

function seedTabs(tabs: Record<string, unknown>[], activeId: string) {
  sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify(tabs))
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", activeId)
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  runPrdGeneration.mockClear()
  loadPrdById.mockClear()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
})

describe("ChatScreen — reopen the artifact panel from the tab strip", () => {
  it("brings the panel back with THIS tab's PRD after the user closes it", async () => {
    seedTabs(
      [{ id: "tab-a", title: "PRD · Alpha", thread: [], dbConvId: null, briefMeta: null, insightBody: null, prdId: 42 }],
      "tab-a",
    )

    await act(async () => { render(appTree()) })
    // The tab restores its PRD and the panel opens on its own.
    await waitFor(() => expect(prdProbe()).toBe("42"))
    // While the panel is OPEN the button stays out of the way — the panel has
    // its own close.
    expect(reopenBtn()).toBeNull()

    // The user closes the panel. It must STAY closed (no effect fights them).
    await act(async () => { fireEvent.click(screen.getByTestId("close-panel")) })
    await act(async () => { await Promise.resolve() })
    expect(panelProbe()).toBe("none")

    // …and the way back is now in the tab strip. The button is icon-only — the
    // strip is chrome, so it carries no visible label — and names itself through
    // its accessible name / tooltip instead.
    const btn = reopenBtn()
    expect(btn).not.toBeNull()
    expect(btn!.textContent?.trim()).toBe("")
    expect(btn!.getAttribute("aria-label")).toBe("View PRD")
    expect(btn!.getAttribute("title")).toBe("View PRD — reopen the panel")
    expect(btn!.querySelector("svg")).not.toBeNull()

    await act(async () => { fireEvent.click(btn!) })
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    // Reopened with the SAME document, from cache — never a regeneration.
    expect(prdProbe()).toBe("42")
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })

  it("sits in the tab strip, outside both scrolling regions", async () => {
    // The requirement is that it's reachable without scrolling — and there are
    // TWO things that scroll here. The thread body scrolls vertically, and the
    // tab list scrolls horizontally once enough tabs are open. The button has
    // to be outside both: in the strip, but a sibling of the tab list rather
    // than a child of it.
    seedTabs(
      [{ id: "tab-a", title: "PRD · Alpha", thread: [], dbConvId: null, briefMeta: null, insightBody: null, prdId: 42 }],
      "tab-a",
    )

    const { container } = await act(async () => render(appTree()))
    await waitFor(() => expect(prdProbe()).toBe("42"))
    await act(async () => { fireEvent.click(screen.getByTestId("close-panel")) })

    await waitFor(() => expect(reopenBtn()).not.toBeNull())
    const strip = container.querySelector(".chat-tab-strip")
    expect(strip).not.toBeNull()
    expect(strip!.contains(reopenBtn())).toBe(true)
    // Not in the horizontally-scrolling tab list…
    expect(within(screen.getByTestId("chat-tab-bar")).queryByTestId("chat-reopen-artifact")).toBeNull()
    // …and not in the vertically-scrolling thread body.
    const scroller = container.querySelector(".od-center-scroll")
    expect(scroller?.contains(reopenBtn())).toBe(false)
  })

  it("shows no button on a plain chat with no artifact to reopen", async () => {
    seedTabs(
      [{ id: "tab-plain", title: "New chat", thread: [], dbConvId: null, briefMeta: null, insightBody: null }],
      "tab-plain",
    )

    await act(async () => { render(appTree()) })
    await act(async () => { await Promise.resolve() })

    expect(panelProbe()).toBe("none")
    expect(reopenBtn()).toBeNull()
  })

  it("follows the active tab: no button on the brief tab, back on a PRD tab", async () => {
    // The button drives the ACTIVE chat tab's PRD. The pinned brief tab owns its
    // own panel wiring, so the strip button must not appear there.
    seedTabs(
      [{ id: "tab-a", title: "PRD · Alpha", thread: [], dbConvId: null, briefMeta: null, insightBody: null, prdId: 42 }],
      "tab-a",
    )

    await act(async () => { render(appTree()) })
    await waitFor(() => expect(prdProbe()).toBe("42"))
    await act(async () => { fireEvent.click(screen.getByTestId("close-panel")) })
    await waitFor(() => expect(reopenBtn()).not.toBeNull())

    // Switch to the pinned brief tab → button gone.
    await act(async () => {
      fireEvent.click(within(screen.getByTestId("chat-tab-bar")).getByText("Top Insights"))
    })
    await waitFor(() => expect(reopenBtn()).toBeNull())

    // Back to the PRD tab: the switch reconcile reopens the panel, so the button
    // is (correctly) hidden again while it's open.
    await act(async () => {
      fireEvent.click(within(screen.getByTestId("chat-tab-bar")).getByText("PRD · Alpha"))
    })
    await waitFor(() => expect(panelProbe()).toBe("prd"))
    expect(reopenBtn()).toBeNull()
  })
})
