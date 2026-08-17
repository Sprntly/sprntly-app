import { describe, expect, it, vi } from "vitest"
import {
  openArtifactDestination,
  type OpenArtifactDestinationAdapter,
} from "../openArtifactDestination"
import type { OpenArtifactCandidate } from "../../../../lib/api"

function candidate(overrides: Partial<OpenArtifactCandidate> = {}): OpenArtifactCandidate {
  return {
    type: "prd",
    id: 1,
    title: "Retry work",
    status: "ready",
    prd_id: 100,
    brief_id: null,
    insight_index: null,
    brief_anchored: false,
    week_label: null,
    conversation_id: null,
    conversation_title: null,
    ...overrides,
  }
}

function adapter(
  over: Partial<OpenArtifactDestinationAdapter> = {},
): OpenArtifactDestinationAdapter & Record<string, ReturnType<typeof vi.fn>> {
  return {
    openEvidence: vi.fn(() => true),
    resumeConversation: vi.fn(() => false),
    openPrd: vi.fn(() => true),
    ...over,
  } as OpenArtifactDestinationAdapter & Record<string, ReturnType<typeof vi.fn>>
}

describe("openArtifactDestination — shared open decision (AC14–AC16)", () => {
  it("test_openArtifactDestination_panel_adapter_opens_panel", () => {
    const a = adapter()
    const ok = openArtifactDestination(candidate({ prd_id: 100 }), a, "open my prd")
    expect(ok).toBe(true)
    expect(a.openPrd).toHaveBeenCalledTimes(1)
    expect(a.openPrd).toHaveBeenCalledWith(expect.objectContaining({ prd_id: 100 }), 100, "open my prd")
    expect(a.openEvidence).not.toHaveBeenCalled()
  })

  it("test_openArtifactDestination_modal_adapter_opens_modal", () => {
    // Same core, a modal-style terminal: proves the decision is adapter-driven
    // (main→panel, project→modal), the sanctioned divergence.
    const openedInModal: OpenArtifactCandidate[] = []
    const modal = adapter({
      openPrd: vi.fn((c) => {
        openedInModal.push(c)
        return true
      }),
    })
    const c = candidate({ prd_id: 7 })
    const ok = openArtifactDestination(c, modal)
    expect(ok).toBe(true)
    expect(openedInModal).toEqual([c])
  })

  it("test_openArtifactDestination_resume_conversation_first", () => {
    // A PRD whose originating conversation survives (id + title) resumes the
    // chat — openPrd is NOT reached.
    const a = adapter({ resumeConversation: vi.fn(() => true) })
    const ok = openArtifactDestination(
      candidate({ prd_id: 100, conversation_id: 55, conversation_title: "Retry chat" }),
      a,
    )
    expect(ok).toBe(true)
    expect(a.resumeConversation).toHaveBeenCalledWith({
      conversationId: 55,
      conversationTitle: "Retry chat",
      prdId: 100,
    })
    expect(a.openPrd).not.toHaveBeenCalled()
  })

  it("resume that declines falls through to openPrd", () => {
    const a = adapter({ resumeConversation: vi.fn(() => false) })
    const ok = openArtifactDestination(
      candidate({ prd_id: 100, conversation_id: 55, conversation_title: "Retry chat" }),
      a,
    )
    expect(ok).toBe(true)
    expect(a.resumeConversation).toHaveBeenCalledTimes(1)
    expect(a.openPrd).toHaveBeenCalledTimes(1)
  })

  it("test_openArtifactDestination_evidence_branch", () => {
    const a = adapter()
    const ok = openArtifactDestination(
      candidate({ type: "evidence", brief_id: 3, insight_index: 2 }),
      a,
    )
    expect(ok).toBe(true)
    expect(a.openEvidence).toHaveBeenCalledTimes(1)
    expect(a.openPrd).not.toHaveBeenCalled()
  })

  it("test_openArtifactDestination_missing_id_returns_false", () => {
    const a = adapter()
    // A PRD candidate whose prd_id AND id are null → no destination.
    const ok = openArtifactDestination(candidate({ prd_id: null, id: null as unknown as number }), a)
    expect(ok).toBe(false)
    expect(a.openPrd).not.toHaveBeenCalled()
    expect(a.resumeConversation).not.toHaveBeenCalled()

    // An evidence candidate missing its (brief, insight) pair → no destination.
    const ev = adapter()
    const ok2 = openArtifactDestination(
      candidate({ type: "evidence", brief_id: null, insight_index: null }),
      ev,
    )
    expect(ok2).toBe(false)
    expect(ev.openEvidence).not.toHaveBeenCalled()
  })
})
