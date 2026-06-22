// @vitest-environment jsdom
//
// ChatScreen brief-tab DOM tests.
//
// The Weekly/Monday Brief is the pinned, non-closable FIRST tab of the unified
// home surface (ChatScreen). It is synthesized in the render — never stored in
// the `tabs` state/localStorage — and is the DEFAULT active tab on first load.
// Selecting it renders <BriefChat/> in place of the chat landing/thread; the "+"
// opens a new chat tab (the landing composer).
//
// These tests mount the REAL ChatScreen inside the real Navigation + Content
// providers, mocking only the network/router/heavy-context boundaries the screen
// touches on mount (the same boundary-mock convention as BriefChat.*.dom.test).
// They assert the integration, not a re-implementation:
//   1. The pinned "Monday brief" tab renders FIRST and has NO close (×) button.
//   2. First load (empty localStorage) → the brief surface renders (BriefChat's
//      greeting), NOT the chat landing ("Welcome back").
//   3. Clicking "+" (New chat) switches to the chat landing composer, and the
//      brief tab stays present (never removed).
//   4. Clicking the brief tab from a chat tab switches back to the brief surface.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// jsdom doesn't implement window.matchMedia; AskReplyBody's typing-animation
// hook reads prefers-reduced-motion on mount when a fresh reply renders (the
// thread-preservation test below renders a chat tab that has a reply).
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
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
    conversationsApi: { create: vi.fn(), addTurn: vi.fn() },
  }
})

// Send path's network call — mocked so a first-message send stays off the
// network and resolves immediately (used by the first-send dedup test below).
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(async () => ({
    answer: "ok", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
  })),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn(() => null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null,
    isTriggering: false,
    showCompleted: false,
    triggerRun: vi.fn(),
  }),
}))

// Router/search-params live in module-scoped mutable holders so tests can drive
// the `?new=1` "New chat" hand-off and assert ChatScreen strips it via replace.
let searchString = ""
// Mutable pathname holder so a test can drive route-based screen derivation
// (NavigationContext computes currentScreen from usePathname()). "/brief" →
// currentScreen === "brief", which activates the pinned brief tab.
let pathname = "/"
const replaceSpy = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceSpy, prefetch: vi.fn() }),
  usePathname: () => pathname,
  useSearchParams: () => new URLSearchParams(searchString),
}))

vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: null,
    refresh: async () => {},
  }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "anonymous" }),
}))

// The prototype map hook fetches per briefId; stub it idle so mount is inert.
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

beforeEach(() => {
  localStorage.clear()
  searchString = ""
  pathname = "/"
  replaceSpy.mockClear()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

// Queries are scoped to the chat surface's own tab bar (AppLayout's sidebar also
// has "Monday brief" / "New chat" affordances, so global queries are ambiguous).
const tabBar = () => within(screen.getByTestId("chat-tab-bar"))

describe("ChatScreen — pinned brief tab", () => {
  it("renders the 'Monday brief' tab first, with no close button", () => {
    renderScreen()
    const bar = screen.getByTestId("chat-tab-bar")
    const briefTab = within(bar).getByText("Monday brief")
    expect(briefTab).toBeTruthy()
    // The pinned brief tab is the FIRST child of the tab bar.
    expect(bar.firstElementChild?.contains(briefTab)).toBe(true)
    // The pinned brief tab carries no × close control (chat tabs do).
    expect(within(briefTab.parentElement as HTMLElement).queryByTitle("Close tab")).toBeNull()
  })

  it("defaults to the brief surface on first load (no chat landing)", () => {
    renderScreen()
    // BriefChat's greeting renders ("Good day … here's this week's brief" or the
    // no-sources variant), and the chat landing's "Welcome back" must be absent.
    expect(screen.queryByText(/Welcome back/i)).toBeNull()
    // BriefChat's root <section aria-label="Weekly brief"> is on screen.
    expect(screen.getByLabelText("Weekly brief")).toBeTruthy()
  })

  it("'+' opens a VISIBLE active 'New chat' tab chip (landing), brief tab stays", () => {
    renderScreen()
    act(() => {
      fireEvent.click(tabBar().getByTitle("New chat"))
    })
    // Chat landing composer is now showing…
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()
    // …and a real "New chat" tab chip appeared in the strip (the bug fix: the
    // user must SEE they're on a new tab and be able to switch back), not a
    // tab-less landing.
    expect(tabBar().getByText(NEW_CHAT_TITLE)).toBeTruthy()
    // …and the pinned brief tab is still there (never removed).
    expect(tabBar().getByText("Monday brief")).toBeTruthy()
  })

  it("'+' renders the new tab as ACTIVE and lets the user switch back to it", () => {
    renderScreen()
    act(() => {
      fireEvent.click(tabBar().getByTitle("New chat"))
    })
    // Switch to the brief tab, then back to the visible "New chat" chip — proving
    // the chip is a real, selectable tab (not a transient landing).
    act(() => {
      fireEvent.click(tabBar().getByText("Monday brief"))
    })
    expect(screen.queryByText(/Welcome back/i)).toBeNull()
    act(() => {
      fireEvent.click(tabBar().getByText(NEW_CHAT_TITLE))
    })
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()
  })

  it("'+' clicked twice reuses the empty 'New chat' tab (no duplicate chips)", () => {
    renderScreen()
    act(() => { fireEvent.click(tabBar().getByTitle("New chat")) })
    act(() => { fireEvent.click(tabBar().getByTitle("New chat")) })
    // Only ONE "New chat" chip — the second click reused the empty tab.
    expect(tabBar().getAllByText(NEW_CHAT_TITLE)).toHaveLength(1)
  })

  it("first message renames the 'New chat' tab in place (no duplicate tab)", async () => {
    renderScreen()
    act(() => { fireEvent.click(tabBar().getByTitle("New chat")) })
    // One "New chat" chip is present and we're on the landing composer.
    expect(tabBar().getByText(NEW_CHAT_TITLE)).toBeTruthy()

    const textarea = document.querySelector(".chat-home-composer-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "what is our churn" } })
    })
    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter" })
    })

    // The placeholder tab was RENAMED to the query (not a second tab spawned).
    await waitFor(() => {
      expect(tabBar().getByText(/what is our churn/i)).toBeTruthy()
    })
    // The "New chat" placeholder title is gone (renamed in place)…
    expect(tabBar().queryByText(NEW_CHAT_TITLE)).toBeNull()
    // …and there is exactly ONE chat-tab close button (one chat tab, no dupe).
    expect(tabBar().getAllByTitle("Close tab")).toHaveLength(1)
  })

  it("clicking the brief tab returns to the brief surface", () => {
    renderScreen()
    act(() => {
      fireEvent.click(tabBar().getByTitle("New chat"))
    })
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()
    act(() => {
      fireEvent.click(tabBar().getByText("Monday brief"))
    })
    expect(screen.queryByText(/Welcome back/i)).toBeNull()
  })

  // The sidebar "New chat" affordance navigates to `/?new=1` (goToNewChat). On a
  // fresh load `/` would default to the brief tab, so the `?new=1` one-shot param
  // is what makes "New chat" land on the chat landing instead. ChatScreen must
  // consume it (start a fresh chat) and strip it via router.replace("/").
  it("'?new=1' hand-off opens the chat landing (not the brief) and strips the param", () => {
    searchString = "new=1"
    renderScreen()
    // The chat landing composer is showing — NOT the brief surface.
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()
    expect(screen.queryByLabelText("Weekly brief")).toBeNull()
    // …it lands on the SAME visible "New chat" tab chip the "+" produces…
    expect(tabBar().getByText(NEW_CHAT_TITLE)).toBeTruthy()
    // …the pinned brief tab is still present (never removed)…
    expect(tabBar().getByText("Monday brief")).toBeTruthy()
    // …and the one-shot param was stripped so a refresh won't re-trigger.
    expect(replaceSpy).toHaveBeenCalledWith("/")
  })
})

// ── Brief-tab gap coverage (B5–B8) ──────────────────────────────────────────
// These extend the suite above with behaviours NOT already covered here OR in
// the pure-logic parallel-chats / concurrent-asks suites (which don't render
// the surface at all): per-company localStorage persistence with the brief tab
// EXCLUDED, thread preservation across a brief↔chat switch, route-driven brief
// activation while mounted on a chat tab, and the ?new=1 one-shot latch.
const tabsKey = "sprntly_chat_tabs_acme"
const activeTabKey = "sprntly_chat_active_tab_acme"

// A persisted chat tab (the slim shape ChatScreen writes — no prd/evidence/
// *Generating). Used to model a restore and to drive thread preservation.
function seedPersistedChatTab(opts?: { withReply?: boolean }) {
  const tabId = "tab-persisted-1"
  const thread = [
    {
      id: "turn-1",
      query: "persisted question",
      ...(opts?.withReply
        ? { reply: { answer: "persisted answer", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } }
        : {}),
    },
  ]
  localStorage.setItem(tabsKey, JSON.stringify([{ id: tabId, title: "Persisted chat", thread, dbConvId: null, briefMeta: null }]))
  localStorage.setItem(activeTabKey, tabId)
  return tabId
}

describe("ChatScreen — brief tab gaps (B5–B8)", () => {
  // B5: open chat tabs persist to the per-company localStorage and restore on
  // remount; the pinned brief tab is synthesized first and is NEVER written into
  // the persisted tabs array or the persisted active-tab.
  it("persists chat tabs (brief excluded) and restores them on remount", () => {
    const tabId = seedPersistedChatTab()
    const { unmount } = renderScreen()

    // The persisted chat tab restored into the tab bar (alongside the synthesized
    // brief tab), and selecting it shows its thread.
    expect(tabBar().getByText("Persisted chat")).toBeTruthy()
    act(() => {
      fireEvent.click(tabBar().getByText("Persisted chat"))
    })
    expect(screen.getByText("persisted question")).toBeTruthy()

    // The persisted `tabs` array contains ONLY the chat tab — the synthesized
    // brief tab is never written to localStorage.
    const persistedTabs = JSON.parse(localStorage.getItem(tabsKey) || "[]") as Array<{ id: string }>
    expect(persistedTabs.map((t) => t.id)).toContain(tabId)
    expect(persistedTabs.some((t) => t.id === "brief")).toBe(false)
    // The persisted active tab is the chat tab id, never the brief sentinel.
    expect(localStorage.getItem(activeTabKey)).toBe(tabId)

    unmount()
    // Remount → the tab is restored again from the same storage.
    renderScreen()
    expect(tabBar().getByText("Persisted chat")).toBeTruthy()
  })

  // B6: switching from a chat tab that HAS a thread → the brief tab → back PRESERVES
  // that chat tab's thread (the brief tab is synthesized and never clobbers state).
  it("preserves a chat tab's thread across a brief↔chat tab switch", () => {
    seedPersistedChatTab({ withReply: true })
    renderScreen()

    // Activate the chat tab and confirm its thread is showing.
    act(() => {
      fireEvent.click(tabBar().getByText("Persisted chat"))
    })
    expect(screen.getByText("persisted question")).toBeTruthy()

    // Switch to the synthesized brief tab → BriefChat shows, chat thread hidden.
    act(() => {
      fireEvent.click(tabBar().getByText("Monday brief"))
    })
    expect(screen.getByLabelText("Weekly brief")).toBeTruthy()
    expect(screen.queryByText("persisted question")).toBeNull()

    // Switch back to the chat tab → its thread is intact (not clobbered).
    act(() => {
      fireEvent.click(tabBar().getByText("Persisted chat"))
    })
    expect(screen.getByText("persisted question")).toBeTruthy()
  })

  // B7: route-driven activation — when currentScreen flips to "brief" while the
  // surface is already mounted on a chat tab, it switches to the brief tab.
  it("activates the brief tab when the route flips to /brief while on a chat tab", () => {
    seedPersistedChatTab()
    const { rerender } = renderScreen()

    // Start on the chat tab (a thread surface, not the brief).
    act(() => {
      fireEvent.click(tabBar().getByText("Persisted chat"))
    })
    expect(screen.getByText("persisted question")).toBeTruthy()
    expect(screen.queryByLabelText("Weekly brief")).toBeNull()

    // Route lands on /brief (sidebar "Monday brief" → goTo("brief")). The
    // currentScreen effect must switch the surface to the pinned brief tab.
    act(() => {
      pathname = "/brief"
      rerender(
        React.createElement(
          NavigationProvider,
          null,
          React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
        ),
      )
    })
    expect(screen.getByLabelText("Weekly brief")).toBeTruthy()
    expect(screen.queryByText("persisted question")).toBeNull()
  })

  // B8: ?new=1 is a ONE-SHOT — it triggers a fresh chat landing exactly once, the
  // param is stripped via router.replace("/"), and re-renders do NOT re-fire it
  // (the consumedNewRef latch). It re-arms only when the param goes absent→present.
  it("consumes ?new=1 exactly once and does not loop on re-render", () => {
    searchString = "new=1"
    const { rerender } = renderScreen()
    // Landing showing; param stripped exactly once.
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()
    expect(replaceSpy).toHaveBeenCalledTimes(1)
    expect(replaceSpy).toHaveBeenCalledWith("/")

    // Re-render with the param STILL present (useSearchParams hands back a fresh
    // object each render) — the latch must prevent a second consume.
    act(() => {
      rerender(
        React.createElement(
          NavigationProvider,
          null,
          React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
        ),
      )
    })
    expect(replaceSpy).toHaveBeenCalledTimes(1) // NOT re-fired

    // Param goes absent → the latch re-arms; a SUBSEQUENT ?new=1 fires again.
    act(() => {
      searchString = ""
      rerender(
        React.createElement(
          NavigationProvider,
          null,
          React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
        ),
      )
    })
    expect(replaceSpy).toHaveBeenCalledTimes(1) // absent → no fire
    act(() => {
      searchString = "new=1"
      rerender(
        React.createElement(
          NavigationProvider,
          null,
          React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
        ),
      )
    })
    // Re-armed: the fresh ?new=1 consumed once more.
    expect(replaceSpy).toHaveBeenCalledTimes(2)
  })
})
