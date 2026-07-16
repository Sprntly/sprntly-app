/**
 * Settings → Team & roles pane.
 *
 * Spec: Sprntly_Onboarding_Flow_Spec_v1 § Settings → Team [Only for Admins].
 * Visual: sprntly-pages/15-settings.html § Team & roles (lines 2245-2262)
 *   - One `set-block` card: combined Members + pending invites list with
 *     header "N members · M pending invites" + inline "+ Invite teammate"
 *     trigger. (The old standalone "Roles" reference card was folded into
 *     the role dropdowns — each option shows its definition.)
 *   - Per-row layout: avatar + name/email + role select + workspaces
 *     multi-select (multi-workspace companies) + status chip + 3-dot
 *     actions menu (replaces the old inline Remove button).
 *
 * Pattern unchanged: pure View component (no hooks, no IO,
 * renderToStaticMarkup-testable) + a default-exported hooks wrapper
 * that does fetches + state.
 *
 * Permission gate: the View renders controls only for owner/admin.
 * Backend enforces the same gate independently.
 */
"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useAuth } from "../../../../lib/auth"
import { useWorkspace as useWorkspaceCtx } from "../../../../context/WorkspaceContext"
import { registerSettingsCacheReset } from "../../../../lib/settingsCache"
import {
  teamApi,
  type InviteRole,
  type TeamInvite,
  type TeamMember,
  type TeamRole,
} from "../../../../lib/teamApi"

// ─────────────────────────── Types ───────────────────────────
// The team API surface + row types moved to web/app/lib/teamApi.ts so
// onboarding and postLoginPath don't import a settings component; re-exported
// here for existing callers.

export { teamApi }
export type { InviteRole, TeamInvite, TeamMember, TeamRole }

/** Unified row type so members + pending invites render in one list. */
type RosterRow =
  | { kind: "member"; member: TeamMember }
  | { kind: "invite"; invite: TeamInvite }

/** Role definitions shown inside the role dropdowns (replaces the old
 *  standalone "Roles" reference card). */
const ROLE_DESCRIPTIONS: Record<TeamRole, string> = {
  owner: "Full access · billing · delete workspace · transfer ownership",
  admin: "Manage team, connectors, settings · cannot delete workspace",
  member:
    "Edit Briefs, PRDs, tickets, prototypes · cannot manage team or billing",
  viewer: "Read-only access · can comment but not edit",
}

const MEMBER_ROLE_OPTIONS: { value: TeamRole; label: string; description: string }[] = [
  { value: "owner", label: "Owner", description: ROLE_DESCRIPTIONS.owner },
  { value: "admin", label: "Admin", description: ROLE_DESCRIPTIONS.admin },
  { value: "member", label: "Member", description: ROLE_DESCRIPTIONS.member },
  { value: "viewer", label: "Viewer", description: ROLE_DESCRIPTIONS.viewer },
]

/** `owner` is reserved — invites can only target admin/member/viewer. */
const INVITE_ROLE_OPTIONS: { value: InviteRole; label: string; description: string }[] = [
  { value: "admin", label: "Admin", description: ROLE_DESCRIPTIONS.admin },
  { value: "member", label: "Member", description: ROLE_DESCRIPTIONS.member },
  { value: "viewer", label: "Viewer", description: ROLE_DESCRIPTIONS.viewer },
]

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
  // Multi-workspace (2026-07): which workspaces the invitee joins on accept.
  // Empty availableWorkspaces (single-workspace company) hides the picker.
  availableWorkspaces?: { id: string; name: string }[]
  inviteWorkspaceIds?: string[]
  onToggleInviteWorkspace?: (id: string) => void

  // Pending invite actions.
  onRevokeInvite: (inviteId: string) => void
  onResendInvite: (inviteId: string) => void

  // Member-row actions.
  onChangeMemberRole: (userId: string, role: TeamRole) => void
  onRemoveMember: (userId: string) => void
  /** Toggle one workspace grant on an existing member (multi-select row). */
  onToggleMemberWorkspace?: (userId: string, workspaceId: string) => void
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
    availableWorkspaces = [],
    inviteWorkspaceIds = [],
    onToggleInviteWorkspace,
    onRevokeInvite,
    onResendInvite,
    onChangeMemberRole,
    onRemoveMember,
    onToggleMemberWorkspace,
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
            <ThemedSelect<InviteRole>
              className="set-team-invite-select"
              value={inviteRole}
              options={INVITE_ROLE_OPTIONS}
              disabled={inviteSubmitting}
              onChange={onChangeInviteRole}
            />
            {availableWorkspaces.length > 1 && onToggleInviteWorkspace && (
              <ThemedMultiSelect
                className="set-team-invite-ws"
                values={inviteWorkspaceIds}
                options={availableWorkspaces.map((w) => ({
                  value: w.id,
                  label: w.name,
                }))}
                placeholder="Select workspaces"
                allLabel="All workspaces"
                disabled={inviteSubmitting}
                ariaLabel="Workspaces for this invite"
                onToggle={onToggleInviteWorkspace}
              />
            )}
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
                  <ThemedSelect<TeamRole>
                    className="set-team-row-select"
                    value={m.role}
                    disabled={isSoleOwner}
                    options={MEMBER_ROLE_OPTIONS}
                    ariaLabel={`Role for ${display}`}
                    onChange={(role) => onChangeMemberRole(m.user_id, role)}
                  />
                ) : (
                  <span className="st neutral">{m.role}</span>
                )}
                {canManage &&
                  availableWorkspaces.length > 1 &&
                  onToggleMemberWorkspace &&
                  (m.role === "owner" || m.role === "admin" ? (
                    // Org owners/admins access every workspace implicitly —
                    // a picker here would lie.
                    <span className="set-team-row-ws-all">All workspaces</span>
                  ) : (
                    <ThemedMultiSelect
                      className="set-team-row-ws"
                      values={m.workspace_ids ?? []}
                      options={availableWorkspaces.map((w) => ({
                        value: w.id,
                        label: w.name,
                      }))}
                      placeholder="No workspaces"
                      allLabel="All workspaces"
                      ariaLabel={`Workspaces for ${display}`}
                      onToggle={(wid) =>
                        onToggleMemberWorkspace(m.user_id, wid)
                      }
                    />
                  ))}
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

      {/* The old "Roles" reference card was folded into the role dropdowns
          (each option now carries its definition). */}
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
    ? { background: "var(--surface-3)", color: "var(--ink-3)" }
    : { background: palette.bg, color: palette.fg, border: `1px solid ${palette.border}` }
  return (
    <span className="set-team-row-av" style={style}>
      {initials}
    </span>
  )
}

/** Close-on-outside-click shared by both themed dropdowns. */
function useCloseOnOutsideClick(
  open: boolean,
  ref: React.RefObject<HTMLDivElement | null>,
  close: () => void,
) {
  useEffect(() => {
    if (!open) return
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) close()
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
}

function Chevron() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <polyline points="6 9 12 15 18 9" />
    </svg>
  )
}

/** Custom themed dropdown — replaces native <select> so the open list
 *  picks up the app theme instead of the OS-native cyan highlight.
 *  Options may carry a `description` rendered under the label (used for
 *  role definitions — the old "Roles" reference card lives here now). */
function ThemedSelect<T extends string>({
  value,
  options,
  disabled,
  className,
  ariaLabel,
  onChange,
}: {
  value: T
  options: { value: T; label: string; description?: string }[]
  disabled?: boolean
  className?: string
  ariaLabel?: string
  onChange: (v: T) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useCloseOnOutsideClick(open, ref, () => setOpen(false))

  const selected = options.find((o) => o.value === value)
  const hasDescriptions = options.some((o) => o.description)

  return (
    <div
      ref={ref}
      className={`themed-select${disabled ? " disabled" : ""}${className ? ` ${className}` : ""}`}
      aria-label={ariaLabel}
    >
      <button
        type="button"
        className="themed-select-trigger"
        onClick={() => !disabled && setOpen((o) => !o)}
        disabled={disabled}
      >
        <span>{selected?.label ?? value}</span>
        <Chevron />
      </button>
      {open && (
        <div className={`themed-select-menu${hasDescriptions ? " wide" : ""}`}>
          {options.map((opt) => (
            <button
              key={opt.value}
              type="button"
              className={`themed-select-option${opt.value === value ? " active" : ""}`}
              onClick={() => { onChange(opt.value); setOpen(false) }}
            >
              <span className="themed-select-option-label">{opt.label}</span>
              {opt.description && (
                <span className="themed-select-option-desc">
                  {opt.description}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** Multi-select variant — checkbox-style options that keep the menu open on
 *  toggle. Trigger summarises the selection ("Design", "2 workspaces",
 *  "All workspaces"). Used for the invite + member-row workspace pickers. */
function ThemedMultiSelect({
  values,
  options,
  disabled,
  className,
  ariaLabel,
  placeholder,
  allLabel,
  onToggle,
}: {
  values: string[]
  options: { value: string; label: string }[]
  disabled?: boolean
  className?: string
  ariaLabel?: string
  placeholder: string
  allLabel: string
  onToggle: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useCloseOnOutsideClick(open, ref, () => setOpen(false))

  const picked = options.filter((o) => values.includes(o.value))
  const summary =
    picked.length === 0
      ? placeholder
      : picked.length === options.length && options.length > 1
        ? allLabel
        : picked.length === 1
          ? picked[0].label
          : `${picked.length} workspaces`

  return (
    <div
      ref={ref}
      className={`themed-select themed-multi-select${disabled ? " disabled" : ""}${className ? ` ${className}` : ""}`}
      aria-label={ariaLabel}
    >
      <button
        type="button"
        className="themed-select-trigger"
        onClick={() => !disabled && setOpen((o) => !o)}
        disabled={disabled}
      >
        <span className={picked.length === 0 ? "placeholder" : undefined}>
          {summary}
        </span>
        <Chevron />
      </button>
      {open && (
        <div className="themed-select-menu">
          {options.map((opt) => {
            const checked = values.includes(opt.value)
            return (
              <button
                key={opt.value}
                type="button"
                role="menuitemcheckbox"
                aria-checked={checked}
                className={`themed-select-option check${checked ? " active" : ""}`}
                onClick={() => onToggle(opt.value)}
              >
                <span className={`themed-select-check${checked ? " on" : ""}`} aria-hidden>
                  {checked && (
                    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  )}
                </span>
                <span className="themed-select-option-label">{opt.label}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
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
/** Maps a stable hash of a seed (user_id / email) to one of the 5 design-kit
 *  semantic colour families. Returns CSS `var()` strings — the actual hexes
 *  resolve from :root in globals.css, so the avatar honours any future
 *  token tweaks without code change. Family mapping mirrors the design
 *  system's agent colour pattern (brand / purple / blue / amber / red). */
function avatarPalette(seed: string): {
  bg: string
  fg: string
  border: string
} {
  const families = [
    { bg: "var(--accent-soft)", fg: "var(--accent-ink)", border: "var(--accent-2)" },
    { bg: "var(--purple-soft)", fg: "var(--purple)", border: "var(--purple-border)" },
    { bg: "var(--info-soft)", fg: "var(--info)", border: "var(--info-border)" },
    { bg: "var(--warn-soft)", fg: "var(--warn)", border: "var(--warn-border)" },
    { bg: "var(--danger-soft)", fg: "var(--danger)", border: "var(--danger-border)" },
  ]
  let hash = 0
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) | 0
  return families[Math.abs(hash) % families.length]
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

// (TeamMembersResp / TeamInvitesResp live in lib/teamApi.ts with the api calls.)

// Module-scoped cache of the last-loaded team (members + invites). Survives the
// pane remounting on a settings tab-switch, so a revisit shows the team
// INSTANTLY and revalidates in the background — no spinner every time. `null` =
// never loaded (the only cold case that shows the spinner). Cleared on sign-out.
let _teamCache: { members: TeamMember[]; invites: TeamInvite[] } | null = null

// Clear on sign-out so a different user never sees the previous account's
// members/invites (see lib/settingsCache).
registerSettingsCacheReset(() => {
  _teamCache = null
})

export function TeamSettings() {
  const auth = useAuth()
  // Seed from cache so a tab-switch return renders instantly; reload() below
  // still revalidates in the background.
  const [members, setMembers] = useState<TeamMember[]>(() => _teamCache?.members ?? [])
  const [invites, setInvites] = useState<TeamInvite[]>(() => _teamCache?.invites ?? [])
  const [loading, setLoading] = useState(() => _teamCache === null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [showInviteForm, setShowInviteForm] = useState(false)
  const [inviteEmail, setInviteEmail] = useState("")
  const [inviteRole, setInviteRole] = useState<InviteRole>("member")
  const [inviteSubmitting, setInviteSubmitting] = useState(false)
  const [inviteError, setInviteError] = useState<string | null>(null)
  const [inviteNotice, setInviteNotice] = useState<
    { kind: "sent" | "saved"; email: string } | null
  >(null)
  // Multi-workspace: which workspaces the invite targets. Defaults to the
  // ACTIVE workspace once the list loads; [] = default workspace at accept.
  const { workspaces, activeWorkspace } = useWorkspaceCtx()
  const [inviteWorkspaceIds, setInviteWorkspaceIds] = useState<string[]>([])
  useEffect(() => {
    if (activeWorkspace) setInviteWorkspaceIds([activeWorkspace.id])
  }, [activeWorkspace])

  function toggleInviteWorkspace(id: string) {
    setInviteWorkspaceIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  const currentUserId = auth.kind === "authed" ? auth.user.id : ""
  const currentRow = members.find((m) => m.user_id === currentUserId)
  const currentUserRole: TeamRole = (currentRow?.role as TeamRole) || "member"

  const reload = useCallback(async () => {
    // No setLoading(true): a warm revisit keeps the current team on screen
    // while this revalidates. The cold-load spinner is the initial state above.
    setLoadError(null)
    try {
      const [m, inv] = await Promise.all([
        teamApi.listMembers(),
        teamApi.listInvites(),
      ])
      const nextMembers = m.members.map((row) => ({
        user_id: row.user_id,
        role: row.role,
        display_name: row.display_name,
        email: row.email,
        avatar_url: row.avatar_url,
        workspace_ids: row.workspace_ids ?? [],
      }))
      _teamCache = { members: nextMembers, invites: inv.invites }
      setMembers(nextMembers)
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
      const created = await teamApi.invite(email, inviteRole, inviteWorkspaceIds)
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

  function toggleMemberWorkspace(userId: string, workspaceId: string) {
    const current =
      members.find((m) => m.user_id === userId)?.workspace_ids ?? []
    const next = current.includes(workspaceId)
      ? current.filter((id) => id !== workspaceId)
      : [...current, workspaceId]
    // Optimistic: the menu stays open across toggles, so reflect the new
    // check state immediately; reload() reconciles with the server after.
    setMembers((prev) =>
      prev.map((m) =>
        m.user_id === userId ? { ...m, workspace_ids: next } : m,
      ),
    )
    void (async () => {
      try {
        await teamApi.setMemberWorkspaces(userId, next)
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : String(e))
      }
      await reload()
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
      availableWorkspaces={workspaces.map((w) => ({ id: w.id, name: w.name }))}
      inviteWorkspaceIds={inviteWorkspaceIds}
      onToggleInviteWorkspace={toggleInviteWorkspace}
      onRevokeInvite={revoke}
      onResendInvite={resend}
      onChangeMemberRole={changeRole}
      onRemoveMember={removeMember}
      onToggleMemberWorkspace={toggleMemberWorkspace}
    />
  )
}

// ─────────────────────────── API surface ───────────────────────────
// teamApi moved to web/app/lib/teamApi.ts (re-exported at the top of this file).
