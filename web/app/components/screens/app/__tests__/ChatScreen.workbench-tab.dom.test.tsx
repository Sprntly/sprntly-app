// @vitest-environment jsdom
//
// ChatScreen — the "Workbench" hand-off (`/?tab=last`).
//
// The sidebar has two doors into this one tabbed surface and they must stay
// distinct: "Top Insights" (→ /brief) always activates the pinned brief tab,
// while "Workbench" (→ /?tab=last, goToWorkbench) always activates the last
// CHAT tab the user was on — never the brief. ChatScreen remembers that tab in
// sessionStorage on every switch and consumes the one-shot param here.
//
// These mount the REAL ChatScreen inside the real Navigation + Content
// providers (boundary mocks only) and assert the resolution order:
//   1. the remembered tab, when it's still open,
//   2. the last tab in the strip, when the remembered one was closed,
//   3. a fresh "New chat" tab, when nothing is open — the nav is never a no-op.
// Plus the guard that matters most: no param → the brief tab still wins, so the
// workbench nav never changed what a plain `/` load does.
import * as React from "react"
import { act, cleanup, render, screen, within } from "@testing-library/react"
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
window.scrollTo = (() => {}) as typeof window.scrollTo

// ── Boundary mocks (network / router / heavy contexts) ─────────────────────
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
      create: vi.fn(),
      addTurn: vi.fn(),
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
      listTurns: vi.fn().mockResolvedValue({ turns: [] }),
    },
  }
})

vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn(() => null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn(),
  }),
}))

let searchString = ""
const replaceSpy = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceSpy, prefetch: vi.fn() }),
  usePathname: () => "/",
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
import { ChatScreen, NEW_CHAT_TITLE } from "../ChatScreen"

function renderScreen() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

// The session keys are user+company scoped; the mocks above pin those to the
// anonymous user in the "acme" company.
const TABS_KEY = "sprntly_chat_tabs_anon_acme"
const ACTIVE_KEY = "sprntly_chat_active_tab_anon_acme"
const LAST_KEY = "sprntly_chat_last_tab_anon_acme"

const savedTab = (id: string, title: string) => ({
  id, title, thread: [], dbConvId: null, briefMeta: null, insightBody: null, prdId: null,
})

/** Seed the session as if the user had these tabs open, sitting on the brief. */
function seedTabs(tabs: { id: string; title: string }[], lastTabId: string | null) {
  sessionStorage.setItem(TABS_KEY, JSON.stringify(tabs.map((t) => savedTab(t.id, t.title))))
  // Persisted active tab is the BRIEF — so any test that lands on a chat tab
  // proves the param moved it, not the restore.
  sessionStorage.setItem(ACTIVE_KEY, "brief")
  if (lastTabId) sessionStorage.setItem(LAST_KEY, lastTabId)
}

const tabBar = () => within(screen.getByTestId("chat-tab-bar"))
/** Title of the currently active tab chip. */
const activeTabTitle = () =>
  screen.getByTestId("chat-tab-bar").querySelector('[data-tab-active="true"]')?.textContent ?? null

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  searchString = ""
  replaceSpy.mockClear()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
})

describe("ChatScreen — ?tab=last (Workbench)", () => {
  it("restores the remembered chat tab instead of the pinned brief tab", async () => {
    seedTabs([{ id: "tab-a", title: "Checkout bug" }, { id: "tab-b", title: "Pricing page" }], "tab-a")
    searchString = "tab=last"
    await act(async () => { renderScreen() })

    expect(activeTabTitle()).toContain("Checkout bug")
    // The one-shot param is stripped so a later refresh doesn't re-fire it.
    expect(replaceSpy).toHaveBeenCalledWith("/")
  })

  it("falls back to the last open tab when the remembered one was closed", async () => {
    // "tab-gone" is remembered but no longer in the strip.
    seedTabs([{ id: "tab-a", title: "Checkout bug" }, { id: "tab-b", title: "Pricing page" }], "tab-gone")
    searchString = "tab=last"
    await act(async () => { renderScreen() })

    expect(activeTabTitle()).toContain("Pricing page")
  })

  it("opens a fresh chat tab when no chat tab is open (never a no-op)", async () => {
    searchString = "tab=last"
    await act(async () => { renderScreen() })

    // A real, active "New chat" chip — not the brief, and not a tab-less landing.
    expect(activeTabTitle()).toContain(NEW_CHAT_TITLE)
    expect(tabBar().getByText(NEW_CHAT_TITLE)).toBeTruthy()
  })

  it("without the param, a plain `/` load still defaults to the pinned brief tab", async () => {
    seedTabs([{ id: "tab-a", title: "Checkout bug" }], "tab-a")
    sessionStorage.removeItem(ACTIVE_KEY)
    searchString = ""
    await act(async () => { renderScreen() })

    expect(activeTabTitle()).toContain("Top Insights")
    expect(replaceSpy).not.toHaveBeenCalled()
  })
})

describe("ChatScreen — remembering the last chat tab", () => {
  it("records the chat tab the user switches to, and never the brief tab", async () => {
    seedTabs([{ id: "tab-a", title: "Checkout bug" }, { id: "tab-b", title: "Pricing page" }], "tab-a")
    searchString = ""
    await act(async () => { renderScreen() })

    act(() => { tabBar().getByText("Pricing page").click() })
    expect(sessionStorage.getItem(LAST_KEY)).toBe("tab-b")

    // Going back to the pinned brief tab must NOT overwrite the memory —
    // that's exactly the tab Workbench has to avoid landing on.
    act(() => { tabBar().getByText("Top Insights").click() })
    expect(sessionStorage.getItem(LAST_KEY)).toBe("tab-b")
  })
})
