// @vitest-environment jsdom
//
// ProjectIndividualChat — the inline brief-turn delegation affordance.
// A delivered-brief `ic-history-agent` turn whose id matches a delegation
// assigned to the caller (via the ledger row's `delivered_turn_id`) renders a
// compact `<DelegationActions>`; its clicks call `emitDelegationEvent`. A turn
// with no matching delegation renders exactly as today — no affordance — and
// the send path, load-on-open effect, and `brief.delivered` subscription are
// untouched. `useRealtimeChannel` is mocked (its own lifecycle is covered by
// `useRealtimeChannel.dom.test.tsx`); this file asserts the LEDGER wiring only.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const individualChatMock = vi.fn()
const individualTurnsMock = vi.fn()
const ledgerMock = vi.fn()
const emitDelegationEventMock = vi.fn()
const runAskGenerationMock = vi.fn()
const resumeAskGenerationMock = vi.fn()
const getPendingAskMock = vi.fn(() => null as { id: string } | null)

const authState = { kind: "authed" as const, user: { id: "u1" } }

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      individualChat: (...a: unknown[]) => individualChatMock(...a),
      individualTurns: (...a: unknown[]) => individualTurnsMock(...a),
      ledger: (...a: unknown[]) => ledgerMock(...a),
      emitDelegationEvent: (...a: unknown[]) => emitDelegationEventMock(...a),
    },
  }
})

vi.mock("../../../../../lib/runAskGeneration", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/runAskGeneration")>(
    "../../../../../lib/runAskGeneration",
  )
  return {
    ...actual,
    runAskGeneration: (...a: unknown[]) => runAskGenerationMock(...a),
    resumeAskGeneration: (...a: unknown[]) => resumeAskGenerationMock(...a),
    getPendingAsk: (...a: unknown[]) => getPendingAskMock(...a),
  }
})

vi.mock("../../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }),
}))

// The component now reads the classifier flag (`chatIntentEnvelopeOn`) to
// decide whether to classify-then-dispatch at all. Explicit OFF here keeps
// every assertion in this file byte-identical to pre-classifier behaviour —
// same stub shape `ProjectIndividualChat.test.tsx` uses.
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false, profile: null,
    workspace: { feature_flags: { chat_intent_envelope: false } },
    refresh: async () => {},
  }),
}))

vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => authState,
}))

const { realtimeSpy } = vi.hoisted(() => ({ realtimeSpy: vi.fn() }))
vi.mock("../useRealtimeChannel", () => ({
  useRealtimeChannel: (
    topic: string | null,
    handlers: { onEvent?: (event: string, payload: unknown) => void; onReconcile?: () => void },
  ) => {
    realtimeSpy(topic, handlers)
    return { status: "degraded", degraded: true }
  },
}))

import { ProjectIndividualChat } from "../ProjectIndividualChat"
import type { DelegationLedgerRow, IndividualTurn } from "../../../../../lib/api"

const briefTurn = (id: number): IndividualTurn => ({
  id,
  role: "assistant",
  content: "You've been handed a task: pull the p95 benchmark.",
  created_at: new Date().toISOString(),
})

const ledgerRow = (overrides: Partial<DelegationLedgerRow>): DelegationLedgerRow => ({
  delegation_id: 5,
  task_summary: "Pull the p95 benchmark",
  status: "assigned",
  status_at: new Date().toISOString(),
  bucket: "open",
  other_party_user_id: "u-other",
  other_party_name: "David",
  delivered_conversation_id: 900,
  delivered_turn_id: 42,
  ...overrides,
})

beforeEach(() => {
  individualChatMock.mockReset()
  individualChatMock.mockResolvedValue({ id: 9001, project_id: 1, user_id: "u1", kind: "individual", created_at: "", updated_at: "" })
  individualTurnsMock.mockReset()
  individualTurnsMock.mockResolvedValue([])
  ledgerMock.mockReset()
  ledgerMock.mockResolvedValue([])
  emitDelegationEventMock.mockReset()
  emitDelegationEventMock.mockResolvedValue({ delegation_id: 5, status: "accepted" })
  runAskGenerationMock.mockReset()
  resumeAskGenerationMock.mockReset()
  getPendingAskMock.mockReset()
  getPendingAskMock.mockReturnValue(null)
  realtimeSpy.mockClear()
})

afterEach(() => cleanup())

describe("ProjectIndividualChat — inline brief-turn affordance (AC7)", () => {
  it("test_inline_brief_actions_no_accept_decline — a matched brief turn renders compact DelegationActions with only in_progress/completed, no accepted/declined, and emits on click", async () => {
    individualTurnsMock.mockResolvedValue([briefTurn(42)])
    ledgerMock.mockResolvedValue([ledgerRow({ delegation_id: 5, delivered_turn_id: 42, status: "assigned" })])

    render(React.createElement(ProjectIndividualChat, { projectId: 1 }))

    await waitFor(() => expect(screen.getByTestId("ic-brief-delegation-actions")).toBeTruthy())
    const affordance = screen.getByTestId("ic-brief-delegation-actions")
    const agentTurn = screen.getByTestId("ic-history-agent")
    // The affordance renders INSIDE the brief turn, not as a floating element.
    expect(agentTurn.contains(affordance)).toBe(true)
    // Assignee on an `assigned` row → Mark in progress + Mark done only.
    expect(within(affordance).getByTestId("delegation-action-in_progress")).toBeTruthy()
    expect(within(affordance).getByTestId("delegation-action-completed")).toBeTruthy()
    expect(within(affordance).queryByTestId("delegation-action-accepted")).toBeNull()
    expect(within(affordance).queryByTestId("delegation-action-declined")).toBeNull()

    // The ledger read is scoped to the caller's assigned view.
    expect(ledgerMock).toHaveBeenCalledWith(1, "assigned_to_me")

    fireEvent.click(within(affordance).getByTestId("delegation-action-in_progress"))
    await waitFor(() => expect(emitDelegationEventMock).toHaveBeenCalledWith(1, 5, "in_progress", undefined))
  })

  it("test_unmatched_turn_has_no_affordance — a non-matching turn renders unchanged; send path + subscription untouched", async () => {
    // Brief turn id 99, but the only delegation was delivered on turn 42 → no match.
    individualTurnsMock.mockResolvedValue([briefTurn(99)])
    ledgerMock.mockResolvedValue([ledgerRow({ delegation_id: 5, delivered_turn_id: 42 })])

    render(React.createElement(ProjectIndividualChat, { projectId: 1 }))

    await waitFor(() => expect(screen.getByTestId("ic-history-agent")).toBeTruthy())
    // The turn renders exactly as today — no affordance.
    expect(screen.queryByTestId("ic-brief-delegation-actions")).toBeNull()
    expect(emitDelegationEventMock).not.toHaveBeenCalled()

    // Non-breakage: the load-on-open history read and the per-user
    // `brief.delivered` subscription still fired, and the composer is present.
    expect(individualTurnsMock).toHaveBeenCalledWith(1)
    expect(realtimeSpy).toHaveBeenCalledWith("project:1:user:u1", expect.anything())
    expect(screen.getByTestId("project-individual-chat")).toBeTruthy()
  })

  it("renders no affordance when the ledger read is empty (best-effort, no throw)", async () => {
    individualTurnsMock.mockResolvedValue([briefTurn(42)])
    ledgerMock.mockResolvedValue([])
    render(React.createElement(ProjectIndividualChat, { projectId: 1 }))
    await waitFor(() => expect(screen.getByTestId("ic-history-agent")).toBeTruthy())
    expect(screen.queryByTestId("ic-brief-delegation-actions")).toBeNull()
  })

  it("a failed ledger read leaves the thread working with no affordance", async () => {
    individualTurnsMock.mockResolvedValue([briefTurn(42)])
    ledgerMock.mockRejectedValue(new Error("ledger down"))
    render(React.createElement(ProjectIndividualChat, { projectId: 1 }))
    await waitFor(() => expect(screen.getByTestId("ic-history-agent")).toBeTruthy())
    expect(screen.queryByTestId("ic-brief-delegation-actions")).toBeNull()
    expect(screen.getByTestId("project-individual-chat")).toBeTruthy()
  })
})
