"use client"

/**
 * The shared intent→executor WIRING fed to `dispatchChatIntent`.
 *
 * `dispatchChatIntent` (the routing SWITCH) is already shared; this is the other
 * shared half — how each intent maps to an executor SLOT, the `onAnswer` no-op
 * default, and the subset-allowed contract. Every SURFACE injects its own flow
 * bodies through the `adapter`; a surface provides only the intents it
 * implements, and any slot the adapter omits falls to the surface's `onAnswer`
 * no-op (never `undefined()`), so a caller that wires fewer intents still routes
 * every envelope without throwing.
 *
 * Only the WIRING lives here — never the flow bodies. Main's `prdCommandFlow`
 * and the private engine's `runGeneratePrd` hit different state/endpoints by
 * design; unifying them is explicitly out of scope. The shared unit is which
 * intent lands on which slot, not what the slot does.
 *
 * `onShareToSlack` stays OPTIONAL and is passed through untouched (undefined
 * when the adapter omits it) so `dispatchChatIntent`'s `share_to_slack` case
 * falls THROUGH to `onAnswer` (`handled:false`) on a surface with no share UI,
 * rather than reporting a post that never happened.
 *
 * `onClarify` is deliberately NOT part of the adapter/hook: it is a turn-state
 * callback (persist/render), not a command-flow body, so callers that receive
 * the `clarify` intent (the private project chat) compose it ON TOP via object
 * spread — `{ ...useChatIntentExecutors(adapter), onClarify }` — keeping the
 * shared contract free of surface turn-state internals.
 *
 * Despite the `use` prefix (naming parity with the surface hooks it sits
 * beside) this is a PURE assembler — it calls no React hooks and is invoked
 * per-send inside the classify continuation, exactly where the executor object
 * used to be built inline.
 */
import type { ChatIntentExecutors } from "../../../lib/chat/dispatchChatIntent"
import type { ChatIntentExecutorAdapter } from "./types"

export function useChatIntentExecutors(
  adapter: ChatIntentExecutorAdapter,
): ChatIntentExecutors {
  // The surface's grounded-ask fall-through, and the target every omitted slot
  // resolves to (so an unimplemented intent answers rather than throwing).
  const onAnswer = adapter.onAnswer ?? (() => {})
  const fallToAnswer = () => onAnswer()

  return {
    onGenerateTickets: adapter.onGenerateTickets ?? fallToAnswer,
    onEditPrd: adapter.onEditPrd ?? fallToAnswer,
    onOpenArtifact: adapter.onOpenArtifact ?? fallToAnswer,
    onGeneratePrd: adapter.onGeneratePrd ?? fallToAnswer,
    onChangeTemplate: adapter.onChangeTemplate ?? fallToAnswer,
    onChangeTicketsTemplate: adapter.onChangeTicketsTemplate ?? fallToAnswer,
    onCreateArtifact: adapter.onCreateArtifact ?? fallToAnswer,
    onAssignTickets: adapter.onAssignTickets ?? fallToAnswer,
    onListArtifacts: adapter.onListArtifacts ?? fallToAnswer,
    // OPTIONAL: undefined when omitted → dispatch falls through to onAnswer.
    onShareToSlack: adapter.onShareToSlack,
    onCreateProject: adapter.onCreateProject,
    onAnswer,
  }
}
