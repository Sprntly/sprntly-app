import { describe, expect, it, vi } from "vitest"
import {
  dispatchChatIntent,
  type ChatIntentExecutors,
  type DispatchChatIntentContext,
} from "../dispatchChatIntent"
import type { ChatIntentEnvelope } from "../../api"

function ctx(
  overrides: Partial<DispatchChatIntentContext> = {},
): DispatchChatIntentContext {
  return {
    hasEditTarget: false,
    editTargetPrdId: null,
    ticketsTarget: null,
    ...overrides,
  }
}

function envelope(overrides: Partial<ChatIntentEnvelope> = {}): ChatIntentEnvelope {
  return {
    intent: "answer",
    confidence: 0.9,
    task: null,
    instruction: null,
    artifact_kind: null,
    artifact_type: null,
    artifact_query: null,
    artifact_template_id: null,
    artifact_template_name: null,
    reason: "test",
    source: "llm",
    prd_id: null,
    prd_title: null,
    ...overrides,
  }
}

function executors(): ChatIntentExecutors & Record<string, ReturnType<typeof vi.fn>> {
  return {
    onEditPrd: vi.fn(),
    onGenerateTickets: vi.fn(),
    onGeneratePrd: vi.fn(),
    onOpenArtifact: vi.fn(),
    onChangeTemplate: vi.fn(),
    onChangeTicketsTemplate: vi.fn(),
    onCreateArtifact: vi.fn(),
    onAssignTickets: vi.fn(),
    onListArtifacts: vi.fn(),
    onEditArtifact: vi.fn(),
    onAnswer: vi.fn(),
    // Deliberately omitted from the default fixture — `onClarify` is
    // OPTIONAL (main-chat callers never supply it) — see the two `clarify`
    // describe blocks below, which add it explicitly where needed.
  }
}

describe("dispatchChatIntent — routes to the executor only when the guard holds (AC12)", () => {
  it("generate_tickets always hits onGenerateTickets, carrying the envelope", () => {
    const ex = executors()
    const env = envelope({ intent: "generate_tickets", task: "the webhook retry work" })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onGenerateTickets).toHaveBeenCalledWith(env)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("generate_prd always hits onGeneratePrd, carrying the envelope", () => {
    const ex = executors()
    const env = envelope({ intent: "generate_prd", task: "dark mode on mobile" })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onGeneratePrd).toHaveBeenCalledWith(env)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("edit_prd with a resolvable target + instruction hits onEditPrd with both", () => {
    const ex = executors()
    const env = envelope({ intent: "edit_prd", instruction: "shorten it", prd_id: 77 })
    const result = dispatchChatIntent(env, ctx({ hasEditTarget: true, editTargetPrdId: 77 }), ex)
    expect(ex.onEditPrd).toHaveBeenCalledWith("shorten it", 77)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("open_artifact with a lookup hits onOpenArtifact with the lookup", () => {
    const ex = executors()
    const open = { status: "resolved" as const, artifact_type: "prd" as const, query: "onboarding", artifact: null, candidates: [] }
    const env = envelope({ intent: "open_artifact", open })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onOpenArtifact).toHaveBeenCalledWith(open)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("create_artifact always hits onCreateArtifact, carrying the envelope", () => {
    const ex = executors()
    const env = envelope({ intent: "create_artifact", artifact_kind: "leadership update" })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onCreateArtifact).toHaveBeenCalledWith(env)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("change_prd_template with a resolvable target + format hits onChangeTemplate with the resolved prd id", () => {
    const ex = executors()
    const env = envelope({ intent: "change_prd_template", prd_id: 77, artifact_template_id: "acme" })
    const result = dispatchChatIntent(env, ctx({ hasEditTarget: true, editTargetPrdId: 77 }), ex)
    expect(ex.onChangeTemplate).toHaveBeenCalledWith(env, 77)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("assign_tickets with a resolvable target + instruction hits onAssignTickets with both", () => {
    const ex = executors()
    const env = envelope({ intent: "assign_tickets", instruction: "give the login ticket to Dave", prd_id: 77 })
    const result = dispatchChatIntent(env, ctx({ hasEditTarget: true, editTargetPrdId: 77 }), ex)
    expect(ex.onAssignTickets).toHaveBeenCalledWith("give the login ticket to Dave", 77)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })
})

describe("dispatchChatIntent — change_prd_template falls through without a resolvable target or format (AC12)", () => {
  it("no resolvable target (hasEditTarget: false) → onAnswer, not onChangeTemplate", () => {
    const ex = executors()
    const env = envelope({ intent: "change_prd_template", artifact_template_id: "acme" })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onChangeTemplate).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })

  it("no artifact_template_id, even with a resolvable target → onAnswer, not onChangeTemplate", () => {
    const ex = executors()
    const env = envelope({ intent: "change_prd_template", prd_id: 77, artifact_template_id: null })
    const result = dispatchChatIntent(env, ctx({ hasEditTarget: true, editTargetPrdId: 77 }), ex)
    expect(ex.onChangeTemplate).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})

describe("dispatchChatIntent — change_tickets_template routes on ITS OWN target, not hasEditTarget", () => {
  it("a standalone-set target + format hits onChangeTicketsTemplate with the set target", () => {
    const ex = executors()
    const env = envelope({ intent: "change_tickets_template", artifact_template_id: "acme-t" })
    // hasEditTarget deliberately false: the set target must be sufficient.
    const result = dispatchChatIntent(env, ctx({ ticketsTarget: { ticketSetId: 7 } }), ex)
    expect(ex.onChangeTicketsTemplate).toHaveBeenCalledWith(env, { ticketSetId: 7 })
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("a PRD target + format hits onChangeTicketsTemplate with the prd target", () => {
    const ex = executors()
    const env = envelope({ intent: "change_tickets_template", prd_id: 77, artifact_template_id: "acme-t" })
    const result = dispatchChatIntent(
      env,
      ctx({ hasEditTarget: true, editTargetPrdId: 77, ticketsTarget: { prdId: 77 } }),
      ex,
    )
    expect(ex.onChangeTicketsTemplate).toHaveBeenCalledWith(env, { prdId: 77 })
    expect(result).toEqual({ handled: true })
  })

  it("no tickets target → onAnswer, even with an edit target present", () => {
    const ex = executors()
    const env = envelope({ intent: "change_tickets_template", artifact_template_id: "acme-t" })
    const result = dispatchChatIntent(env, ctx({ hasEditTarget: true, editTargetPrdId: 77 }), ex)
    expect(ex.onChangeTicketsTemplate).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })

  it("no artifact_template_id, even with a target → onAnswer", () => {
    const ex = executors()
    const env = envelope({ intent: "change_tickets_template", artifact_template_id: null })
    const result = dispatchChatIntent(env, ctx({ ticketsTarget: { ticketSetId: 7 } }), ex)
    expect(ex.onChangeTicketsTemplate).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})

describe("dispatchChatIntent — list_artifacts routes on the PRESENCE of rows, empty included", () => {
  it("a populated listing hits onListArtifacts with the envelope", () => {
    const ex = executors()
    const env = envelope({
      intent: "list_artifacts", list_kind: "prd",
      artifact_list: [{
        type: "prd", id: 7, title: "Checkout", status: "ready",
        created_at: null, brief_anchored: false, source: {}, open: { prd_id: 7 },
      }],
    })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onListArtifacts).toHaveBeenCalledWith(env)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("an EMPTY listing still routes — 'none yet' is the listing's own answer", () => {
    const ex = executors()
    const env = envelope({ intent: "list_artifacts", list_kind: "all", artifact_list: [] })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onListArtifacts).toHaveBeenCalledWith(env)
    expect(result).toEqual({ handled: true })
  })

  it("an ABSENT artifact_list (older backend) falls through to onAnswer", () => {
    const ex = executors()
    const env = envelope({ intent: "list_artifacts", list_kind: "prd" })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onListArtifacts).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})

describe("dispatchChatIntent — edit_prd falls through without a resolvable target or instruction (AC12)", () => {
  it("no resolvable target (hasEditTarget: false) → onAnswer, not onEditPrd", () => {
    const ex = executors()
    const env = envelope({ intent: "edit_prd", instruction: "shorten it" })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onEditPrd).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })

  it("empty instruction, even with a resolvable target → onAnswer, not onEditPrd", () => {
    const ex = executors()
    const env = envelope({ intent: "edit_prd", instruction: null, prd_id: 77 })
    const result = dispatchChatIntent(env, ctx({ hasEditTarget: true, editTargetPrdId: 77 }), ex)
    expect(ex.onEditPrd).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})

describe("dispatchChatIntent — assign_tickets falls through without a resolvable target or instruction (AC12)", () => {
  it("no resolvable target (hasEditTarget: false) → onAnswer, not onAssignTickets", () => {
    const ex = executors()
    const env = envelope({ intent: "assign_tickets", instruction: "assign it to Dave" })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onAssignTickets).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })

  it("empty instruction, even with a resolvable target → onAnswer, not onAssignTickets", () => {
    const ex = executors()
    const env = envelope({ intent: "assign_tickets", instruction: null, prd_id: 77 })
    const result = dispatchChatIntent(env, ctx({ hasEditTarget: true, editTargetPrdId: 77 }), ex)
    expect(ex.onAssignTickets).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})

describe("dispatchChatIntent — open_artifact falls through without a lookup (AC12)", () => {
  it("no envelope.open → onAnswer, not onOpenArtifact", () => {
    const ex = executors()
    const env = envelope({ intent: "open_artifact" })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onOpenArtifact).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})

describe("dispatchChatIntent — clarify (AC7, AC11 main-chat safety)", () => {
  it("with an onClarify executor + clarification text, calls onClarify with both", () => {
    const ex = executors()
    const onClarify = vi.fn()
    const options = [{ id: 501, title: "Onboarding" }, { id: 502, title: "Billing" }]
    const env = envelope({
      intent: "clarify",
      clarification: "This project has more than one PRD — tell me which to edit by id: …",
      prd_options: options,
    })
    const result = dispatchChatIntent(env, ctx(), { ...ex, onClarify })
    expect(onClarify).toHaveBeenCalledWith(env.clarification, options)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("no onClarify executor supplied (main chat) falls through to onAnswer", () => {
    // No `onClarify` in the executors object at all — the exact shape a
    // caller that never wires the clarify render (main chat, today) uses.
    const ex = executors()
    const env = envelope({
      intent: "clarify",
      clarification: "This project has more than one PRD — tell me which to edit by id: …",
      prd_options: [{ id: 501, title: "Onboarding" }],
    })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })

  it("onClarify present but no clarification text still falls through to onAnswer", () => {
    const ex = executors()
    const onClarify = vi.fn()
    const env = envelope({ intent: "clarify", clarification: undefined, prd_options: undefined })
    const result = dispatchChatIntent(env, ctx(), { ...ex, onClarify })
    expect(onClarify).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})

describe("dispatchChatIntent — answer / low-confidence / unknown / generate_prototype all fall through (AC12)", () => {
  it.each([
    ["answer", { intent: "answer" as const }],
    ["low confidence (still intent=answer per the 0.6-floor downgrade)", { intent: "answer" as const, confidence: 0.2, source: "low_confidence" }],
    ["generate_prototype (moot everywhere — no executor exists)", { intent: "generate_prototype" as const }],
    ["an unrecognized future intent string", { intent: "future_intent" as unknown as ChatIntentEnvelope["intent"] }],
  ])("%s → onAnswer, no structured executor", (_label, overrides) => {
    const ex = executors()
    const env = envelope(overrides)
    const result = dispatchChatIntent(env, ctx({ hasEditTarget: true, editTargetPrdId: 1 }), ex)
    expect(ex.onEditPrd).not.toHaveBeenCalled()
    expect(ex.onGenerateTickets).not.toHaveBeenCalled()
    expect(ex.onGeneratePrd).not.toHaveBeenCalled()
    expect(ex.onOpenArtifact).not.toHaveBeenCalled()
    expect(ex.onChangeTemplate).not.toHaveBeenCalled()
    expect(ex.onChangeTicketsTemplate).not.toHaveBeenCalled()
    expect(ex.onCreateArtifact).not.toHaveBeenCalled()
    expect(ex.onAssignTickets).not.toHaveBeenCalled()
    expect(ex.onListArtifacts).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})

describe("dispatchChatIntent — create_project", () => {
  it("routes to onCreateProject with the envelope when a name came through", () => {
    const ex = executors()
    const onCreateProject = vi.fn()
    const env = envelope({ intent: "create_project", task: "Billing revamp" })
    const result = dispatchChatIntent(env, ctx(), { ...ex, onCreateProject })
    expect(onCreateProject).toHaveBeenCalledWith(env)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("falls through to the grounded ask on a surface with no project affordance", () => {
    // The honest outcome: a surface that cannot make a project must answer,
    // never reply as though it made one.
    const ex = executors()
    const env = envelope({ intent: "create_project", task: "Billing revamp" })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onAnswer).toHaveBeenCalled()
    expect(result).toEqual({ handled: false })
  })

  it("falls through when no name came through, rather than minting an untitled one", () => {
    const ex = executors()
    const onCreateProject = vi.fn()
    const env = envelope({ intent: "create_project", task: null })
    const result = dispatchChatIntent(env, ctx(), { ...ex, onCreateProject })
    expect(onCreateProject).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalled()
    expect(result).toEqual({ handled: false })
  })
})

describe("dispatchChatIntent — share_to_slack", () => {
  it("routes to onShareToSlack with the envelope, unguarded", () => {
    // No target/channel guard on purpose: the executor's preview call resolves
    // both server-side and ASKS about whichever it couldn't settle. Guarding
    // here would turn "share this on slack" — the commonest phrasing, which
    // names no channel — into a grounded ask.
    const ex = executors()
    const onShareToSlack = vi.fn()
    const env = envelope({ intent: "share_to_slack" })
    const result = dispatchChatIntent(env, ctx(), { ...ex, onShareToSlack })
    expect(onShareToSlack).toHaveBeenCalledWith(env)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("carries the channel and note the planner extracted", () => {
    const ex = executors()
    const onShareToSlack = vi.fn()
    const env = envelope({
      intent: "share_to_slack",
      share_channel: "product-team",
      share_note: "Would love the team's feedback on this.",
      artifact_type: "prd",
    })
    dispatchChatIntent(env, ctx(), { ...ex, onShareToSlack })
    expect(onShareToSlack.mock.calls[0][0]).toMatchObject({
      share_channel: "product-team",
      share_note: "Would love the team's feedback on this.",
    })
  })

  it("falls through to onAnswer on a surface with no share UI", () => {
    // `onShareToSlack` is OPTIONAL (the project chat omits it). The fall-
    // through must reach the ask path — which crucially does NOT claim
    // anything was posted — rather than silently doing nothing.
    const ex = executors()
    const env = envelope({ intent: "share_to_slack" })
    const result = dispatchChatIntent(env, ctx(), ex)
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})

// ── edit_artifact: the report or document open beside the chat ──────────────
// The target rides the ENVELOPE (re-read server-side under the caller's
// company), not the caller's own state — it is the same read the planner was
// told about when it chose this action, so resolving it again on the client
// could edit a document the decision was never about.
describe("dispatchChatIntent — edit_artifact", () => {
  const target = { kind: "report", id: 12, title: "Voice of customer" }

  it("hits onEditArtifact with the instruction and the envelope's target", () => {
    const ex = executors()
    const env = envelope({
      intent: "edit_artifact",
      instruction: "convert the RICE section into a table",
      open_artifact: target,
    })
    expect(dispatchChatIntent(env, ctx(), ex).handled).toBe(true)
    expect(ex.onEditArtifact).toHaveBeenCalledWith(
      "convert the RICE section into a table", target,
    )
    expect(ex.onAnswer).not.toHaveBeenCalled()
  })

  it("falls through to the ask when nothing is open", () => {
    const ex = executors()
    const env = envelope({ intent: "edit_artifact", instruction: "shorten it" })
    expect(dispatchChatIntent(env, ctx(), ex).handled).toBe(false)
    expect(ex.onEditArtifact).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalled()
  })

  it("falls through to the ask with nothing to apply", () => {
    const ex = executors()
    const env = envelope({ intent: "edit_artifact", open_artifact: target })
    expect(dispatchChatIntent(env, ctx(), ex).handled).toBe(false)
    expect(ex.onAnswer).toHaveBeenCalled()
  })

  it("falls through on a surface with no panel to edit in", () => {
    // The project group chat omits the executor entirely; an intent it cannot
    // act on must answer rather than report an edit that never happened.
    const ex = { ...executors(), onEditArtifact: undefined }
    const env = envelope({
      intent: "edit_artifact", instruction: "cut the appendix", open_artifact: target,
    })
    expect(dispatchChatIntent(env, ctx(), ex).handled).toBe(false)
  })
})

describe("a goal typed into chat reaches Goal Analysis", () => {
  // THE FAILURE THIS EXISTS TO STOP, observed live: a user asked to "increase
  // revenue by 5%" and got a list of opportunities — no definition confirmed,
  // no plan shown, nothing approved. The planner had no goal action, so it fell
  // to `answer`; once it had one, the CLIENT still dropped it here.
  const goal = () => envelope({ intent: "analyse_goal", task: "increase revenue by 5%" })

  it("hands the goal to the panel", () => {
    const seen: string[] = []
    const ex = {
      ...executors(),
      onAnalyseGoal: (g: string) => { seen.push(g); return true },
    }
    const out = dispatchChatIntent(goal(), ctx(), ex)
    expect(out).toEqual({ handled: true })
    expect(seen).toEqual(["increase revenue by 5%"])
    expect(ex.onAnswer).not.toHaveBeenCalled()
  })

  it("answers instead on a surface with no panel", () => {
    // The brief chat and the AI bar have nowhere to open a run. Falling through
    // is right; swallowing the goal silently is not.
    const ex = executors()
    const out = dispatchChatIntent(goal(), ctx(), ex)
    expect(out).toEqual({ handled: false })
    expect(ex.onAnswer).toHaveBeenCalled()
  })

  it("answers when the executor declines to act", () => {
    // `handled` follows what the executor DID. The panel slot optional-chains
    // through a ref; if that ref were ever null the call would be a silent
    // no-op, and reporting `handled: true` would roll back the user's turn and
    // start nothing — the message would simply vanish.
    const ex = { ...executors(), onAnalyseGoal: () => false }
    const out = dispatchChatIntent(goal(), ctx(), ex)
    expect(out).toEqual({ handled: false })
    expect(ex.onAnswer).toHaveBeenCalled()
  })

  it("answers rather than starting a run with no goal text", () => {
    const ex = { ...executors(), onAnalyseGoal: vi.fn() }
    const out = dispatchChatIntent(
      envelope({ intent: "analyse_goal", task: "" }), ctx(), ex)
    expect(out).toEqual({ handled: false })
    expect(ex.onAnalyseGoal).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalled()
  })
})
