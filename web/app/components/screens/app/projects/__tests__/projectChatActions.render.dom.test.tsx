// @vitest-environment jsdom
//
// Project (private) chat Copy / Edit / Ask-again — the RENDER + ownership
// contract.
//
// The project surface draws its transcript exactly the way ConversationView
// does: `mapMainTurns(thread, mapDeps)` → `ChatTranscript` → `ChatBubble`.
// These tests drive that REAL path with a project-shaped `mapDeps` bag (the
// copy/edit/retry deps present), so they prove the mapper's per-turn
// eligibility and the shared bubble's button rendering together — not a
// stub of either.
//
// The load-bearing assertion: PRIVATE (single-author) — every user turn
// offers Copy + Edit + Ask-again, and each click reaches the wired handler.
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
    activeTab: { id: "project-1-individual", prdId: null, prd: null, prdGenerating: false },
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
  // Named explicitly, not omitted: `MapMainTurnsDeps` requires the goal
  // fields so a surface cannot drop them with a clean `tsc` — which is how
  // the in-thread gates shipped inert.
  goalGateBusyTurnId: undefined,
  confirmGoalDefinition: undefined,
  approveGoalPlan: undefined,
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
