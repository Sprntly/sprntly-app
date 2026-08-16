// @vitest-environment jsdom
//
// ChatScreen — a document written from chat belongs to the chat that asked.
//
// Two defects, found by browsing staging, that compound into one symptom: the
// Document tab opens and stays blank.
//
//   1. `conversation_id` was NULL on every document created from a NEW chat.
//      The id was read synchronously off the tab, and on a tab's first message
//      the conversation row does not exist yet — `pushPendingConversation`
//      fires the create and does not await it. So the MOST COMMON path orphaned
//      the document from its thread, and `useThreadDocumentSync` could never
//      re-attach it.
//   2. That orphaning then blanked the panel. `activeConvId` going null → real
//      id was treated by the thread-reset effect as a SWITCH, which cleared
//      `documentId` — a beat after the document was opened into the panel.
//
// The panel tests that existed set `documentId` directly, so none of them ever
// exercised a conversation coming into existence underneath it. These do.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
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

const NEW_CONV_ID = 412

const { resolveIntent, generateDoc, createConversation, listDocsForConversation } =
  vi.hoisted(() => ({
    resolveIntent: vi.fn(),
    generateDoc: vi.fn().mockResolvedValue({ id: 99, status: "generating" }),
    createConversation: vi.fn().mockResolvedValue({ id: 412 }),
    listDocsForConversation: vi.fn().mockResolvedValue([]),
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
    prdApi: {
      generateFromTask: vi.fn(),
      classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
      clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
      changeTemplate: vi.fn(),
      importDoc: vi.fn(),
    },
    storiesApi: { changeTemplate: vi.fn() },
    chatIntentApi: { resolve: resolveIntent },
    customArtifactsApi: {
      generate: (...a: unknown[]) => generateDoc(...a),
      listForConversation: (...a: unknown[]) => listDocsForConversation(...a),
      get: vi.fn(),
      update: vi.fn(),
    },
    ticketSetsApi: { byConversation: vi.fn().mockResolvedValue({ ticket_sets: [] }), get: vi.fn() },
    reportsApi: { listForConversation: vi.fn().mockResolvedValue([]), get: vi.fn() },
    artifactsApi: { chatSummary: vi.fn().mockResolvedValue({ summary: null }) },
    conversationsApi: {
      create: (...a: unknown[]) => createConversation(...a),
      addTurn: vi.fn().mockResolvedValue({}),
      update: vi.fn().mockResolvedValue({}),
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
      listTurns: vi.fn().mockResolvedValue({ turns: [] }),
    },
  }
})

vi.mock("../../../../lib/runTicketSetGeneration", () => ({
  runTicketSetGeneration: vi.fn(), loadTicketSet: vi.fn().mockResolvedValue({ ok: true }),
}))
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(), resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(), loadPrdById: vi.fn(),
}))
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn().mockResolvedValue({
    answer: "canned", sources: [], follow_ups: [], key_points: [], citations: [],
    confidence: 1, unanswered: "",
  }),
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
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: new Map(), loading: false, error: false, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

/** Probes for the two pieces of content state this suite is about. */
function Harness() {
  const { content } = useContent()
  return (
    <>
      <div data-testid="doc-probe">
        {content.documentId != null ? String(content.documentId) : "none"}
      </div>
      <div data-testid="conv-probe">
        {content.conversationId != null ? String(content.conversationId) : "none"}
      </div>
      <ChatScreen />
    </>
  )
}

function renderChat() {
  return render(
    <NavigationProvider>
      <ContentProvider>
        <Harness />
      </ContentProvider>
    </NavigationProvider>,
  )
}

const docProbe = () => screen.getByTestId("doc-probe").textContent
const convProbe = () => screen.getByTestId("conv-probe").textContent

async function typeAndSend(text: string) {
  const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
  expect(textarea).toBeTruthy()
  await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
  await act(async () => { fireEvent.click(sendBtn) })
}

async function settle() {
  await act(async () => { await new Promise((r) => setTimeout(r, 40)) })
}

function wantsADocument() {
  resolveIntent.mockResolvedValue({
    intent: "create_artifact", confidence: 0.95,
    task: "Q3 reliability, for the leadership team",
    instruction: null, artifact_kind: "leadership update",
    reason: "asked for a document", source: "llm", prd_id: null, prd_title: null,
  })
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
  generateDoc.mockResolvedValue({ id: 99, status: "generating" })
  createConversation.mockResolvedValue({ id: NEW_CONV_ID })
  listDocsForConversation.mockResolvedValue([])
})
afterEach(() => { cleanup(); localStorage.clear() })

describe("a document written from a brand-new chat", () => {
  it("is stored against that chat, not orphaned with a null conversation", async () => {
    // THE DEFECT. The first message of a chat is the most common way anyone
    // asks for a document, and it was the one path that could not attach one.
    wantsADocument()
    await act(async () => { renderChat() })
    await typeAndSend("Draft a leadership update on the upgrade check problems")
    await settle()

    expect(generateDoc).toHaveBeenCalledTimes(1)
    const payload = generateDoc.mock.calls[0][0] as { conversation_id: number | null }
    expect(payload.conversation_id).toBe(NEW_CONV_ID)
  })

  it("reuses the conversation the turn persistence is already creating", async () => {
    // `ensureConversation` shares the in-flight create rather than starting a
    // second one. A document that minted its own conversation would split one
    // thread across two rows — the chat's turns in one, the document in the
    // other.
    wantsADocument()
    await act(async () => { renderChat() })
    await typeAndSend("Draft a leadership update")
    await settle()

    expect(createConversation).toHaveBeenCalledTimes(1)
  })

  it("still writes the document when the conversation cannot be created", async () => {
    // Degrades to an unlinked document — still generated, still in the library
    // — rather than to no document at all.
    wantsADocument()
    createConversation.mockRejectedValue(new Error("offline"))
    await act(async () => { renderChat() })
    await typeAndSend("Draft a leadership update")
    await settle()

    expect(generateDoc).toHaveBeenCalledTimes(1)
    expect((generateDoc.mock.calls[0][0] as { conversation_id: number | null }).conversation_id)
      .toBeNull()
  })

  it("keeps the panel's document when the chat gains its conversation id", async () => {
    // THE SECOND HALF. The conversation comes into existence UNDERNEATH the
    // open panel; the reset effect used to read that as a thread switch and
    // wipe `documentId`, leaving the tab visible over an empty body.
    wantsADocument()
    await act(async () => { renderChat() })
    await typeAndSend("Draft a leadership update")
    await settle()

    await waitFor(() => expect(convProbe()).toBe(String(NEW_CONV_ID)))
    expect(docProbe()).toBe("99")
  })
})

describe("a document never lands on someone else's thread", () => {
  it("does not open into the panel if the user has moved to another chat", async () => {
    // The create + generate round trips leave a window for the user to move
    // on, and the reset effect no longer covers it: a conversation coming into
    // existence is deliberately not a switch any more, so this flow has to
    // check for itself. Otherwise chat A's document opens in front of whoever
    // is now reading chat B.
    wantsADocument()
    let releaseGenerate: (v: unknown) => void = () => {}
    generateDoc.mockReturnValue(new Promise((r) => { releaseGenerate = r }))

    await act(async () => { renderChat() })
    await typeAndSend("Draft a leadership update")

    const newChat = within(screen.getByTestId("chat-tab-bar")).getByLabelText("New chat")
    await act(async () => { fireEvent.click(newChat) })
    await act(async () => { releaseGenerate({ id: 99, status: "generating" }) })
    await settle()

    expect(docProbe()).toBe("none")
  })
})

describe("a genuine thread change still retires the document", () => {
  it("clears it when moving between two chats that have no conversation yet", async () => {
    // PINS THE `activeTabId` DEPENDENCY. Neither chat ever gets a conversation
    // row, so `activeConvId` stays null across the switch and a conv-id-only
    // effect would not even run — the previous thread's document would ride
    // along into a chat that has nothing to do with it.
    wantsADocument()
    // A failed create is the reachable way to hold a chat with a document and
    // no conversation row: `ensureConversation` resolves null, the document is
    // still written (unlinked), and the tab never gains a `dbConvId`.
    createConversation.mockRejectedValue(new Error("offline"))

    await act(async () => { renderChat() })
    await typeAndSend("Draft a leadership update")
    await settle()
    expect(docProbe()).toBe("99")
    expect(convProbe()).toBe("none")

    const newChat = within(screen.getByTestId("chat-tab-bar")).getByLabelText("New chat")
    await act(async () => { fireEvent.click(newChat) })
    await settle()

    expect(convProbe()).toBe("none")
    expect(docProbe()).toBe("none")
  })

  it("opening a new chat clears the previous thread's document", async () => {
    // The fix above must not become "never clear it". A document belongs to the
    // thread that wrote it, so moving to another chat must still retire it —
    // otherwise a brand-new chat shows the previous thread's document.
    wantsADocument()
    await act(async () => { renderChat() })
    await typeAndSend("Draft a leadership update")
    await settle()
    expect(docProbe()).toBe("99")

    const newChat = within(screen.getByTestId("chat-tab-bar")).getByLabelText("New chat")
    await act(async () => { fireEvent.click(newChat) })
    await settle()

    expect(docProbe()).toBe("none")
  })
})
