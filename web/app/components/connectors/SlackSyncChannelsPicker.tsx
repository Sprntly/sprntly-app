/**
 * Slack pull-channel picker — mounted next to the notification-target picker
 * in the Slack config slots (post-connect modal + Configure drawer). Lets the
 * user choose WHICH channels the corpus sync reads messages from; the
 * selection is stored on the connection config (sync_channel_ids /
 * sync_channel_names) and honored by /slack/sync-to-corpus.
 *
 * No selection stored = the sync's legacy behavior: every channel the bot is
 * a member of. Saving an empty selection clears back to that.
 *
 * Pure View pattern (props in, JSX out) for unit testing via
 * renderToStaticMarkup, plus a hooks-wired wrapper that handles the
 * fetch + save round-trips — mirrors SlackChannelPicker.
 */
"use client"

import { useCallback, useEffect, useState } from "react"
import {
  ApiError,
  apiErrorMessage,
  connectorsApi,
  type SlackChannel,
} from "../../lib/api"

// ─────────────────────────── Pure View ───────────────────────────

export type SlackSyncChannelsPickerViewProps = {
  channels: SlackChannel[]
  loading: boolean
  /** Inline error from list/save, or null. */
  error: string | null
  /** Channel ids currently ticked in the picker (not yet saved). */
  selectedIds: ReadonlySet<string>
  /** How many channels the persisted selection has (0 = pull everything). */
  savedCount: number
  isSaving: boolean
  onToggle: (channelId: string) => void
  onSave: () => void
}

function channelLabel(c: SlackChannel): string {
  // 🔒 for private, # for public — mirrors Slack's own UI conventions.
  return `${c.is_private ? "🔒" : "#"} ${c.name}`
}

export function SlackSyncChannelsPickerView({
  channels,
  loading,
  error,
  selectedIds,
  savedCount,
  isSaving,
  onToggle,
  onSave,
}: SlackSyncChannelsPickerViewProps) {
  return (
    <div className="conn-slack-setup">
      <div>
        <span className="conn-slack-label">Channels to pull from</span>
        <p className="conn-slack-hint">
          Sprntly reads messages only from the channels you select here.
          With nothing selected, it reads every channel the bot has been
          invited to.
        </p>
      </div>

      {savedCount > 0 ? (
        <div className="conn-slack-saved">
          Pulling from <strong>{savedCount}</strong>{" "}
          {savedCount === 1 ? "channel" : "channels"}
        </div>
      ) : null}

      {loading ? (
        <p className="conn-slack-hint">Loading channels…</p>
      ) : channels.length === 0 ? (
        <p className="conn-slack-empty">
          No channels visible. Invite the Sprntly bot to a channel in
          Slack, then reopen this panel.
        </p>
      ) : (
        <div className="conn-slack-checklist" role="group" aria-label="Channels to pull from">
          {channels.map((c) => (
            <label key={c.id} className="conn-slack-check">
              <input
                type="checkbox"
                checked={selectedIds.has(c.id)}
                onChange={() => onToggle(c.id)}
              />
              {channelLabel(c)}
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
          {isSaving ? "Saving…" : "Save channels"}
        </button>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  /** The persisted selection from the connection's config (sync_channel_ids). */
  savedChannelIds?: string[] | null
  /** Fired after a successful save so the parent can reload connections. */
  onSaved: () => void
}

export function SlackSyncChannelsPicker({ savedChannelIds, onSaved }: Props) {
  const [channels, setChannels] = useState<SlackChannel[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Start from the persisted selection so the user sees their choices.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    () => new Set(savedChannelIds ?? []),
  )
  const [isSaving, setIsSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await connectorsApi.listSlackChannels()
      setChannels(r.channels)
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : e instanceof Error
            ? e.message
            : String(e)
      setError(msg)
      setChannels([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const handleToggle = useCallback((channelId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(channelId)) next.delete(channelId)
      else next.add(channelId)
      return next
    })
  }, [])

  const handleSave = useCallback(async () => {
    setIsSaving(true)
    setError(null)
    try {
      // Send {id, name} pairs so the backend can report an unjoined channel
      // by name at sync time. Ids the channel list no longer knows (e.g. a
      // previously saved private channel not visible right now) are kept —
      // saving must never silently drop part of the user's selection.
      const byId = new Map(channels.map((c) => [c.id, c.name]))
      const payload = [...selectedIds].map((id) => ({
        id,
        name: byId.get(id),
      }))
      await connectorsApi.setSlackSyncChannels(payload)
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
  }, [channels, selectedIds, onSaved])

  return (
    <SlackSyncChannelsPickerView
      channels={channels}
      loading={loading}
      error={error}
      selectedIds={selectedIds}
      savedCount={(savedChannelIds ?? []).length}
      isSaving={isSaving}
      onToggle={handleToggle}
      onSave={() => void handleSave()}
    />
  )
}
