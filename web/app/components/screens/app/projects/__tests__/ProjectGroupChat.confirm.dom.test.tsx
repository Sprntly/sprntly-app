// @vitest-environment jsdom
//
// ProjectGroupChat — the confirmation gate riding a persisted group agent
// turn: an assistant turn whose stored reply carries `pending_mutation`
// renders ChatBubble's native confirm card, and Confirm calls the confirm
// route with the token (the "Done" group turn then arrives via the existing
// realtime/poll — no local turn synthesis). Mocking mirrors
// `ProjectGroupChat.bubbles.dom.test.tsx`.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const groupTurnsMock = vi.fn()
const prdChatEditConfirmMock = vi.fn()
const prdChatEditCancelMock = vi.fn()

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      groupTurns: (...a: unknown[]) => groupTurnsMock(...a),
      prdChatEditConfirm: (...a: unknown[]) => prdChatEditConfirmMock(...a),
      prdChatEditCancel: (...a: unknown[]) => prdChatEditCancelMock(...a),
    },
  }
})
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed" as const, user: { id: "u1" } }),
}))

import { ProjectGroupChat } from "../ProjectGroupChat"
import type { GroupTurn } from "../../../../../lib/api"

const agentTurnWithMutation: GroupTurn = {
  id: 3,
  role: "assistant",
  content: "Proposed: tighten the problem statement.",
  author_user_id: null,
  author_name: "Sprntly",
  author_job_role: null,
  created_at: new Date().toISOString(),
  reply: {
    answer: "Proposed: tighten the problem statement.",
    key_points: [], citations: [], confidence: 1, unanswered: "",
    pending_mutation: { token: "tok-9", summary: "Proposed: tighten the problem statement.", prd_id: 501 },
  },
} as GroupTurn

beforeEach(() => {
  groupTurnsMock.mockReset()
  prdChatEditConfirmMock.mockReset()
  prdChatEditCancelMock.mockReset()
})
afterEach(() => cleanup())

describe("ProjectGroupChat — pending-mutation confirm card", () => {
  it("test_group_pending_mutation_turn_renders_confirm", async () => {
    groupTurnsMock.mockResolvedValue([agentTurnWithMutation])
    prdChatEditConfirmMock.mockResolvedValue({
      edited: true, prd: { payload_md: "<p>tightened</p>" },
      sections_changed: ["Problem"], summary: "Tightened the problem statement.",
    })
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))

    // The persisted assistant turn's reply.pending_mutation renders the card.
    const card = await screen.findByTestId("mutation-confirm-card")
    expect(card).toBeTruthy()
    expect(screen.getByTestId("mutation-summary").textContent).toBe(
      "Proposed: tighten the problem statement.",
    )
    // It rides the agent lane's turn.
    expect(screen.getByTestId("gc-msg-agent").contains(card)).toBe(true)

    // Confirm calls the confirm route with the token; once the token is
    // spent the card clears (the "Done" turn arrives via realtime/poll).
    await act(async () => {
      fireEvent.click(screen.getByTestId("mutation-confirm"))
    })
    await waitFor(() => expect(prdChatEditConfirmMock).toHaveBeenCalledWith(101, "tok-9"))
    await waitFor(() => expect(screen.queryByTestId("mutation-confirm-card")).toBeNull())
    expect(prdChatEditCancelMock).not.toHaveBeenCalled()
  })

  it("test_group_cancel_clears_card_without_confirm", async () => {
    groupTurnsMock.mockResolvedValue([agentTurnWithMutation])
    prdChatEditCancelMock.mockResolvedValue({ cancelled: true })
    render(React.createElement(ProjectGroupChat, { projectId: 101 }))

    await screen.findByTestId("mutation-confirm-card")
    await act(async () => {
      fireEvent.click(screen.getByTestId("mutation-cancel"))
    })
    await waitFor(() => expect(prdChatEditCancelMock).toHaveBeenCalledWith(101, "tok-9"))
    await waitFor(() => expect(screen.queryByTestId("mutation-confirm-card")).toBeNull())
    expect(prdChatEditConfirmMock).not.toHaveBeenCalled()
  })
})
