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

import type { ChatIntentEnvelope } from "../../../../lib/api"
import type { AskResponse } from "../../../../lib/api"
import type { ThreadTurn } from "../../../screens/app/ChatScreen"

/**
 * The per-surface configuration an action reads. The caller (main, private,
 * group) supplies the surface-specific bits; the action logic is identical.
 */
export interface ActionConfig {
  /** Place a fully-formed, SETTLED turn into THIS conversation — render + persist.
   *  Main → append to its target tab (or spawn one) + client/server persist; a
   *  project surface → the engine's turns + server-only persist. The action never
   *  learns which. */
  emitTurn(turn: ThreadTurn): void
}

/** Mint a turn id (crypto when available). */
function newTurnId(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
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
