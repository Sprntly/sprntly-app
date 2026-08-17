// @vitest-environment jsdom
//
// The deterministic golden DOM suite for the byte-identical main-chat shell
// extraction. Each test renders the REAL ChatScreen across the state matrix and
// snapshots the shell-owned subtree — `<main class="od-center">` — as exact
// outerHTML. The snapshots are recorded at C1 against the UNMODIFIED ChatScreen
// (provably pre-migration: C1's diffstat carries zero ChatScreen.tsx lines), and
// must remain byte-identical at C2 (post-mapMainTurns) and C3 (rendered through
// ChatShell). That equality — not any assertion in this file — is the proof the
// migration changed no rendered DOM.
//
// ALL nondeterminism is seeded: fixed turn ids in the fixtures, fake timers for
// wall-clock reads, a stubbed crypto.randomUUID, prefers-reduced-motion forced
// on so simulated-typing settles instantly with no timers, and deterministic
// mocked payloads — so byte-equality never fails for noise (a flaky gate gets
// "fixed" by loosening, which would void the proof).
//
// This suite is MIGRATION SCAFFOLDING — a later ticket deletes it once the
// project surfaces are folded; the durable semantic assertions live in
// chat-shell/__tests__/ChatShell.unit.dom.test.tsx.
//
// BASELINE MOVED ONCE, deliberately (edit-and-resend, 2026-08-17). Four
// goldens — stopped / error / timed-out / interrupted — gained a
// `.bc-user-actions` row carrying the edit affordance, because those are
// exactly the four states a question can be edited and re-sent from. That is a
// FEATURE changing rendered DOM, not the extraction changing it: the other
// fifteen goldens are untouched, which is what still makes the C1→C3 equality
// argument above hold for everything the migration covered. A future diff here
// that is NOT a named, intended feature is still the bug this suite exists to
// catch.
//
// NOTE (authorized overage): this ticket's diff exceeds the ~500-line guideline
// by design. It is a byte-identical extraction whose bulk is this file plus the
// auto-generated .snap goldens — authorized overage, not a split trigger.
//
// A handful of states are live-only (an ask genuinely in flight: busy composer,
// the optimistic pending-send bubble, the mid-stream partial, the open clarify
// gate, the async suggestions strip). They cannot be reached from sessionStorage
// alone, so those cases seed their nearest deterministic static form; the
// byte-identity guarantee is unaffected because the identical seed runs at C1
// and C3.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeAll, beforeEach, afterAll, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (q: string) =>
    ({
      matches: true, // reduced motion → replies settle instantly, no timers
      media: q,
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

// ── Boundary mocks (network / router / heavy contexts), mirroring the existing
//    ChatScreen.*.dom.test.tsx suites ────────────────────────────────────────
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  const noop = vi.fn()
  return {
    ApiError,
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: noop, skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: { create: noop, addTurn: noop },
    artifactsApi: {},
    attachmentsApi: {},
    chatSuggestionsApi: { forTab: vi.fn().mockResolvedValue({ suggestions: [] }) },
    customArtifactsApi: {},
    storiesApi: {},
    ticketDataApi: {},
  }
})
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn(() => null),
  AskCancelledError: class extends Error {},
  AskStoppedError: class extends Error {},
  AskTimeoutError: class extends Error {},
}))
vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))
let searchString = ""
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
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
import { ChatScreen } from "../ChatScreen"

const FIXED_NOW = 1_700_000_000_000

function renderScreen() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

type Turn = Record<string, unknown> & { id: string; query: string }

function seedTabs(tabs: Array<Record<string, unknown>>, activeId: string) {
  sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify(tabs))
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", activeId)
}
function seedThread(thread: Turn[], extra: Record<string, unknown> = {}) {
  seedTabs([{ id: "tab-1", title: "Seeded chat", thread, dbConvId: null, briefMeta: null, ...extra }], "tab-1")
}

const REPLY = {
  answer: "The settled answer body.",
  sources: [],
  follow_ups: [],
  key_points: [],
  citations: [],
  confidence: 1,
  unanswered: "",
}

/** Render, let effects settle deterministically, snapshot the shell subtree. */
async function snapshotShell() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
  const main = document.querySelector("main.od-center")
  expect(main, "main.od-center must render for the active (non-brief) tab").toBeTruthy()
  return (main as HTMLElement).outerHTML
}

let randomCounter = 0
beforeAll(() => {
  // Fake only the wall clock — NOT setTimeout/setInterval, which @testing-library's
  // waitFor/act polling relies on. Reduced-motion is forced on, so the simulated
  // stream schedules no timers anyway.
  vi.useFakeTimers({ now: FIXED_NOW, toFake: ["Date", "performance"] })
  vi.stubGlobal("crypto", {
    ...globalThis.crypto,
    randomUUID: () => `00000000-0000-4000-8000-${String(++randomCounter).padStart(12, "0")}`,
  })
})
afterAll(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
beforeEach(() => {
  localStorage.clear()
  searchString = ""
  randomCounter = 0
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen shell golden DOM (byte-identical extraction)", () => {
  it("test_golden_landing_dom_snapshot", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(screen.getByText(/Welcome back/i)).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_thread_settled_dom_snapshot", async () => {
    seedThread([{ id: "turn-1", query: "settled question", reply: REPLY }])
    renderScreen()
    await waitFor(() => expect(screen.getByText("settled question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_streaming_partial_dom_snapshot", async () => {
    // live-only true stream → nearest deterministic static form
    seedThread([{ id: "turn-1", query: "streaming question", partial: "partial streamed text" }])
    renderScreen()
    await waitFor(() => expect(screen.getByText("streaming question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_stopped_dom_snapshot", async () => {
    seedThread([{ id: "turn-1", query: "stopped question", stopped: true }])
    renderScreen()
    await waitFor(() => expect(screen.getByText("stopped question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_error_dom_snapshot", async () => {
    seedThread([{ id: "turn-1", query: "error question", error: "the run failed" }])
    renderScreen()
    await waitFor(() => expect(screen.getByText("error question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_timed_out_dom_snapshot", async () => {
    seedThread([{ id: "turn-1", query: "timed out question", timedOut: true }])
    renderScreen()
    await waitFor(() => expect(screen.getByText("timed out question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_clarify_open_dom_snapshot", async () => {
    // the open-gate clarify card is live-only (pendingClarify is transient, not
    // restored) → nearest deterministic form: the clarify turn without an open gate
    seedThread([
      { id: "turn-1", query: "clarify question", clarify: [{ prompt: "Which area?", options: ["A", "B"], header: "Scope" }] },
    ])
    renderScreen()
    await waitFor(() => expect(screen.getByText("clarify question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_clarify_resolved_dom_snapshot", async () => {
    seedThread([
      {
        id: "turn-1",
        query: "clarify resolved question",
        clarify: [{ prompt: "Which area?", options: ["A", "B"], header: "Scope" }],
        clarifyResolved: { answers: [{ prompt: "Which area?", answer: "A", assumed: false }] },
        reply: REPLY,
      },
    ])
    renderScreen()
    await waitFor(() => expect(screen.getByText("clarify resolved question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_attachments_on_turn_dom_snapshot", async () => {
    seedThread([
      { id: "turn-1", query: "attachments question", attachments: [{ name: "spec.pdf" }], reply: REPLY },
    ])
    renderScreen()
    await waitFor(() => expect(screen.getByText("attachments question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_pending_send_dom_snapshot", async () => {
    // the optimistic pending-send bubble is live-only → nearest form: a turn
    // whose artifact summary is still pending (a distinct in-flight footer)
    seedThread([{ id: "turn-1", query: "pending send question", reply: REPLY, summaryPending: true }])
    renderScreen()
    await waitFor(() => expect(screen.getByText("pending send question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_hydrating_dom_snapshot", async () => {
    // history-hydrating is live-only → nearest form: an insight-bound tab with an
    // empty thread (showThreadView true, composer mounted, no turns)
    seedThread([], { prdId: 42 })
    renderScreen()
    await waitFor(() => expect(document.querySelector("main.od-center")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_insight_header_open_dom_snapshot", async () => {
    seedThread([{ id: "turn-1", query: "insight header question", reply: REPLY }], { prdId: 77 })
    renderScreen()
    await waitFor(() => expect(screen.getByText("insight header question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_inline_prd_open_dom_snapshot", async () => {
    seedThread([{ id: "turn-1", query: "inline prd question", reply: REPLY }], {
      prdId: 88,
      prdInFlow: true,
      prdFlowTurnId: "turn-1",
    })
    renderScreen()
    await waitFor(() => expect(screen.getByText("inline prd question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_ticket_set_footer_dom_snapshot", async () => {
    // the ticket-set action footer is host-derived from transient run state →
    // nearest form: a settled no-PRD chat carrying a persisted ticketSetId
    seedThread([{ id: "turn-1", query: "ticket set question", reply: REPLY }], { ticketSetId: 5 })
    renderScreen()
    await waitFor(() => expect(screen.getByText("ticket set question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_suggestions_strip_dom_snapshot", async () => {
    // the suggestions strip is fed by an async per-tab fetch → nearest form:
    // a settled thread (empty suggestions render nothing, as designed)
    seedThread([{ id: "turn-1", query: "suggestions question", reply: REPLY }])
    renderScreen()
    await waitFor(() => expect(screen.getByText("suggestions question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_busy_composer_with_hint_dom_snapshot", async () => {
    // a busy composer is live-only (an ask in flight) → nearest form: a settled
    // thread; the composer renders in its idle dock state
    seedThread([{ id: "turn-1", query: "busy composer question", reply: REPLY }])
    renderScreen()
    await waitFor(() => expect(screen.getByText("busy composer question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_tab_strip_n_tabs_reopen_dom_snapshot", async () => {
    seedTabs(
      [
        { id: "tab-1", title: "First chat", thread: [{ id: "turn-1", query: "tab one question", reply: REPLY }], dbConvId: null, briefMeta: null },
        { id: "tab-2", title: "Second chat", thread: [{ id: "turn-2", query: "tab two question", reply: REPLY }], dbConvId: null, briefMeta: null },
        { id: "tab-3", title: "Third chat", thread: [{ id: "turn-3", query: "tab three question", reply: REPLY }], dbConvId: null, briefMeta: null },
      ],
      "tab-1",
    )
    renderScreen()
    await waitFor(() => expect(screen.getByText("tab one question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  // ── Transition cases ──────────────────────────────────────────────────────

  it("test_golden_transition_landing_to_thread", async () => {
    // two sequential renders, snapshot after each; both must stay byte-stable
    // across C1→C3
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(screen.getByText(/Welcome back/i)).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
    cleanup()

    searchString = ""
    seedThread([{ id: "turn-1", query: "landed thread question", reply: REPLY }])
    renderScreen()
    await waitFor(() => expect(screen.getByText("landed thread question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })

  it("test_golden_transition_tab_switch", async () => {
    seedTabs(
      [
        { id: "tab-1", title: "Alpha chat", thread: [{ id: "turn-1", query: "alpha question", reply: REPLY }], dbConvId: null, briefMeta: null },
        { id: "tab-2", title: "Beta chat", thread: [{ id: "turn-2", query: "beta question", reply: REPLY }], dbConvId: null, briefMeta: null },
      ],
      "tab-1",
    )
    renderScreen()
    await waitFor(() => expect(screen.getByText("alpha question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()

    // switch to the second tab and snapshot the shell after the switch
    const betaTab = screen.getByText("Beta chat").closest(".chat-tab") as HTMLElement
    await act(async () => {
      fireEvent.click(betaTab)
      await Promise.resolve()
    })
    await waitFor(() => expect(screen.getByText("beta question")).toBeTruthy())
    expect(await snapshotShell()).toMatchSnapshot()
  })
})
