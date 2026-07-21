"use client"

import { useCallback, useEffect, useState } from "react"
import {
  profileDisplayName,
  useWorkspace,
} from "../../../../context/WorkspaceContext"
import {
  ApiError,
  workspacesApi,
  type WorkspaceMemberRecord,
  type WorkspaceSummary,
} from "../../../../lib/api"
import { CreateWorkspaceModal } from "../../../shared/CreateWorkspaceModal"
import { SettingsMessage, SettingsSection } from "./SettingsLayout"

/**
 * Settings → Workspaces (multi-workspace 2026-07).
 *
 * NOTE: distinct from WorkspaceSettings.tsx (the company's Product & Category
 * pane, which predates real workspaces). This pane manages the `workspaces`
 * rows themselves: list, inline rename, delete (non-default, admin), and each
 * workspace's member roster. Creation lives in the STICKY HEADER BAR (the
 * Profile-pane pattern) and opens the shared CreateWorkspaceModal, which also
 * flips the new workspace to ACTIVE app-wide on success.
 */

const ROLES = ["admin", "member", "viewer"] as const

export function WorkspacesSettings() {
  const { workspaces, activeWorkspace, orgRole, profile, refresh } = useWorkspace()
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)

  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState("")

  // Per-workspace expanded member roster.
  const [openMembersId, setOpenMembersId] = useState<string | null>(null)
  const [members, setMembers] = useState<WorkspaceMemberRecord[] | null>(null)

  const loadMembers = useCallback(async (workspaceId: string) => {
    setMembers(null)
    try {
      const r = await workspacesApi.members(workspaceId)
      setMembers(r.members)
    } catch {
      setMembers([])
    }
  }, [])

  useEffect(() => {
    if (openMembersId) void loadMembers(openMembersId)
  }, [openMembersId, loadMembers])

  async function renameWorkspace(w: WorkspaceSummary) {
    const name = renameValue.trim()
    if (!name || name === w.name) {
      setRenamingId(null)
      return
    }
    setBusyId(w.id)
    setError(null)
    try {
      await workspacesApi.rename(w.id, name)
      setRenamingId(null)
      await refresh()
    } catch {
      setError("Couldn't rename the workspace.")
    } finally {
      setBusyId(null)
    }
  }

  async function deleteWorkspace(w: WorkspaceSummary) {
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        `Delete workspace "${w.name}"? Its briefs, tickets, and chats are removed permanently.`,
      )
    ) {
      return
    }
    setBusyId(w.id)
    setError(null)
    try {
      await workspacesApi.remove(w.id)
      setNotice(`Workspace "${w.name}" deleted.`)
      await refresh()
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "The default workspace can't be deleted."
          : "Couldn't delete the workspace.",
      )
    } finally {
      setBusyId(null)
    }
  }

  async function setMemberRole(
    workspaceId: string,
    userId: string,
    role: (typeof ROLES)[number],
  ) {
    try {
      await workspacesApi.setMemberRole(workspaceId, userId, role)
      await loadMembers(workspaceId)
    } catch {
      setError("Couldn't update the member's role.")
    }
  }

  async function removeMember(workspaceId: string, userId: string) {
    try {
      await workspacesApi.removeMember(workspaceId, userId)
      await loadMembers(workspaceId)
    } catch {
      setError("Couldn't remove the member.")
    }
  }

  const identityMeta =
    [profileDisplayName(profile ?? null, profile?.email), profile?.email]
      .filter(Boolean)
      .join(" · ") || null
  // Workspace creation is ORG owner/admin only (backend-enforced) — a
  // workspace-level admin who is a plain org member doesn't get the button.
  const canCreate = orgRole === "owner" || orgRole === "admin"

  return (
    <div className="pset">
      {/* Sticky header bar (Profile-pane pattern) with the create action. */}
      <div className="pset-bar">
        <div className="pset-bar-id">
          <span className="pset-bar-title">Workspaces</span>
          {identityMeta && <span className="pset-bar-meta">· {identityMeta}</span>}
        </div>
        {canCreate && (
          <div className="pset-bar-actions">
            <button
              type="button"
              className="btn btn-primary pset-save"
              onClick={() => setCreateOpen(true)}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              New workspace
            </button>
          </div>
        )}
      </div>

      <div className="pset-body">
        <h2 className="pset-title">Workspaces</h2>
        <p className="pset-sub">
          A workspace is the space where you and your team collaborate. It is
          where we send insights, you draft PRD, tickets etc. Create different
          workspaces for different teams.
        </p>

        {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
        {notice && !error && (
          <SettingsMessage kind="success">{notice}</SettingsMessage>
        )}

        <SettingsSection
          title="Your workspaces"
          sub={`${workspaces.length} workspace${workspaces.length === 1 ? "" : "s"}`}
        >
          <div className="settings-list">
            {workspaces.map((w) => (
              <div key={w.id} className="settings-row" data-workspace={w.slug}>
                <div>
                  {renamingId === w.id ? (
                    <input
                      className="input"
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault()
                          void renameWorkspace(w)
                        }
                        if (e.key === "Escape") setRenamingId(null)
                      }}
                      maxLength={100}
                      autoFocus
                      aria-label={`Rename workspace ${w.name}`}
                    />
                  ) : (
                    <div className="settings-row-label">
                      {w.name}
                      {w.is_default && (
                        <span className="pset-card-hint"> · default</span>
                      )}
                      {activeWorkspace?.id === w.id && (
                        <span className="pset-card-hint"> · active</span>
                      )}
                    </div>
                  )}
                  <div className="settings-row-sub">
                    {w.dataset ? `dataset: ${w.dataset}` : w.slug} · your role: {w.role}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() =>
                      setOpenMembersId((cur) => (cur === w.id ? null : w.id))
                    }
                  >
                    {openMembersId === w.id ? "Hide members" : "Members"}
                  </button>
                  {w.role === "admin" &&
                    (renamingId === w.id ? (
                      <button
                        type="button"
                        className="btn btn-secondary"
                        onClick={() => void renameWorkspace(w)}
                        disabled={busyId === w.id}
                      >
                        Save
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-secondary"
                        onClick={() => {
                          setRenamingId(w.id)
                          setRenameValue(w.name)
                        }}
                      >
                        Rename
                      </button>
                    ))}
                  {w.role === "admin" && !w.is_default && (
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={() => void deleteWorkspace(w)}
                      disabled={busyId === w.id}
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>
            ))}
            {workspaces.length === 0 && (
              <p className="settings-placeholder">No workspaces yet.</p>
            )}
          </div>

          {openMembersId && (
            <div style={{ marginTop: 10 }}>
              <div className="settings-row-sub" style={{ marginBottom: 6 }}>
                Members of{" "}
                {workspaces.find((w) => w.id === openMembersId)?.name ?? "workspace"}
                {" · "}org admins always have access.
              </div>
              {members === null && (
                <p className="settings-loading">Loading members…</p>
              )}
              {members?.length === 0 && (
                <p className="settings-placeholder">
                  No explicit members — invite teammates to this workspace from
                  Settings → Team.
                </p>
              )}
              {members?.map((m) => (
                <div key={m.user_id} className="settings-row">
                  <div>
                    <div className="settings-row-label">
                      {m.display_name ?? m.email ?? m.user_id}
                    </div>
                    <div className="settings-row-sub">{m.email ?? ""}</div>
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <select
                      className="input"
                      value={m.role}
                      onChange={(e) =>
                        void setMemberRole(
                          openMembersId,
                          m.user_id,
                          e.target.value as (typeof ROLES)[number],
                        )
                      }
                      aria-label={`Role for ${m.email ?? m.user_id}`}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={() => void removeMember(openMembersId, m.user_id)}
                    >
                      Remove
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </SettingsSection>
      </div>

      <CreateWorkspaceModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  )
}
