// Team API surface (/v1/team/* + /v1/invites/accept), extracted from
// TeamSettings so non-settings callers (onboarding TeamStep, postLoginPath's
// auto-accept) don't import a settings component. TeamSettings re-exports all
// of this for compatibility.
import { api } from "./api"
import { JOB_ROLE_OPTIONS } from "./onboarding/types"

export type TeamRole = "owner" | "admin" | "member" | "viewer"
/** Roles a non-owner invite/edit can target. `owner` is reserved. */
export type InviteRole = "admin" | "member" | "viewer"

export type TeamMember = {
  user_id: string
  role: TeamRole
  display_name: string | null
  email: string | null
  avatar_url: string | null
  /** Explicit workspace grants. Org owners/admins usually have none —
   *  their access is implicit across every workspace. */
  workspace_ids?: string[]
  /** Job designation (profiles.role — ROLE_OPTIONS taxonomy: Founder / PM /
   *  Engineer / Data Scientist / Designer / Other), captured at onboarding
   *  and self-editable from Settings. Distinct from `role` above, which is
   *  the permission role (owner/admin/member/viewer). */
  job_role?: string | null
}

export type TeamInvite = {
  id: string
  email: string
  role: InviteRole
  created_at: string | null
  /** The workspaces the invitee joins on accept ([] = the default workspace,
   *  resolved at accept time). */
  workspace_ids?: string[]
  /** Returned on POST /invites and POST /invites/{id}/resend. */
  email_sent?: boolean
}

export type TeamMembersResp = { members: TeamMember[] }
export type TeamInvitesResp = { invites: TeamInvite[] }

export const teamApi = {
  listMembers: () => api.get<TeamMembersResp>("/v1/team/members"),
  listInvites: () => api.get<TeamInvitesResp>("/v1/team/invites"),
  invite: (
    email: string,
    role: InviteRole,
    workspaceIds: string[] = [],
    /** The teammate's JOB role (Data Science, Engineer…) — display-only. */
    jobRole?: string,
  ) =>
    api.post<TeamInvite>("/v1/team/invites", {
      email,
      role,
      workspace_ids: workspaceIds,
      ...(jobRole?.trim() ? { job_role: jobRole.trim() } : {}),
    }),
  revokeInvite: (id: string) =>
    api.delete<void>(`/v1/team/invites/${encodeURIComponent(id)}`),
  resendInvite: (id: string) =>
    api.post<TeamInvite>(
      `/v1/team/invites/${encodeURIComponent(id)}/resend`,
    ),
  patchMemberRole: (userId: string, role: TeamRole) =>
    api.patch<{ user_id: string; role: TeamRole }>(
      `/v1/team/members/${encodeURIComponent(userId)}`,
      { role },
    ),
  /** Self-only: set the caller's own job designation (profiles.role).
   *  `userId` must be the signed-in user's own id — the backend 403s any
   *  other target regardless of what's requested. */
  patchMyJobRole: (userId: string, jobRole: string | null) =>
    api.patch<{ user_id: string; job_role: string | null }>(
      `/v1/team/members/${encodeURIComponent(userId)}/job-role`,
      { role: jobRole },
    ),
  setMemberWorkspaces: (userId: string, workspaceIds: string[]) =>
    api.put<{ user_id: string; workspace_ids: string[] }>(
      `/v1/team/members/${encodeURIComponent(userId)}/workspaces`,
      { workspace_ids: workspaceIds },
    ),
  removeMember: (userId: string) =>
    api.delete<void>(`/v1/team/members/${encodeURIComponent(userId)}`),
  acceptInvite: () =>
    api.post<{
      company_id: string
      role: TeamRole
      /** The workspaces granted by this accept. */
      workspace_ids?: string[]
      /** The project this invite carried (AD-TNM3), or null/absent for a
       *  plain workspace/org invite. When present, the sign-in flow lands the
       *  accepter directly in that project's private chat. */
      project_id?: number | null
    }>("/v1/invites/accept"),
}

// ─────────────────────── Bulk invite parsing ───────────────────────
// Pulled out of the retired onboarding InviteStep (2026-09-03) when its bulk
// paste + CSV import moved to Settings → Team & roles — the only reason these
// two are pure, exported functions rather than living inline in that pane.

export type InviteRow = { email: string; jobRole: string; permission: InviteRole }

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const PERMISSIONS: InviteRole[] = ["admin", "member", "viewer"]

function asPermission(raw: string): InviteRole {
  const v = raw.trim().toLowerCase()
  return (PERMISSIONS as string[]).includes(v) ? (v as InviteRole) : "member"
}

/**
 * Split a pasted blob into invite rows. Accepts commas, semicolons, newlines
 * and whitespace as separators, since a paste out of Slack/email/a spreadsheet
 * can carry any of them. Invalid and duplicate addresses are dropped silently —
 * the point of the bulk field is speed, and a wrong row is one click to remove
 * from the roster afterward (Settings → Team already lists every invite sent).
 */
export function parsePastedEmails(
  raw: string,
  existing: readonly InviteRow[] = [],
): InviteRow[] {
  const seen = new Set(
    existing.map((r) => r.email.trim().toLowerCase()).filter(Boolean),
  )
  const rows: InviteRow[] = []
  for (const token of raw.split(/[,;\s]+/)) {
    const email = token.trim().toLowerCase()
    if (!email || !EMAIL_RE.test(email)) continue
    if (seen.has(email)) continue
    seen.add(email)
    rows.push({ email, jobRole: JOB_ROLE_OPTIONS[0], permission: "member" })
  }
  return rows
}

/**
 * Parse an invites CSV: one teammate per line, `email[,job role[,permission]]`.
 * A header row (first cell "email") is skipped; malformed / duplicate emails
 * are dropped.
 */
export function parseInvitesCsv(text: string): InviteRow[] {
  const rows: InviteRow[] = []
  const seen = new Set<string>()
  for (const line of text.split(/\r?\n/)) {
    const cells = line.split(",").map((c) => c.trim().replace(/^"|"$/g, ""))
    const email = (cells[0] ?? "").toLowerCase()
    if (!email || email === "email" || !EMAIL_RE.test(email)) continue
    if (seen.has(email)) continue
    seen.add(email)
    rows.push({
      email,
      jobRole: cells[1] || JOB_ROLE_OPTIONS[0],
      permission: asPermission(cells[2] ?? ""),
    })
  }
  return rows
}
