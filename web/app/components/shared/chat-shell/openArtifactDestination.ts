"use client"

/**
 * The shared open-artifact DESTINATION decision — the item→where-it-opens rule
 * lifted out of the main chat host so the decision (evidence-vs-PRD branch, the
 * resume-the-originating-conversation-first rule, reuse-by-prd-id, and the
 * null-id guards) lives in one place instead of only inside `ChatScreen`.
 *
 * The DECISION is shared; the TERMINAL action is the surface's `adapter`. Main
 * passes a PANEL adapter (open a PRD/evidence tab beside the chat, or resume the
 * conversation via localStorage). The project surfaces open the artifacts MODAL
 * instead — a SANCTIONED divergence already named in `PARITY_OPT_OUTS`
 * (`open.destination`), so they open the modal directly and do NOT route their
 * opens through this panel-shaped decision. This module exists so main's fork is
 * gone and the decision is testable in isolation.
 *
 * Returns `true` when a terminal action ran, `false` when the candidate had no
 * usable destination (a missing evidence pair or a null prd id) — the caller
 * treats `false` as a NOT-FOUND and says so rather than opening an empty panel.
 */
import type { OpenArtifactCandidate } from "../../../lib/api"

export interface OpenArtifactDestinationAdapter {
  /** Open an evidence candidate at its destination. Called only after the
   *  (brief_id, insight_index) pair is confirmed present. */
  openEvidence: (candidate: OpenArtifactCandidate, seedQuery?: string) => boolean
  /** Resume the conversation that produced this PRD (main: stash + checkResume).
   *  Return true when the resume took over; false to fall through to `openPrd`
   *  (e.g. storage unavailable, or a surface with no resume path). */
  resumeConversation: (info: {
    conversationId: number
    conversationTitle: string
    prdId: number
  }) => boolean
  /** Open a PRD by id at its destination (main: reuse-by-prd-id tab open). */
  openPrd: (candidate: OpenArtifactCandidate, prdId: number, seedQuery?: string) => boolean
}

export function openArtifactDestination(
  candidate: OpenArtifactCandidate,
  adapter: OpenArtifactDestinationAdapter,
  seedQuery?: string,
): boolean {
  if (candidate.type === "evidence") {
    // Needs a real (brief, insight) pair to open — an evidence card without one
    // has no destination.
    if (candidate.brief_id == null || candidate.insight_index == null) return false
    return adapter.openEvidence(candidate, seedQuery)
  }
  const prdId = candidate.prd_id ?? candidate.id
  if (prdId == null) return false
  // The PRD's own THREAD outranks a panel-beside-this-chat open: when the
  // conversation that produced the document survives (both id AND title — a
  // title-less id means the chat row is gone), "open the PRD" means going back
  // to that chat. Uploaded/brief-generated PRDs carry neither, so they keep the
  // panel-only open — never a fake history.
  if (candidate.conversation_id != null && candidate.conversation_title) {
    if (
      adapter.resumeConversation({
        conversationId: candidate.conversation_id,
        conversationTitle: candidate.conversation_title,
        prdId,
      })
    ) {
      return true
    }
  }
  return adapter.openPrd(candidate, prdId, seedQuery)
}
