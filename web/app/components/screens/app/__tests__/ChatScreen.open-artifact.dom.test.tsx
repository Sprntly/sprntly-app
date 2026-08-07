// @vitest-environment jsdom
//
// ChatScreen — "open the PRD for X" (envelope intent `open_artifact`).
//
// The reported gap was not comprehension. Live on staging, "open the PRD for
// compliance reporting" was already understood: it did NOT generate a new PRD,
// it found both candidates, and it asked which one was meant. What was missing
// was the ACTION — the suggestion chip it offered ("Open PRD #2216") merely
// re-sent its own label as a chat message, and the assistant answered by
// reconstructing the document as chat text. The panel never opened.
//
// So this suite is about three things, in order of how badly they used to fail:
//   1. a resolved open LOADS the document into the chat's right-hand panel
//      (loadPrdById), and generates nothing;
//   2. a disambiguation chip OPENS its artifact and posts NO message — no
//      second envelope round trip, no ask, no new turn;
//   3. opening a PRD already on screen reuses that tab BY PRD ID, never
//      spawning a duplicate (the #1039 failure, from a new entry point).
// Plus the negative half of the contract: 0 matches opens nothing.
import * as React from "react"
import { act, cleanup, fireEvent, screen, waitFor, within, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = () => {}
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const { generateFromTask, classifyCommand, clarifyTask, resolveIntent, addTurn } =
  vi.hoisted(() => ({
    generateFromTask: vi.fn().mockResolvedValue({
      prd_id: 501, title: "Generated", status: "generating", variant: "v3",
    }),
    classifyCommand: vi.fn().mockResolvedValue({
      is_prd_command: false, task: null, confidence: 0.9,
    }),
    clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
    resolveIntent: vi.fn(),
    addTurn: vi.fn().mockResolvedValue({}),
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
    briefApi: { current: vi.fn().mockResolvedValue({ id: 7, insights: [{ title: "x" }] }) },
    prdApi: { generateFromTask, classifyCommand, clarifyTask },
    chatIntentApi: { resolve: resolveIntent },
    chatSuggestionsApi: { next: vi.fn().mockResolvedValue({ suggestions: [] }) },
    ticketSetsApi: {
      byConversation: vi.fn().mockResolvedValue({ ticket_sets: [] }),
      get: vi.fn(),
    },
    artifactsApi: { chatSummary: vi.fn().mockResolvedValue({ summary: null }) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: (...a: unknown[]) => addTurn(...a),
      byPrd: vi.fn().mockResolvedValue({ conversation: null }),
    },
  }
})

const loadPrdById = vi.fn()
const runPrdGeneration = vi.fn()
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...a: unknown[]) => runPrdGeneration(...a),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: (...a: unknown[]) => loadPrdById(...a),
}))

vi.mock("../../../../lib/runTicketSetGeneration", () => ({
  runTicketSetGeneration: vi.fn(),
  loadTicketSet: vi.fn().mockResolvedValue({ ok: true }),
}))

const runAskGeneration = vi.fn().mockResolvedValue({
  answer: "canned", sources: [], follow_ups: [], key_points: [], citations: [],
  confidence: 1, unanswered: "",
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
  useSearchParams: () => new URLSearchParams("new=1"),
}))
vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    loading: false, profile: null,
    workspace: { feature_flags: { chat_intent_envelope: true } },
    refresh: async () => {},
  }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

const { protoMap } = vi.hoisted(() => ({ protoMap: new Map<number, unknown>() }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: protoMap, loading: false, error: false, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

function renderChat() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

async function typeAndSend(text: string) {
  const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
  expect(textarea).toBeTruthy()
  await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
  await act(async () => { fireEvent.click(sendBtn) })
}

const tabBar = () => within(screen.getByTestId("chat-tab-bar"))

function candidate(id: number, title: string, week_label: string | null = null) {
  return {
    type: "prd" as const, id, title, status: "ready",
    prd_id: id, brief_id: 7, insight_index: 0, week_label,
  }
}

/** The envelope for an open request, with the backend's lookup attached. */
function openEnvelope(open: Record<string, unknown>) {
  return {
    intent: "open_artifact", confidence: 0.95, task: null, instruction: null,
    artifact_type: "prd", artifact_query: "compliance reporting",
    reason: "opening verb", source: "llm", prd_id: null, prd_title: null,
    open: { artifact_type: "prd", query: "compliance reporting", ...open },
  }
}

const PRD_2216 = { prd_id: 2216, title: "Compliance Reporting", metaLine: "", sections: [] }

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  protoMap.clear()
  runAskGeneration.mockClear()
  runPrdGeneration.mockClear()
  generateFromTask.mockClear()
  classifyCommand.mockClear()
  addTurn.mockClear()
  loadPrdById.mockReset()
  loadPrdById.mockResolvedValue({ ok: true, prd: PRD_2216 })
  resolveIntent.mockReset()
  resolveIntent.mockResolvedValue({
    intent: "answer", confidence: 0.9, task: null, instruction: null,
    artifact_type: null, artifact_query: null, reason: "q", source: "llm",
    prd_id: null, prd_title: null,
  })
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — open_artifact opens the panel", () => {
  it("a single match LOADS that PRD into the panel and generates nothing", async () => {
    resolveIntent.mockResolvedValue(openEnvelope({
      status: "resolved",
      artifact: candidate(2216, "Compliance Reporting"),
      candidates: [candidate(2216, "Compliance Reporting")],
    }))
    renderChat()
    await typeAndSend("open the PRD for compliance reporting")

    // The existing same-tab PRD panel path — by id, the document we were told.
    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(2216))
    // The whole point: an OPEN never becomes a generate.
    expect(generateFromTask).not.toHaveBeenCalled()
    expect(runPrdGeneration).not.toHaveBeenCalled()
    // …and it is not answered as chat text either (the old behaviour).
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("the open lands in the CHAT the user is typing in, not a tab of its own", async () => {
    resolveIntent
      .mockResolvedValueOnce({
        intent: "answer", confidence: 0.9, task: null, instruction: null,
        artifact_type: null, artifact_query: null, reason: "q", source: "llm",
        prd_id: null, prd_title: null,
      })
      .mockResolvedValue(openEnvelope({
        status: "resolved",
        artifact: candidate(2216, "Compliance Reporting"),
        candidates: [candidate(2216, "Compliance Reporting")],
      }))
    renderChat()
    // A real conversation first, so there IS a chat to open beside.
    await typeAndSend("what did customers say about compliance?")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    const before = tabBar().getAllByTitle("Close tab").length

    await typeAndSend("open the PRD for compliance reporting")
    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(2216))

    // Same tab — no second chat spawned for the document.
    expect(tabBar().getAllByTitle("Close tab")).toHaveLength(before)
  })

  it("asking from ANOTHER chat focuses the tab already holding that PRD", async () => {
    // The duplicate-tab trap, from this new entry point. The naive wiring
    // ("open it in whatever chat I'm typing in") would load PRD 2216 into a
    // SECOND tab; reuse is keyed on the prd id, so it focuses the first —
    // the same rule #1039 established for `?prd=` deep links, which title
    // matching would silently break the moment two documents share a name.
    resolveIntent
      .mockResolvedValueOnce({
        intent: "answer", confidence: 0.9, task: null, instruction: null,
        artifact_type: null, artifact_query: null, reason: "q", source: "llm",
        prd_id: null, prd_title: null,
      })
      .mockResolvedValue(openEnvelope({
        status: "resolved",
        artifact: candidate(2216, "Compliance Reporting"),
        candidates: [candidate(2216, "Compliance Reporting")],
      }))
    renderChat()
    // Chat one, with a title of its own, then the PRD opened into it.
    await typeAndSend("how is compliance tracked today?")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    await typeAndSend("open the PRD for compliance reporting")
    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(2216))

    // A brand-new chat, then the SAME open request from inside it.
    await act(async () => { fireEvent.click(tabBar().getByTitle("New chat")) })
    expect(tabBar().getAllByTitle("Close tab")).toHaveLength(2)
    await typeAndSend("open the PRD for compliance reporting")

    // Focus moved back to the chat that holds the document…
    await waitFor(() =>
      expect(
        document.querySelector('[data-tab-active="true"]')?.textContent,
      ).toContain("how is compliance tracked today?"),
    )
    // …the second chat never became a second home for the same PRD (one load,
    // and no third tab).
    expect(loadPrdById).toHaveBeenCalledTimes(1)
    expect(tabBar().getAllByTitle("Close tab")).toHaveLength(2)
  })

  it("no match opens NOTHING and says so — it never offers to generate one instead", async () => {
    resolveIntent.mockResolvedValue(openEnvelope({
      status: "not_found", artifact: null, candidates: [],
    }))
    renderChat()
    await typeAndSend("open the PRD for compliance reporting")

    await waitFor(() =>
      expect(screen.getByText(/couldn't find a PRD for "compliance reporting"/i)).toBeTruthy(),
    )
    expect(loadPrdById).not.toHaveBeenCalled()
    expect(generateFromTask).not.toHaveBeenCalled()
    expect(runPrdGeneration).not.toHaveBeenCalled()
    expect(screen.queryByTestId("open-artifact-chips")).toBeNull()
  })
})

describe("ChatScreen — disambiguation chips are actions, not messages", () => {
  const AMBIGUOUS = openEnvelope({
    status: "ambiguous",
    artifact: null,
    candidates: [
      candidate(2216, "Compliance Reporting", "Week of Aug 1"),
      candidate(2214, "Compliance Reporting", "Week of Jul 1"),
    ],
  })

  it("two matches ask, opening nothing, and render one chip per candidate", async () => {
    resolveIntent.mockResolvedValue(AMBIGUOUS)
    renderChat()
    await typeAndSend("open the PRD for compliance reporting")

    await waitFor(() => expect(screen.getByTestId("open-artifact-chips")).toBeTruthy())
    expect(screen.getAllByTestId("open-artifact-chip")).toHaveLength(2)
    expect(screen.getByText(/more than one PRD matching/i)).toBeTruthy()
    // Asking is not opening.
    expect(loadPrdById).not.toHaveBeenCalled()
  })

  it("a chip click OPENS its artifact and posts NO chat message", async () => {
    resolveIntent.mockResolvedValue(AMBIGUOUS)
    renderChat()
    await typeAndSend("open the PRD for compliance reporting")
    await waitFor(() => expect(screen.getByTestId("open-artifact-chips")).toBeTruthy())

    const envelopeCallsBefore = resolveIntent.mock.calls.length
    const turnsBefore = addTurn.mock.calls.length
    const chip = screen
      .getAllByTestId("open-artifact-chip")
      .find((el) => el.getAttribute("data-artifact-id") === "2214")!
    await act(async () => { fireEvent.click(chip) })

    // It opened the one that was clicked — the older document, so a "newest
    // wins" shortcut would pass the wrong id here.
    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(2214))
    // …and it was an ACTION: no message left the composer, so no second
    // intent round trip and no new persisted turn. This is exactly what the
    // old suggestion chip did wrong.
    expect(resolveIntent.mock.calls).toHaveLength(envelopeCallsBefore)
    expect(runAskGeneration).not.toHaveBeenCalled()
    expect(addTurn.mock.calls).toHaveLength(turnsBefore)
    expect(generateFromTask).not.toHaveBeenCalled()
  })
})
