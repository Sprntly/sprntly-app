// @vitest-environment jsdom
//
// Project-surface Copy / Edit / Ask-again — the RENDER + ownership contract.
//
// The project surfaces (private + group) draw their transcript exactly the way
// ConversationView does: `mapMainTurns(thread, mapDeps)` → `ChatTranscript` →
// `ChatBubble`. These tests drive that REAL path with a project-shaped `mapDeps`
// bag (the copy/edit/retry deps present, plus the group `renderUserBody`), so
// they prove the mapper's per-turn eligibility and the shared bubble's button
// rendering together — not a stub of either.
//
// The load-bearing assertions:
//   * PRIVATE (single-author, no `author` on any turn): every user turn offers
//     Copy + Edit + Ask-again, and each click reaches the wired handler.
//   * GROUP: the viewer's OWN turn (no `author`) offers all three; a PEER's turn
//     (carries `author`) offers Copy ONLY — never edit/retry on a message the
//     viewer didn't write.
import * as React from "react"
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

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

import { mapMainTurns } from "../../mapMainTurns"
import { ChatTranscript } from "../../../../shared/ChatTranscript"
import type { ThreadTurn } from "../../ChatScreen"
import type { MapMainTurnsDeps } from "../../../../shared/chat-shell/types"

afterEach(cleanup)

/** A project-shaped `mapDeps` — the fields `useProjectConversation` supplies,
 *  including the past-prompt copy/edit/retry deps this feature adds. `over`
 *  lets a test swap the handlers for spies. */
function makeProjectDeps(over: Partial<MapMainTurnsDeps> = {}): MapMainTurnsDeps {
  return {
    animatedTurnIds: { current: new Set<string>() },
    askStartRef: { current: new Map<string, number>() },
    resumedTurnsRef: { current: new Set<string>() },
    lastLiveTurnIdx: 99, // nothing in-flight for these settled turns
    busy: false,
    activeTab: { id: "project-1-group", prdId: null, prd: null, prdGenerating: false },
    name: "Ada",
    userInitials: "AD",
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
    // The feature under test: the past-prompt action deps the project controller
    // now threads through (present ⇒ the mapper draws the row).
    editingTurnId: null,
    copiedTurnId: null,
    onCopyTurn: vi.fn(),
    onRetryTurn: vi.fn(),
    onEditTurn: vi.fn(),
    onSubmitTurnEdit: vi.fn(),
    onCancelTurnEdit: vi.fn(),
    ...over,
  }
}

function renderTurns(thread: ThreadTurn[], deps: MapMainTurnsDeps) {
  return render(<ChatTranscript turns={mapMainTurns(thread, deps)} />)
}

describe("project private surface — Copy / Edit / Ask-again", () => {
  it("offers all three on every (single-author) turn and each click reaches its handler", () => {
    const onCopyTurn = vi.fn()
    const onEditTurn = vi.fn()
    const onRetryTurn = vi.fn()
    const thread: ThreadTurn[] = [
      { id: "p1", query: "first question", reply: { answer: "a1" } as ThreadTurn["reply"] },
    ]
    const view = renderTurns(thread, makeProjectDeps({ onCopyTurn, onEditTurn, onRetryTurn }))

    expect(view.getByTestId("user-turn-copy")).toBeTruthy()
    expect(view.getByTestId("user-turn-edit")).toBeTruthy()
    expect(view.getByTestId("user-turn-retry")).toBeTruthy()

    fireEvent.click(view.getByTestId("user-turn-copy"))
    fireEvent.click(view.getByTestId("user-turn-edit"))
    fireEvent.click(view.getByTestId("user-turn-retry"))
    expect(onCopyTurn).toHaveBeenCalledTimes(1)
    expect(onEditTurn).toHaveBeenCalledWith("p1")
    expect(onRetryTurn).toHaveBeenCalledTimes(1)
    expect(onRetryTurn.mock.calls[0][0].id).toBe("p1")
  })

  it("opens the in-place editor for the turn named by editingTurnId", () => {
    const thread: ThreadTurn[] = [{ id: "p9", query: "editable question", reply: { answer: "a" } as ThreadTurn["reply"] }]
    const view = renderTurns(thread, makeProjectDeps({ editingTurnId: "p9" }))
    const textarea = view.getByLabelText("Edit your message") as HTMLTextAreaElement
    expect(textarea.value).toBe("editable question")
  })
})

describe("project group surface — ownership: edit/retry on OWN turns only", () => {
  // The viewer's own group turn leaves `author` unset (postedOnly); a peer turn
  // carries `author` (the group adapter's self/peer discriminator).
  const ownTurn: ThreadTurn = { id: "g-own", query: "my message", postedOnly: true }
  const peerTurn: ThreadTurn = {
    id: "g-peer",
    query: "a teammate's message",
    author: { name: "Bao", role: "PM", userId: "u-2", initials: "BA" },
  }

  it("the viewer's OWN turn offers Copy + Edit + Ask-again", () => {
    const view = renderTurns([ownTurn], makeProjectDeps())
    expect(view.getByTestId("user-turn-copy")).toBeTruthy()
    expect(view.getByTestId("user-turn-edit")).toBeTruthy()
    expect(view.getByTestId("user-turn-retry")).toBeTruthy()
  })

  it("a PEER's turn offers Copy ONLY — no edit/retry affordance", () => {
    const view = renderTurns([peerTurn], makeProjectDeps())
    expect(view.getByTestId("user-turn-copy")).toBeTruthy()
    expect(view.queryByTestId("user-turn-edit")).toBeNull()
    expect(view.queryByTestId("user-turn-retry")).toBeNull()
  })

  it("in a mixed thread, only the own turn carries the edit/retry buttons", () => {
    const view = renderTurns([peerTurn, ownTurn], makeProjectDeps())
    // Exactly one edit and one retry in the whole thread (the own turn's).
    expect(view.getAllByTestId("user-turn-copy")).toHaveLength(2)
    expect(view.getAllByTestId("user-turn-edit")).toHaveLength(1)
    expect(view.getAllByTestId("user-turn-retry")).toHaveLength(1)
  })

  it("copy is still wired on a peer turn (allowed on any message)", () => {
    const onCopyTurn = vi.fn()
    const view = renderTurns([peerTurn], makeProjectDeps({ onCopyTurn }))
    fireEvent.click(view.getByTestId("user-turn-copy"))
    expect(onCopyTurn).toHaveBeenCalledTimes(1)
    expect(onCopyTurn.mock.calls[0][0].id).toBe("g-peer")
  })
})
