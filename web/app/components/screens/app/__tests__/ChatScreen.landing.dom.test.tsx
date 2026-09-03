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
//   3. Clicking the revenue chip FILLS Ask with its prompt (no auto-send);
//      clicking the project chip opens project creation and sends nothing.
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
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
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

// The real modal drags in its own hooks + routing; a marker is enough to prove
// the landing chip opens it.
vi.mock("../projects/CreateProjectModal", () => ({
  CreateProjectModal: (props: { open: boolean }) =>
    props.open ? React.createElement("div", { "data-testid": "create-project-modal" }) : null,
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

// The two curated home chips under the composer (spec §14, 2026-09-03). They
// replaced "Show me this week's top insights" and "Give me summary on last
// week's customer conversations" — see DEFAULT_HOME_STARTER_CARDS.
const REVENUE_CHIP = "What should I do to drive revenue"
const PROJECT_CHIP = "Create a project and collaborate with my team"

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
// shape ChatScreen restores from sessionStorage.
function seedThreadTab() {
  const tabId = "tab-seed-1"
  sessionStorage.setItem(
    "sprntly_chat_tabs_anon_acme",
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
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", tabId)
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

  it("renders the two curated home chips under the composer on the landing", () => {
    searchString = "new=1"
    renderScreen()
    expect(screen.getByText(REVENUE_CHIP)).toBeTruthy()
    expect(screen.getByText(PROJECT_CHIP)).toBeTruthy()
  })

  it("no longer offers the top-insights or customer-conversations chips", () => {
    // Replaced, not added to — the row is still exactly two chips.
    searchString = "new=1"
    renderScreen()
    expect(screen.queryByText(/this week's top insights/i)).toBeNull()
    expect(screen.queryByText(/customer conversations/i)).toBeNull()
    expect(document.querySelectorAll(".home-chip").length).toBe(2)
  })

  it("does NOT render the landing chips once a tab has a thread", () => {
    seedThreadTab()
    renderScreen()
    expect(screen.getByText("first question")).toBeTruthy()
    expect(screen.queryByText(REVENUE_CHIP)).toBeNull()
    expect(screen.queryByText(PROJECT_CHIP)).toBeNull()
  })

  it("pre-fills the composer with the revenue chip's prompt when clicked", async () => {
    searchString = "new=1"
    renderScreen()
    const composer = screen.getByPlaceholderText(/Ask Sprntly anything/i) as HTMLTextAreaElement
    expect(composer.value).toBe("")

    const btn = screen.getByText(REVENUE_CHIP).closest("button") as HTMLButtonElement
    expect(btn).toBeTruthy()
    await act(async () => {
      fireEvent.click(btn)
    })
    // Fills Ask (does not auto-send) — same mechanics the old feedback chip
    // had, so the question can be edited before it goes.
    await waitFor(() => {
      expect(composer.value).toContain("drive revenue")
    })
    expect(screen.queryByTestId("create-project-modal")).toBeNull()
  })

  it("opens project creation — and sends nothing — when the project chip is clicked", async () => {
    // The one home card that is neither a prompt nor a navigation: it starts
    // something. Same modal as Projects → "New project".
    searchString = "new=1"
    renderScreen()
    const composer = screen.getByPlaceholderText(/Ask Sprntly anything/i) as HTMLTextAreaElement
    expect(screen.queryByTestId("create-project-modal")).toBeNull()

    const btn = screen.getByText(PROJECT_CHIP).closest("button") as HTMLButtonElement
    expect(btn).toBeTruthy()
    await act(async () => {
      fireEvent.click(btn)
    })
    await waitFor(() => {
      expect(screen.getByTestId("create-project-modal")).toBeTruthy()
    })
    // Nothing was typed into Ask and nothing was sent.
    expect(composer.value).toBe("")
  })
})
