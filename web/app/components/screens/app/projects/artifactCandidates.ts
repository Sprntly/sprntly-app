// Shared by both project chat hosts (private + group): an artifact-list card
// reduced to the candidate shape the hosts' open-artifact callback takes.
// The project surfaces route chips AND cards to the same artifacts-modal
// destination, whose consumers read the candidate's `type`/ids. `type` is
// wider than `OpenArtifactKind` on purpose — the modal filters by the item's
// real kind.
import type { ArtifactItem, ChatArtifactItem, OpenArtifactCandidate } from "../../../../lib/api"

export function artifactItemAsCandidate(item: ChatArtifactItem): OpenArtifactCandidate {
  return {
    type: item.type as OpenArtifactCandidate["type"],
    id: item.id,
    title: item.title,
    status: item.status,
    prd_id: item.open.prd_id ?? null,
    brief_id: item.open.brief_id ?? null,
    insight_index: item.open.insight_index ?? null,
    brief_anchored: item.brief_anchored,
    week_label: item.source.week_label ?? null,
    conversation_id: item.source.conversation_id ?? null,
    conversation_title: item.source.conversation_title ?? null,
  }
}

/** The mirror of `artifactItemAsCandidate` above: an open-artifact candidate
 *  (already resolved to a real `prd`/`evidence` id server-side) reduced to
 *  the minimal `ArtifactItem` shape `ProjectArtifactDrawer` opens in-place —
 *  a direct field copy, no new lookup. Only called once
 *  `openArtifactDestination` has already confirmed the candidate carries a
 *  usable id for its type (a null-id / no-pair candidate never reaches this
 *  helper — that decision lives in `openArtifactDestination` itself).
 *
 *  `prdId` is the id `openArtifactDestination`'s `openPrd` adapter arg
 *  already resolved (`candidate.prd_id ?? candidate.id`) — required for the
 *  `prd` branch, ignored for `evidence` (an evidence candidate's own id is
 *  its `evidence_id`, not a PRD's). */
export function openArtifactCandidateAsItem(
  candidate: OpenArtifactCandidate,
  prdId?: number,
): Extract<ArtifactItem, { type: "prd" }> | Extract<ArtifactItem, { type: "evidence" }> {
  const briefId = candidate.brief_id ?? 0
  const insightIndex = candidate.insight_index
  const source = { brief_id: briefId, week_label: candidate.week_label, insight_index: insightIndex }
  if (candidate.type === "evidence") {
    return {
      type: "evidence",
      id: candidate.id,
      title: candidate.title,
      status: candidate.status,
      created_at: "",
      source,
      open: { brief_id: briefId, insight_index: insightIndex, evidence_id: candidate.id },
    }
  }
  return {
    type: "prd",
    id: prdId ?? candidate.id,
    title: candidate.title,
    status: candidate.status,
    created_at: "",
    source,
    open: { brief_id: briefId, insight_index: insightIndex, prd_id: prdId ?? candidate.id },
  }
}
