// Shared by both project chat hosts (private + group): an artifact-list card
// reduced to the candidate shape the hosts' open-artifact callback takes.
// The project surfaces route chips AND cards to the same artifacts-modal
// destination, whose consumers read the candidate's `type`/ids. `type` is
// wider than `OpenArtifactKind` on purpose — the modal filters by the item's
// real kind.
import type { ChatArtifactItem, OpenArtifactCandidate } from "../../../../lib/api"

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
