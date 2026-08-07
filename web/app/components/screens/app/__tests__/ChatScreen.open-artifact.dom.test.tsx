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
    attachmentsApi: { upload: vi.fn().mockResolvedValue(null) },
    storiesApi: { generate: vi.fn().mockResolvedValue({}) },
    evidenceApi: { get: vi.fn().mockResolvedValue(null) },
    reportsApi: { list: vi.fn().mockResolvedValue({ reports: [] }) },
    // The persistence layer reaches this module through a DYNAMIC import
    // (`chatPersistence`'s getApi). A partial mock here does not merely leave a
    // method undefined — the real module gets pulled in alongside it and the
    // conversation `create` goes out over the network, so the tab never gets a
    // conversation id and every turn after it is silently dropped. Mirror the
    // sibling command suites' full shape.
    conversationsApi: {
      list: vi.fn().mockResolvedValue({ conversations: [] }),
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: (...a: unknown[]) => addTurn(...a),
      update: vi.fn().mockResolvedValue({}),
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
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
import { ContentProvider, useContent } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

/** Surfaces the shared panel state the DOM cannot show. `prdMeta` is what the
 *  panel's Evidence tab loads from (ContentPanel fetches by briefId +
 *  insightIndex), so "did the open carry the finding through?" is only
 *  answerable here. */
function ContentProbe() {
  const { content } = useContent()
  return React.createElement(
    "div",
    { "data-testid": "content-probe", "data-prd-meta": JSON.stringify(content.prdMeta ?? null) },
  )
}

const prdMetaFromProbe = (): { briefId: number; insightIndex: number } | null =>
  JSON.parse(screen.getByTestId("content-probe").getAttribute("data-prd-meta") || "null")

function renderChat() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(
        ContentProvider,
        null,
        React.createElement(ChatScreen),
        React.createElement(ContentProbe),
      ),
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

function candidate(
  id: number,
  title: string,
  week_label: string | null = null,
  extra: Partial<{ brief_anchored: boolean; brief_id: number; insight_index: number }> = {},
) {
  return {
    type: "prd" as const, id, title, status: "ready",
    prd_id: id, brief_id: 7, insight_index: 0,
    // Default false: the seeded documents here are chat PRDs, whose
    // insight_index 0 is a storage sentinel rather than a real finding.
    brief_anchored: false,
    week_label,
    ...extra,
  }
}

function evidenceCandidate(id: number, title: string, insightIndex: number) {
  return {
    type: "evidence" as const, id, title, status: "ready",
    prd_id: null, brief_id: 7, insight_index: insightIndex,
    brief_anchored: true, week_label: null,
  }
}

/** The envelope for an open request, with the backend's lookup attached. */
function openEnvelope(open: Record<string, unknown>, artifactType = "prd") {
  return {
    intent: "open_artifact", confidence: 0.95, task: null, instruction: null,
    artifact_type: artifactType, artifact_query: "compliance reporting",
    reason: "opening verb", source: "llm", prd_id: null, prd_title: null,
    open: { artifact_type: artifactType, query: "compliance reporting", ...open },
  }
}

/** The `resolved` envelope for PRD 2216, the document most tests here open. */
const resolved2216 = (extra = {}) =>
  openEnvelope({
    status: "resolved",
    artifact: candidate(2216, "Compliance Reporting", null, extra),
    candidates: [candidate(2216, "Compliance Reporting", null, extra)],
  })

/** An `answer` envelope, for the ordinary send that sets a conversation up. */
const ANSWER_ENVELOPE = {
  intent: "answer", confidence: 0.9, task: null, instruction: null,
  artifact_type: null, artifact_query: null, reason: "q", source: "llm",
  prd_id: null, prd_title: null,
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

  it("carries the finding through, so the panel's Evidence tab has something to load", async () => {
    // A brief-anchored PRD: `insight_index` names a real finding, and the
    // panel's Evidence tab resolves exactly that pair. Passing null meta here
    // is what made the tab dead from chat while the SAME document opened from
    // Artifacts worked.
    resolveIntent.mockResolvedValue(
      resolved2216({ brief_anchored: true, brief_id: 7, insight_index: 3 }),
    )
    renderChat()
    await typeAndSend("open the PRD for compliance reporting")

    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(2216))
    await waitFor(() =>
      expect(prdMetaFromProbe()).toEqual({ briefId: 7, insightIndex: 3 }),
    )
  })

  it("does NOT carry a sentinel insight_index through for a chat PRD", async () => {
    // A chat / ideation / uploaded PRD is stored at insight_index 0 as a pure
    // sentinel. Passing it would make the Evidence tab load the brief's FIRST
    // finding underneath a document that has nothing to do with it — worse
    // than the empty tab, because it looks like an answer.
    resolveIntent.mockResolvedValue(
      resolved2216({ brief_anchored: false, brief_id: 7, insight_index: 0 }),
    )
    renderChat()
    await typeAndSend("open the PRD for compliance reporting")

    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(2216))
    expect(prdMetaFromProbe()).toBeNull()
  })

  it("names an artifact kind this panel cannot show instead of opening a PRD", async () => {
    // "Open the dark mode prototype" with a dark mode PRD sitting right there:
    // substituting it would hand over the wrong document silently.
    resolveIntent.mockResolvedValue(
      openEnvelope(
        { status: "unsupported_type", artifact: null, candidates: [] },
        "prototype",
      ),
    )
    renderChat()
    await typeAndSend("open the dark mode prototype")

    await waitFor(() =>
      expect(screen.getByText(/A prototype doesn't open in this panel/i)).toBeTruthy(),
    )
    expect(loadPrdById).not.toHaveBeenCalled()
    expect(generateFromTask).not.toHaveBeenCalled()
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

describe("ChatScreen — the open acknowledgment is not a promise", () => {
  it("withholds 'Opening that PRD' until the document has actually loaded", async () => {
    let settle: (v: unknown) => void = () => {}
    loadPrdById.mockReturnValue(new Promise((res) => { settle = res }))
    resolveIntent.mockResolvedValue(resolved2216())
    renderChat()
    await typeAndSend("open the PRD for compliance reporting")

    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(2216))
    // In flight: the claim has not come true yet, so it is not on screen —
    // and, just as importantly, not in the persisted conversation.
    expect(screen.queryByText(/Opening that PRD in the panel on the right/i)).toBeNull()

    await act(async () => { settle({ ok: true, prd: PRD_2216 }) })
    await waitFor(() =>
      expect(screen.getByText(/Opening that PRD in the panel on the right/i)).toBeTruthy(),
    )
  })

  it("PERSISTS the ack when the document was already cached, not just renders it", async () => {
    // The instant path: a repeat open finds the PRD cached on the tab and
    // returns before any load. Two bugs have lived here, in order:
    //
    //   1. no ack at all — the turn sat under a thinking indicator forever;
    //   2. an ack rendered but never SAVED — `settleCommandAck` writes the
    //      visible thread unconditionally and only persists when it finds a
    //      registered deferred entry, and the settle ran before the
    //      registration. On screen it looked perfect; reopening the chat from
    //      the rail showed "No response was generated for this message."
    //
    // So the visible-thread assertion cannot stand alone: it passed straight
    // through (2). Every ack must reach `conversationsApi.addTurn` as an
    // assistant turn, and the user→assistant pairing must stay in order —
    // `hydratePrdThread`'s rebuild depends on it.
    resolveIntent.mockResolvedValue(resolved2216())
    renderChat()
    // #1 does the real load and establishes the tab's conversation; #2 is the
    // first CACHED open. Assertions begin after both, from a cleared spy — the
    // opening turn races its own conversation `create` in this harness, and
    // that race is not what is under test. Running a cached open BEFORE the
    // measured one also sets the scenario-B trap: with the bug, #2 stranded a
    // registration that #3 would then consume and write against #2's turn id.
    await typeAndSend("open the PRD for compliance reporting")
    await waitFor(() => expect(loadPrdById).toHaveBeenCalledTimes(1))
    await typeAndSend("open the PRD for compliance reporting")
    await waitFor(() => expect(addTurn).toHaveBeenCalled())
    addTurn.mockClear()

    // #3 — cached. openPrdInTab returns before ever reaching its async block.
    await typeAndSend("open the PRD for compliance reporting")

    await waitFor(() =>
      expect(
        screen.getAllByText(/Opening that PRD in the panel on the right/i),
      ).toHaveLength(3),
    )
    // No re-fetch for a document already in hand.
    expect(loadPrdById).toHaveBeenCalledTimes(1)

    // …and THIS exchange reached the conversation, user before assistant. That
    // is the half the visible-thread assertion cannot see, and the half that
    // was broken: the reply rendered perfectly and was never saved.
    await waitFor(() => {
      expect(addTurn.mock.calls.map((c) => c[1])).toEqual(["user", "assistant"])
    })
    expect(String(addTurn.mock.calls[1][2])).toMatch(
      /Opening that PRD in the panel on the right/i,
    )
  })

  it("says what really happened when the document refuses to load", async () => {
    // A PRD mid-regeneration: resolvable (it has an id) but not showable.
    // The old flow left "Opening that PRD…" in the thread beside a panel that
    // never opened.
    loadPrdById.mockResolvedValue({ ok: false, message: "PRD isn't ready yet" })
    resolveIntent.mockResolvedValue(resolved2216())
    renderChat()
    await typeAndSend("open the PRD for compliance reporting")

    await waitFor(() =>
      expect(screen.getByText(/I couldn't open that PRD/i)).toBeTruthy(),
    )
    expect(screen.queryByText(/Opening that PRD in the panel on the right/i)).toBeNull()
    // "start" would describe a generation the user never asked for.
    expect(screen.queryByText(/I couldn't start that PRD/i)).toBeNull()
  })
})

describe("ChatScreen — an evidence open respects the tab binding", () => {
  const EVIDENCE_ENVELOPE = openEnvelope(
    {
      status: "resolved",
      artifact: evidenceCandidate(31, "Bulk Export Demand", 3),
      candidates: [evidenceCandidate(31, "Bulk Export Demand", 3)],
    },
    "evidence",
  )

  it("does not hijack a tab that is already holding a PRD", async () => {
    // The reported hazard: openPrdInTab's evidence branch writes
    // `evidenceOnly` + insight B's detail onto whatever tab it is given. Given
    // the tab holding PRD 2216, the panel would render B's evidence beside A's
    // document, and the tab would be flagged evidence-only while still
    // carrying a prd id. `reusableActiveTab` declines exactly that tab.
    resolveIntent
      .mockResolvedValueOnce(ANSWER_ENVELOPE)
      .mockResolvedValueOnce(resolved2216())
      .mockResolvedValue(EVIDENCE_ENVELOPE)
    renderChat()
    await typeAndSend("how is compliance tracked today?")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    await typeAndSend("open the PRD for compliance reporting")
    await waitFor(() => expect(loadPrdById).toHaveBeenCalledWith(2216))
    const tabsBefore = tabBar().getAllByTitle("Close tab").length

    await typeAndSend("pull up the evidence for bulk export demand")

    // The evidence got a chat of its own rather than being written over the
    // PRD's tab.
    await waitFor(() =>
      expect(tabBar().getAllByTitle("Close tab")).toHaveLength(tabsBefore + 1),
    )
    // And nothing re-loaded or regenerated the PRD on the way.
    expect(loadPrdById).toHaveBeenCalledTimes(1)
    expect(generateFromTask).not.toHaveBeenCalled()
  })

  it("opens into the current chat when that chat is not bound to anything", async () => {
    // The other half: the guard must not push every evidence open into a new
    // tab. A plain conversation is a legitimate host.
    resolveIntent
      .mockResolvedValueOnce(ANSWER_ENVELOPE)
      .mockResolvedValue(EVIDENCE_ENVELOPE)
    renderChat()
    await typeAndSend("what did customers say about exports?")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    const tabsBefore = tabBar().getAllByTitle("Close tab").length

    await typeAndSend("pull up the evidence for bulk export demand")

    await waitFor(() =>
      expect(screen.getByText(/Opening that evidence in the panel on the right/i)).toBeTruthy(),
    )
    expect(tabBar().getAllByTitle("Close tab")).toHaveLength(tabsBefore)
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

  it("each chip is announced as a BUTTON, not a list item", async () => {
    // `role="listitem"` on a <button> REPLACES the button role, so a screen
    // reader announces "list item" with no hint that the only control
    // answering the question above it can be pressed.
    resolveIntent.mockResolvedValue(AMBIGUOUS)
    renderChat()
    await typeAndSend("open the PRD for compliance reporting")
    await waitFor(() => expect(screen.getByTestId("open-artifact-chips")).toBeTruthy())

    for (const chip of screen.getAllByTestId("open-artifact-chip")) {
      expect(chip.tagName).toBe("BUTTON")
      expect(chip.getAttribute("role")).toBeNull()
    }
    expect(screen.getAllByRole("button", { name: /Compliance Reporting/ })).toHaveLength(2)
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
