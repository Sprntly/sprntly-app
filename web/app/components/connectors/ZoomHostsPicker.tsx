/**
 * Zoom host picker — mounted inside ZoomConfigSlot (the post-connect modal and
 * the Configure drawer). Chooses WHICH hosts' cloud-recording transcripts the
 * KG ingest reads. The selection is COMPANY-WIDE: stored on the company's Zoom
 * connection via POST /v1/connectors/zoom/users (admin-only — non-admins get a
 * read-only picker, and a 403 is rendered as the admin sentence, never a raw
 * status), honored by the puller and the scheduled refresh.
 *
 * No selection stored = every LICENSED host on the account. Saving an empty
 * selection clears back to that, which is also what a connection made before
 * this picker existed has.
 *
 * DELIBERATELY NOT Confluence's permissions line. Confluence's 3LO token acts
 * as the connecting person, so its picker says "Sprntly sees exactly what the
 * person who connected can see" — that sentence is FALSE here. Zoom's scopes
 * are all `:admin`, so one connection reaches every host on the account
 * regardless of who clicked Connect. The sentence that replaces it is about
 * what we read (transcripts and meeting details, never video or audio), which
 * is the thing a person actually wants to know before ticking a colleague's
 * name.
 *
 * Pure View pattern (props in, JSX out) for unit testing via
 * renderToStaticMarkup, plus a hooks-wired wrapper for the fetch + save
 * round-trips — mirrors ConfluenceSpacesPicker / SlackSyncChannelsPicker.
 */
"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ApiError,
  apiErrorMessage,
  connectorsApi,
  type ZoomUser,
} from "../../lib/api"
import { useWorkspace } from "../../context/WorkspaceContext"

/** A host the admin selected who is no longer in the live listing — see
 *  `ghostRows` below for why these are rendered rather than dropped. */
export type ZoomGhostHost = { id: string; name: string }

// ─────────────────────────── Pure View ───────────────────────────

export type ZoomHostsPickerViewProps = {
  hosts: ZoomUser[]
  /** Selected ids absent from `hosts` — rendered as dimmed, still-tickable
   *  rows so a stale selection is visible and removable rather than silent. */
  ghosts: ZoomGhostHost[]
  loading: boolean
  /** Inline error from list/save, or null. */
  error: string | null
  /** Host ids currently ticked in the picker (not yet saved). */
  selectedIds: ReadonlySet<string>
  /** How many hosts the persisted selection has (0 = sync every licensed host). */
  savedCount: number
  isSaving: boolean
  /** Zoom had more hosts than one listing pass returns. */
  fetchCapped: boolean
  /** False for a non-admin: checkboxes really disabled, actions replaced by a
   *  sentence saying who can change this. */
  canManage: boolean
  /** True while the connection is in the reconnect state — saving a selection
   *  against a dead token would just fail. */
  authExpired?: boolean
  filter: string
  onFilterChange: (value: string) => void
  onToggle: (hostId: string) => void
  onSave: () => void
  onClear: () => void
}

/** Accessible name for a row: BOTH the name and the email, because a Zoom
 *  account routinely holds two people with the same display name and a
 *  screen-reader user picking between them has only this string. */
export function hostLabel(h: Pick<ZoomUser, "display_name" | "email" | "id">): string {
  const name = h.display_name || h.email || h.id
  return h.email && h.email !== name ? `${name} (${h.email})` : name
}

/** The second line of a row. `recording_count` is null in this version, and
 *  the row still renders — a host we cannot count is not a host to hide. */
export function hostMeta(h: ZoomUser): string {
  const left = h.email || "No email on the Zoom account"
  const right =
    typeof h.recording_count === "number"
      ? `${h.recording_count} recording${h.recording_count === 1 ? "" : "s"}`
      : "Recording count unavailable"
  return `${left} · ${right}`
}

export function ZoomHostsPickerView({
  hosts,
  ghosts,
  loading,
  error,
  selectedIds,
  savedCount,
  isSaving,
  fetchCapped,
  canManage,
  authExpired = false,
  filter,
  onFilterChange,
  onToggle,
  onSave,
  onClear,
}: ZoomHostsPickerViewProps) {
  const query = filter.trim().toLowerCase()
  const visible = query
    ? hosts.filter(
        (h) =>
          (h.display_name || "").toLowerCase().includes(query) ||
          (h.email || "").toLowerCase().includes(query),
      )
    : hosts
  // Ghost rows are filtered by the same query so the filter never hides the
  // fact that a stale selection exists when the user is looking for it.
  const visibleGhosts = query
    ? ghosts.filter((g) => g.name.toLowerCase().includes(query))
    : ghosts

  const nothingToShow =
    !loading && hosts.length === 0 && ghosts.length === 0
  const filteredToNothing =
    !loading && !nothingToShow && visible.length === 0 && visibleGhosts.length === 0

  return (
    <div className="conn-slack-setup">
      <div>
        <span className="conn-slack-label">Hosts to sync</span>
        <p className="conn-slack-hint">
          Sprntly reads cloud-recording transcripts from the hosts selected
          here, on a schedule. This selection applies to your whole workspace
          and only admins can change it. With nothing selected, every licensed
          host on the Zoom account syncs.
        </p>
        <p className="conn-slack-hint">
          Sprntly reads transcripts and meeting details only — never the
          recording video or audio.
        </p>
      </div>

      {savedCount > 0 ? (
        <div className="conn-slack-saved">
          Syncing <strong>{savedCount}</strong>{" "}
          {savedCount === 1 ? "host" : "hosts"}
        </div>
      ) : null}

      {loading ? (
        <p className="conn-slack-hint">Loading hosts…</p>
      ) : nothingToShow ? (
        <p className="conn-slack-empty">
          No licensed Zoom users found. Cloud recording requires a paid Zoom
          licence — check that at least one user on the account is licensed,
          then reopen this panel.
        </p>
      ) : (
        <>
          <input
            type="search"
            className="conn-zoom-filter"
            aria-label="Filter hosts"
            placeholder="Filter hosts by name or email"
            value={filter}
            onChange={(e) => onFilterChange(e.target.value)}
          />

          {fetchCapped ? (
            <p className="conn-slack-hint">
              Showing the first {hosts.length} licensed hosts. Use the filter to
              find a specific host.
            </p>
          ) : null}

          {filteredToNothing ? (
            <p className="conn-slack-empty">No hosts match &ldquo;{filter}&rdquo;.</p>
          ) : (
            <div
              className="conn-slack-checklist"
              role="group"
              aria-label="Hosts to sync"
            >
              {visible.map((h) => (
                <label key={h.id} className="conn-zoom-check">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(h.id)}
                    disabled={!canManage}
                    onChange={() => onToggle(h.id)}
                  />
                  <span>
                    {hostLabel(h)}
                    <span className="conn-zoom-check-meta">{hostMeta(h)}</span>
                  </span>
                </label>
              ))}
              {/* A saved id the live listing no longer returns. Dropping it
                  would silently narrow a selection the admin made — and the
                  puller still syncs that host, so the picker would be lying
                  about what is happening. */}
              {visibleGhosts.map((g) => (
                <label
                  key={g.id}
                  className="conn-zoom-check conn-zoom-check--ghost"
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(g.id)}
                    disabled={!canManage}
                    onChange={() => onToggle(g.id)}
                  />
                  <span>
                    {g.name}
                    <span className="conn-zoom-check-meta">
                      {g.name || g.id} — no longer a licensed Zoom user
                    </span>
                  </span>
                </label>
              ))}
            </div>
          )}
        </>
      )}

      {error ? (
        <p className="conn-slack-error" role="alert">
          {error}
        </p>
      ) : null}

      {canManage ? (
        <div className="conn-slack-actions" style={{ gap: 8 }}>
          {selectedIds.size > 0 ? (
            <button
              type="button"
              className="btn btn-sm"
              disabled={isSaving || loading}
              onClick={onClear}
            >
              Clear selection — sync all hosts
            </button>
          ) : null}
          <button
            type="button"
            className="btn btn-sm btn-primary"
            aria-busy={isSaving}
            disabled={isSaving || loading || authExpired}
            onClick={onSave}
          >
            {isSaving ? "Saving…" : "Save hosts"}
          </button>
        </div>
      ) : (
        <p className="conn-slack-hint">
          Only a workspace admin can change which hosts sync.
        </p>
      )}
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  /** The persisted selection from the connection's config (sync_user_ids). */
  savedUserIds?: string[] | null
  /** {id: email-or-name} stored alongside the ids, so a deactivated host can
   *  be shown by name rather than as an opaque Zoom user id. */
  savedUserNames?: Record<string, string> | null
  /** True while the connection is in the reconnect state. */
  authExpired?: boolean
  /** Fired after a successful save so the parent can reload connections. */
  onSaved: () => void
}

export function ZoomHostsPicker({
  savedUserIds,
  savedUserNames,
  authExpired = false,
  onSaved,
}: Props) {
  const { orgRole } = useWorkspace()
  const canManage = orgRole === "owner" || orgRole === "admin"

  const [hosts, setHosts] = useState<ZoomUser[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Start from the persisted selection so the user sees their choices.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    () => new Set(savedUserIds ?? []),
  )
  const [names, setNames] = useState<Record<string, string>>(
    () => ({ ...(savedUserNames ?? {}) }),
  )
  const [isSaving, setIsSaving] = useState(false)
  const [fetchCapped, setFetchCapped] = useState(false)
  const [filter, setFilter] = useState("")

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await connectorsApi.listZoomUsers()
      setHosts(r.users)
      setFetchCapped(Boolean(r.fetch_capped))
      // Prefer the server's copy of both — it is the authority, and the props
      // can be a render behind after a save.
      if (Array.isArray(r.selected_ids)) setSelectedIds(new Set(r.selected_ids))
      if (r.selected_names) setNames((prev) => ({ ...prev, ...r.selected_names }))
    } catch (e) {
      setError(errorText(e))
      setHosts([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const handleToggle = useCallback((hostId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(hostId)) next.delete(hostId)
      else next.add(hostId)
      return next
    })
  }, [])

  const handleSave = useCallback(async () => {
    setIsSaving(true)
    setError(null)
    try {
      // Send {id, email} pairs so the backend can report a host who later
      // leaves the account by name. Ids the current listing doesn't know (a
      // deactivated host, or one past the listing cap) are KEPT — saving must
      // never silently drop part of the selection.
      const byId = new Map(hosts.map((h) => [h.id, h.email]))
      const payload = [...selectedIds].map((id) => ({
        id,
        email: byId.get(id) ?? names[id] ?? null,
      }))
      await connectorsApi.setZoomSyncUsers(payload)
      onSaved()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setIsSaving(false)
    }
  }, [hosts, selectedIds, names, onSaved])

  const handleClear = useCallback(() => {
    setSelectedIds(new Set())
  }, [])

  // Selected ids the live listing does not carry. The puller still syncs
  // these, so hiding them would misreport what Sprntly is doing.
  const ghosts = useMemo<ZoomGhostHost[]>(() => {
    const known = new Set(hosts.map((h) => h.id))
    return [...selectedIds]
      .filter((id) => !known.has(id))
      .map((id) => ({ id, name: names[id] || id }))
  }, [hosts, selectedIds, names])

  return (
    <ZoomHostsPickerView
      hosts={hosts}
      ghosts={ghosts}
      loading={loading}
      error={error}
      selectedIds={selectedIds}
      savedCount={(savedUserIds ?? []).length}
      isSaving={isSaving}
      fetchCapped={fetchCapped}
      canManage={canManage}
      authExpired={authExpired}
      filter={filter}
      onFilterChange={setFilter}
      onToggle={handleToggle}
      onSave={() => void handleSave()}
      onClear={handleClear}
    />
  )
}

/** A 403 here has exactly one cause — a non-admin trying to change an org-wide
 *  connector — and the backend's own sentence is the right one. Anything that
 *  is not an ApiError is rendered as its message rather than a status code:
 *  "Request failed (500)" tells a user nothing they can act on. */
function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) {
      return "Only a workspace admin can change which hosts sync."
    }
    return apiErrorMessage(e.status, e.body)
  }
  return e instanceof Error ? e.message : String(e)
}
