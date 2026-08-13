// @vitest-environment jsdom
//
// ChatScreen — "break this into tickets" in a chat with NO PRD.
//
// Four invariants, in rough order of how much they cost when broken:
//
//  1. ONE run per request. The insight path does not dedupe (two phrasings of
//     one ask are two `ticket_sets` rows and two multi-minute LLM bills), so a
//     second send while a run is in flight must not start a second one.
//  2. No markdown wall. The request never reaches the ask agent, and the ticket
//     bodies never appear in a chat bubble — the whole point of the artifact.
//  3. The PLANNER is the only dispatcher, and its failure mode is deliberate:
//     a failed /v1/chat/intent call degrades the message to a grounded ANSWER,
//     never to a client-side guess that bills a multi-minute generation. (The
//     `chat_intent_envelope` kill switch and the legacy regex ladder it fell
//     back to are gone.)
//  4. Reopening a thread SHOWS what it produced — a live run on its progress, a
//     finished set on its tickets. Never a stale "Writing tickets…", never a
//     blank Tickets tab over a set that exists.
//
// The panel itself lives in AppShell, not here, so (4) is asserted on the shared
// content ChatScreen writes — which is exactly what ContentPanel's branches read
// (`ticketSetGenerating` + no stories → the generating pane; a settled set with
// stories → the list).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = () => {}
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      // Reduced motion → replies render in full immediately (no typing sim).
      matches: /prefers-reduced-motion/.test(query), media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const STORY_A = {
  id: "s1", title: "Retry the webhook with backoff", body: "",
  acceptance_criteria: [], priority: null, route: null,
}
const STORY_B = {
  id: "s2", title: "Surface the failure in the audit log", body: "",
  acceptance_criteria: [], priority: null, route: null,
}

const {
  resolveIntent, chatSummary, byConversation, getSet, runTicketSetGeneration,
} = vi.hoisted(() => ({
  resolveIntent: vi.fn(),
  chatSummary: vi.fn(),
  byConversation: vi.fn(),
  getSet: vi.fn(),
  runTicketSetGeneration: vi.fn(),
}))

vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 7, insights: [] }) },
    prdApi: { classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }) },
    artifactsApi: { chatSummary: (...a: unknown[]) => chatSummary(...a) },
    chatIntentApi: { resolve: (...a: unknown[]) => resolveIntent(...a) },
    ticketSetsApi: {
      byConversation: (...a: unknown[]) => byConversation(...a),
      get: (...a: unknown[]) => getSet(...a),
    },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 42 }),
      addTurn: vi.fn().mockResolvedValue({}),
      update: vi.fn().mockResolvedValue({}),
      listTurns: vi.fn().mockResolvedValue({ turns: [] }),
    },
  }
})

// `loadTicketSet` stays REAL — it is the open-by-id path under test in the
// resume cases. Only the generation runner is stubbed, so a test can hold a run
// open and assert what the chat does while it is in flight.
vi.mock("../../../../lib/runTicketSetGeneration", async () => {
  const actual = await vi.importActual<typeof import("../../../../lib/runTicketSetGeneration")>(
    "../../../../lib/runTicketSetGeneration",
  )
  return { ...actual, runTicketSetGeneration: (...a: unknown[]) => runTicketSetGeneration(...a) }
})

// A generating row must PARK after its first read, not spin the loop for the
// length of the test.
vi.mock("../../../../lib/poll", () => ({
  sleepUntilNextPoll: () => new Promise<void>(() => {}),
}))

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(), resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(), loadPrdById: vi.fn(),
}))
const runAskGeneration = vi.fn().mockResolvedValue({
  answer: "canned", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
})
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: (...a: unknown[]) => runAskGeneration(...a),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
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
  useWorkspace: () => ({
    loading: false, profile: null,
    // No feature_flags: planner dispatch is unconditional — there is no flag
    // left for a workspace to carry.
    workspace: { feature_flags: undefined }, refresh: async () => {},
  }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: new Map(), loading: false, error: false, refetch: vi.fn() }),
}))

import { NavigationProvider, useNavigation } from "../../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

/** The state ContentPanel branches on, surfaced for assertions — the panel is
 *  rendered by AppShell, not by ChatScreen. */
function Probe() {
  const { content } = useContent()
  const { contentPanelTab, closeContentPanel } = useNavigation()
  return (
    <>
    {/* OUTSIDE the probe node: `probeState` parses that node's textContent, so
        anything else rendered inside it lands in the JSON and breaks the parse.
        The panel's own close button lives in ContentPanel, which AppShell
        renders and these tests do not — so the strip's reopen affordance (only
        shown while the panel is CLOSED) needs a way to get there. */}
    <button type="button" data-testid="probe-close-panel" onClick={closeContentPanel}>close</button>
    <div data-testid="probe" data-panel={contentPanelTab ?? ""}>
      {JSON.stringify({
        generating: !!content.ticketSetGenerating,
        standalone: !!content.ticketSetStandalone,
        set: content.ticketSet
          ? {
              id: content.ticketSet.id,
              status: content.ticketSet.status,
              stories: content.ticketSet.stories.length,
            }
          : null,
      })}
    </div>
    </>
  )
}

function probeState() {
  return JSON.parse(screen.getByTestId("probe").textContent || "{}") as {
    generating: boolean; standalone: boolean
    set: { id: number; status: string; stories: number } | null
  }
}
function panelTab() {
  return screen.getByTestId("probe").getAttribute("data-panel")
}

function renderChat() {
  return render(
    <NavigationProvider>
      <ContentProvider>
        <Probe />
        <ChatScreen />
      </ContentProvider>
    </NavigationProvider>,
  )
}

async function typeAndSend(text: string) {
  const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
  expect(textarea).toBeTruthy()
  await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
  await act(async () => { fireEvent.click(sendBtn) })
}

/** A resumed chat tab: a real conversation, one replied turn, and no PRD. */
function seedThreadTab() {
  sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
    {
      id: "tab-thread",
      title: "Webhook failures",
      dbConvId: 42,
      briefMeta: null,
      prdId: null,
      thread: [{
        id: "t1", query: "why are webhooks failing?",
        reply: {
          answer: "Retries stop after the first 5xx.",
          sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
        },
      }],
    },
  ]))
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-thread")
}

/** A promise the test resolves by hand, so a run can be held in flight. */
function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
  resolveIntent.mockResolvedValue({ intent: "generate_tickets", task: "webhook retry failures" })
  chatSummary.mockResolvedValue({ summary: "Six tickets covering retry, backoff and audit." })
  byConversation.mockResolvedValue({ ticket_sets: [] })
  getSet.mockResolvedValue({
    id: 7, title: "Webhook retries", status: "ready", stories: [STORY_A, STORY_B],
    ticket_count: 2, conversation_id: 42, source_text: "break this into tickets",
  })
  runTicketSetGeneration.mockResolvedValue({
    ok: true,
    set: {
      id: 7, title: "Webhook retries", stories: [STORY_A, STORY_B],
      conversationId: 42, status: "ready",
    },
  })
})
afterEach(() => { cleanup(); localStorage.clear(); sessionStorage.clear() })

describe("ChatScreen — asking a PRD-less chat for tickets", () => {
  it("opens the panel on tickets and starts exactly one run", async () => {
    seedThreadTab()
    await act(async () => { renderChat() })
    await typeAndSend("break this into tickets")

    await waitFor(() => expect(runTicketSetGeneration).toHaveBeenCalledTimes(1))
    // The insight the envelope composed, stamped with the thread it was born in.
    expect(runTicketSetGeneration.mock.calls[0][0]).toBe("webhook retry failures")
    expect(runTicketSetGeneration.mock.calls[0][1]).toBe(42)
    await waitFor(() => expect(panelTab()).toBe("tickets"))
  })

  it("acknowledges the request with a pointer, not a wall of ticket markdown", async () => {
    seedThreadTab()
    await act(async () => { renderChat() })
    await typeAndSend("break this into tickets")

    await waitFor(() =>
      expect(document.body.textContent).toContain("Writing tickets for that"))
    // The pointer line — also the marker that keeps this turn out of a later
    // PRD's grounding transcript (TICKET_SET_ANSWER_RE).
    expect(document.body.textContent).toContain(
      "Use the View Tickets button in this chat to reopen them anytime.",
    )
    // The ticket BODIES never reach the bubble…
    expect(document.body.textContent).not.toContain("Retry the webhook with backoff")
    // …because the request never reached the ask agent (which answers the
    // user-stories skill in raw markdown — the behaviour this replaces).
    expect(runAskGeneration).not.toHaveBeenCalled()
    // The user's own message is still the only user bubble on the thread.
    const bubbles = Array.from(document.querySelectorAll(".bc-user-bubble")).map((b) => b.textContent)
    expect(bubbles).toContain("break this into tickets")
  })

  it("a second ask while the first run is in flight does NOT start a second run", async () => {
    const held = deferred<unknown>()
    runTicketSetGeneration.mockReturnValueOnce(held.promise)
    seedThreadTab()
    await act(async () => { renderChat() })

    await typeAndSend("break this into tickets")
    await waitFor(() => expect(runTicketSetGeneration).toHaveBeenCalledTimes(1))
    // The footer says a run owns the button, and it can't be clicked again.
    await waitFor(() => expect(screen.getByTestId("chat-ticket-set-cta")).toBeTruthy())
    expect(screen.getByTestId("chat-ticket-set-cta").textContent).toContain("Writing tickets")
    expect((screen.getByTestId("chat-ticket-set-cta") as HTMLButtonElement).disabled).toBe(true)

    // A DIFFERENT phrasing of the same ask — which the backend would NOT dedupe.
    resolveIntent.mockResolvedValue({ intent: "generate_tickets", task: "webhooks, as work items" })
    await typeAndSend("actually turn that into tickets please")

    expect(runTicketSetGeneration).toHaveBeenCalledTimes(1)
    // Nothing was lost: the refused message is handed back to the composer.
    await waitFor(() =>
      expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value)
        .toBe("actually turn that into tickets please"))

    await act(async () => {
      held.resolve({
        ok: true,
        set: { id: 7, title: "Webhook retries", stories: [STORY_A], conversationId: 42, status: "ready" },
      })
    })
    await waitFor(() => expect(screen.getByTestId("chat-ticket-set-cta").textContent).toContain("View Tickets"))
  })

  it("posts the artifact summary once the set lands", async () => {
    seedThreadTab()
    await act(async () => { renderChat() })
    await typeAndSend("break this into tickets")

    await waitFor(() => expect(chatSummary).toHaveBeenCalledWith("ticket_set", 7))
    await waitFor(() =>
      expect(document.body.textContent).toContain("Six tickets covering retry, backoff and audit."))
  })

  it("a planner failure degrades to an ANSWER, never to a guessed artifact", async () => {
    // This slot used to lock the legacy regex ladder behind the
    // `chat_intent_envelope` kill switch. Both are gone: the planner is the
    // only dispatcher, and when its call fails the message falls through to
    // the grounded ask path. That degradation is chosen, not accidental — the
    // worst case is a genuine tickets request being answered as a question and
    // asked again; a client-side guess restores the accidental multi-minute
    // generation the ladder's removal exists to prevent.
    resolveIntent.mockRejectedValue(new Error("planner down"))
    seedThreadTab()
    await act(async () => { renderChat() })
    await typeAndSend("break this into tickets")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))
    expect(resolveIntent).toHaveBeenCalledTimes(1)
    // No artifact ran, no panel yanked open over an answer.
    expect(runTicketSetGeneration).not.toHaveBeenCalled()
    expect(panelTab()).not.toBe("tickets")
  })
})

describe("ChatScreen — reopening a thread that produced tickets", () => {
  it("lands a still-generating set on the live progress, not a blank tab", async () => {
    byConversation.mockResolvedValue({
      ticket_sets: [{ id: 9, title: "", status: "generating", created_at: null }],
    })
    getSet.mockResolvedValue({
      id: 9, title: "", status: "generating", stories: [],
      ticket_count: 0, conversation_id: 42, source_text: "break this into tickets",
    })
    seedThreadTab()
    await act(async () => { renderChat() })

    await waitFor(() => expect(byConversation).toHaveBeenCalledWith(42))
    await waitFor(() => expect(panelTab()).toBe("tickets"))
    // generating + nothing written yet → ContentPanel's standalone generating
    // pane, driven by a row read that is following the run to a terminal state.
    await waitFor(() => expect(probeState().generating).toBe(true))
    expect(probeState().set).toMatchObject({ id: 9, status: "generating", stories: 0 })
    expect(probeState().standalone).toBe(false)
  })

  it("lands a finished set on its tickets", async () => {
    byConversation.mockResolvedValue({
      ticket_sets: [{ id: 7, title: "Webhook retries", status: "ready", created_at: null }],
    })
    seedThreadTab()
    await act(async () => { renderChat() })

    await waitFor(() => expect(panelTab()).toBe("tickets"))
    // Settled — never a stale "Writing tickets…" over a run that finished.
    await waitFor(() => expect(probeState().set).toMatchObject({ id: 7, status: "ready", stories: 2 }))
    expect(probeState().generating).toBe(false)
    // And the chat offers the way back in.
    await waitFor(() => expect(screen.getByTestId("chat-ticket-set-cta").textContent).toContain("View Tickets"))
  })

  it("takes the NEWEST set when a thread has several", async () => {
    // A second ask creates a second set by design; the backend returns them
    // newest-first and the panel must land on the one just written.
    byConversation.mockResolvedValue({
      ticket_sets: [
        { id: 12, title: "Audit log", status: "ready", created_at: null },
        { id: 7, title: "Webhook retries", status: "ready", created_at: null },
      ],
    })
    seedThreadTab()
    await act(async () => { renderChat() })

    await waitFor(() => expect(getSet).toHaveBeenCalledWith(12))
    expect(getSet).not.toHaveBeenCalledWith(7)
  })

  it("does not reopen a chat on a failed run, but still offers the retry", async () => {
    byConversation.mockResolvedValue({
      ticket_sets: [{ id: 3, title: "", status: "failed", created_at: null }],
    })
    seedThreadTab()
    await act(async () => { renderChat() })

    await waitFor(() => expect(byConversation).toHaveBeenCalledWith(42))
    await waitFor(() => expect(screen.getByTestId("chat-ticket-set-cta").textContent).toContain("Retry tickets"))
    // Reopening a chat should not greet you with an error state you already saw.
    expect(panelTab()).toBe("")
    expect(getSet).not.toHaveBeenCalled()
  })

  it("leaves a thread with no tickets alone", async () => {
    seedThreadTab()
    await act(async () => { renderChat() })

    await waitFor(() => expect(byConversation).toHaveBeenCalledWith(42))
    expect(panelTab()).toBe("")
    expect(screen.queryByTestId("chat-ticket-set-cta")).toBeNull()
  })
})

describe("ChatScreen — the strip's way back to a closed ticket panel", () => {
  it("offers the reopen button once the panel is closed, and reopens on tickets", async () => {
    // The in-chat CTA sits on the turn that produced the set, so it scrolls away
    // as the thread grows. The strip button does not move — it is the affordance
    // a PRD and a report already have, and a ticket set had none.
    byConversation.mockResolvedValue({
      ticket_sets: [{ id: 7, title: "Webhook retries", status: "ready", created_at: null }],
    })
    seedThreadTab()
    await act(async () => { renderChat() })

    await waitFor(() => expect(panelTab()).toBe("tickets"))
    // Hidden while the panel is open — it has its own close.
    expect(screen.queryByTestId("chat-reopen-artifact")).toBeNull()

    await act(async () => { fireEvent.click(screen.getByTestId("probe-close-panel")) })
    const reopen = await waitFor(() => screen.getByTestId("chat-reopen-artifact"))
    expect(reopen.getAttribute("aria-label")).toBe("View Tickets")

    getSet.mockClear()
    await act(async () => { fireEvent.click(reopen) })
    await waitFor(() => expect(panelTab()).toBe("tickets"))
    // Re-read rather than trusting shared content — the panel is global.
    await waitFor(() => expect(getSet).toHaveBeenCalledWith(7))
  })

  it("does not offer it for a FAILED set — the footer's retry owns that", async () => {
    byConversation.mockResolvedValue({
      ticket_sets: [{ id: 3, title: "", status: "failed", created_at: null }],
    })
    seedThreadTab()
    await act(async () => { renderChat() })

    await waitFor(() => expect(screen.getByTestId("chat-ticket-set-cta").textContent).toContain("Retry tickets"))
    expect(panelTab()).toBe("")
    // The strip reopens things that exist; it is not a second retry button.
    expect(screen.queryByTestId("chat-reopen-artifact")).toBeNull()
  })
})

describe("ChatScreen — a set belongs to ONE thread", () => {
  it("drops another thread's set when switching tabs", async () => {
    // `content.ticketSet` is global, but it is also what makes the Tickets tab
    // APPEAR. Carried across a switch it lands on the wrong thread — and on a
    // thread with a PRD it displaces that PRD's own tickets. `handleOpenPrd`
    // clears it, but only when opening an EXISTING PRD; a new chat that
    // GENERATES one never goes through there, which is how a stale set showed
    // up on a freshly generated PRD and then "fixed itself" moments later.
    sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
      { id: "tab-a", title: "Webhook failures", dbConvId: 42, briefMeta: null, prdId: null, thread: [] },
      { id: "tab-b", title: "Something else", dbConvId: 43, briefMeta: null, prdId: null, thread: [] },
    ]))
    sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-a")
    byConversation.mockImplementation(async (cid: number) =>
      cid === 42
        ? { ticket_sets: [{ id: 7, title: "Webhook retries", status: "ready", created_at: null }] }
        : { ticket_sets: [] })

    await act(async () => { renderChat() })
    await waitFor(() => expect(probeState().set).toMatchObject({ id: 7 }))

    const tabB = Array.from(document.querySelectorAll(".chat-tab"))
      .find((el) => (el.textContent || "").includes("Something else")) as HTMLElement
    expect(tabB).toBeTruthy()
    await act(async () => { fireEvent.click(tabB) })

    // Thread B produced no tickets, so it must show none.
    await waitFor(() => expect(probeState().set).toBeNull())
    expect(probeState().generating).toBe(false)
  })

  it("keeps a set when switching BACK to the thread that owns it", async () => {
    // The mirror case: clearing indiscriminately would flicker the panel every
    // time the owning tab regained focus.
    sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([
      { id: "tab-a", title: "Webhook failures", dbConvId: 42, briefMeta: null, prdId: null, thread: [] },
      { id: "tab-b", title: "Something else", dbConvId: 43, briefMeta: null, prdId: null, thread: [] },
    ]))
    sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-a")
    byConversation.mockImplementation(async (cid: number) =>
      cid === 42
        ? { ticket_sets: [{ id: 7, title: "Webhook retries", status: "ready", created_at: null }] }
        : { ticket_sets: [] })

    await act(async () => { renderChat() })
    await waitFor(() => expect(probeState().set).toMatchObject({ id: 7 }))

    const tab = (name: string) => Array.from(document.querySelectorAll(".chat-tab"))
      .find((el) => (el.textContent || "").includes(name)) as HTMLElement
    await act(async () => { fireEvent.click(tab("Something else")) })
    await waitFor(() => expect(probeState().set).toBeNull())

    await act(async () => { fireEvent.click(tab("Webhook failures")) })
    await waitFor(() => expect(probeState().set).toMatchObject({ id: 7, status: "ready" }))
  })
})
