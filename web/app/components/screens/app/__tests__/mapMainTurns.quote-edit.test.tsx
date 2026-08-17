// @vitest-environment jsdom
//
// The two product rules mapMainTurns owns for the quote/edit wave:
//
//  1. A stored message's trailing blockquote is LIFTED out of the query, so it
//     renders as a quote block above the bubble instead of as literal "> "
//     text inside it — and every turn written before quoting existed is
//     untouched by that.
//  2. `canEditTurn` — which turns offer edit-and-resend at all. Getting this
//     wrong is the dangerous half of the feature: an editable ANSWERED turn
//     would orphan the reply below it.
import * as React from "react"
import { describe, expect, it, vi } from "vitest"
import { mapMainTurns } from "../mapMainTurns"
import type { ThreadTurn } from "../ChatScreen"
import type { MapMainTurnsDeps } from "../../../shared/chat-shell/types"
import type { AskResponse } from "../../../../lib/api"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

function reply(answer: string): AskResponse {
  return {
    answer, sources: [], follow_ups: [], key_points: [], citations: [],
    confidence: 1, unanswered: "",
  } as AskResponse
}

function makeDeps(over: Partial<MapMainTurnsDeps> = {}): MapMainTurnsDeps {
  return {
    animatedTurnIds: { current: new Set<string>() },
    askStartRef: { current: new Map<string, number>() },
    resumedTurnsRef: { current: new Set<string>() },
    lastLiveTurnIdx: 0,
    busy: false,
    activeTab: { id: "tab-1", prdId: null, prd: null, prdGenerating: false },
    name: "Ada",
    userInitials: "A",
    skillForQuery: () => null,
    ticketSetActionState: null,
    showInsightMsg: false,
    chatEvidenceExists: false,
    chatPrdExists: false,
    chatPrdCtaWaiting: false,
    chatProtoPrdId: null,
    chatPrototypeReady: false,
    inlinePrdCards: false,
    inlinePrdAnchorIdx: null,
    insightCardNode: null,
    prdQuestionsNode: null,
    clarifyPopupOpen: false,
    pendingClarifyTurn: null,
    handleAskAgain: vi.fn(),
    handleStopAsk: vi.fn(),
    submitClarifyAnswers: vi.fn(),
    setViewerAttachment: vi.fn(),
    openReportByTitle: vi.fn(),
    openArtifactInPanel: vi.fn(),
    openChatArtifactItem: vi.fn(),
    handleTicketSetAction: vi.fn(),
    handleOpenEvidence: vi.fn(),
    handleOpenPrd: vi.fn(),
    handleViewPrototype: vi.fn(),
    handlePrototypeSettled: vi.fn(),
    onCopyTurn: vi.fn(),
    onRetryTurn: vi.fn(),
    onEditTurn: vi.fn(),
    onSubmitTurnEdit: vi.fn(),
    onCancelTurnEdit: vi.fn(),
    ...over,
  }
}

describe("mapMainTurns — quoted passage", () => {
  it("lifts a trailing blockquote out of the query onto user.quote", () => {
    const thread: ThreadTurn[] = [
      {
        id: "t1",
        query: "Which manual is that?\n\n> findings must be documented",
        reply: reply("The IAA's."),
      },
    ]
    const [turn] = mapMainTurns(thread, makeDeps({ lastLiveTurnIdx: 0 }))
    expect(turn.user?.query).toBe("Which manual is that?")
    expect(turn.user?.quote).toBe("findings must be documented")
  })

  it("leaves an ordinary message exactly as it was", () => {
    const thread: ThreadTurn[] = [
      { id: "t1", query: "is 5 > 3 relevant here?", reply: reply("yes") },
    ]
    const [turn] = mapMainTurns(thread, makeDeps())
    expect(turn.user?.query).toBe("is 5 > 3 relevant here?")
    expect(turn.user?.quote).toBeNull()
  })

  it("leaves no blockquote marker anywhere the user reads", () => {
    const thread: ThreadTurn[] = [
      {
        id: "t1",
        query: "Which manual is that?\n\n> findings must be documented\n> in the audit file",
        reply: reply("The IAA's."),
      },
    ]
    const [turn] = mapMainTurns(thread, makeDeps())
    expect(turn.user?.query).not.toContain(">")
    expect(turn.user?.quote).not.toContain(">")
    expect(turn.user?.quote).toBe("findings must be documented\nin the audit file")
  })

  it("wires the quote to open in the viewer, since the block is clamped", () => {
    const setViewerAttachment = vi.fn()
    const thread: ThreadTurn[] = [
      { id: "t1", query: "Which manual?\n\n> a long excerpt", reply: reply("a") },
    ]
    const [turn] = mapMainTurns(thread, makeDeps({ setViewerAttachment }))
    expect(typeof turn.user?.onOpenQuote).toBe("function")
    turn.user!.onOpenQuote!()
    expect(setViewerAttachment).toHaveBeenCalledWith({
      name: "Quoted from the answer",
      content: "a long excerpt",
      // Verbatim, NOT through markdown: the excerpt was already lifted out of
      // a rendered answer, and re-parsing it collapses the newlines that are
      // its structure (a quoted list came back as one run-on paragraph).
      plain: true,
    })
  })

  it("offers no viewer on a turn with no quote", () => {
    const [turn] = mapMainTurns([{ id: "t1", query: "plain", reply: reply("a") }], makeDeps())
    expect(turn.user?.onOpenQuote).toBeUndefined()
  })
})

describe("mapMainTurns — copy a past prompt", () => {
  const copyOf = (turn: ThreadTurn, over: Partial<MapMainTurnsDeps> = {}) =>
    mapMainTurns([turn], makeDeps(over))[0]

  it("is offered on any turn the user spoke, answered or not", () => {
    // Copy changes nothing, so none of the re-ask exclusions apply to it.
    expect(!!copyOf({ id: "t", query: "q", reply: reply("a") }).onCopyUserTurn).toBe(true)
    expect(!!copyOf({ id: "t", query: "q", stopped: true }).onCopyUserTurn).toBe(true)
    expect(
      !!copyOf({ id: "t", query: "q", attachments: [{ name: "spec.pdf" }] }).onCopyUserTurn,
    ).toBe(true)
    expect(!!copyOf({ id: "t", query: "q" }, { busy: true, lastLiveTurnIdx: 0 }).onCopyUserTurn)
      .toBe(true)
  })

  it("is NOT offered on an agent-only turn", () => {
    expect(!!copyOf({ id: "t", query: "", reply: reply("a") }).onCopyUserTurn).toBe(false)
  })

  it("marks only the turn named by copiedTurnId", () => {
    const mapped = mapMainTurns(
      [{ id: "t1", query: "one", reply: reply("a") }, { id: "t2", query: "two", reply: reply("b") }],
      makeDeps({ copiedTurnId: "t2", lastLiveTurnIdx: 1 }),
    )
    expect(mapped[0].copied).toBe(false)
    expect(mapped[1].copied).toBe(true)
  })
})

describe("mapMainTurns — re-asking a past prompt (edit / retry)", () => {
  const editableOf = (turn: ThreadTurn, over: Partial<MapMainTurnsDeps> = {}) =>
    !!mapMainTurns([turn], makeDeps(over))[0].onEditUserTurn
  const retryableOf = (turn: ThreadTurn, over: Partial<MapMainTurnsDeps> = {}) =>
    !!mapMainTurns([turn], makeDeps(over))[0].onRetryUserTurn

  it("offers edit and retry on a question that never got an answer", () => {
    expect(editableOf({ id: "t", query: "waht is it", stopped: true })).toBe(true)
    expect(editableOf({ id: "t", query: "waht is it", error: "boom" })).toBe(true)
    expect(editableOf({ id: "t", query: "waht is it", timedOut: true })).toBe(true)
    expect(editableOf({ id: "t", query: "waht is it", interrupted: true })).toBe(true)
    expect(retryableOf({ id: "t", query: "waht is it", stopped: true })).toBe(true)
  })

  it("offers edit and retry on an ANSWERED turn", () => {
    // The behaviour this wave added. It is safe because re-asking rewinds the
    // conversation to that turn on BOTH sides — the thread on screen and the
    // persisted record — so the old answer is never orphaned under a rewritten
    // question.
    const turn: ThreadTurn = { id: "t", query: "q", reply: reply("a") }
    expect(editableOf(turn)).toBe(true)
    expect(retryableOf(turn)).toBe(true)
  })

  it("does NOT offer either while the question is still generating", () => {
    expect(editableOf({ id: "t", query: "q" }, { busy: true, lastLiveTurnIdx: 0 })).toBe(false)
    expect(retryableOf({ id: "t", query: "q" }, { busy: true, lastLiveTurnIdx: 0 })).toBe(false)
  })

  it("does NOT offer either while an artifact summary is still being written", () => {
    expect(editableOf({ id: "t", query: "q", summaryPending: true })).toBe(false)
    expect(retryableOf({ id: "t", query: "q", summaryPending: true })).toBe(false)
  })

  it("does NOT offer either on a turn that carried attachments", () => {
    // Re-sending drops the files (their bytes left component state on the
    // original send), so the re-asked question would silently be a different
    // one. Those turns keep "Ask again", which hands the text to the composer.
    const turn: ThreadTurn = {
      id: "t", query: "q", stopped: true, attachments: [{ name: "spec.pdf" }],
    }
    expect(editableOf(turn)).toBe(false)
    expect(retryableOf(turn)).toBe(false)
  })

  it("does NOT offer either on a turn with an open clarify batch", () => {
    const turn: ThreadTurn = {
      id: "t",
      query: "q",
      stopped: true,
      clarify: [{ prompt: "Which?", options: ["A"], header: "Scope" }],
    }
    expect(editableOf(turn)).toBe(false)
    expect(retryableOf(turn)).toBe(false)
  })

  it("does NOT offer either on an agent-only turn", () => {
    expect(editableOf({ id: "t", query: "", reply: reply("a") })).toBe(false)
    expect(retryableOf({ id: "t", query: "", reply: reply("a") })).toBe(false)
  })

  it("offers nothing at all when the host wires no such flow", () => {
    const [turn] = mapMainTurns(
      [{ id: "t", query: "q", stopped: true }],
      makeDeps({ onEditTurn: undefined, onRetryTurn: undefined, onCopyTurn: undefined }),
    )
    expect(turn.onEditUserTurn).toBeUndefined()
    expect(turn.onRetryUserTurn).toBeUndefined()
    expect(turn.onCopyUserTurn).toBeUndefined()
    expect(turn.editing).toBe(false)
  })

  it("hands the whole turn to retry, so the host can rewind to it", () => {
    const onRetryTurn = vi.fn()
    const turn: ThreadTurn = { id: "t1", query: "q", reply: reply("a"), dbTurnId: 42 }
    const [mapped] = mapMainTurns([turn], makeDeps({ onRetryTurn }))
    mapped.onRetryUserTurn?.()
    expect(onRetryTurn).toHaveBeenCalledWith(turn)
  })

  it("opens the editor for the turn named by editingTurnId, and only that one", () => {
    const thread: ThreadTurn[] = [
      { id: "t1", query: "first", reply: reply("a") },
      { id: "t2", query: "second", stopped: true },
    ]
    const mapped = mapMainTurns(thread, makeDeps({ editingTurnId: "t2", lastLiveTurnIdx: 1 }))
    expect(mapped[0].editing).toBe(false)
    expect(mapped[1].editing).toBe(true)
    expect(typeof mapped[1].onSubmitEdit).toBe("function")
  })

  it("hands the ORIGINAL turn (quote and all) back on save", () => {
    const onSubmitTurnEdit = vi.fn()
    const turn: ThreadTurn = {
      id: "t1",
      query: "waht is it?\n\n> findings must be documented",
      stopped: true,
    }
    const [mapped] = mapMainTurns([turn], makeDeps({ editingTurnId: "t1", onSubmitTurnEdit }))
    mapped.onSubmitEdit?.("what is it?")
    expect(onSubmitTurnEdit).toHaveBeenCalledWith(turn, "what is it?")
  })
})
