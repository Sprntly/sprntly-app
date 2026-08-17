"use client"

/**
 * The shared after-node composer — the inline insight/PRD-questions card
 * placement rule lifted out of the main turn mapper so any surface can render
 * the same inline cards through the descriptor's `turnAfterNode(turn, idx)`
 * seam instead of the host re-implementing the placement.
 *
 * The card DATA is host-local and injected via the adapter (main computes
 * `insightCardNode`/`prdQuestionsNode` from its active-tab state; the anchor is
 * `inlinePrdAnchorIdx`). `extra` is a per-turn node the caller renders AFTER the
 * cards at the anchor and as the standalone after-node elsewhere (main: the
 * slack-share preview card, which stays mounted as the record of a post).
 *
 * The placement rule itself — cards ONLY at the in-chat-command anchor turn
 * (`inlinePrdCards && idx === inlinePrdAnchorIdx`), otherwise just the extra —
 * is the shared unit. When `inlinePrdCards` is false the cards render pinned
 * ABOVE the whole thread (the host's top-fallback), so they must NOT appear in
 * the after-node here.
 */
import type { ReactNode } from "react"

export interface TurnAfterNodeAdapter {
  /** The insight card node (host-local; main derives it from active-tab state).
   *  Null when the surface has no insight to show. */
  insightCardNode: ReactNode
  /** The PRD-input-questions card node (host-local). Null when absent. */
  prdQuestionsNode: ReactNode
  /** True when an in-chat command placed the cards inline BELOW the command
   *  turn (rather than pinned above the whole conversation). */
  inlinePrdCards: boolean
  /** Which turn index the inline cards anchor to (-1/null when none). */
  inlinePrdAnchorIdx: number | null
  /** A per-turn node rendered after the cards at the anchor, and as the
   *  standalone after-node on every other turn (main: the slack-share preview).
   *  Absent → nothing extra. */
  extra?: ReactNode
}

/** Compose one turn's after-node. `turn` is part of the seam contract (the
 *  descriptor exposes `turnAfterNode?(turn, idx)`) — main's placement is driven
 *  by `idx` + adapter, but a future surface may derive its cards from `turn`. */
export function turnAfterNode(
  _turn: unknown,
  idx: number,
  adapter: TurnAfterNodeAdapter,
): ReactNode {
  const { insightCardNode, prdQuestionsNode, inlinePrdCards, inlinePrdAnchorIdx, extra = null } = adapter
  return inlinePrdCards && idx === inlinePrdAnchorIdx ? (
    <>
      {insightCardNode}
      {prdQuestionsNode}
      {extra}
    </>
  ) : extra
}
