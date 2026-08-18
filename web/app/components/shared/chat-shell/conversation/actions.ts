"use client"

/**
 * The shared chat-ACTION layer — the command bodies a resolved intent dispatches
 * to (list artifacts, generate a PRD, share to Slack, …), written ONCE and
 * CONFIGURED by the caller. Every surface (main, project private, project group)
 * runs the SAME action; the only per-surface differences flow through the
 * `ActionConfig` a caller supplies.
 *
 * THE HARD RULE (the reason this layer exists): an action body NEVER branches on
 * the surface (`if (surface === "main")` / `if (isMain)`). If a surface needs to
 * differ, that difference is a field on `ActionConfig`, not a branch here. Adding
 * a new action to this layer therefore works on every surface automatically — no
 * per-surface re-implementation, no fork to keep in sync.
 *
 * `ActionConfig` is kept deliberately small and stable. It starts with the one
 * primitive the lowest-coupling action needs (`emitTurn`) and grows only as
 * higher-coupling actions land (a generation preview sink, current-context ids) —
 * each addition a new optional field, never a surface branch.
 */

import {
  slackShareApi,
  type AskResponse,
  type ChatIntentEnvelope,
  type PrdRecord,
  type SlackShareTargetRef,
} from "../../../../lib/api"
import { slackShareQuestionFor, type SlackShareQuestion } from "../../../../lib/chat/slackShareQuestion"
import type { ThreadTurn } from "../../../screens/app/ChatScreen"

/** The fields an async action settles onto its turn — always a reply, plus any
 *  turn extras (a Slack preview card, artifact cards). */
export type ActionTurnPatch = Partial<ThreadTurn> & { reply: AskResponse }

/**
 * The per-surface configuration an action reads. The caller (main, private,
 * group) supplies the surface-specific bits; the action logic is identical.
 *
 * Grows by field as higher-coupling actions land — each a new primitive, NEVER a
 * surface branch. So far:
 *  - `emitTurn`        — synchronous settled turn (list-artifacts).
 *  - `runActionTurn`   — the async command-turn lifecycle (edit, Slack, generate…).
 *  - `contextIds`      — the surface's current artifact context (which PRD is open).
 *  - `onArtifactUpdated` — discrete "the document changed, show the new one".
 * (The STREAMING generation preview sink lands with generation, later.)
 */
export interface ActionConfig {
  /** Place a fully-formed, SETTLED turn into THIS conversation — render + persist.
   *  Main → append to its target tab (or spawn one) + client/server persist; a
   *  project surface → the engine's turns + server-only persist. The action never
   *  learns which. */
  emitTurn(turn: ThreadTurn): void
  /** Run an ASYNC command turn: seed an optimistic turn, mark busy, await the
   *  worker's turn-patch (reply + any extras), settle the turn, clear busy, and
   *  persist — the surface owns all of it (main → its tab + client persist; a
   *  project surface → the engine's turns + server persist). Returns the settled
   *  turn id so an action can drive a follow-up (Slack's channel question).
   *  Optional: a sync-only action (list-artifacts) never needs it. */
  runActionTurn?(query: string, worker: () => Promise<ActionTurnPatch>): Promise<{ turnId: string }>
  /** The surface's current artifact context — which PRD / evidence / ticket-set
   *  is "open" here (main a tab's, a project surface its drawer's). Actions read
   *  their edit/target from it. */
  contextIds?: { prdId?: number | null; evidenceId?: number | null; ticketSetId?: number | null }
  /** Apply a freshly-changed artifact to the surface's artifact view — a DISCRETE
   *  one-shot refresh (main → its ContentPanel; a project surface → its drawer).
   *  NOT the streaming generation preview sink (that lands with generation). */
  onArtifactUpdated?(update: { kind: "prd"; prdId: number; record: PrdRecord }): void
  /** Resolve which artifact a Slack share posts back — the surface's own context
   *  first (main a tab's open document, a project surface its drawer's). */
  resolveShareRef?(envelope: ChatIntentEnvelope): SlackShareTargetRef
  /** Whether this surface can run the interactive channel/document PICKER (main's
   *  dock QuestionPopup). When false, a preview that needs a pick settles an
   *  honest limited note instead of a card with a dead control. */
  canPickChannel?: boolean
  /** Ask the user which channel/document (main → set the dock question). Only
   *  reached when `canPickChannel` is true. */
  onNeedsChannel?(turnId: string, question: SlackShareQuestion): void
}

/** Mint a turn id (crypto when available). */
function newTurnId(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
}

/** A plain agent reply from a prose answer (a command's acknowledgement). */
function asReply(answer: string): AskResponse {
  return {
    answer,
    sources: [],
    follow_ups: [],
    key_points: [],
    citations: [],
    confidence: 1,
    unanswered: "",
  } as AskResponse
}

/**
 * "Make this PRD shorter" / "add a risks section" — apply a scoped chat-edit to
 * the PRD open on this surface (`config.contextIds.prdId`), acknowledge what
 * changed in the thread, and hand the fresh document to the surface's artifact
 * view. Extracted verbatim from main's inline `prdChatEditFlow`; the only
 * surface-specific bits — the async-turn lifecycle, which PRD is open, and where
 * the updated document renders — are all `config`.
 */
export async function runEditPrdAction(instruction: string, config: ActionConfig): Promise<void> {
  const prdId = config.contextIds?.prdId ?? null
  // The dispatch guard only routes here with an edit target; a null is a safe
  // no-op rather than an unscoped edit.
  if (prdId == null || !config.runActionTurn) return
  await config.runActionTurn(instruction, async () => {
    try {
      const { prdApi } = await import("../../../../lib/api")
      const res = await prdApi.chatEdit(prdId, instruction)
      if (res.sections_changed.length) {
        // The scoped edit produced a fresh document — hand it to the surface's
        // artifact view (main's panel / a project drawer).
        config.onArtifactUpdated?.({ kind: "prd", prdId, record: res.prd })
      }
      return {
        reply: asReply(
          res.sections_changed.length
            ? `Updated ${res.sections_changed.join(", ")}${res.summary ? ` — ${res.summary}` : "."}`
            : res.summary ||
                "That didn't read as a change to the document, so I left the PRD as is — tell me what to update and I'll apply it.",
        ),
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      return { reply: asReply(`I couldn't update the PRD — ${msg}. The document is unchanged; try rephrasing the edit.`) }
    }
  })
}

/**
 * "Share this PRD on Slack" — resolve the document + channel and put a PREVIEW
 * card on screen; the post itself waits for the user's Send in the card. Lifted
 * from main's inline `shareToSlackFlow`. Surface-specific bits are all config:
 * which document (`resolveShareRef`), the async-turn lifecycle (`runActionTurn`),
 * and whether this surface can run the channel/document PICKER (`canPickChannel`
 * / `onNeedsChannel`). A surface that can't pick settles an honest limited note
 * rather than a card with a dead Send.
 */
export async function runShareToSlackAction(
  query: string,
  envelope: ChatIntentEnvelope,
  config: ActionConfig,
): Promise<void> {
  if (!config.runActionTurn || !config.resolveShareRef) return
  const ref = config.resolveShareRef(envelope)
  // The pick this preview still needs (if any), read after the turn settles so
  // the surface with a picker can raise it against the settled turn.
  let question: SlackShareQuestion | null = null
  const { turnId } = await config.runActionTurn(query, async () => {
    try {
      const preview = await slackShareApi.preview(ref, {
        channel: envelope.share_channel ?? null,
        note: envelope.share_note ?? null,
      })
      const q = slackShareQuestionFor(preview)
      // A preview that still needs a pick, on a surface that CAN'T pick, is an
      // honest limited note — never a card with a dead Send/picker.
      if (q && !config.canPickChannel) {
        return {
          reply: asReply(
            "I found the document, but choosing a Slack channel isn't available in this chat yet — share it from the main chat and I'll post it there.",
          ),
        }
      }
      question = q
      // The prose is deliberately short and NEVER claims a post happened — the
      // card below it is the whole interaction.
      const lead =
        preview.status === "ready"
          ? "Here's what I'll post — have a look before I send it."
          : preview.status === "needs_channel"
            ? "Almost — I just need to know where this should go."
            : preview.status === "blocked"
              ? "I can't post there yet."
              : preview.status === "unsupported_type"
                ? "That one can't be shared to Slack."
                : "Which document did you mean?"
      return { reply: asReply(lead), slackShare: { ref, preview } }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      return {
        reply: asReply(`I couldn't set that share up — ${msg}. Nothing was posted to Slack.`),
      }
    }
  })
  if (question && config.canPickChannel) config.onNeedsChannel?.(turnId, question)
}

const KIND_NOUN: Record<string, [string, string]> = {
  prd: ["PRD", "PRDs"],
  evidence: ["evidence document", "evidence documents"],
  prototype: ["prototype", "prototypes"],
  report: ["report", "reports"],
  ticket_set: ["ticket set", "ticket sets"],
  custom_artifact: ["document", "documents"],
}

/**
 * "What are my PRDs?" — the rows ride the resolved envelope; render them as one
 * settled turn carrying a click-to-open `artifactList` (the surface's transcript
 * routes a card click to its own drawer/panel — a render concern, not this
 * action's). A count ask leads with the server-computed numbers.
 *
 * Extracted verbatim from the main screen's `listArtifactsFlow`; the ONLY thing
 * that was surface-specific — where the turn lands and how it persists — is now
 * `config.emitTurn`.
 */
export function runListArtifactsAction(
  seedQuery: string,
  envelope: ChatIntentEnvelope,
  config: ActionConfig,
): void {
  const items = envelope.artifact_list ?? []
  const kind = envelope.list_kind && envelope.list_kind !== "all" ? envelope.list_kind : null
  const [one, many] = kind ? KIND_NOUN[kind] ?? ["artifact", "artifacts"] : ["artifact", "artifacts"]
  // A HOW-MANY ask leads with the NUMBERS — computed server-side over the whole
  // library, never counted off the capped card list.
  const counts = envelope.list_mode === "count" ? envelope.artifact_counts : null
  const answer = counts
    ? [
        `You've created ${counts.today} ${counts.today === 1 ? one : many} today and ${counts.yesterday} yesterday`,
        counts.total > counts.today + counts.yesterday ? ` — ${counts.total} in total.` : ".",
        items.length ? ` The newest are below — click one to open it with its chat.` : "",
      ].join("")
    : items.length === 0
      ? `You haven't created any ${many} yet — generate one from a chat or the Top Insights brief and it'll show up here.`
      : items.length === 1
        ? `Here's your most recent ${one} — click it to open it with its chat.`
        : `Here are your ${items.length} newest ${many} — click one to open it with its chat.`
  const reply: AskResponse = {
    answer,
    sources: [],
    follow_ups: [],
    key_points: [],
    citations: [],
    confidence: 1,
    unanswered: "",
  } as AskResponse
  const turn: ThreadTurn = {
    id: newTurnId(),
    query: seedQuery,
    reply,
    ...(items.length ? { artifactList: items } : {}),
  }
  config.emitTurn(turn)
}
