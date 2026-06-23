// @vitest-environment jsdom
//
// ChatScreen WELCOME-SUGGESTIONS DOM tests.
//
// The chat LANDING (fresh-chat empty state, reached via `?new=1` / the "+" New
// chat button) renders a lightweight "not sure where to start?" affordance: a
// row of concrete suggestion chips that steer the user toward real PM actions
// instead of leaving them to send a vague "hey". This is a pure UI nudge — it
// does NOT change the agent's backend response logic. Clicking a suggestion
// sends it as an ask.
//
// What is covered:
//   1. The landing renders the welcome-suggestions affordance with the concrete
//      suggestion labels (prioritize / analyze feedback / generate a PRD).
//   2. The suggestions only appear on the empty landing — once a tab has a
//      thread (the THREAD composer state), they're gone.
//   3. Clicking a suggestion sends its prompt as an ask (the send path fires).
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

describe("ChatScreen welcome suggestions (empty landing)", () => {
  it("renders the welcome-suggestions affordance with concrete suggestions on the landing", () => {
    searchString = "new=1"
    renderScreen()
    // We are on the chat landing, not the brief surface.
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()

    const container = screen.getByTestId("chat-welcome-suggestions")
    expect(container).toBeTruthy()
    // Concrete, action-oriented suggestions steering the user.
    expect(screen.getByText("Help me prioritize projects")).toBeTruthy()
    expect(screen.getByText("Analyze feedback")).toBeTruthy()
    expect(screen.getByText("Generate a PRD")).toBeTruthy()
    // They are real, clickable buttons (not inert text).
    const items = container.querySelectorAll("button")
    expect(items.length).toBe(3)
  })

  it("does NOT render the welcome suggestions once a tab has a thread", () => {
    seedThreadTab()
    renderScreen()
    expect(screen.getByText("first question")).toBeTruthy()
    expect(screen.queryByTestId("chat-welcome-suggestions")).toBeNull()
  })

  it("sends the suggestion's prompt as an ask when clicked", async () => {
    searchString = "new=1"
    renderScreen()
    const btn = screen.getByText("Generate a PRD")
    await act(async () => {
      fireEvent.click(btn)
    })
    await waitFor(() => {
      expect(askedQueries.length).toBeGreaterThan(0)
    })
    const sent = askedQueries[askedQueries.length - 1]
    expect(sent).toContain("Generate a PRD")
  })
})
