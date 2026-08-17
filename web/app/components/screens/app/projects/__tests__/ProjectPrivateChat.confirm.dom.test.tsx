// @vitest-environment jsdom
//
// ProjectPrivateChat — the confirmation gate on PRD chat-edits. An edit no
// longer writes on classify-dispatch: the route PROPOSES and returns
// `{ edited: false, pending: true, mutation }`, the turn parks the token +
// preview, and the shell renders ChatBubble's native confirm card. Confirm
// calls the confirm route with the token and settles the applied summary;
// Cancel drops the proposal and keeps the proposal-summary reply as the
// record. Mocking mirrors `ProjectPrivateChat.dispatch.dom.test.tsx`.
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

const runAskGenerationMock = vi.fn()
const resolveIntentMock = vi.fn()
const prdChatEditMock = vi.fn()
const prdChatEditConfirmMock = vi.fn()
const prdChatEditCancelMock = vi.fn()
const individualChatMock = vi.fn((id: number) =>
  Promise.resolve({
    id: 9001, project_id: id, user_id: "u1", kind: "individual" as const,
    created_at: "2026-08-11T00:00:00Z", updated_at: "2026-08-11T00:00:00Z",
  }),
)
const individualTurnsMock = vi.fn().mockResolvedValue([])
const ledgerMock = vi.fn().mockResolvedValue([])

vi.mock("../../../../../lib/runAskGeneration", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/runAskGeneration")>(
    "../../../../../lib/runAskGeneration",
  )
  return {
    ...actual,
    runAskGeneration: (...a: unknown[]) => runAskGenerationMock(...a),
    resumeAskGeneration: vi.fn(),
    getPendingAsk: vi.fn().mockReturnValue(null),
  }
})

vi.mock("../../../../../lib/poll", () => ({
  sleepUntilNextPoll: () => Promise.resolve(),
}))

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      individualChat: (...a: unknown[]) => individualChatMock(...(a as [number])),
      individualTurns: (...a: unknown[]) => individualTurnsMock(...a),
      ledger: (...a: unknown[]) => ledgerMock(...a),
      prdChatEdit: (...a: unknown[]) => prdChatEditMock(...a),
      prdChatEditConfirm: (...a: unknown[]) => prdChatEditConfirmMock(...a),
      prdChatEditCancel: (...a: unknown[]) => prdChatEditCancelMock(...a),
      resolveIntent: (...a: unknown[]) => resolveIntentMock(...a),
    },
  }
})

vi.mock("../../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }),
}))
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false, profile: null,
    workspace: { feature_flags: { chat_intent_envelope: true } },
    refresh: async () => {},
  }),
}))
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed" as const, user: { id: "u1" } }),
}))
// Same pre-existing, out-of-scope mount-time gap the sibling dispatch suite
// works around: the always-mounted retired PRD-patch banner calls
// `useNavigation()` unconditionally, which throws without a provider.
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn() }),
}))

import { ProjectPrivateChat } from "../ProjectPrivateChat"

const PENDING_RESULT = {
  edited: false as const,
  pending: true as const,
  mutation: {
    token: "tok-1",
    summary: "Proposed: tighten the problem statement.",
    sections_changed: ["Problem"],
    prd_id: 501,
  },
}

beforeEach(() => {
  runAskGenerationMock.mockReset()
  resolveIntentMock.mockReset()
  prdChatEditMock.mockReset()
  prdChatEditConfirmMock.mockReset()
  prdChatEditCancelMock.mockReset()
  individualChatMock.mockClear()
  individualTurnsMock.mockReset().mockResolvedValue([])
  ledgerMock.mockReset().mockResolvedValue([])
})
afterEach(() => cleanup())

async function sendEdit() {
  resolveIntentMock.mockResolvedValue({
    intent: "edit_prd", confidence: 0.9, task: null,
    instruction: "tighten the problem statement", reason: "edit", source: "llm",
    prd_id: null, prd_title: null,
  })
  prdChatEditMock.mockResolvedValue(PENDING_RESULT)
  render(React.createElement(ProjectPrivateChat, { projectId: 202 }))
  const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
  await act(async () => {
    fireEvent.change(textarea, { target: { value: "tighten the problem statement" } })
  })
  await act(async () => {
    fireEvent.click(screen.getByLabelText("Send"))
  })
  await waitFor(() => expect(screen.getByTestId("mutation-confirm-card")).toBeTruthy())
}

describe("ProjectPrivateChat — the PRD-edit confirmation gate", () => {
  it("test_edit_shows_confirm_card_from_pending_result", async () => {
    await sendEdit()
    // Nothing wrote: the edit call PROPOSED, and the turn shows the proposal
    // summary plus the confirm card — no "Updated the PRD." text anywhere.
    expect(prdChatEditMock).toHaveBeenCalledWith(
      202, "tighten the problem statement", undefined, expect.any(String),
    )
    expect(screen.getByTestId("mutation-summary").textContent).toBe(
      "Proposed: tighten the problem statement.",
    )
    expect(screen.getByTestId("ic-msg-agent").textContent).toContain(
      "Proposed: tighten the problem statement.",
    )
    expect(screen.queryByText(/Updated the PRD/)).toBeNull()
    expect(prdChatEditConfirmMock).not.toHaveBeenCalled()
  })

  it("test_confirm_calls_api_and_clears", async () => {
    prdChatEditConfirmMock.mockResolvedValue({
      edited: true, prd: { payload_md: "<p>tightened</p>" },
      sections_changed: ["Problem"], summary: "Tightened the problem statement.",
    })
    await sendEdit()

    await act(async () => {
      fireEvent.click(screen.getByTestId("mutation-confirm"))
    })

    await waitFor(() => expect(prdChatEditConfirmMock).toHaveBeenCalledWith(202, "tok-1"))
    // The card clears and the turn settles with the APPLIED summary.
    await waitFor(() => expect(screen.queryByTestId("mutation-confirm-card")).toBeNull())
    expect(screen.getByTestId("ic-msg-agent").textContent).toContain(
      "Tightened the problem statement.",
    )
  })

  it("test_cancel_calls_api_and_clears", async () => {
    prdChatEditCancelMock.mockResolvedValue({ cancelled: true })
    await sendEdit()

    await act(async () => {
      fireEvent.click(screen.getByTestId("mutation-cancel"))
    })

    await waitFor(() => expect(prdChatEditCancelMock).toHaveBeenCalledWith(202, "tok-1"))
    // The card clears; the proposal-summary reply stays as the record and no
    // write ever happened.
    await waitFor(() => expect(screen.queryByTestId("mutation-confirm-card")).toBeNull())
    expect(screen.getByTestId("ic-msg-agent").textContent).toContain(
      "Proposed: tighten the problem statement.",
    )
    expect(prdChatEditConfirmMock).not.toHaveBeenCalled()
  })
})
