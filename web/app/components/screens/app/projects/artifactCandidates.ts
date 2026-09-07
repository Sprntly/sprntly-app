// Shared by both project chat hosts (private + group): an artifact-list card
// reduced to the candidate shape the hosts' open-artifact callback takes.
// The project surfaces route chips AND cards to the same artifacts-modal
// destination, whose consumers read the candidate's `type`/ids. `type` is
// wider than `OpenArtifactKind` on purpose — the modal filters by the item's
// real kind.
import { isProjectArtifactType, type ArtifactItem, type ChatArtifactItem, type OpenArtifactCandidate, type ProjectArtifactType } from "../../../../lib/api"

/** THE TWO VOCABULARIES, bridged in one place.
 *
 *  An open request speaks the USER's words — `OpenArtifactKind` is "tickets"
 *  and "document" — while every listing, badge map and browse filter speaks the
 *  STORAGE words, "ticket_set" and "custom_artifact". The backend bridges the
 *  same gap in `artifact_open._LISTING_TYPE`; this is the client half.
 *
 *  It also absorbs the smuggled kinds: `artifactItemAsCandidate` above casts a
 *  card's real listing type straight into the candidate union (deliberately —
 *  see the header comment), so a candidate reaching here can already be
 *  carrying "ticket_set" or "prototype". Both spellings map to the same browse
 *  filter, and anything unrecognised returns undefined, which browses the whole
 *  library rather than a filter nothing matches.
 */
export function candidateBrowseType(type: string): ProjectArtifactType | undefined {
  const storage = type === "tickets" ? "ticket_set" : type === "document" ? "custom_artifact" : type
  return isProjectArtifactType(storage as ArtifactItem["type"]) ? storage as ProjectArtifactType : undefined
}

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
