// Thin client for the bare-link guest-access primitive (/v1/prd-access/*) —
// the token-less sibling of artifactShareApi.ts. Same shapes, keyed by a
// PRD's opaque `public_id` (UUID) instead of a share token — NEVER the raw
// sequential `prds.id`, which a URL would otherwise expose for blind
// enumeration once gated only by company-domain match. No `sharer_name`/
// mint (there is no share row here — every prd is reachable this way).
import { api, type EvidenceRecord, type GeneratedStory, type PrdRecord } from "./api"

export type PrdAccessMetadata = {
  title: string
  owning_company_name: string
  required_email_domain: string | null
}

export type PrdAccessResolveOutcome =
  | { outcome: "guest_view"; artifact_id: number; artifact_type: "prd"; owning_company_name: string }
  | { outcome: "blocked"; reason: "different_company" }

export type PrdAccessJoinResult = {
  owning_company_name: string
  workspace_id: string
}

export type PrdAccessContentResponse = {
  prd: PrdRecord
  evidence: EvidenceRecord | null
  tickets: { stories: GeneratedStory[] } | null
}

export const prdAccessApi = {
  getMetadata: (publicId: string) =>
    api.get<PrdAccessMetadata>(`/v1/prd-access/${encodeURIComponent(publicId)}`),
  resolve: (publicId: string) =>
    api.get<PrdAccessResolveOutcome>(
      `/v1/prd-access/${encodeURIComponent(publicId)}/resolve`,
    ),
  join: (publicId: string) =>
    api.post<PrdAccessJoinResult>(
      `/v1/prd-access/${encodeURIComponent(publicId)}/join`,
      {},
    ),
  autoJoinCompany: (publicId: string) =>
    api.post<{ joined_company_id: string | null }>(
      `/v1/prd-access/${encodeURIComponent(publicId)}/auto-join-company`,
      {},
    ),
  content: (publicId: string) =>
    api.get<PrdAccessContentResponse>(
      `/v1/prd-access/${encodeURIComponent(publicId)}/content`,
    ),
}

/** Same fail-closed-to-undefined wrapping as artifactShareApi's
 *  resolveArtifactShare — see that function's docstring. */
export async function resolvePrdAccess(
  publicId: string,
): Promise<PrdAccessResolveOutcome | undefined> {
  try {
    return await prdAccessApi.resolve(publicId)
  } catch {
    return undefined
  }
}

/** Same fail-closed-to-undefined wrapping as artifactShareApi's
 *  tryAutoJoinCompanyOnDomainMatch — see that function's docstring. */
export async function tryAutoJoinCompanyOnDomainMatchForPrd(
  publicId: string,
): Promise<string | null | undefined> {
  try {
    const result = await prdAccessApi.autoJoinCompany(publicId)
    return result.joined_company_id
  } catch {
    return undefined
  }
}
