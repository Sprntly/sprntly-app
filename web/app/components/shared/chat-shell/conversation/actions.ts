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

import type { AskResponse, ChatIntentEnvelope, PrdRecord } from "../../../../lib/api"
import type { ThreadTurn } from "../../../screens/app/ChatScreen"

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
   *  worker's reply, settle the turn, clear busy, and persist — the surface owns
   *  all of it (main → its tab + client persist; a project surface → the engine's
   *  turns + server persist). The action supplies only the async work + its reply.
   *  Optional: a sync-only action (list-artifacts) never needs it. */
  runActionTurn?(query: string, worker: () => Promise<AskResponse>): Promise<void>
  /** The surface's current artifact context — which PRD / evidence / ticket-set
   *  is "open" here (main a tab's, a project surface its drawer's). Actions read
   *  their edit/target from it. */
  contextIds?: { prdId?: number | null; evidenceId?: number | null; ticketSetId?: number | null }
  /** Apply a freshly-changed artifact to the surface's artifact view — a DISCRETE
   *  one-shot refresh (main → its ContentPanel; a project surface → its drawer).
   *  NOT the streaming generation preview sink (that lands with generation). */
  onArtifactUpdated?(update: { kind: "prd"; prdId: number; record: PrdRecord }): void
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
      return asReply(
        res.sections_changed.length
          ? `Updated ${res.sections_changed.join(", ")}${res.summary ? ` — ${res.summary}` : "."}`
          : res.summary ||
              "That didn't read as a change to the document, so I left the PRD as is — tell me what to update and I'll apply it.",
      )
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      return asReply(`I couldn't update the PRD — ${msg}. The document is unchanged; try rephrasing the edit.`)
    }
  })
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
