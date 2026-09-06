// @vitest-environment jsdom
//
// useProjectConversation — the composer draft hand-off (client decision D1,
// 2026-09-02). A half-typed main-chat message stashed on
// `content.pendingComposerDraft` (by ChatScreen's `bindActiveProject`, right
// before it navigates into this project) must be picked up into THIS
// conversation's composer exactly once, then cleared back to null so it
// never leaks into a later mount (a different project, or the same project
// reopened later).
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const h = vi.hoisted(() => ({
  runConversationAsk: vi.fn(async () => {}),
  handleStopAsk: vi.fn(),
  runActionTurnInTab: vi.fn(async () => {}),
  individualChat: vi.fn(async () => ({ id: 7 })),
  listTurns: vi.fn(async () => ({ turns: [] })),
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
// (the real composer, the REAL ContentContext) genuine — this test's whole
// point is proving a real content-store round trip.
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
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ openContentPanel: vi.fn(), contentPanelTab: null, showToast: vi.fn() }),
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
      addTurn: vi.fn(async () => ({ id: 1, conversation_id: 7, role: "user", content: "", created_at: "" })),
    },
    chatIntentApi: { ...actual.chatIntentApi, resolve: vi.fn(async () => null) },
    chatSuggestionsApi: { ...actual.chatSuggestionsApi, next: vi.fn(async () => ({ suggestions: [] })) },
    askApi: { ...actual.askApi, skills: vi.fn(async () => ({ skills: [] })), extractFile: vi.fn(async () => ({ markdown: "" })) },
  }
})

import { useProjectConversation, type ProjectConversationProps } from "../useProjectConversation"
import { ContentProvider, useContent } from "../../../../../context/ContentContext"

let bag: ProjectConversationProps | null = null
function Harness({ projectId }: { projectId: number | string }) {
  bag = useProjectConversation(projectId)
  return null
}

/** Seeds `content.pendingComposerDraft` (as ChatScreen's `bindActiveProject`
 *  already did, before navigating) the moment it mounts, exactly mirroring
 *  the `SeedPrd` pattern in `ProjectDetailScreen.prd-restore-no-flash.test.tsx`. */
function SeedDraft({ draft }: { draft: string | null }) {
  const { setContent } = useContent()
  React.useEffect(() => {
    if (draft) setContent({ pendingComposerDraft: draft })
  }, [])
  return null
}

async function mount(projectId: number | string, seed: string | null) {
  await act(async () => {
    render(
      <ContentProvider>
        <SeedDraft draft={seed} />
        <Harness projectId={projectId} />
      </ContentProvider>,
    )
  })
  await waitFor(() => expect(h.individualChat).toHaveBeenCalled())
}

beforeEach(() => { bag = null; vi.clearAllMocks() })
afterEach(cleanup)

describe("useProjectConversation — composer draft hand-off (D1)", () => {
  it("test_draft_handoff_applied_and_cleared — a pending draft is applied to THIS conversation's composer, then cleared from shared content (never leaks into a later mount)", async () => {
    let latestContent: { pendingComposerDraft: string | null } = { pendingComposerDraft: null }
    function Probe() {
      const { content } = useContent()
      latestContent = content
      return null
    }
    await act(async () => {
      render(
        <ContentProvider>
          <SeedDraft draft="also check the tablet layout" />
          <Harness projectId={101} />
          <Probe />
        </ContentProvider>,
      )
    })
    await waitFor(() => expect(h.individualChat).toHaveBeenCalled())
    await waitFor(() => expect(bag!.draft).toBe("also check the tablet layout"))
    await waitFor(() => expect(latestContent.pendingComposerDraft).toBeNull())
  })

  it("test_no_draft_handoff_when_absent — no pendingComposerDraft on shared content leaves the composer draft empty (cold open, the common case)", async () => {
    await mount(101, null)
    expect(bag!.draft).toBe("")
  })
})
