import { describe, expect, it, vi } from "vitest"
import { dispatchChatIntent, type ChatIntentExecutors } from "../dispatchChatIntent"
import type { ChatIntentEnvelope } from "../../api"

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
    onCreateArtifact: vi.fn(),
    onAnswer: vi.fn(),
  }
}

describe("dispatchChatIntent — routes to the executor only when the guard holds (AC12)", () => {
  it("generate_tickets always hits onGenerateTickets, carrying the envelope", () => {
    const ex = executors()
    const env = envelope({ intent: "generate_tickets", task: "the webhook retry work" })
    const result = dispatchChatIntent(env, { hasEditTarget: false, editTargetPrdId: null }, ex)
    expect(ex.onGenerateTickets).toHaveBeenCalledWith(env)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("generate_prd always hits onGeneratePrd, carrying the envelope", () => {
    const ex = executors()
    const env = envelope({ intent: "generate_prd", task: "dark mode on mobile" })
    const result = dispatchChatIntent(env, { hasEditTarget: false, editTargetPrdId: null }, ex)
    expect(ex.onGeneratePrd).toHaveBeenCalledWith(env)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("edit_prd with a resolvable target + instruction hits onEditPrd with both", () => {
    const ex = executors()
    const env = envelope({ intent: "edit_prd", instruction: "shorten it", prd_id: 77 })
    const result = dispatchChatIntent(env, { hasEditTarget: true, editTargetPrdId: 77 }, ex)
    expect(ex.onEditPrd).toHaveBeenCalledWith("shorten it", 77)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("open_artifact with a lookup hits onOpenArtifact with the lookup", () => {
    const ex = executors()
    const open = { status: "resolved" as const, artifact_type: "prd" as const, query: "onboarding", artifact: null, candidates: [] }
    const env = envelope({ intent: "open_artifact", open })
    const result = dispatchChatIntent(env, { hasEditTarget: false, editTargetPrdId: null }, ex)
    expect(ex.onOpenArtifact).toHaveBeenCalledWith(open)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("create_artifact always hits onCreateArtifact, carrying the envelope", () => {
    const ex = executors()
    const env = envelope({ intent: "create_artifact", artifact_kind: "leadership update" })
    const result = dispatchChatIntent(env, { hasEditTarget: false, editTargetPrdId: null }, ex)
    expect(ex.onCreateArtifact).toHaveBeenCalledWith(env)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })

  it("change_prd_template with a resolvable target + format hits onChangeTemplate with the resolved prd id", () => {
    const ex = executors()
    const env = envelope({ intent: "change_prd_template", prd_id: 77, artifact_template_id: "acme" })
    const result = dispatchChatIntent(env, { hasEditTarget: true, editTargetPrdId: 77 }, ex)
    expect(ex.onChangeTemplate).toHaveBeenCalledWith(env, 77)
    expect(ex.onAnswer).not.toHaveBeenCalled()
    expect(result).toEqual({ handled: true })
  })
})

describe("dispatchChatIntent — change_prd_template falls through without a resolvable target or format (AC12)", () => {
  it("no resolvable target (hasEditTarget: false) → onAnswer, not onChangeTemplate", () => {
    const ex = executors()
    const env = envelope({ intent: "change_prd_template", artifact_template_id: "acme" })
    const result = dispatchChatIntent(env, { hasEditTarget: false, editTargetPrdId: null }, ex)
    expect(ex.onChangeTemplate).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })

  it("no artifact_template_id, even with a resolvable target → onAnswer, not onChangeTemplate", () => {
    const ex = executors()
    const env = envelope({ intent: "change_prd_template", prd_id: 77, artifact_template_id: null })
    const result = dispatchChatIntent(env, { hasEditTarget: true, editTargetPrdId: 77 }, ex)
    expect(ex.onChangeTemplate).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})

describe("dispatchChatIntent — edit_prd falls through without a resolvable target or instruction (AC12)", () => {
  it("no resolvable target (hasEditTarget: false) → onAnswer, not onEditPrd", () => {
    const ex = executors()
    const env = envelope({ intent: "edit_prd", instruction: "shorten it" })
    const result = dispatchChatIntent(env, { hasEditTarget: false, editTargetPrdId: null }, ex)
    expect(ex.onEditPrd).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })

  it("empty instruction, even with a resolvable target → onAnswer, not onEditPrd", () => {
    const ex = executors()
    const env = envelope({ intent: "edit_prd", instruction: null, prd_id: 77 })
    const result = dispatchChatIntent(env, { hasEditTarget: true, editTargetPrdId: 77 }, ex)
    expect(ex.onEditPrd).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})

describe("dispatchChatIntent — open_artifact falls through without a lookup (AC12)", () => {
  it("no envelope.open → onAnswer, not onOpenArtifact", () => {
    const ex = executors()
    const env = envelope({ intent: "open_artifact" })
    const result = dispatchChatIntent(env, { hasEditTarget: false, editTargetPrdId: null }, ex)
    expect(ex.onOpenArtifact).not.toHaveBeenCalled()
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
    const result = dispatchChatIntent(env, { hasEditTarget: true, editTargetPrdId: 1 }, ex)
    expect(ex.onEditPrd).not.toHaveBeenCalled()
    expect(ex.onGenerateTickets).not.toHaveBeenCalled()
    expect(ex.onGeneratePrd).not.toHaveBeenCalled()
    expect(ex.onOpenArtifact).not.toHaveBeenCalled()
    expect(ex.onChangeTemplate).not.toHaveBeenCalled()
    expect(ex.onCreateArtifact).not.toHaveBeenCalled()
    expect(ex.onAnswer).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: false })
  })
})
