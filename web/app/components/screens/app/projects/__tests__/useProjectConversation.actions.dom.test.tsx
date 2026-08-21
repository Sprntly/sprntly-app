// @vitest-environment jsdom
//
// Project chat past-prompt EDIT / ASK-AGAIN — the surface-specific behaviour
// that is the reason this feature is not just prop-forwarding. Mounts the REAL
// `useProjectConversation` controller (the same hook `ProjectMainThread`
// mounts) with the generation ENGINE stubbed to a spy, and drives its
// `mapDeps.onRetryTurn` / `onSubmitTurnEdit` — the exact handlers the rendered
// action buttons call — asserting:
//
//   * Edit/retry rewinds the persisted conversation to the turn (its OWN
//     `rewindToTurn`, not main's) and re-asks — screen and DB agree.
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const h = vi.hoisted(() => ({
  // The generation-engine seam. `runConversationAsk` firing == "the agent was
  // asked to reply"; not firing == "posted silently / no reply".
  runConversationAsk: vi.fn(async () => {}),
  handleStopAsk: vi.fn(),
  runActionTurnInTab: vi.fn(async () => {}),
  // Project turn API spies.
  individualChat: vi.fn(async () => ({ id: 7 })),
  listTurns: vi.fn(async () => ({ turns: [] })),
  rewindToTurn: vi.fn(async () => {}),
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

// Stub the generation engine + IO-bound project hooks; keep everything else
// (the real composer, generation flows, persistence) genuine.
vi.mock("../../useMainConversation", () => ({
  useMainConversation: () => ({
    runConversationAsk: h.runConversationAsk,
    handleStopAsk: h.handleStopAsk,
    runActionTurnInTab: h.runActionTurnInTab,
  }),
}))
vi.mock("../useRealtimeChannel", () => ({ useRealtimeChannel: () => {} }))

// Contexts.
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

// The api barrel: keep every real export, override only the turn-API objects
// with deterministic spies (covers both the static import and the persistence
// layer's dynamic `import(...)`).
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
      rewindToTurn: h.rewindToTurn,
      update: vi.fn(async () => ({})),
      create: vi.fn(async () => ({ id: 7 })),
      addTurn: vi.fn(async () => ({ id: 1, conversation_id: 7, role: "user", content: "", created_at: "" })),
    },
    chatIntentApi: { ...actual.chatIntentApi, resolve: vi.fn(async () => null) },
    chatSuggestionsApi: { ...actual.chatSuggestionsApi, next: vi.fn(async () => ({ suggestions: [] })) },
    askApi: { ...actual.askApi, skills: vi.fn(async () => ({ skills: [] })), extractFile: vi.fn(async () => ({ markdown: "" })) },
  }
})

import { useProjectConversation, type ProjectConversationProps } from "../useProjectConversation"

let bag: ProjectConversationProps | null = null
function Harness({ projectId }: { projectId: number | string }) {
  bag = useProjectConversation(projectId)
  return null
}

async function mount(projectId: number | string) {
  await act(async () => {
    render(<Harness projectId={projectId} />)
  })
  await waitFor(() => expect(h.individualChat).toHaveBeenCalled())
}

beforeEach(() => { bag = null; vi.clearAllMocks() })
afterEach(cleanup)

describe("private edit/retry rewinds the conversation and re-asks", () => {
  it("retry rewinds to the turn (its OWN rewindToTurn) and fires the ask", async () => {
    await mount(101)
    await act(async () => {
      bag!.mapDeps.onRetryTurn!({ id: "resumed-7-0", query: "what changed?", dbTurnId: 42 } as never)
    })
    // The persisted conversation is rewound to this DB row, then re-asked.
    await waitFor(() => expect(h.rewindToTurn).toHaveBeenCalledWith(7, 42))
    await waitFor(() => expect(h.runConversationAsk).toHaveBeenCalled())
  })
})

describe("engine hook — private path intact, group symbols gone (AC4/AC6)", () => {
  it("test_use_project_conversation_sends_individual_turn — a send resolves through the individual conversation path; the api client has no group turn method left to call", async () => {
    const api = await import("../../../../../lib/api")
    // Closed-world at the call-surface this hook actually uses: there is no
    // `postGroupTurn`/`groupTurns`/`groupChat` method left on `projectsApi`
    // for a send to reach even by accident.
    expect("postGroupTurn" in api.projectsApi).toBe(false)
    await mount(101)
    await act(async () => {
      void bag!.submitAsk("what changed?")
    })
    await waitFor(() => expect(h.runConversationAsk).toHaveBeenCalled())
  })

  it("test_use_project_conversation_hydrates_individual_turns — hydrate resolves via projectsApi.individualChat + conversationsApi.listTurns; groupTurns/groupChat do not exist to hydrate from", async () => {
    await mount(101)
    expect(h.individualChat).toHaveBeenCalledWith(101)
    expect(h.listTurns).toHaveBeenCalledWith(7)
    const api = await import("../../../../../lib/api")
    expect("groupTurns" in api.projectsApi).toBe(false)
    expect("groupChat" in api.projectsApi).toBe(false)
  })

  it("test_use_project_conversation_keeps_greeting_body_renderer — renderAgentBody still routes a MORE_MARKER reply to GreetingTurnBody", async () => {
    const { MORE_MARKER } = await import("../../../../shared/chat-shell/types")
    await mount(101)
    const node = bag!.mapDeps.renderAgentBody!({ reply: { answer: `Welcome!${MORE_MARKER}more detail` } })
    expect(node).toBeTruthy()
    // A reply with no marker stays on the default reply ladder (null here).
    expect(bag!.mapDeps.renderAgentBody!({ reply: { answer: "plain answer" } })).toBeNull()
  })
})
