// dispatchChatIntent — the shared client intent→executor switch (AD-P13a).
//
// Lifted, ONE TIME, from `ChatScreen`'s own reducer (the block that used to
// sit inline at `ChatScreen.tsx:3824-3908`) so BOTH `ChatScreen` and the
// project individual chat can classify a message via `chatIntentApi.resolve`
// and route the SAME way — true single-source dispatch, zero drift between
// the main chat and the project chats as the main chat gains intents.
//
// This is a pure routing primitive: it never touches component state, never
// calls an API itself, and knows nothing about tabs, panels, or attachments.
// Each caller supplies its own executor callbacks (today's inline flows —
// `ChatScreen` passes its tab-mutating closures, the project chat passes
// project-scoped ones) and its own pre-resolved `ctx` (the caller decides
// what "a resolvable edit target" means for ITS surface — see
// `DispatchChatIntentContext.hasEditTarget` below).
//
// `generate_prototype` has no executor anywhere: neither `ChatScreen` nor any
// project chat has a chat-driven prototype flow (parity with the pre-
// extraction ladder, which never intercepted prototype phrasings) — it falls
// through to `onAnswer`, same as an ordinary `answer`/low-confidence/unknown
// envelope.
import type { ChatIntentEnvelope } from "../api"

/** Per-dispatch context the CALLER resolves before invoking
 *  `dispatchChatIntent` — the parts of "is there a resolvable edit target"
 *  that are specific to the caller's own surface, not to the routing rule
 *  itself.
 *
 *  `ChatScreen` sets `hasEditTarget` from ITS OWN doc/tab state (no attached
 *  document, an active tab, and a target prd id from the envelope or the
 *  tab) and passes the resolved numeric id through `editTargetPrdId` so its
 *  executor can call the SAME flow it always has
 *  (`prdChatEditFlow(instruction, tabId, prdId)`).
 *
 *  The project chat sets `hasEditTarget: true` UNCONDITIONALLY and
 *  `editTargetPrdId: null` — the project route resolves its OWN target
 *  SERVER-side (`_resolve_prd_id` over the project's own PRDs, never a
 *  client-supplied id) and degrades to a no-edit reply itself on 0/ambiguous
 *  PRDs, so there is nothing to pre-resolve on the client for that surface. */
export interface DispatchChatIntentContext {
  hasEditTarget: boolean
  editTargetPrdId: number | null
  /** change_tickets_template's resolved target — WHOSE tickets switch format.
   *  Caller-resolved like `editTargetPrdId`, but its own field rather than a
   *  reuse of it, because the tickets target is not always a PRD: a thread
   *  that generated a STANDALONE set switches that set (it is what "the
   *  tickets" means on screen), and only a set-less thread falls back to the
   *  tab's PRD. `ChatScreen` resolves set-over-PRD from its own tab state;
   *  the project chat passes null (no tickets surface there) so the intent
   *  falls through to its grounded ask. */
  ticketsTarget: { ticketSetId: number } | { prdId: number } | null
}

/** The executor callbacks a caller injects — its own inline flows, unchanged.
 *  Every callback is fire-and-forget from `dispatchChatIntent`'s point of
 *  view: it manages its own component-local state (busy indicators, turn
 *  rendering, tab mutation) exactly as it did before the extraction. */
export interface ChatIntentExecutors {
  onEditPrd: (instruction: string, prdId: number | null) => void
  /** Carries the doc-vs-existing-PRD-vs-standalone decision to the executor
   *  (never made here) — the caller's own inline state (an attached file, an
   *  already-open PRD) decides which of its flows to run. */
  onGenerateTickets: (envelope: ChatIntentEnvelope) => void
  onGeneratePrd: (envelope: ChatIntentEnvelope) => void
  onOpenArtifact: (open: NonNullable<ChatIntentEnvelope["open"]>) => void
  /** change_prd_template: switch the target PRD into a different uploaded
   *  format in place. `prdId` mirrors `onEditPrd`'s own resolved-target
   *  parameter — the SAME `ctx.editTargetPrdId` a caller resolved for
   *  edit_prd, reused here rather than a second per-caller resolution path,
   *  since both intents target "the tab's PRD or the conversation's". */
  onChangeTemplate: (envelope: ChatIntentEnvelope, prdId: number | null) => void
  /** change_tickets_template: switch the thread's TICKETS into a different
   *  uploaded ticket format in place (POST /v1/stories/change-template).
   *  `target` is `ctx.ticketsTarget` passed through — see that field for why
   *  it is its own resolution rather than a reuse of `editTargetPrdId`. */
  onChangeTicketsTemplate: (
    envelope: ChatIntentEnvelope,
    target: { ticketSetId: number } | { prdId: number },
  ) => void
  /** create_artifact: write a document of any kind into the shared library.
   *  Needs no target PRD — a leadership update stands alone — so unlike
   *  edit_prd/change_prd_template this has no guard beyond the intent match. */
  onCreateArtifact: (envelope: ChatIntentEnvelope) => void
  /** assign_tickets: change who OWNS one or more tickets. `prdId` mirrors
   *  `onEditPrd`'s resolved-target parameter — the SAME `ctx.editTargetPrdId`
   *  a caller resolved for edit_prd, reused here rather than a second
   *  per-caller resolution path, since an assignment is targeted exactly like
   *  an edit ("the tab's PRD or the conversation's" owns the tickets). */
  onAssignTickets: (instruction: string, prdId: number | null) => void
  /** list_artifacts: show what the user has CREATED as clickable items. The
   *  rows ride the envelope (`artifact_list`, resolved server-side); an
   *  EMPTY array still routes here — "you haven't made any yet" is the
   *  listing's own honest answer, not a fall-through. Only an ABSENT array
   *  (an older backend that doesn't attach rows) falls back to `onAnswer`. */
  onListArtifacts: (envelope: ChatIntentEnvelope) => void
  /** share_to_slack: post an artifact into the company's Slack. Unguarded
   *  here on purpose, unlike edit_prd and friends — the executor's FIRST step
   *  is a preview call that resolves the target and the channel server-side,
   *  and its answers to "which document?" and "which channel?" are questions
   *  the user can answer in place. A client-side guard would turn both into a
   *  grounded ask that has to re-derive what the preview already knows.
   *
   *  OPTIONAL, like `onClarify`: a surface with no share UI (the project chat)
   *  omits it and the intent falls through to its ask path, rather than every
   *  caller being required to implement a flow it does not have. */
  onShareToSlack?: (envelope: ChatIntentEnvelope) => void
  /** `clarify` — the private project chat's classify route asks which PRD
   *  is meant (2+ PRDs, an unresolved edit target) instead of silently
   *  answering. OPTIONAL: `clarify` is only ever emitted by the project-
   *  scoped classify route (never the shared `/v1/chat/intent` route main
   *  chat runs), so a caller that omits this executor — main chat, today —
   *  falls through to `onAnswer` rather than being required to implement a
   *  render path it can never reach. */
  onClarify?: (clarification: string, prdOptions: { id: number; title: string }[]) => void
  /** The grounded-ask fall-through — called whenever no structured intent's
   *  guard held (an unresolvable edit_prd/change_prd_template/assign_tickets
   *  target, a lookup-less open_artifact, or `answer`/low-confidence/unknown/
   *  `generate_prototype`). */
  onAnswer: () => void
}

export type DispatchChatIntentResult = { handled: true } | { handled: false }

/** Route one action envelope to the matching executor — the intent→executor
 *  SWITCH itself, matching `ChatScreen.tsx:3824-3908`'s guards exactly:
 *
 *  - `edit_prd` → `onEditPrd` only when `ctx.hasEditTarget` AND a non-empty
 *    `envelope.instruction` — else `onAnswer` (`{handled: false}`).
 *  - `open_artifact` → `onOpenArtifact` only when `envelope.open` is present
 *    — else `onAnswer`.
 *  - `generate_tickets` / `generate_prd` / `create_artifact` → the matching
 *    executor unconditionally (no guard beyond the intent match) — the
 *    doc-vs-existing-PRD-vs-standalone decision is the EXECUTOR's, not this
 *    switch's.
 *  - `change_prd_template` → `onChangeTemplate` only when `ctx.hasEditTarget`
 *    AND `envelope.artifact_template_id` — else `onAnswer`. Same target guard
 *    as `edit_prd` (a format switch is targeted exactly like an edit).
 *  - `change_tickets_template` → `onChangeTicketsTemplate` only when
 *    `ctx.ticketsTarget` AND `envelope.artifact_template_id` — else
 *    `onAnswer`. Its OWN target field, not `hasEditTarget`: the tickets
 *    target may be a standalone set rather than a PRD.
 *  - `assign_tickets` → `onAssignTickets` only when `ctx.hasEditTarget` AND a
 *    non-empty `envelope.instruction` — else `onAnswer`. Same target guard as
 *    `edit_prd` (an assignment is targeted exactly like an edit).
 *  - `list_artifacts` → `onListArtifacts` only when `envelope.artifact_list`
 *    is PRESENT (empty included — see the executor's note) — else `onAnswer`
 *    (an older backend attached no rows).
 *  - `share_to_slack` → `onShareToSlack` when the caller supplied one — else
 *    `onAnswer`. No target/channel guard: the executor's preview call resolves
 *    both server-side and asks the user about whichever it could not settle.
 *  - anything else (`answer`, low confidence, unknown, `generate_prototype`)
 *    → `onAnswer`.
 *
 *  Returns `{handled: true}` when a structured executor ran, `{handled:
 *  false}` whenever `onAnswer` ran instead — callers that keep their own
 *  grounded-ask code inline (rather than folding it into `onAnswer`) use the
 *  return value to decide whether to run it. */
export function dispatchChatIntent(
  envelope: ChatIntentEnvelope,
  ctx: DispatchChatIntentContext,
  executors: ChatIntentExecutors,
): DispatchChatIntentResult {
  switch (envelope.intent) {
    case "generate_tickets":
      executors.onGenerateTickets(envelope)
      return { handled: true }

    case "edit_prd":
      if (ctx.hasEditTarget && envelope.instruction) {
        executors.onEditPrd(envelope.instruction, ctx.editTargetPrdId)
        return { handled: true }
      }
      executors.onAnswer()
      return { handled: false }

    case "open_artifact":
      if (envelope.open) {
        executors.onOpenArtifact(envelope.open)
        return { handled: true }
      }
      executors.onAnswer()
      return { handled: false }

    case "generate_prd":
      executors.onGeneratePrd(envelope)
      return { handled: true }

    case "change_prd_template":
      if (ctx.hasEditTarget && envelope.artifact_template_id) {
        executors.onChangeTemplate(envelope, ctx.editTargetPrdId)
        return { handled: true }
      }
      executors.onAnswer()
      return { handled: false }

    case "change_tickets_template":
      // Guarded like change_prd_template (a format id must be present — the
      // backend downgrades format-less switches, so this is the older-backend
      // edge), but on the TICKETS target the caller resolved: a standalone
      // set, a PRD's tickets, or nothing → grounded ask.
      if (ctx.ticketsTarget && envelope.artifact_template_id) {
        executors.onChangeTicketsTemplate(envelope, ctx.ticketsTarget)
        return { handled: true }
      }
      executors.onAnswer()
      return { handled: false }

    case "create_artifact":
      executors.onCreateArtifact(envelope)
      return { handled: true }

    case "assign_tickets":
      if (ctx.hasEditTarget && envelope.instruction) {
        executors.onAssignTickets(envelope.instruction, ctx.editTargetPrdId)
        return { handled: true }
      }
      executors.onAnswer()
      return { handled: false }

    case "list_artifacts":
      // Array.isArray, not truthiness: [] is a real listing whose honest
      // rendering is "you haven't made any yet" — only an ABSENT field (an
      // older backend) falls through.
      if (Array.isArray(envelope.artifact_list)) {
        executors.onListArtifacts(envelope)
        return { handled: true }
      }
      executors.onAnswer()
      return { handled: false }

    case "share_to_slack":
      if (executors.onShareToSlack) {
        executors.onShareToSlack(envelope)
        return { handled: true }
      }
      // No share UI on this surface — the grounded ask at least answers the
      // message, and crucially does NOT claim anything was posted.
      executors.onAnswer()
      return { handled: false }

    case "clarify":
      if (executors.onClarify && envelope.clarification) {
        executors.onClarify(envelope.clarification, envelope.prd_options ?? [])
        return { handled: true }
      }
      // No onClarify executor supplied (main chat never receives this
      // intent in the first place) or no clarification text — fall through
      // to the grounded ask, same as any other unhandled envelope.
      executors.onAnswer()
      return { handled: false }

    default:
      // answer / low_confidence / unknown / generate_prototype — moot
      // everywhere (parity with the pre-extraction ladder).
      executors.onAnswer()
      return { handled: false }
  }
}
