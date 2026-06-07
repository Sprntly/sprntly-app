/**
 * Settings → Team & roles pane (C4 of the team-roles slice).
 *
 * Spec: Sprntly_Onboarding_Flow_Spec_v1 § Settings → Team [Only for Admins].
 *
 *   - Invite new members — email + role.
 *   - Edit roles for existing members.
 *   - Remove members.
 *   - Pending invite management — resend or revoke.
 *
 * Pattern: pure View component (no hooks, no IO, unit-tested via
 * renderToStaticMarkup) + a default-exported hooks-wrapper that does the
 * fetches + state management. Mirrors ConnectorsSettings.tsx.
 *
 * Permission gate: members see a read-only roster (no controls); admins
 * and owners see the full team-management surface. The backend enforces
 * the same gate independently; the UI hide is purely cosmetic.
 */
"use client"

import { useCallback, useEffect, useState } from "react"
import { useAuth } from "../../../../lib/auth"
import { api } from "../../../../lib/api"
import { SettingsSection } from "./SettingsLayout"

// ─────────────────────────── Types ───────────────────────────

export type TeamRole = "owner" | "admin" | "member"
/** Roles a non-owner invite/edit can target. `owner` is reserved. */
export type InviteRole = "admin" | "member"

export type TeamMember = {
  user_id: string
  role: TeamRole
  /** Display name (full_name); falls back to email in the View. */
  display: string | null
  email: string | null
}

export type TeamInvite = {
  id: string
  email: string
  role: InviteRole
  created_at: string | null
}

// ─────────────────────────── Pure View ───────────────────────────

export type TeamSettingsViewProps = {
  members: TeamMember[]
  invites: TeamInvite[]
  currentUserId: string
  currentUserRole: TeamRole
  loading: boolean
  loadError: string | null

  // Invite form (controlled by the wrapper).
  inviteEmail: string
  inviteRole: InviteRole
  inviteSubmitting: boolean
  inviteError: string | null
  onChangeInviteEmail: (value: string) => void
  onChangeInviteRole: (value: InviteRole) => void
  onSubmitInvite: () => Promise<void>

  // Pending invite actions.
  onRevokeInvite: (inviteId: string) => void
  onResendInvite: (inviteId: string) => void

  // Member-row actions.
  onChangeMemberRole: (userId: string, role: TeamRole) => void
  onRemoveMember: (userId: string) => void
}

export function TeamSettingsView(props: TeamSettingsViewProps) {
  const {
    members,
    invites,
    currentUserId,
    currentUserRole,
    loading,
    loadError,
    inviteEmail,
    inviteRole,
    inviteSubmitting,
    inviteError,
    onChangeInviteEmail,
    onChangeInviteRole,
    onSubmitInvite,
    onRevokeInvite,
    onResendInvite,
    onChangeMemberRole,
    onRemoveMember,
  } = props

  const canManage = currentUserRole === "owner" || currentUserRole === "admin"
  const ownerCount = members.filter((m) => m.role === "owner").length

  return (
    <SettingsSection
      title="Team & roles"
      sub="Invite teammates, change their roles, and manage pending invites."
    >
      {loading && <p className="settings-placeholder">Loading team…</p>}
      {loadError && (
        <p className="settings-error">Could not load team: {loadError}</p>
      )}

      {/* ── Members ────────────────────────────────────────────────── */}
      <div className="team-block">
        <h3 className="team-block-title">Members</h3>
        <table className="team-table">
          <thead>
            <tr>
              <th>Member</th>
              <th>Role</th>
              {canManage && <th aria-label="Actions" />}
            </tr>
          </thead>
          <tbody>
            {members.map((m) => {
              const isSoleOwner = m.role === "owner" && ownerCount <= 1
              const display = m.display || m.email || m.user_id
              return (
                <tr key={m.user_id}>
                  <td>
                    <div className="team-member-name">{display}</div>
                    {m.email && m.display && (
                      <div className="team-member-email">{m.email}</div>
                    )}
                  </td>
                  <td>
                    {canManage ? (
                      <select
                        className="team-role-select"
                        value={m.role}
                        disabled={isSoleOwner}
                        onChange={(e) =>
                          onChangeMemberRole(m.user_id, e.target.value as TeamRole)
                        }
                        aria-label={`Role for ${display}`}
                      >
                        <option value="owner">Owner</option>
                        <option value="admin">Admin</option>
                        <option value="member">Member</option>
                      </select>
                    ) : (
                      <span className="team-role-chip">{m.role}</span>
                    )}
                    {isSoleOwner && (
                      <span className="team-role-note"> · sole owner</span>
                    )}
                  </td>
                  {canManage && (
                    <td className="team-row-actions">
                      <button
                        type="button"
                        className="btn-link danger"
                        disabled={isSoleOwner || m.user_id === currentUserId}
                        onClick={() => onRemoveMember(m.user_id)}
                      >
                        Remove
                      </button>
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* ── Invite form (admin/owner only) ────────────────────────── */}
      {canManage && (
        <div className="team-block">
          <h3 className="team-block-title">Invite a new member</h3>
          <form
            className="team-invite-form"
            onSubmit={(e) => {
              e.preventDefault()
              void onSubmitInvite()
            }}
          >
            <input
              type="email"
              className="settings-input"
              placeholder="teammate@company.com"
              value={inviteEmail}
              onChange={(e) => onChangeInviteEmail(e.target.value)}
              required
              disabled={inviteSubmitting}
            />
            <select
              className="settings-input"
              value={inviteRole}
              onChange={(e) => onChangeInviteRole(e.target.value as InviteRole)}
              disabled={inviteSubmitting}
            >
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
            <button
              type="submit"
              className="btn primary"
              disabled={inviteSubmitting || !inviteEmail.trim()}
            >
              {inviteSubmitting ? "Sending…" : "Send invite"}
            </button>
          </form>
          {inviteError && <p className="settings-error">{inviteError}</p>}
        </div>
      )}

      {/* ── Pending invites ───────────────────────────────────────── */}
      <div className="team-block">
        <h3 className="team-block-title">Pending invites</h3>
        {invites.length === 0 ? (
          <p className="settings-placeholder">No pending invites.</p>
        ) : (
          <table className="team-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th>Sent</th>
                {canManage && <th aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {invites.map((inv) => (
                <tr key={inv.id}>
                  <td>{inv.email}</td>
                  <td>
                    <span className="team-role-chip">{inv.role}</span>
                  </td>
                  <td>{formatSent(inv.created_at)}</td>
                  {canManage && (
                    <td className="team-row-actions">
                      <button
                        type="button"
                        className="btn-link"
                        onClick={() => onResendInvite(inv.id)}
                      >
                        Resend
                      </button>
                      <button
                        type="button"
                        className="btn-link danger"
                        onClick={() => onRevokeInvite(inv.id)}
                      >
                        Revoke
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </SettingsSection>
  )
}

function formatSent(iso: string | null): string {
  if (!iso) return "—"
  try {
    const d = new Date(iso)
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    })
  } catch {
    return iso
  }
}

// ─────────────────────────── Hooks wrapper ───────────────────────────

type TeamMembersResp = {
  members: { user_id: string; role: TeamRole; created_at: string | null }[]
}
type TeamInvitesResp = { invites: TeamInvite[] }

export function TeamSettings() {
  const auth = useAuth()
  const [members, setMembers] = useState<TeamMember[]>([])
  const [invites, setInvites] = useState<TeamInvite[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [inviteEmail, setInviteEmail] = useState("")
  const [inviteRole, setInviteRole] = useState<InviteRole>("member")
  const [inviteSubmitting, setInviteSubmitting] = useState(false)
  const [inviteError, setInviteError] = useState<string | null>(null)

  const currentUserId = auth.kind === "authed" ? auth.user.id : ""
  const currentRow = members.find((m) => m.user_id === currentUserId)
  const currentUserRole: TeamRole = (currentRow?.role as TeamRole) || "member"

  const reload = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const [m, inv] = await Promise.all([
        teamApi.listMembers(),
        teamApi.listInvites(),
      ])
      // Backend members carry only user_id + role; resolve display names from
      // profiles when we can. For now we render user_id as fallback.
      setMembers(
        m.members.map((row) => ({
          user_id: row.user_id,
          role: row.role,
          display: null,
          email: null,
        })),
      )
      setInvites(inv.invites)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  async function submitInvite() {
    const email = inviteEmail.trim()
    if (!email) return
    setInviteSubmitting(true)
    setInviteError(null)
    try {
      await teamApi.invite(email, inviteRole)
      setInviteEmail("")
      setInviteRole("member")
      await reload()
    } catch (e) {
      setInviteError(e instanceof Error ? e.message : String(e))
    } finally {
      setInviteSubmitting(false)
    }
  }

  function revoke(id: string) {
    void (async () => {
      try {
        await teamApi.revokeInvite(id)
        await reload()
      } catch (e) {
        setInviteError(e instanceof Error ? e.message : String(e))
      }
    })()
  }

  function resend(id: string) {
    void (async () => {
      try {
        await teamApi.resendInvite(id)
        await reload()
      } catch (e) {
        setInviteError(e instanceof Error ? e.message : String(e))
      }
    })()
  }

  function changeRole(userId: string, role: TeamRole) {
    void (async () => {
      try {
        await teamApi.patchMemberRole(userId, role)
        await reload()
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : String(e))
      }
    })()
  }

  function removeMember(userId: string) {
    if (!confirm("Remove this member from the team?")) return
    void (async () => {
      try {
        await teamApi.removeMember(userId)
        await reload()
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : String(e))
      }
    })()
  }

  return (
    <TeamSettingsView
      members={members}
      invites={invites}
      currentUserId={currentUserId}
      currentUserRole={currentUserRole}
      loading={loading}
      loadError={loadError}
      inviteEmail={inviteEmail}
      inviteRole={inviteRole}
      inviteSubmitting={inviteSubmitting}
      inviteError={inviteError}
      onChangeInviteEmail={setInviteEmail}
      onChangeInviteRole={setInviteRole}
      onSubmitInvite={submitInvite}
      onRevokeInvite={revoke}
      onResendInvite={resend}
      onChangeMemberRole={changeRole}
      onRemoveMember={removeMember}
    />
  )
}

// ─────────────────────────── API surface ───────────────────────────
//
// Defined here (rather than in lib/api.ts) so the team feature lives in one
// place. If a second surface needs these, lift them out into lib/api.ts.

export const teamApi = {
  listMembers: () => api.get<TeamMembersResp>("/v1/team/members"),
  listInvites: () => api.get<TeamInvitesResp>("/v1/team/invites"),
  invite: (email: string, role: InviteRole) =>
    api.post<TeamInvite>("/v1/team/invites", { email, role }),
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
  removeMember: (userId: string) =>
    api.delete<void>(`/v1/team/members/${encodeURIComponent(userId)}`),
  acceptInvite: () =>
    api.post<{ company_id: string; role: TeamRole }>("/v1/invites/accept"),
}
