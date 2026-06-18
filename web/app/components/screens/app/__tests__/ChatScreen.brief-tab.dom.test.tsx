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
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

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
  }
})

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null,
    isTriggering: false,
    showCompleted: false,
    triggerRun: vi.fn(),
  }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
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

  it("'+' opens the chat landing; the brief tab stays present", () => {
    renderScreen()
    act(() => {
      fireEvent.click(tabBar().getByTitle("New chat"))
    })
    // Chat landing composer is now showing…
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()
    // …and the pinned brief tab is still there (never removed).
    expect(tabBar().getByText("Monday brief")).toBeTruthy()
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
})
