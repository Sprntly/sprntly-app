import { type ChatIntentEnvelope, type SlackShareTargetRef } from "../../../../lib/api"

/** The surface's own share context — the ids of the documents currently in
 *  front of the user, in the stacking order the panel presents them (PRD, then
 *  ticket set, then report). Every surface builds this from its own state (a
 *  tab, a conversation's meta, the shared content store) and hands it in; the
 *  resolver itself never reads a store, so it stays a pure mapping of
 *  context → reference and cannot disagree across surfaces. */
export type ShareRefContext = {
  prdId: number | null
  ticketSetId: number | null
  reportId: number | null
}

/** The reference a share posts back — the client's OWN context first.
 *
 *  "Share this PRD" means the document in front of the user, so an explicit
 *  id from the surface's context beats the planner's reading of the phrase
 *  every time; the phrase is the fallback for "share the checkout PRD", where
 *  there is no context to prefer. The backend applies the same precedence, so
 *  the two cannot disagree about which document was meant.
 *
 *  Branch order (shared by every surface): named tickets → named report →
 *  named prd → unnamed stack (prd, then ticket set, then report) → title
 *  fallback. */
export function resolveShareRef(
  envelope: ChatIntentEnvelope,
  ctx: ShareRefContext,
): SlackShareTargetRef {
  const prdId = envelope.prd_id ?? ctx.prdId ?? null
  // A KIND the user named wins over the surface's default document: "share the
  // tickets" in a thread that has both a PRD and a set means the set, and
  // falling through to prd_id there would share the wrong artifact under a
  // name the user did give us.
  const named = (envelope.artifact_type || "").toLowerCase()
  if (named === "tickets" && ctx.ticketSetId) {
    return { ticket_set_id: ctx.ticketSetId }
  }
  if (named === "report" && ctx.reportId) {
    return { report_id: ctx.reportId }
  }
  if (named === "prd" && prdId) {
    return { prd_id: prdId }
  }
  // No subject named ("share this on slack") → whatever is in front of them,
  // in the order the panel stacks it.
  if (!envelope.artifact_query) {
    if (prdId) return { prd_id: prdId }
    if (ctx.ticketSetId) return { ticket_set_id: ctx.ticketSetId }
    if (ctx.reportId) return { report_id: ctx.reportId }
  }
  // A named subject with no matching context — "share the checkout PRD" —
  // resolves by title against the caller's own library, server-side.
  return {
    artifact_type: envelope.artifact_type ?? null,
    artifact_query: envelope.artifact_query ?? null,
  }
}
