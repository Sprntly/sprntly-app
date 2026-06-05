/**
 * Slack channel picker — mounted in the Configure drawer's slot for the
 * Slack connector. Lets the user pick which channel Sprntly posts brief
 * notifications into.
 *
 * Pure View pattern (props in, JSX out) for unit testing via
 * renderToStaticMarkup, plus a hooks-wired wrapper that handles the
 * fetch + save round-trips.
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

export type SlackChannelPickerViewProps = {
  channels: SlackChannel[]
  loading: boolean
  /** Inline error from list/save, or null. */
  error: string | null
  /** The channel the user currently has selected in the picker (not yet saved). */
  selectedChannelId: string | null
  /** Name of the already-persisted target channel, if any. */
  savedChannelName: string | null
  isSaving: boolean
  onSelect: (channelId: string) => void
  onSave: () => void
}

function channelLabel(c: SlackChannel): string {
  // 🔒 for private, # for public — mirrors Slack's own UI conventions.
  return `${c.is_private ? "🔒" : "#"} ${c.name}`
}

export function SlackChannelPickerView({
  channels,
  loading,
  error,
  selectedChannelId,
  savedChannelName,
  isSaving,
  onSelect,
  onSave,
}: SlackChannelPickerViewProps) {
  const canSave = Boolean(selectedChannelId) && !isSaving

  return (
    <div className="conn-slack-setup">
      {savedChannelName ? (
        <div className="conn-slack-saved">
          Posting to <strong>#{savedChannelName}</strong>
        </div>
      ) : null}

      {loading ? (
        <p className="conn-slack-hint">Loading channels…</p>
      ) : channels.length === 0 ? (
        <p className="conn-slack-empty">
          No channels visible. Invite the Sprntly bot to a channel in
          Slack, then refresh this drawer.
        </p>
      ) : (
        <>
          <label className="conn-slack-label" htmlFor="slack-channel-select">
            Target channel
          </label>
          <select
            id="slack-channel-select"
            className="conn-slack-select"
            value={selectedChannelId ?? ""}
            onChange={(e) => onSelect(e.target.value)}
          >
            <option value="" disabled>
              Choose a channel…
            </option>
            {channels.map((c) => (
              <option key={c.id} value={c.id}>
                {channelLabel(c)}
              </option>
            ))}
          </select>
        </>
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
          disabled={!canSave}
          onClick={onSave}
        >
          {isSaving ? "Saving…" : "Save channel"}
        </button>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  /** The currently-saved channel id from the connection's config (if any). */
  savedChannelId?: string | null
  /** The currently-saved channel name from the connection's config. */
  savedChannelName?: string | null
  /** Fired after a successful save so the parent can reload connections. */
  onSaved: () => void
}

export function SlackChannelPicker({
  savedChannelId,
  savedChannelName,
  onSaved,
}: Props) {
  const [channels, setChannels] = useState<SlackChannel[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Default the picker to the already-saved channel so the user sees
  // their existing selection rather than the "Choose…" placeholder.
  const [selectedChannelId, setSelectedChannelId] = useState<string | null>(
    savedChannelId ?? null,
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

  const handleSave = useCallback(async () => {
    if (!selectedChannelId) return
    const picked = channels.find((c) => c.id === selectedChannelId)
    setIsSaving(true)
    setError(null)
    try {
      await connectorsApi.setSlackConfig(selectedChannelId, picked?.name)
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
  }, [selectedChannelId, channels, onSaved])

  return (
    <SlackChannelPickerView
      channels={channels}
      loading={loading}
      error={error}
      selectedChannelId={selectedChannelId}
      savedChannelName={savedChannelName ?? null}
      isSaving={isSaving}
      onSelect={setSelectedChannelId}
      onSave={() => void handleSave()}
    />
  )
}
