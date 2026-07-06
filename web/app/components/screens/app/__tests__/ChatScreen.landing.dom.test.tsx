// @vitest-environment jsdom
//
// ChatScreen LANDING DOM tests.
//
// The chat LANDING (fresh-chat empty state, reached via `?new=1` / the "+" New
// chat button) shows a greeting + composer + a small row of curated suggestion
// chips UNDER the composer (the home-chip row). The old "not sure where to
// start? Try one of these:" welcome-suggestion chips ABOVE the composer were
// removed — this file guards that they stay gone and that the remaining landing
// still works.
//
// What is covered:
//   1. The landing renders the greeting but NOT the removed welcome-suggestions
//      affordance (nor its concrete labels).
//   2. The curated home chips render under the composer on the empty landing,
//      and are gone once a tab has a thread (the THREAD composer state).
//   3. Clicking a curated home chip sends its prompt as an ask (send path fires).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// jsdom doesn't implement window.matchMedia; AskReplyBody's typing-animation
// hook reads prefers-reduced-motion on mount when a fresh reply renders.
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

const askedQueries: string[] = []
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(async (query: string) => {
    askedQueries.push(query)
    return { answer: "ok", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" }
  }),
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

let searchString = ""
const replaceSpy = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceSpy, prefetch: vi.fn() }),
  usePathname: () => "/",
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

vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: {}, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

// The curated home chip (under the composer) that fires an ask when clicked.
const FEEDBACK_CHIP = "Give me feedback on last week's customer conversations"

function renderScreen() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

// Seed a persisted chat tab WITH a thread so the THREAD composer renders on
// mount (active tab = a tab that already has a turn). Mirrors the persisted
// shape ChatScreen restores from localStorage.
function seedThreadTab() {
  const tabId = "tab-seed-1"
  localStorage.setItem(
    "sprntly_chat_tabs_acme",
    JSON.stringify([
      {
        id: tabId,
        title: "Seeded chat",
        thread: [
          {
            id: "turn-1",
            query: "first question",
            reply: { answer: "first answer", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" },
          },
        ],
        dbConvId: null,
        briefMeta: null,
      },
    ]),
  )
  localStorage.setItem("sprntly_chat_active_tab_acme", tabId)
}

beforeEach(() => {
  localStorage.clear()
  searchString = ""
  replaceSpy.mockClear()
  askedQueries.length = 0
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen landing", () => {
  it("renders the greeting but NOT the removed welcome-suggestions affordance", () => {
    searchString = "new=1"
    renderScreen()
    // We are on the chat landing, not the brief surface.
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()

    // The old "Try one of these:" chip row (and its concrete labels) is gone.
    expect(screen.queryByTestId("chat-welcome-suggestions")).toBeNull()
    expect(screen.queryByText("Help me prioritize projects")).toBeNull()
    expect(screen.queryByText("Analyze feedback")).toBeNull()
    expect(screen.queryByText("Generate a PRD")).toBeNull()
  })

  it("renders the curated home chips under the composer on the landing", () => {
    searchString = "new=1"
    renderScreen()
    expect(screen.getByText(FEEDBACK_CHIP)).toBeTruthy()
  })

  it("does NOT render the landing chips once a tab has a thread", () => {
    seedThreadTab()
    renderScreen()
    expect(screen.getByText("first question")).toBeTruthy()
    expect(screen.queryByText(FEEDBACK_CHIP)).toBeNull()
  })

  it("pre-fills the composer with the chip's prompt when a curated chip is clicked", async () => {
    searchString = "new=1"
    renderScreen()
    const composer = screen.getByPlaceholderText(/Ask Sprntly anything/i) as HTMLTextAreaElement
    expect(composer.value).toBe("")

    const btn = screen.getByText(FEEDBACK_CHIP).closest("button") as HTMLButtonElement
    expect(btn).toBeTruthy()
    await act(async () => {
      fireEvent.click(btn)
    })
    // The curated "feedback" chip fills Ask (does not auto-send), so the
    // composer draft is populated with the chip's prompt for the user to send.
    await waitFor(() => {
      expect(composer.value).toContain("customer conversations")
    })
  })
})
