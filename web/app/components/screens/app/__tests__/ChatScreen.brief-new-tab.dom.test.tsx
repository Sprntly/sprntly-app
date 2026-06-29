// @vitest-environment jsdom
//
// ChatScreen — "a chat started from the weekly brief opens a NEW chat tab".
//
// The weekly/Monday brief is the pinned first tab of the unified home surface
// (ChatScreen); selecting it renders <BriefChat/>. A question typed on that
// brief surface must NOT thread inline into the brief — it opens its OWN chat
// tab on the host surface, one fresh tab per chat started. BriefChat hands the
// query off via NavigationContext.pendingChatHandoff; ChatScreen consumes it and
// spawns the tab (the brief tab is synthetic + thread-less, so an inline append
// would silently no-op anyway).
//
// These tests mount the REAL ChatScreen inside the real Navigation + Content
// providers (same boundary-mock convention as ChatScreen.brief-tab.dom.test),
// adding a runAskGeneration mock so the seeded ask resolves deterministically.
// They assert the integration end-to-end: brief composer submit → new tab.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// AskReplyBody's typing-animation hook reads prefers-reduced-motion on mount when
// a fresh reply renders; jsdom lacks matchMedia.
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
    // Per-tab persistence (fire-and-forget) touches these; canned ids keep it inert.
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
    },
  }
})

// The brief composer routes a plain question through runAskGeneration (fire-and-
// forget POST). Resolve it to a canned reply so the new tab's thread settles.
const runAskGeneration = vi.fn().mockResolvedValue({
  answer: "canned answer",
  sources: [],
  follow_ups: [],
  key_points: [],
  citations: [],
  confidence: 1,
  unanswered: "",
})
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: (...args: unknown[]) => runAskGeneration(...args),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null,
    isTriggering: false,
    showCompleted: false,
    triggerRun: vi.fn(),
  }),
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

const tabBar = () => within(screen.getByTestId("chat-tab-bar"))
// The BriefChat surface is <section class="briefx" aria-label="Weekly brief">.
// The sidebar rail item shares the "Weekly brief" accessible name, so match the
// section by class (a global getByLabelText would be ambiguous).
const briefSection = () => document.querySelector("section.briefx")

// Type a question into the brief composer and submit (Enter).
async function askFromBrief(question: string) {
  const composer = screen.getByPlaceholderText(/Ask anything/i)
  await act(async () => {
    fireEvent.change(composer, { target: { value: question } })
    fireEvent.keyDown(composer, { key: "Enter" })
  })
}

beforeEach(() => {
  localStorage.clear()
  pathname = "/"
  runAskGeneration.mockClear()
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen — chat from the brief opens a new tab", () => {
  it("spawns a new chat tab seeded with the query (not inline in the brief)", async () => {
    renderScreen()
    // Default surface is the brief.
    expect(briefSection()).toBeTruthy()

    await askFromBrief("How is onboarding trending this week?")

    // A new chat tab appeared, titled with the query; the pinned brief tab stays.
    expect(tabBar().getByText("How is onboarding trending this week?")).toBeTruthy()
    expect(tabBar().getByText("Weekly brief")).toBeTruthy()

    // The surface switched off the brief (the question did NOT thread inline into
    // it) — the brief section is unmounted while the new chat tab is active.
    expect(briefSection()).toBeNull()
    // The question shows as the new tab's chat turn (its user bubble).
    expect(document.querySelector(".bc-user-bubble")?.textContent).toBe(
      "How is onboarding trending this week?",
    )

    // The handoff actually fired the ask in the new tab.
    expect(runAskGeneration).toHaveBeenCalledTimes(1)
  })

  it("opens ANOTHER new tab the next time a chat is started from the brief", async () => {
    renderScreen()
    await askFromBrief("First question about retention?")
    // Back to the brief tab to start a second chat.
    act(() => {
      fireEvent.click(tabBar().getByText("Weekly brief"))
    })
    expect(briefSection()).toBeTruthy()
    await askFromBrief("Second unrelated question about churn?")

    // Two distinct chat tabs now exist alongside the pinned brief tab.
    expect(tabBar().getByText("First question about retention?")).toBeTruthy()
    expect(tabBar().getByText("Second unrelated question about churn?")).toBeTruthy()
    expect(tabBar().getByText("Weekly brief")).toBeTruthy()
    expect(runAskGeneration).toHaveBeenCalledTimes(2)
  })

  it("keeps a PRD command on the brief (rail), not a new chat tab", async () => {
    renderScreen()
    await askFromBrief("generate a PRD for onboarding")

    // A PRD command is not a chat — it drives the right rail in place. No new
    // chat tab is spawned and the brief surface stays active.
    expect(briefSection()).toBeTruthy()
    expect(tabBar().queryByText(/generate a PRD for onboarding/i)).toBeNull()
    expect(runAskGeneration).not.toHaveBeenCalled()
  })
})
