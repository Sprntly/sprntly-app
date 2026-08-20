// @vitest-environment jsdom
//
// Project chat past-prompt EDIT / ASK-AGAIN — the surface-specific behaviour
// that is the reason this feature is not just prop-forwarding. Mounts the REAL
// `useProjectConversation` controller (the same hook `ProjectMainThread`
// mounts) with the generation ENGINE stubbed to a spy, and drives its
// `mapDeps.onRetryTurn` / `onSubmitTurnEdit` — the exact handlers the rendered
// action buttons call — asserting:
//
//   * GROUP re-post respects the EXISTING 2-mode reply gate. A multi-human
//     project re-posting an UNTAGGED message posts silently (no agent ask); the
//     same project re-posting an `@Sprntly` message, and a SOLO project
//     re-posting anything, DO fire the ask. The re-post never rewinds shared
//     history (peers' messages must survive).
//   * PRIVATE (single-author) edit/retry rewinds the persisted conversation to
//     the turn (its OWN `rewindToTurn`, not main's) and re-asks — screen and DB
//     agree — and never touches the group post path.
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
  groupChat: vi.fn(async () => ({ id: 7 })),
  individualChat: vi.fn(async () => ({ id: 7 })),
  groupTurns: vi.fn(async () => []),
  postGroupTurn: vi.fn(async () => ({ id: 101 })),
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
// (the real composer, generation flows, persistence, mention helpers) genuine.
vi.mock("../../useMainConversation", () => ({
  useMainConversation: () => ({
    runConversationAsk: h.runConversationAsk,
    handleStopAsk: h.handleStopAsk,
    runActionTurnInTab: h.runActionTurnInTab,
  }),
}))
vi.mock("../useRealtimeChannel", () => ({ useRealtimeChannel: () => {} }))
vi.mock("../useMentionPicker", () => ({
  useMentionPicker: () => ({ handleKeys: vi.fn(), handleComposerInput: vi.fn(), pickerNode: null, open: false }),
}))

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
      groupChat: h.groupChat,
      individualChat: h.individualChat,
      groupTurns: h.groupTurns,
      postGroupTurn: h.postGroupTurn,
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
function Harness({ surface, memberCount }: { surface: "group" | "individual"; memberCount: number }) {
  bag = useProjectConversation(101, surface, undefined, "Acme", memberCount, ["Me", "Bao"])
  return null
}

async function mount(surface: "group" | "individual", memberCount: number) {
  await act(async () => {
    render(<Harness surface={surface} memberCount={memberCount} />)
  })
  const resolver = surface === "group" ? h.groupChat : h.individualChat
  await waitFor(() => expect(resolver).toHaveBeenCalled())
}

beforeEach(() => { bag = null; vi.clearAllMocks() })
afterEach(cleanup)

describe("group re-post respects the 2-mode reply gate", () => {
  it("multi-human + UNTAGGED retry re-posts silently — the agent is NOT asked", async () => {
    await mount("group", 2)
    await act(async () => {
      bag!.mapDeps.onRetryTurn!({ id: "g-own", query: "let's sync friday", postedOnly: true } as never)
    })
    await waitFor(() => expect(h.postGroupTurn).toHaveBeenCalled())
    // The message went back onto the shared thread, but no reply was triggered…
    expect(h.runConversationAsk).not.toHaveBeenCalled()
    // …and shared history was NOT rewound (a peer's messages must survive).
    expect(h.rewindToTurn).not.toHaveBeenCalled()
  })

  it("multi-human + @Sprntly retry DOES ask the agent to reply", async () => {
    await mount("group", 2)
    await act(async () => {
      bag!.mapDeps.onRetryTurn!({ id: "g-own2", query: "@Sprntly summarize the thread", postedOnly: false } as never)
    })
    await waitFor(() => expect(h.runConversationAsk).toHaveBeenCalled())
    expect(h.rewindToTurn).not.toHaveBeenCalled()
  })

  it("SOLO project asks the agent even on an untagged retry", async () => {
    await mount("group", 1)
    await act(async () => {
      bag!.mapDeps.onRetryTurn!({ id: "g-solo", query: "how's the launch looking", postedOnly: false } as never)
    })
    await waitFor(() => expect(h.runConversationAsk).toHaveBeenCalled())
  })

  it("an edited untagged re-post is still gated silent; an edit that adds @Sprntly asks", async () => {
    await mount("group", 2)
    // Edit that stays untagged → silent re-post, no ask.
    await act(async () => {
      bag!.mapDeps.onSubmitTurnEdit!({ id: "g-e1", query: "old wording" } as never, "revised plain wording")
    })
    await waitFor(() => expect(h.postGroupTurn).toHaveBeenCalledTimes(1))
    expect(h.runConversationAsk).not.toHaveBeenCalled()

    // A second edit that adds @Sprntly → the gate lets the ask through.
    await act(async () => {
      bag!.mapDeps.onSubmitTurnEdit!({ id: "g-e2", query: "old wording" } as never, "@Sprntly please weigh in")
    })
    await waitFor(() => expect(h.runConversationAsk).toHaveBeenCalled())
  })
})

describe("private edit/retry rewinds the conversation and re-asks", () => {
  it("retry rewinds to the turn (its OWN rewindToTurn) and fires the ask; never posts to the group", async () => {
    await mount("individual", 1)
    await act(async () => {
      bag!.mapDeps.onRetryTurn!({ id: "resumed-7-0", query: "what changed?", dbTurnId: 42 } as never)
    })
    // The persisted conversation is rewound to this DB row, then re-asked.
    await waitFor(() => expect(h.rewindToTurn).toHaveBeenCalledWith(7, 42))
    await waitFor(() => expect(h.runConversationAsk).toHaveBeenCalled())
    // Private is single-author — it never touches the group post path.
    expect(h.postGroupTurn).not.toHaveBeenCalled()
  })
})
