// @vitest-environment jsdom
//
// ChatScreen — an in-flight Ask resumes even when auth resolves AFTER the first
// render, which is what the real AuthProvider always does.
//
// The mount-time resume effect (ChatScreen: "Resume orphaned in-flight ASK
// jobs") reads `tabsRef.current` — a ref, so it is NOT reactive — while its
// dependency array is `[activeCompany, finalizeConversationTurn]`. Neither
// changes when auth lands, but the SESSION-STORAGE KEY does: it is
// `sprntly_chat_tabs_${authUserId}_${company}`, and `authUserId` is "anon"
// until `useAuth()` leaves `{kind:"loading"}`. So on a real page load the
// sequence is:
//
//   render 1  → key is …_anon_… → sessionStorage miss → tabs = []
//   effects   → resume scan iterates an EMPTY list, finds nothing
//   auth lands→ key changes → the reload effect restores the real tabs
//   …and the resume effect never runs again.
//
// The pending ask_id stays in localStorage, the server finishes the answer, and
// the thread sits on a question with no reply forever. Every existing test
// mocks `useAuth` as already-settled, so the whole suite is blind to it.
import * as React from "react"
import { cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = () => {}
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: /prefers-reduced-motion/.test(query), media: query, onchange: null,
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
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
    },
    prdApi: {
      get: vi.fn().mockResolvedValue({ id: 1, status: "ready" }),
      listInputQuestions: vi.fn().mockResolvedValue([]),
      answerInputQuestion: vi.fn(),
    },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: vi.fn().mockResolvedValue({ ok: true, prd: null }),
}))

const resumeAskGeneration = vi.fn()
const getPendingAsk = vi.fn()
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(),
  resumeAskGeneration: (...a: unknown[]) => resumeAskGeneration(...a),
  getPendingAsk: (...a: unknown[]) => getPendingAsk(...a),
  AskCancelledError: class AskCancelledError extends Error {},
  AskStoppedError: class AskStoppedError extends Error {},
  AskTimeoutError: class AskTimeoutError extends Error {},
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

// The whole point of this file: auth is NOT settled on the first render, the
// way the real AuthProvider behaves (it starts at {kind:"loading"} and resolves
// once Supabase answers). `authState` is flipped by the test, and a parent
// state bump re-renders ChatScreen so the hook returns the new value.
let authState: unknown = { kind: "loading" }
vi.mock("../../../../lib/auth", () => ({ useAuth: () => authState }))

vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: new Map(), refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

const USER_ID = "u-1"
// The keys ChatScreen uses ONCE AUTH HAS LANDED. Nothing is ever written under
// the "anon" key in the real app either — the tabs were saved by the previous
// mount, which was authed.
const TABS_KEY = `sprntly_chat_tabs_${USER_ID}_acme`
const ACTIVE_KEY = `sprntly_chat_active_tab_${USER_ID}_acme`

/** A tab whose last turn is still awaiting its reply — the canonical "asking…"
 *  marker the resume effect looks for. */
function seedAwaitingTab() {
  sessionStorage.setItem(TABS_KEY, JSON.stringify([{
    id: "tab-1",
    title: "Customer conversations",
    thread: [{ id: "turn-1", query: "Give me summary on last week's customer conversations." }],
    dbConvId: null, briefMeta: null, insightBody: null, prdId: null,
  }]))
  sessionStorage.setItem(ACTIVE_KEY, "tab-1")
}

let bumpAuth: () => void = () => {}

function Harness() {
  const [, setN] = React.useState(0)
  bumpAuth = () => {
    authState = { kind: "authed", user: { id: USER_ID }, session: {} }
    setN((n) => n + 1)
  }
  return React.createElement(ChatScreen)
}

function renderWith() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(Harness)),
    ),
  )
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  authState = { kind: "loading" }
  resumeAskGeneration.mockReset().mockReturnValue(new Promise(() => {}))
  // The ask_id persisted by the mount that started this answer. Keyed per tab,
  // so it only answers for the tab that actually has one in flight.
  getPendingAsk.mockReset().mockImplementation((_c: string, tabId: string) =>
    (tabId === "tab-1" ? { id: "77" } : null))
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
})

describe("ChatScreen — in-flight Ask resume when auth settles after mount", () => {
  it("re-attaches to the running ask once the authed tabs are restored", async () => {
    seedAwaitingTab()

    renderWith()

    // Render 1 ran under authUserId "anon": the tabs key missed, so there was
    // nothing to resume yet. That part is expected.
    expect(resumeAskGeneration).not.toHaveBeenCalled()

    // Auth lands. The tabs key becomes the authed one and the reload effect
    // restores the tab with its unanswered turn — at which point the running
    // ask MUST be re-attached by id.
    await waitFor(() => { bumpAuth() })

    // Re-attached BY ID (77) against the same tab — not re-POSTed. The trailing
    // `undefined` is the phase callback, which is absent while the grounded
    // progress flag is off.
    await waitFor(() =>
      expect(resumeAskGeneration).toHaveBeenCalledWith(
        77, "acme", "tab-1",
        expect.any(Function), expect.any(Function), expect.any(Function),
        expect.any(Function), undefined,
      ))
  })
})
