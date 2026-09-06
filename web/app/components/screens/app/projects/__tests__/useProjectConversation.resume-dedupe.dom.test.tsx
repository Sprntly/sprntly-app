// @vitest-environment jsdom
//
// useProjectConversation — the resume-effect side of the ask-completion
// double-write fix. This project surface's ask-scope (`askScope(convKey)`,
// keyed on the project — not per-tab) is the SAME across every mount/tab
// watching the same conversation, so a SECOND mount resuming an ask another
// mount already started/completed must persist under the SAME
// `client_message_id` the originating send minted — the server's idempotent
// upsert then collapses a same-key double-submit to one row.
//
// This exercises the REAL resume effect (no mock on useProjectConversation
// itself); only its two IO seams are mocked: `runAskGeneration` (to control
// the persisted pending-job record + the resumed poll's outcome) and the api
// barrel (to assert exactly what `addTurn` was called with).
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const h = vi.hoisted(() => ({
  runConversationAsk: vi.fn(async () => {}),
  handleStopAsk: vi.fn(),
  runActionTurnInTab: vi.fn(async () => {}),
  individualChat: vi.fn(async () => ({ id: 7 })),
  listTurns: vi.fn(async () => ({
    // ONE reply-less user turn — hydrate restores it with no paired
    // assistant row, exactly the shape the resume effect looks for.
    turns: [{ id: 42, role: "user", content: "what changed since Friday?", created_at: "2026-09-02T00:00:00Z" }],
  })),
  addTurn: vi.fn(async (..._a: unknown[]) => ({ id: 1, conversation_id: 7, role: "assistant", content: "", created_at: "" })),
  getPendingAsk: vi.fn(),
  resumeAskGeneration: vi.fn(),
}))

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
  if (typeof window !== "undefined" && !window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {}, dispatchEvent() { return false },
    })) as unknown as typeof window.matchMedia
  }
})

vi.mock("../../useMainConversation", () => ({
  useMainConversation: () => ({
    runConversationAsk: h.runConversationAsk,
    handleStopAsk: h.handleStopAsk,
    runActionTurnInTab: h.runActionTurnInTab,
  }),
}))
vi.mock("../useRealtimeChannel", () => ({ useRealtimeChannel: () => {} }))
vi.mock("../../../../../context/CompanyContext", () => ({ useCompany: () => ({ activeCompany: { id: 1 } }) }))
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ profile: { id: "me-1", full_name: "Me" } }),
  profileDisplayName: () => "Me",
}))
vi.mock("../../../../../context/ContentContext", () => ({
  useContent: () => ({
    content: { prd: null, documentId: null, threadReports: [], threadReportsConversationId: null, reportFocusId: null },
    setContent: vi.fn(),
  }),
}))
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ openContentPanel: vi.fn(), contentPanelTab: null, showToast: vi.fn() }),
}))

vi.mock("../../../../../lib/runAskGeneration", () => ({
  getPendingAsk: (...a: unknown[]) => h.getPendingAsk(...a),
  resumeAskGeneration: (...a: unknown[]) => h.resumeAskGeneration(...a),
  askScope: (tabId: string) => `t:${tabId}`,
  AskCancelledError: class AskCancelledError extends Error {},
  AskStoppedError: class AskStoppedError extends Error {},
  AskTimeoutError: class AskTimeoutError extends Error {},
}))

vi.mock("../../../../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../../lib/api")>()
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      individualChat: h.individualChat,
    },
    conversationsApi: {
      ...actual.conversationsApi,
      listTurns: h.listTurns,
      update: vi.fn(async () => ({})),
      create: vi.fn(async () => ({ id: 7 })),
      addTurn: h.addTurn,
    },
    chatIntentApi: { ...actual.chatIntentApi, resolve: vi.fn(async () => null) },
    chatSuggestionsApi: { ...actual.chatSuggestionsApi, next: vi.fn(async () => ({ suggestions: [] })) },
    askApi: { ...actual.askApi, skills: vi.fn(async () => ({ skills: [] })), extractFile: vi.fn(async () => ({ markdown: "" })) },
  }
})

import { useProjectConversation, type ProjectConversationProps } from "../useProjectConversation"

let bag: ProjectConversationProps | null = null
function Harness({ projectId, currentUserId }: { projectId: number | string; currentUserId?: string | null }) {
  bag = useProjectConversation(projectId, currentUserId)
  return null
}

async function mount(projectId: number | string) {
  await act(async () => {
    render(<Harness projectId={projectId} currentUserId="user-1" />)
  })
  await waitFor(() => expect(h.individualChat).toHaveBeenCalled())
}

beforeEach(() => {
  bag = null
  vi.clearAllMocks()
  h.getPendingAsk.mockReturnValue(null)
})
afterEach(cleanup)

describe("useProjectConversation — resume effect stamps the originating send's client_message_id", () => {
  it("test_resume_persists_with_the_same_clientMessageId_the_pending_job_record_carries", async () => {
    // The ORIGINATING send (another tab, or an earlier mount of this same
    // conversation) already persisted this pending-job record — ask_id 555,
    // reply dedup key "ask-555-reply".
    h.getPendingAsk.mockReturnValue({ id: "555", clientMessageId: "ask-555-reply" })
    h.resumeAskGeneration.mockResolvedValue({
      answer: "Three tickets moved to Done.", key_points: [], citations: [], confidence: 1, unanswered: "",
    })

    await mount(101)

    await waitFor(() => expect(h.resumeAskGeneration).toHaveBeenCalledWith(
      555, expect.anything(), expect.anything(),
      expect.any(Function), expect.any(Function), expect.any(Function), expect.any(Function),
      undefined,
    ))
    await waitFor(() => expect(h.addTurn).toHaveBeenCalled())

    const call = h.addTurn.mock.calls[0]
    expect(call[1]).toBe("assistant")
    expect(call[2]).toBe("Three tickets moved to Done.")
    // The 6th positional arg is the client_message_id `addTurn` sends on
    // the wire — stamped from the SAME pending-job record, not freshly
    // minted by this (resuming) mount.
    expect(call[5]).toBe("ask-555-reply")
  })

  it("test_resume_with_no_persisted_clientMessageId_omits_it_unchanged", async () => {
    // A pending job persisted BEFORE this ticket (or any non-project-chat
    // ask scope) carries no clientMessageId at all — the resume must still
    // work, simply with no dedup key (byte-identical to before this fix).
    h.getPendingAsk.mockReturnValue({ id: "556" })
    h.resumeAskGeneration.mockResolvedValue({
      answer: "All good.", key_points: [], citations: [], confidence: 1, unanswered: "",
    })

    await mount(102)

    await waitFor(() => expect(h.addTurn).toHaveBeenCalled())
    const call = h.addTurn.mock.calls[0]
    expect(call[5]).toBeUndefined()
  })
})
