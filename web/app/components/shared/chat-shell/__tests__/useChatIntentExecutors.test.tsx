import { describe, expect, it, vi } from "vitest"
import {
  dispatchChatIntent,
  type DispatchChatIntentContext,
} from "../../../../lib/chat/dispatchChatIntent"
import type { ChatIntentEnvelope } from "../../../../lib/api"
import { useChatIntentExecutors } from "../useChatIntentExecutors"
import type { ChatIntentExecutorAdapter } from "../types"

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

describe("useChatIntentExecutors — the shared intent→executor wiring (AC1–AC4)", () => {
  it("test_useChatIntentExecutors_routes_generate_prd_to_adapter", () => {
    // A generate_prd envelope invokes adapter.onGeneratePrd, and the adapter's
    // own settle-on-exit runs. (AC1)
    const settle = vi.fn()
    const onGeneratePrd = vi.fn(() => settle())
    const executors = useChatIntentExecutors({ onGeneratePrd })

    const result = dispatchChatIntent(
      envelope({ intent: "generate_prd", task: "the retry work" }),
      ctx(),
      executors,
    )

    expect(onGeneratePrd).toHaveBeenCalledTimes(1)
    expect(settle).toHaveBeenCalledTimes(1)
    expect(result).toEqual({ handled: true })
  })

  it("test_useChatIntentExecutors_omitted_slot_falls_to_onAnswer_noop", () => {
    // A subset adapter omitting onAssignTickets resolves an assign envelope to
    // the surface's onAnswer no-op — invoked, not `undefined()` — with no
    // throw. (AC4)
    const onAnswer = vi.fn()
    const adapter: ChatIntentExecutorAdapter = { onAnswer }
    const executors = useChatIntentExecutors(adapter)

    expect(() =>
      dispatchChatIntent(
        envelope({ intent: "assign_tickets", instruction: "give it to Ada" }),
        ctx({ hasEditTarget: true, editTargetPrdId: 7 }),
        executors,
      ),
    ).not.toThrow()

    // dispatch routes assign_tickets to onAssignTickets (guard holds); the
    // omitted slot falls through to onAnswer.
    expect(onAnswer).toHaveBeenCalledTimes(1)
  })

  it("test_useChatIntentExecutors_private_adapter_wires_edit_and_generate", () => {
    // The private-shaped adapter's runEditPrd/runGeneratePrd fire for edit and
    // generate envelopes. (AC2)
    const runEditPrd = vi.fn()
    const runGeneratePrd = vi.fn()
    const runAsk = vi.fn()
    const executors = useChatIntentExecutors({
      onEditPrd: (instruction) => runEditPrd(instruction),
      onGeneratePrd: (env) => runGeneratePrd(env.task),
      onAnswer: () => runAsk(),
    })

    // Private passes hasEditTarget: true unconditionally.
    dispatchChatIntent(
      envelope({ intent: "edit_prd", instruction: "tighten the scope" }),
      ctx({ hasEditTarget: true }),
      executors,
    )
    dispatchChatIntent(
      envelope({ intent: "generate_prd", task: "a fresh brief" }),
      ctx({ hasEditTarget: true }),
      executors,
    )

    expect(runEditPrd).toHaveBeenCalledWith("tighten the scope")
    expect(runGeneratePrd).toHaveBeenCalledWith("a fresh brief")
    expect(runAsk).not.toHaveBeenCalled()
  })

  it("test_useChatIntentExecutors_group_unimplemented_intent_answers", () => {
    // A group-shaped subset adapter (answer only) routes an unimplemented intent
    // to onAnswer — the surface falls through to a plain answer, no throw. (AC3)
    const runAnswer = vi.fn()
    const executors = useChatIntentExecutors({ onAnswer: () => runAnswer() })

    const result = dispatchChatIntent(
      envelope({ intent: "generate_prd", task: "unsupported here" }),
      ctx({ hasEditTarget: true }),
      executors,
    )

    expect(runAnswer).toHaveBeenCalledTimes(1)
    // generate_prd routes to onGeneratePrd (handled:true) — but the omitted
    // slot IS the onAnswer no-op, so nothing beyond the answer runs.
    expect(result).toEqual({ handled: true })
  })

  it("omitting onShareToSlack leaves the slot undefined so dispatch falls through", () => {
    // A surface with no share UI omits onShareToSlack; the hook must NOT
    // synthesize a slot for it, so dispatch's share_to_slack case falls through
    // to onAnswer (handled:false) rather than reporting a post that never
    // happened.
    const onAnswer = vi.fn()
    const executors = useChatIntentExecutors({ onAnswer })
    expect(executors.onShareToSlack).toBeUndefined()

    const result = dispatchChatIntent(
      envelope({ intent: "share_to_slack" }),
      ctx(),
      executors,
    )
    expect(result).toEqual({ handled: false })
    expect(onAnswer).toHaveBeenCalledTimes(1)
  })
})
