// Thin client for the artifact-share primitive's pre-auth/guest routes
// (/v1/artifact-share/*), extracted to its own module rather than appended to
// the already-large api.ts (kept file-scoped for this workstream's review).
import { api, type EvidenceRecord, type GeneratedStory, type PrdRecord } from "./api"

export type ArtifactShareMetadata = {
  artifact_type: string
  title: string
  sharer_name: string
  owning_company_name: string
  required_email_domain: string | null
}

export type ArtifactShareResolveOutcome =
  | {
      outcome: "guest_view"
      artifact_type: "prd"
      artifact_id: number
      owning_company_name: string
      sharer_name: string
    }
  | { outcome: "blocked"; reason: "different_company" | "domain_mismatch" }

export type ArtifactShareJoinResult = {
  sharer_name: string
  owning_company_name: string
  workspace_id: string
}

/** GET .../content's shape: the raw rendered PRD row, its evidence doc (when
 *  one exists for this PRD's theme — null otherwise), and the persisted
 *  ticket set (null when tickets were never generated). Mirrors the shapes
 *  `prdApi.get`/`evidenceApi.get`/`storiesApi.getForPrd` already return —
 *  GuestArtifactViewer maps this with the SAME adapters those callers use
 *  (markdownToPrdState / markdownToEvidenceState), never a parallel parser. */
export type ArtifactShareContentResponse = {
  prd: PrdRecord
  evidence: EvidenceRecord | null
  tickets: { stories: GeneratedStory[] } | null
}

export const artifactShareApi = {
  getMetadata: (token: string) =>
    api.get<ArtifactShareMetadata>(`/v1/artifact-share/${encodeURIComponent(token)}`),
  resolve: (token: string) =>
    api.get<ArtifactShareResolveOutcome>(
      `/v1/artifact-share/${encodeURIComponent(token)}/resolve`,
    ),
  join: (token: string) =>
    api.post<ArtifactShareJoinResult>(
      `/v1/artifact-share/${encodeURIComponent(token)}/join`,
      {},
    ),
  content: (token: string) =>
    api.get<ArtifactShareContentResponse>(
      `/v1/artifact-share/${encodeURIComponent(token)}/content`,
    ),
}

/** `resolve` wrapped to fail closed-to-undefined on any error (network/4xx/5xx)
 *  rather than throw — postLoginPath() treats undefined as "fall open to
 *  onboarding", per its existing best-effort pattern for tryAcceptInvite(). */
export async function resolveArtifactShare(
  token: string,
): Promise<ArtifactShareResolveOutcome | undefined> {
  try {
    return await artifactShareApi.resolve(token)
  } catch {
    return undefined
  }
}
