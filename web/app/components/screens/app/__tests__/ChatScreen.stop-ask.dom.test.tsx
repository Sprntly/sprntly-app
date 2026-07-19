// @vitest-environment jsdom
//
// ChatScreen STOP-an-ask DOM tests.
//
// While an ask is generating, the composer's Send button is replaced by a Stop
// button (aria-label "Stop generating"). Clicking it must:
//   1. POST the backend cancel (askApi.cancel) for the tab's pending ask_id,
//   2. reclaim the composer AT ONCE (Send returns, no longer busy),
//   3. replace the in-flight turn's thinking skeleton with a muted "You stopped
//      this response." note (not an error bubble).
//
// The send path (runAskGeneration) is mocked to return a promise that never
// resolves on its own, so the ask stays in flight and `busy` stays true until we
// stop it — mirroring a real slow generation.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

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

// ── Boundary mocks ─────────────────────────────────────────────────────────
// vi.hoisted so the spy exists when the (hoisted) vi.mock factory below runs.
const { cancelSpy } = vi.hoisted(() => ({ cancelSpy: vi.fn() }))
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }), cancel: cancelSpy },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: { create: vi.fn(), addTurn: vi.fn() },
  }
})

// runAskGeneration returns a promise that resolves ONLY when the harness wants —
// so the ask stays "in flight" (busy) and the Stop button renders. isStopped is
// captured so we can assert the component wired the stop signal through.
let capturedIsStopped: (() => boolean) | undefined
let resolveAsk: ((v: unknown) => void) | undefined
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn((_q: string, _c: string, _t: string, opts?: { isStopped?: () => boolean }) => {
    capturedIsStopped = opts?.isStopped
    return new Promise((resolve) => { resolveAsk = resolve })
  }),
  resumeAskGeneration: vi.fn(),
  // The tab's pending ask_id the Stop handler reads to call askApi.cancel.
  getPendingAsk: vi.fn(() => ({ id: "321" })),
  // Real error classes so the component's `instanceof` swallow-checks work.
  AskCancelledError: class AskCancelledError extends Error {},
  AskStoppedError: class AskStoppedError extends Error {},
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
  cancelSpy.mockReset()
  cancelSpy.mockResolvedValue({ ask_id: 321, status: "cancelled" })
  capturedIsStopped = undefined
  resolveAsk = undefined
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
})

describe("ChatScreen — Stop an in-flight ask", () => {
  it("swaps Send for a Stop button while generating, then cancels + reclaims the composer on click", async () => {
    seedThreadTab()
    renderScreen()
    await screen.findByText("first question")

    const composer = () => document.querySelector(".bc-composer") as HTMLElement
    // Before sending: Send is present, no Stop.
    expect(within(composer()).queryByLabelText("Send")).toBeTruthy()
    expect(within(composer()).queryByLabelText("Stop generating")).toBeNull()

    // Send a question — the mocked ask parks in flight (never auto-resolves).
    const textarea = document.querySelector(".bc-composer-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "a slow question to stop" } })
    })
    await act(async () => {
      fireEvent.click(within(composer()).getByLabelText("Send"))
    })

    // Now generating: the composer shows Stop, not Send, and the new turn shows
    // the thinking skeleton (no reply yet).
    const stopBtn = await waitFor(() => {
      const b = within(composer()).getByLabelText("Stop generating")
      expect(b).toBeTruthy()
      return b
    })
    expect(within(composer()).queryByLabelText("Send")).toBeNull()

    // Click Stop.
    await act(async () => {
      fireEvent.click(stopBtn)
    })

    // 1) Backend cancel POSTed for the pending ask_id (from getPendingAsk → 321).
    await waitFor(() => expect(cancelSpy).toHaveBeenCalledWith(321))
    // 2) The isStopped signal the component wired now reports true.
    expect(capturedIsStopped?.()).toBe(true)
    // 3) Composer reclaimed immediately: Send is back, Stop is gone.
    await waitFor(() => {
      expect(within(composer()).queryByLabelText("Send")).toBeTruthy()
      expect(within(composer()).queryByLabelText("Stop generating")).toBeNull()
    })
    // 4) The in-flight turn shows the muted stopped note (not an error bubble).
    await waitFor(() => {
      expect(document.querySelector(".bc-stopped")?.textContent).toMatch(/stopped this response/i)
    })
    expect(document.querySelector(".bc-error")).toBeNull()

    // Cleanly settle the parked ask promise so no unhandled rejection lingers.
    resolveAsk?.({ answer: "late", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" })
  })
})
