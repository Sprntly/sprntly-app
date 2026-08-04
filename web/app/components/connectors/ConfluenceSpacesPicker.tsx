/**
 * Confluence space picker — mounted in the Confluence config slots (the
 * post-connect modal and the Configure drawer). Chooses WHICH spaces the KG
 * ingest reads pages and blog posts from. The selection is COMPANY-WIDE:
 * stored on the company's Confluence connection via POST
 * /v1/connectors/confluence/spaces (admin-only — non-admins get the
 * admin-gate error inline), honored by the puller and the scheduled refresh.
 *
 * No selection stored = every space the connected account can read. Saving an
 * empty selection clears back to that, which is also what a connection made
 * before this picker existed has.
 *
 * The permissions line in the hint copy is the one that matters most: 3LO
 * acts AS the connecting user, so what appears here is bounded by that
 * person's own Confluence access. "Sprntly is missing our X docs" is almost
 * always that, not a bug.
 *
 * Pure View pattern (props in, JSX out) for unit testing via
 * renderToStaticMarkup, plus a hooks-wired wrapper for the fetch + save
 * round-trips — mirrors SlackSyncChannelsPicker.
 */
"use client"

import { useCallback, useEffect, useState } from "react"
import {
  ApiError,
  apiErrorMessage,
  connectorsApi,
  type ConfluenceSpace,
} from "../../lib/api"

// ─────────────────────────── Pure View ───────────────────────────

export type ConfluenceSpacesPickerViewProps = {
  spaces: ConfluenceSpace[]
  loading: boolean
  /** Inline error from list/save, or null. */
  error: string | null
  /** Space ids currently ticked in the picker (not yet saved). */
  selectedIds: ReadonlySet<string>
  /** How many spaces the persisted selection has (0 = sync everything). */
  savedCount: number
  isSaving: boolean
  onToggle: (spaceId: string) => void
  onSave: () => void
}

function spaceLabel(s: ConfluenceSpace): string {
  const name = s.name || s.key || s.id
  return s.key && s.key !== name ? `${name} (${s.key})` : name
}

export function ConfluenceSpacesPickerView({
  spaces,
  loading,
  error,
  selectedIds,
  savedCount,
  isSaving,
  onToggle,
  onSave,
}: ConfluenceSpacesPickerViewProps) {
  return (
    <div className="conn-slack-setup">
      <div>
        <span className="conn-slack-label">Spaces to sync</span>
        <p className="conn-slack-hint">
          Sprntly reads pages and blog posts only from the spaces selected
          here, on a schedule. This selection applies to your whole workspace
          and only admins can change it. With nothing selected, every space
          the connected account can read is synced.
        </p>
        <p className="conn-slack-hint">
          Sprntly sees exactly what the person who connected Confluence can
          see — space permissions and page restrictions still apply.
        </p>
      </div>

      {savedCount > 0 ? (
        <div className="conn-slack-saved">
          Syncing <strong>{savedCount}</strong>{" "}
          {savedCount === 1 ? "space" : "spaces"}
        </div>
      ) : null}

      {loading ? (
        <p className="conn-slack-hint">Loading spaces…</p>
      ) : spaces.length === 0 ? (
        <p className="conn-slack-empty">
          No spaces visible. The connected Confluence account needs access to
          at least one space — check its permissions, then reopen this panel.
        </p>
      ) : (
        <div
          className="conn-slack-checklist"
          role="group"
          aria-label="Spaces to sync"
        >
          {spaces.map((s) => (
            <label key={s.id} className="conn-slack-check">
              <input
                type="checkbox"
                checked={selectedIds.has(s.id)}
                onChange={() => onToggle(s.id)}
              />
              {spaceLabel(s)}
            </label>
          ))}
        </div>
      )}

      {error ? (
        <p className="conn-slack-error" role="alert">
          {error}
        </p>
      ) : null}

      <div className="conn-slack-actions">
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={isSaving || loading}
          onClick={onSave}
        >
          {isSaving ? "Saving…" : "Save spaces"}
        </button>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  /** The persisted selection from the connection's config (sync_space_ids). */
  savedSpaceIds?: string[] | null
  /** Fired after a successful save so the parent can reload connections. */
  onSaved: () => void
}

export function ConfluenceSpacesPicker({ savedSpaceIds, onSaved }: Props) {
  const [spaces, setSpaces] = useState<ConfluenceSpace[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Start from the persisted selection so the user sees their choices.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    () => new Set(savedSpaceIds ?? []),
  )
  const [isSaving, setIsSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await connectorsApi.listConfluenceSpaces()
      setSpaces(r.spaces)
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setError(msg)
      setSpaces([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const handleToggle = useCallback((spaceId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(spaceId)) next.delete(spaceId)
      else next.add(spaceId)
      return next
    })
  }, [])

  const handleSave = useCallback(async () => {
    setIsSaving(true)
    setError(null)
    try {
      // Send {id, key} pairs so the backend can report a space that later
      // becomes unreadable by name. Ids the current listing doesn't know
      // (a space temporarily invisible, or one past the listing cap) are
      // KEPT — saving must never silently drop part of the selection.
      const byId = new Map(spaces.map((s) => [s.id, s.key]))
      const payload = [...selectedIds].map((id) => ({
        id,
        key: byId.get(id),
      }))
      await connectorsApi.setConfluenceSyncSpaces(payload)
      onSaved()
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setError(msg)
    } finally {
      setIsSaving(false)
    }
  }, [spaces, selectedIds, onSaved])

  return (
    <ConfluenceSpacesPickerView
      spaces={spaces}
      loading={loading}
      error={error}
      selectedIds={selectedIds}
      savedCount={(savedSpaceIds ?? []).length}
      isSaving={isSaving}
      onToggle={handleToggle}
      onSave={() => void handleSave()}
    />
  )
}
