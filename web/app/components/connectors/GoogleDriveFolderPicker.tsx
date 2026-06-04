"use client"

import { useCallback, useEffect, useState } from "react"
import { ApiError, apiErrorMessage, connectorsApi, type DriveFolderBrowse } from "../../lib/api"

type Crumb = { id: string; name: string }

type Props = {
  workspaceId: string
  dataset: string
  selectedFolderId?: string | null
  selectedFolderName?: string | null
  onSelected: () => void
}

export function GoogleDriveFolderPicker({
  workspaceId,
  dataset,
  selectedFolderId,
  selectedFolderName,
  onSelected,
}: Props) {
  const [open, setOpen] = useState(!selectedFolderId)
  const [breadcrumbs, setBreadcrumbs] = useState<Crumb[]>([
    { id: "root", name: "My Drive" },
  ])
  const [browse, setBrowse] = useState<DriveFolderBrowse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const currentId = breadcrumbs[breadcrumbs.length - 1]?.id ?? "root"

  const load = useCallback(async (parentId: string) => {
    setLoading(true)
    setError(null)
    try {
      const r = await connectorsApi.browseGoogleDriveFolders(workspaceId, parentId)
      setBrowse(r)
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setError(msg)
      setBrowse(null)
    } finally {
      setLoading(false)
    }
  }, [workspaceId])

  useEffect(() => {
    if (!open) return
    void load(currentId)
  }, [open, currentId, load])

  const openFolder = (id: string, name: string) => {
    setBreadcrumbs((prev) => [...prev, { id, name }])
  }

  const goToCrumb = (index: number) => {
    setBreadcrumbs((prev) => prev.slice(0, index + 1))
  }

  const selectCurrentFolder = async () => {
    if (currentId === "root") {
      setError("Choose a folder inside My Drive, not the root.")
      return
    }
    setSaving(true)
    setError(null)
    try {
      const name = browse?.current.name ?? currentId
      await connectorsApi.setGoogleDriveConfig(workspaceId, currentId, dataset, name)
      setOpen(false)
      onSelected()
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setError(msg)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="conn-drive-setup">
      {selectedFolderId ? (
        <div className="conn-drive-selected">
          <span className="conn-drive-selected-label">Synced folder</span>
          <strong>{selectedFolderName ?? selectedFolderId}</strong>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setOpen(true)}
          >
            Change folder
          </button>
        </div>
      ) : null}

      {open ? (
        <div className="conn-drive-browser">
          <div className="conn-drive-browser-head">
            <span className="conn-drive-browser-title">Choose a folder</span>
            {selectedFolderId ? (
              <button
                type="button"
                className="conn-drive-browser-cancel"
                onClick={() => setOpen(false)}
              >
                Cancel
              </button>
            ) : null}
          </div>

          <nav className="conn-drive-crumbs" aria-label="Folder path">
            {breadcrumbs.map((c, i) => (
              <span key={c.id} className="conn-drive-crumb-wrap">
                {i > 0 ? <span className="conn-drive-crumb-sep">/</span> : null}
                <button
                  type="button"
                  className="conn-drive-crumb"
                  disabled={i === breadcrumbs.length - 1}
                  onClick={() => goToCrumb(i)}
                >
                  {c.name}
                </button>
              </span>
            ))}
          </nav>

          {error ? <p className="conn-drive-error">{error}</p> : null}

          <div className="conn-drive-list" role="list">
            {loading ? (
              <p className="conn-drive-list-empty">Loading folders…</p>
            ) : browse && browse.folders.length === 0 ? (
              <p className="conn-drive-list-empty">No subfolders here.</p>
            ) : (
              browse?.folders.map((f) => (
                <div key={f.id} className="conn-drive-row" role="listitem">
                  <button
                    type="button"
                    className="conn-drive-row-open"
                    onClick={() => openFolder(f.id, f.name)}
                  >
                    <span className="conn-drive-folder-icon" aria-hidden />
                    <span className="conn-drive-row-name">{f.name}</span>
                  </button>
                </div>
              ))
            )}
          </div>

          <div className="conn-drive-browser-actions">
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={saving || loading || currentId === "root"}
              onClick={() => void selectCurrentFolder()}
            >
              {saving ? "Saving…" : `Use “${browse?.current.name ?? "this folder"}”`}
            </button>
          </div>

          <p className="conn-drive-hint">
            Open a folder to browse inside it, then choose which folder to sync into{" "}
            <strong>{dataset}</strong>. Files appear under Sources after you run Sync now.
          </p>
        </div>
      ) : null}
    </div>
  )
}
