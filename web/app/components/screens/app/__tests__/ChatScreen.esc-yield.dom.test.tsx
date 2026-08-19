// @vitest-environment jsdom
//
// ChatScreen ESC-YIELD regression (R1) — the capture-phase Esc-to-stop guard in
// `useConversation`.
//
// While an ask is generating, a bare Escape cancels it. But Escape is ALSO the
// close key for the slash/skills palette, the `+` menu, and the attachment
// viewer. The stop listener is registered on the CAPTURE phase and reads a
// yield-ref committed at the instant Escape was pressed, so:
//   * something open + Esc → the ask SURVIVES (stop NOT called); the overlay
//     closes on its own bubble handler;
//   * nothing open + bare Esc while busy → stop is called exactly once;
//   * open → Esc (closes, no cancel) → bare Esc → cancels — proving the ref is
//     never latched into a stuck state after an overlay close.
//
// `runAskGeneration` is parked (never auto-resolves) so `busy` stays true and
// the Esc listener stays registered, mirroring a real slow generation. The
// backend cancel (`askApi.cancel`) is the observable for "stop was called".
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
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

const { cancelSpy } = vi.hoisted(() => ({ cancelSpy: vi.fn() }))
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: {
      ask: vi.fn(),
      cancel: cancelSpy,
      // The skills palette (Esc closes it) needs at least one option to open.
      skills: vi.fn().mockResolvedValue({
        skills: [
          { id: "my-estimator", label: "My Estimator", trigger: "/my-estimator", description: "Scores features", category: "Custom" },
        ],
      }),
    },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: { create: vi.fn(), addTurn: vi.fn() },
  }
})

let resolveAsk: ((v: unknown) => void) | undefined
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(() => new Promise((resolve) => { resolveAsk = resolve })),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn(() => ({ id: "321" })),
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
        id: tabId, title: "Seeded chat",
        thread: [
          { id: "turn-1", query: "first question", reply: { answer: "first answer", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } },
        ],
        dbConvId: null, briefMeta: null,
      },
    ]),
  )
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", tabId)
}

const composer = () => document.querySelector(".cx") as HTMLElement
const textarea = () => document.querySelector(".cx-input") as HTMLTextAreaElement

/** Send a question so an ask parks in flight and `busy` stays true. */
async function sendAndBecomeBusy() {
  await act(async () => { fireEvent.change(textarea(), { target: { value: "a slow question" } }) })
  await act(async () => { fireEvent.click(within(composer()).getByLabelText("Send")) })
  await waitFor(() => expect(within(composer()).getByLabelText("Stop generating")).toBeTruthy())
}

async function pressEscape() {
  await act(async () => { fireEvent.keyDown(textarea(), { key: "Escape" }) })
}

beforeEach(() => {
  localStorage.clear()
  cancelSpy.mockReset()
  cancelSpy.mockResolvedValue({ ask_id: 321, status: "cancelled" })
  resolveAsk = undefined
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
  // Settle the parked ask so no unhandled rejection lingers.
  resolveAsk?.({ answer: "late", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" })
})

describe("ChatScreen — Esc yields to open overlays, cancels only when nothing is open", () => {
  it("an OPEN skills palette + Esc closes the palette but does NOT cancel the ask", async () => {
    seedThreadTab()
    renderScreen()
    await screen.findByText("first question")
    await sendAndBecomeBusy()

    // Open the skills palette (⌘/) — this is a yield-owning overlay.
    await act(async () => { fireEvent.keyDown(textarea(), { key: "/", metaKey: true }) })
    await screen.findByRole("listbox", { name: "Skills" })

    await pressEscape()

    // The palette closed on its own handler…
    await waitFor(() => expect(screen.queryByRole("listbox", { name: "Skills" })).toBeNull())
    // …but the ask SURVIVED: no backend cancel, still generating (Stop present).
    expect(cancelSpy).not.toHaveBeenCalled()
    expect(within(composer()).getByLabelText("Stop generating")).toBeTruthy()
  })

  it("nothing open + a bare Esc while busy cancels the ask exactly once", async () => {
    seedThreadTab()
    renderScreen()
    await screen.findByText("first question")
    await sendAndBecomeBusy()

    await pressEscape()

    await waitFor(() => expect(cancelSpy).toHaveBeenCalledWith(321))
    expect(cancelSpy).toHaveBeenCalledTimes(1)
  })

  it("open → Esc (closes, no cancel) → bare Esc → cancels: the yield-ref never latches", async () => {
    seedThreadTab()
    renderScreen()
    await screen.findByText("first question")
    await sendAndBecomeBusy()

    // Open palette, Esc → closes without cancelling.
    await act(async () => { fireEvent.keyDown(textarea(), { key: "/", metaKey: true }) })
    await screen.findByRole("listbox", { name: "Skills" })
    await pressEscape()
    await waitFor(() => expect(screen.queryByRole("listbox", { name: "Skills" })).toBeNull())
    expect(cancelSpy).not.toHaveBeenCalled()

    // A second, now-bare Esc DOES cancel — proving the close left no stuck ref.
    await pressEscape()
    await waitFor(() => expect(cancelSpy).toHaveBeenCalledWith(321))
    expect(cancelSpy).toHaveBeenCalledTimes(1)
  })
})
