// @vitest-environment jsdom
//
// mapMainTurns — the extracted, verbatim main turn-mapping. These assertions
// prove the extraction reproduces the inline block's per-turn output (footer +
// afterNode included) and preserves the render-pass `animatedTurnIds` dedup
// mutation. Byte-level equality to the PRE-extraction render is separately
// proven by the golden DOM suite (which renders through this function at C2/C3
// and still matches the snapshot recorded against the unmodified screen); this
// file pins the structural field-level contract and the mutation semantics.
import * as React from "react"
import { describe, expect, it, vi } from "vitest"
import { mapMainTurns } from "../mapMainTurns"
import type { ThreadTurn } from "../ChatScreen"
import { ChatArtifactActions, ChatTicketSetActions } from "../../../shared/chat-shell/ChatArtifactActions"
import type { MapMainTurnsDeps } from "../../../shared/chat-shell/types"
import type { AskResponse } from "../../../../lib/api"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

function reply(answer: string): AskResponse {
  return { answer, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse
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
    insightCardNode: <div data-testid="insight" />,
    prdQuestionsNode: <div data-testid="prd-questions" />,
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
    ...over,
  }
}

describe("mapMainTurns", () => {
  it("test_map_main_turns_matches_inline_output", () => {
    const thread: ThreadTurn[] = [
      { id: "t-settled", query: "settled q", reply: reply("settled a") },
      { id: "t-stream", query: "stream q", partial: "streaming…" },
      { id: "t-error", query: "error q", error: "boom" },
      {
        id: "t-clarify",
        query: "clarify q",
        clarify: [{ prompt: "Which?", options: ["A", "B"], header: "Scope" }],
      },
    ]
    // last live turn is the clarify one; the clarify gate is open on the tab
    const deps = makeDeps({
      lastLiveTurnIdx: 3,
      openReportByTitle: vi.fn(),
      openChatArtifactItem: vi.fn(),
      handleStopAsk: vi.fn(),
      activeTab: { id: "tab-1", prdId: null, prd: null, prdGenerating: false, pendingClarify: { turnId: "t-clarify" } },
    })
    const out = mapMainTurns(thread, deps)
    expect(out.map((t) => t.turnId)).toEqual(["t-settled", "t-stream", "t-error", "t-clarify"])

    // agent identity is constant across turns
    for (const t of out) {
      expect(t.agentBadge).toBeUndefined()
      expect(t.user?.name).toBe("Ada")
      expect(t.user?.initials).toBe("A")
      // direct pass-throughs (identity), proving the wiring is verbatim
      expect(t.onOpenReport).toBe(deps.openReportByTitle)
      expect(t.onOpenArtifactItem).toBe(deps.openChatArtifactItem)
      expect(t.onStop).toBe(deps.handleStopAsk)
    }

    const [settled, stream, error, clarify] = out
    // settled turn — reply carried through, animated (fresh), not last
    expect(settled.reply?.answer).toBe("settled a")
    expect(settled.isLast).toBe(false)
    expect(settled.isAnimated).toBe(true)
    // streaming turn — partial carried, no reply
    expect(stream.partial).toBe("streaming…")
    expect(stream.reply).toBeUndefined()
    expect(stream.isAnimated).toBe(false)
    // error turn — error carried
    expect(error.error).toBe("boom")
    // clarify turn — is last, gate open reflects the tab's pendingClarify
    expect(clarify.isLast).toBe(true)
    expect(clarify.clarify?.[0].prompt).toBe("Which?")
    expect(clarify.clarifyGateOpen).toBe(true)
    // no footer/afterNode on a no-PRD, no-ticketSet chat
    for (const t of out) {
      expect(t.footer).toBeNull()
      expect(t.afterNode).toBeNull()
    }
  })

  it("test_map_main_turns_renders_ticket_set_footer_when_configured", () => {
    const thread: ThreadTurn[] = [{ id: "turn-a", query: "q", reply: reply("a") }]
    const deps = makeDeps({ lastLiveTurnIdx: 0, ticketSetActionState: "ready" })
    const [t] = mapMainTurns(thread, deps)
    expect(React.isValidElement(t.footer)).toBe(true)
    expect((t.footer as React.ReactElement).type).toBe(ChatTicketSetActions)
  })

  it("test_map_main_turns_renders_artifact_footer_and_inline_prd_afternode", () => {
    const thread: ThreadTurn[] = [{ id: "turn-a", query: "q", reply: reply("a") }]
    const deps = makeDeps({
      lastLiveTurnIdx: 0,
      showInsightMsg: false,
      activeTab: { id: "tab-1", prdId: 5, prd: null, prdGenerating: false },
      inlinePrdCards: true,
      inlinePrdAnchorIdx: 0,
    })
    const [t] = mapMainTurns(thread, deps)
    // footer is the artifact-action row (prdId set, insight not showing)
    expect((t.footer as React.ReactElement).type).toBe(ChatArtifactActions)
    // afterNode carries the inline insight + prd-questions nodes
    expect(React.isValidElement(t.afterNode)).toBe(true)
  })

  it("test_map_main_turns_preserves_animation_dedup_mutation", () => {
    const animatedTurnIds = { current: new Set<string>() }
    const thread: ThreadTurn[] = [{ id: "t-fresh", query: "q", reply: reply("a") }]
    const deps = makeDeps({ animatedTurnIds, lastLiveTurnIdx: 0 })

    // first pass: fresh reply → animated, id added exactly once
    const first = mapMainTurns(thread, deps)
    expect(first[0].isAnimated).toBe(true)
    expect(animatedTurnIds.current.has("t-fresh")).toBe(true)
    expect(animatedTurnIds.current.size).toBe(1)

    // second pass: id already present → NOT animated, no double-add
    const second = mapMainTurns(thread, deps)
    expect(second[0].isAnimated).toBe(false)
    expect(animatedTurnIds.current.size).toBe(1)
  })
})

describe("the goal plan gate carries what was approved", () => {
  // THE WIRING IS THE FEATURE. `approveGoalPlan` grew a fifth argument — the
  // plan as shown — so the settled card in the thread can keep listing the
  // sources, with the ones the reader dropped struck through rather than
  // silently gone. Nothing asserted that the argument was passed: dropping it
  // leaves the settled record with no `plan.sources`, which falls back to a
  // raw-slug line, and produces no failure anywhere. This is the whole of that
  // seam, tested where it is cheap.
  const PLAN = {
    goal_text: "raise net revenue retention",
    definition_text: "expansion minus churn",
    currency: "accounts",
    total_signals: 412,
    sources: [
      { source_type: "customer_voice", signal_count: 260,
        label: "calls and customer tickets", witnesses: "what customers asked for" },
      { source_type: "project_mgmt", signal_count: 152,
        label: "the tracker", witnesses: "what was built" },
    ],
    cannot_answer: [],
    will_produce: [],
    excluded_sources: [] as string[],
    hypotheses: [] as string[],
  }

  function approvedWith(gate: Record<string, unknown>) {
    const approveGoalPlan = vi.fn()
    const thread = [{
      id: "t-goal", query: "increase revenue by 5%", goalGate: gate,
    }] as unknown as ThreadTurn[]
    const out = mapMainTurns(thread, makeDeps({
      lastLiveTurnIdx: 0,
      approveGoalPlan,
      activeTab: { id: "tab-1", prdId: null, prd: null, prdGenerating: false },
    } as Partial<MapMainTurnsDeps>))
    const decision = { excluded_sources: ["customer_voice"], hypotheses: [] }
    ;(out[0] as unknown as {
      onApproveGoalPlan?: (d: unknown) => void
    }).onApproveGoalPlan?.(decision)
    return { approveGoalPlan, decision }
  }

  it("hands the approved plan to the approve handler", () => {
    const { approveGoalPlan, decision } = approvedWith({
      kind: "plan", runId: 42, plan: PLAN,
    })
    expect(approveGoalPlan).toHaveBeenCalledWith(
      "tab-1", "t-goal", 42, decision, PLAN,
    )
    // Named explicitly: the sources are what the settled card renders, and an
    // argument that arrives as undefined is the silent version of this bug.
    const passed = approveGoalPlan.mock.calls[0][4] as typeof PLAN | undefined
    expect(passed?.sources?.map((x) => x.source_type))
      .toEqual(["customer_voice", "project_mgmt"])
  })

  it("survives a gate persisted before the plan was carried", () => {
    // Older records have no `plan` on the gate. That must reach the handler as
    // undefined rather than throwing — the settled card has a fallback for it.
    const { approveGoalPlan, decision } = approvedWith({ kind: "plan", runId: 42 })
    expect(approveGoalPlan).toHaveBeenCalledWith(
      "tab-1", "t-goal", 42, decision, undefined,
    )
  })

  it("does not approve from a definition gate", () => {
    const { approveGoalPlan } = approvedWith({
      kind: "definition", runId: 42, goalText: "g", ask: "?",
    })
    expect(approveGoalPlan).not.toHaveBeenCalled()
  })
})
