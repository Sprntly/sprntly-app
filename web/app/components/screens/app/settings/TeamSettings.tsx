/**
 * Settings → Team & roles pane.
 *
 * Spec: Sprntly_Onboarding_Flow_Spec_v1 § Settings → Team [Only for Admins].
 * Visual: sprntly-pages/15-settings.html § Team & roles (lines 2245-2262)
 *   - Two `set-block` cards:
 *       (1) combined Members + pending invites list with header
 *           "N members · M pending invites" + inline "+ Invite teammate"
 *           trigger.
 *       (2) "Roles" reference card explaining what each role can do.
 *   - Per-row layout: avatar + name/email + role select + status chip
 *     + 3-dot actions menu (replaces the old inline Remove button).
 *
 * Pattern unchanged: pure View component (no hooks, no IO,
 * renderToStaticMarkup-testable) + a default-exported hooks wrapper
 * that does fetches + state.
 *
 * Permission gate: the View renders controls only for owner/admin.
 * Backend enforces the same gate independently.
 */
"use client"

import { useCallback, useEffect, useState } from "react"
import { useAuth } from "../../../../lib/auth"
import { api } from "../../../../lib/api"

// ─────────────────────────── Types ───────────────────────────

export type TeamRole = "owner" | "admin" | "member" | "viewer"
/** Roles a non-owner invite/edit can target. `owner` is reserved. */
export type InviteRole = "admin" | "member" | "viewer"

export type TeamMember = {
  user_id: string
  role: TeamRole
  display_name: string | null
  email: string | null
  avatar_url: string | null
}

export type TeamInvite = {
  id: string
  email: string
  role: InviteRole
  created_at: string | null
  /** Returned on POST /invites and POST /invites/{id}/resend. */
  email_sent?: boolean
}

/** Unified row type so members + pending invites render in one list. */
type RosterRow =
  | { kind: "member"; member: TeamMember }
  | { kind: "invite"; invite: TeamInvite }

// ─────────────────────────── Pure View ───────────────────────────

export type TeamSettingsViewProps = {
  members: TeamMember[]
  invites: TeamInvite[]
  currentUserId: string
  currentUserRole: TeamRole
  loading: boolean
  loadError: string | null

  // Invite form state (controlled by the wrapper).
  showInviteForm: boolean
  inviteEmail: string
  inviteRole: InviteRole
  inviteSubmitting: boolean
  inviteError: string | null
  inviteNotice: { kind: "sent" | "saved"; email: string } | null
  onToggleInviteForm: () => void
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
    showInviteForm,
    inviteEmail,
    inviteRole,
    inviteSubmitting,
    inviteError,
    inviteNotice,
    onToggleInviteForm,
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
    <div className="set-pane sp-team">
      <div className="set-h">Team &amp; roles</div>
      <div className="set-sub">
        Anyone on your team can sign in to this workspace. Roles govern what
        they can edit.
      </div>

      {loading && <p className="set-block-s-inline">Loading team…</p>}
      {loadError && (
        <p className="settings-error">Could not load team: {loadError}</p>
      )}

      {/* ── Block 1: Members + pending invites ─────────────────────── */}
      <div className="set-block">
        <div className="set-block-h">
          <div className="set-block-t">
            {members.length} {pluralize(members.length, "member")} ·{" "}
            {invites.length}{" "}
            {pluralize(invites.length, "pending invite")}
          </div>
          {canManage && (
            <div className="set-block-meta">
              <button
                type="button"
                className="set-team-invite-trigger"
                onClick={onToggleInviteForm}
              >
                {showInviteForm ? "Cancel" : "+ Invite teammate"}
              </button>
            </div>
          )}
        </div>

        {canManage && showInviteForm && (
          <form
            className="set-team-invite-form"
            onSubmit={(e) => {
              e.preventDefault()
              void onSubmitInvite()
            }}
          >
            <input
              type="email"
              className="set-team-invite-input"
              placeholder="teammate@company.com"
              value={inviteEmail}
              onChange={(e) => onChangeInviteEmail(e.target.value)}
              required
              disabled={inviteSubmitting}
            />
            <select
              className="set-team-invite-select"
              value={inviteRole}
              onChange={(e) =>
                onChangeInviteRole(e.target.value as InviteRole)
              }
              disabled={inviteSubmitting}
            >
              <option value="member">Member</option>
              <option value="admin">Admin</option>
              <option value="viewer">Viewer</option>
            </select>
            <button
              type="submit"
              className="set-team-invite-submit"
              disabled={inviteSubmitting || !inviteEmail.trim()}
            >
              {inviteSubmitting ? "Sending…" : "Send invite"}
            </button>
          </form>
        )}

        {canManage && inviteError && (
          <p className="settings-error">{inviteError}</p>
        )}
        {canManage && !inviteError && inviteNotice && (
          <p
            className={
              inviteNotice.kind === "sent"
                ? "settings-notice"
                : "settings-warning"
            }
          >
            {inviteNotice.kind === "sent"
              ? `Invite emailed to ${inviteNotice.email}.`
              : `Invite saved for ${inviteNotice.email}, but the email didn't send. Click Resend to retry.`}
          </p>
        )}

        {/* Combined roster — members then pending invites. */}
        {buildRoster(members, invites).map((row) => {
          if (row.kind === "member") {
            const m = row.member
            const isSoleOwner = m.role === "owner" && ownerCount <= 1
            const isSelf = m.user_id === currentUserId
            const display = m.display_name || m.email || m.user_id
            return (
              <div className="set-team-row" key={`m-${m.user_id}`}>
                <Avatar
                  seed={m.user_id}
                  initials={initialsFor(m.display_name, m.email, m.user_id)}
                  url={m.avatar_url}
                />
                <div className="set-team-row-info">
                  <div className="nm">{display}</div>
                  {m.email && m.email !== display && (
                    <div className="em">{m.email}</div>
                  )}
                </div>
                {canManage ? (
                  <select
                    className="set-team-row-select"
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
                    <option value="viewer">Viewer</option>
                  </select>
                ) : (
                  <span className="st neutral">{m.role}</span>
                )}
                <span className="st active">
                  <span className="st-dot" /> Active
                </span>
                {canManage ? (
                  <RowActionsMenu
                    label="Member actions"
                    items={
                      isSoleOwner
                        ? []
                        : isSelf
                          ? []
                          : [
                              {
                                label: "Remove from team",
                                tone: "danger",
                                onClick: () => onRemoveMember(m.user_id),
                              },
                            ]
                    }
                  />
                ) : (
                  <span className="set-team-row-actions-spacer" />
                )}
              </div>
            )
          }
          // pending invite
          const inv = row.invite
          return (
            <div className="set-team-row pending" key={`i-${inv.id}`}>
              <Avatar
                seed={inv.email}
                initials={initialsFor(null, inv.email, inv.email)}
                url={null}
                muted
              />
              <div className="set-team-row-info">
                <div className="nm">{inv.email}</div>
                <div className="em">Invited {formatSent(inv.created_at)}</div>
              </div>
              <span className="st neutral">{inv.role}</span>
              <span className="st invited">
                <span className="st-dot" /> Invited
              </span>
              {canManage ? (
                <RowActionsMenu
                  label="Invite actions"
                  items={[
                    {
                      label: "Resend email",
                      onClick: () => onResendInvite(inv.id),
                    },
                    {
                      label: "Revoke invite",
                      tone: "danger",
                      onClick: () => onRevokeInvite(inv.id),
                    },
                  ]}
                />
              ) : (
                <span className="set-team-row-actions-spacer" />
              )}
            </div>
          )
        })}

        {members.length === 0 && invites.length === 0 && (
          <p className="set-block-s-inline">
            No team members yet. Invite your first teammate above.
          </p>
        )}
      </div>

      {/* ── Block 2: Roles reference ───────────────────────────────── */}
      <div className="set-block">
        <div className="set-block-h">
          <div className="set-block-t">Roles</div>
        </div>
        <div className="set-row">
          <span className="k">
            <strong>Owner</strong>
          </span>
          <span className="v">
            Full access · billing · delete workspace · transfer ownership
          </span>
        </div>
        <div className="set-row">
          <span className="k">
            <strong>Admin</strong>
          </span>
          <span className="v">
            Manage team, connectors, settings · cannot delete workspace
          </span>
        </div>
        <div className="set-row">
          <span className="k">
            <strong>Member</strong>
          </span>
          <span className="v">
            Edit Briefs, PRDs, tickets, prototypes · cannot manage team or
            billing
          </span>
        </div>
        <div className="set-row">
          <span className="k">
            <strong>Viewer</strong>
          </span>
          <span className="v">
            Read-only access · can comment but not edit
          </span>
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────── Subcomponents ───────────────────────────

function Avatar({
  seed,
  initials,
  url,
  muted,
}: {
  seed: string
  initials: string
  url: string | null
  muted?: boolean
}) {
  if (url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img className="set-team-row-av img" src={url} alt="" />
    )
  }
  const palette = avatarPalette(seed)
  const style = muted
    ? { background: "var(--surface-2, #F0F2F0)", color: "#828D87" }
    : { background: palette.bg, color: palette.fg, border: `1px solid ${palette.border}` }
  return (
    <span className="set-team-row-av" style={style}>
      {initials}
    </span>
  )
}

/** Native <details> popover so the View stays pure (no React state). */
function RowActionsMenu({
  label,
  items,
}: {
  label: string
  items: { label: string; tone?: "danger"; onClick: () => void }[]
}) {
  if (items.length === 0) {
    return <span className="set-team-row-actions-spacer" aria-hidden="true" />
  }
  return (
    <details className="set-team-row-actions">
      <summary aria-label={label}>⋯</summary>
      <div className="set-team-row-actions-menu" role="menu">
        {items.map((it) => (
          <button
            key={it.label}
            type="button"
            role="menuitem"
            className={
              it.tone === "danger"
                ? "set-team-row-actions-item danger"
                : "set-team-row-actions-item"
            }
            onClick={it.onClick}
          >
            {it.label}
          </button>
        ))}
      </div>
    </details>
  )
}

// ─────────────────────────── Helpers ───────────────────────────

function buildRoster(
  members: TeamMember[],
  invites: TeamInvite[],
): RosterRow[] {
  return [
    ...members.map((m): RosterRow => ({ kind: "member", member: m })),
    ...invites.map((i): RosterRow => ({ kind: "invite", invite: i })),
  ]
}

function pluralize(n: number, singular: string): string {
  return n === 1 ? singular : singular + "s"
}

function initialsFor(
  name: string | null,
  email: string | null,
  fallback: string,
): string {
  const source = (name || email || fallback || "").trim()
  if (!source) return "?"
  const parts = source.split(/[\s@.]+/).filter(Boolean)
  if (parts.length === 0) return source.slice(0, 2).toUpperCase()
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
}

/** Deterministic per-user color pick from a small palette. Matches the
 *  mockup's color vocabulary (brand/green/purple/blue/amber). */
function avatarPalette(seed: string): {
  bg: string
  fg: string
  border: string
} {
  const palettes = [
    { bg: "#DBF1E7", fg: "#0E6E49", border: "#9BDCC1" }, // brand/green
    { bg: "#EAE4F5", fg: "#634AB0", border: "#C8B8E5" }, // purple
    { bg: "#E4EEFB", fg: "#1F5AB6", border: "#B8CFEF" }, // blue
    { bg: "#F9EBD3", fg: "#C16A0B", border: "#F0BF73" }, // amber
    { bg: "#FBE0E0", fg: "#B0314A", border: "#EFB8C0" }, // rose
  ]
  let hash = 0
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) | 0
  return palettes[Math.abs(hash) % palettes.length]
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
  members: {
    user_id: string
    role: TeamRole
    display_name: string | null
    email: string | null
    avatar_url: string | null
  }[]
}
type TeamInvitesResp = { invites: TeamInvite[] }

export function TeamSettings() {
  const auth = useAuth()
  const [members, setMembers] = useState<TeamMember[]>([])
  const [invites, setInvites] = useState<TeamInvite[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [showInviteForm, setShowInviteForm] = useState(false)
  const [inviteEmail, setInviteEmail] = useState("")
  const [inviteRole, setInviteRole] = useState<InviteRole>("member")
  const [inviteSubmitting, setInviteSubmitting] = useState(false)
  const [inviteError, setInviteError] = useState<string | null>(null)
  const [inviteNotice, setInviteNotice] = useState<
    { kind: "sent" | "saved"; email: string } | null
  >(null)

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
      setMembers(
        m.members.map((row) => ({
          user_id: row.user_id,
          role: row.role,
          display_name: row.display_name,
          email: row.email,
          avatar_url: row.avatar_url,
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
    setInviteNotice(null)
    try {
      const created = await teamApi.invite(email, inviteRole)
      setInviteEmail("")
      setInviteRole("member")
      setShowInviteForm(false)
      setInviteNotice({
        kind: created.email_sent ? "sent" : "saved",
        email: created.email,
      })
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
        setInviteNotice(null)
        await reload()
      } catch (e) {
        setInviteError(e instanceof Error ? e.message : String(e))
      }
    })()
  }

  function resend(id: string) {
    void (async () => {
      try {
        const updated = await teamApi.resendInvite(id)
        setInviteError(null)
        setInviteNotice({
          kind: updated.email_sent ? "sent" : "saved",
          email: updated.email,
        })
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
      showInviteForm={showInviteForm}
      inviteEmail={inviteEmail}
      inviteRole={inviteRole}
      inviteSubmitting={inviteSubmitting}
      inviteError={inviteError}
      inviteNotice={inviteNotice}
      onToggleInviteForm={() => setShowInviteForm((prev) => !prev)}
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
