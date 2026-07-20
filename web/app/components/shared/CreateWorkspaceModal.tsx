"use client"

import { useState } from "react"
import { useWorkspace } from "../../context/WorkspaceContext"
import { ApiError, workspacesApi } from "../../lib/api"
import { IconClose } from "./app-icons"

/**
 * "Create workspace" modal (multi-workspace 2026-07), shared by the sidebar
 * workspace switcher and Settings → Workspaces. Asks for a name, creates the
 * workspace, refreshes the workspace list, and makes the NEW workspace the
 * ACTIVE one (which re-scopes every screen via X-Workspace-Id + the dataset
 * slug). Parent owns only the open/close boolean.
 */
export function CreateWorkspaceModal({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const { refresh, setActiveWorkspace } = useWorkspace()
  const [name, setName] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!open) return null

  const close = () => {
    setName("")
    setError(null)
    setSubmitting(false)
    onClose()
  }

  const submit = async () => {
    const trimmed = name.trim()
    if (!trimmed) {
      setError("Please enter a workspace name.")
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const created = await workspacesApi.create(trimmed)
      // Reinitialize: reload the workspaces list, then flip the ACTIVE
      // workspace to the new one — every screen re-fetches under its dataset.
      await refresh()
      setActiveWorkspace(created.id)
      close()
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 403
          ? "Only admins can create workspaces."
          : "Couldn't create the workspace. Please try again.",
      )
      setSubmitting(false)
    }
  }

  return (
    <div
      className="modal-overlay open"
      role="dialog"
      aria-modal="true"
      aria-label="Create workspace"
      onClick={(e) => e.target === e.currentTarget && close()}
    >
      <div className="modal">
        <div className="modal-head">
          <div className="modal-head-text">
            <div className="modal-badge">Workspaces</div>
            <h2 className="modal-title">Create a workspace</h2>
            <p className="modal-sub">
              A workspace holds its own briefs, tickets, and chats — one per
              product area or team.
            </p>
          </div>
          <button
            type="button"
            className="modal-close"
            onClick={close}
            aria-label="Close"
          >
            <IconClose size={16} />
          </button>
        </div>

        <div style={{ padding: "0 26px 20px" }}>
          <label className="field-label" htmlFor="create-workspace-name">
            Workspace name
          </label>
          <input
            id="create-workspace-name"
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault()
                void submit()
              }
            }}
            maxLength={100}
            placeholder="e.g. Notifications"
            autoFocus
            disabled={submitting}
          />
          {error && (
            <p
              role="alert"
              style={{ color: "var(--danger, #c0392b)", fontSize: 13, marginTop: 10 }}
            >
              {error}
            </p>
          )}
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={close} disabled={submitting}>
            Cancel
          </button>
          <button
            className="btn btn-accent"
            onClick={() => void submit()}
            disabled={submitting || !name.trim()}
          >
            {submitting ? "Creating…" : "Create workspace"}
          </button>
        </div>
      </div>
    </div>
  )
}
