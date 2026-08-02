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
      /** The PRD's opaque, unguessable external identifier — what a
       *  redirect/copyable link should use instead of artifact_id. */
      public_id: string | null
      owning_company_name: string
      sharer_name: string
    }
  // "domain_mismatch" is retired as a /resolve reason (revision 2026-08-02):
  // it now only ever originates from the sign-up form's client-side gate
  // (validateShareDomainEmail), which never calls /resolve at all — a
  // zero-membership caller is always "different_company" here regardless of
  // domain (see the backend's resolve_share_access docstring).
  | { outcome: "blocked"; reason: "different_company" }

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
  /** Mint a fresh share token for an artifact (e.g. a PRD) the caller can see.
   *  Authed workspace route — no admin-only restriction. */
  mint: (artifactType: string, artifactId: number) =>
    api.post<{ token: string }>("/v1/artifact-share", {
      artifact_type: artifactType,
      artifact_id: artifactId,
    }),
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
  /** One-shot, signup-time-only: grants COMPANY (never workspace)
   *  membership on a matching email domain. Always 200 — a null
   *  `joined_company_id` means no-op (no match / already a member / bad
   *  token), never an error. See postLoginPath()'s guest branch, the only
   *  caller. */
  autoJoinCompany: (token: string) =>
    api.post<{ joined_company_id: string | null }>(
      `/v1/artifact-share/${encodeURIComponent(token)}/auto-join-company`,
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

/** `autoJoinCompany` wrapped to fail closed-to-undefined on any error
 *  (network/4xx/5xx) rather than throw — same best-effort pattern as
 *  resolveArtifactShare. postLoginPath()'s guest branch calls this ONCE,
 *  right after email verification, before resolving the share; a failure
 *  here just means the following /resolve call sees no new membership and
 *  falls through to its normal blocked handling, never a stuck screen. */
export async function tryAutoJoinCompanyOnDomainMatch(
  token: string,
): Promise<string | null | undefined> {
  try {
    const result = await artifactShareApi.autoJoinCompany(token)
    return result.joined_company_id
  } catch {
    return undefined
  }
}
