/**
 * Slack notification-target picker — mounted in the Configure drawer's slot
 * for the Slack connector. Lets the user choose where Sprntly posts brief
 * notifications: a direct message to themselves, or a channel.
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

export type SlackTargetType = "channel" | "dm"

export type SlackChannelPickerViewProps = {
  channels: SlackChannel[]
  loading: boolean
  /** Inline error from list/save, or null. */
  error: string | null
  /** Where to deliver: a channel, or a DM to the connected user. */
  targetType: SlackTargetType
  /** The channel the user currently has selected in the picker (not yet saved). */
  selectedChannelId: string | null
  /** Name of the already-persisted target channel, if any. */
  savedChannelName: string | null
  /** The persisted target type, for the "currently delivering to…" line. */
  savedTargetType: SlackTargetType | null
  isSaving: boolean
  onTargetTypeChange: (t: SlackTargetType) => void
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
  targetType,
  selectedChannelId,
  savedChannelName,
  savedTargetType,
  isSaving,
  onTargetTypeChange,
  onSelect,
  onSave,
}: SlackChannelPickerViewProps) {
  // DM needs no channel; channel needs one picked.
  const canSave =
    !isSaving && (targetType === "dm" || Boolean(selectedChannelId))

  return (
    <div className="conn-slack-setup">
      {savedTargetType === "dm" ? (
        <div className="conn-slack-saved">
          Sending you a <strong>direct message</strong>
        </div>
      ) : savedChannelName ? (
        <div className="conn-slack-saved">
          Posting to <strong>#{savedChannelName}</strong>
        </div>
      ) : null}

      <fieldset className="conn-slack-target">
        <label className="conn-slack-radio">
          <input
            type="radio"
            name="slack-target"
            value="dm"
            checked={targetType === "dm"}
            onChange={() => onTargetTypeChange("dm")}
          />
          Direct message to me
        </label>
        <label className="conn-slack-radio">
          <input
            type="radio"
            name="slack-target"
            value="channel"
            checked={targetType === "channel"}
            onChange={() => onTargetTypeChange("channel")}
          />
          A channel
        </label>
      </fieldset>

      {targetType === "channel" ? (
        loading ? (
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
        )
      ) : null}

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
          {isSaving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  /** The currently-saved target type from the connection's config. */
  savedTargetType?: SlackTargetType | null
  /** The currently-saved channel id from the connection's config (if any). */
  savedChannelId?: string | null
  /** The currently-saved channel name from the connection's config. */
  savedChannelName?: string | null
  /** Fired after a successful save so the parent can reload connections. */
  onSaved: () => void
}

export function SlackChannelPicker({
  savedTargetType,
  savedChannelId,
  savedChannelName,
  onSaved,
}: Props) {
  const [channels, setChannels] = useState<SlackChannel[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [targetType, setTargetType] = useState<SlackTargetType>(
    savedTargetType ?? "channel",
  )
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
    // Only the channel target needs the channel list.
    if (targetType === "channel") void load()
  }, [load, targetType])

  const handleSave = useCallback(async () => {
    setIsSaving(true)
    setError(null)
    try {
      if (targetType === "dm") {
        await connectorsApi.setSlackConfig({ targetType: "dm" })
      } else {
        if (!selectedChannelId) return
        const picked = channels.find((c) => c.id === selectedChannelId)
        await connectorsApi.setSlackConfig({
          targetType: "channel",
          channelId: selectedChannelId,
          channelName: picked?.name,
        })
      }
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
  }, [targetType, selectedChannelId, channels, onSaved])

  return (
    <SlackChannelPickerView
      channels={channels}
      loading={loading}
      error={error}
      targetType={targetType}
      selectedChannelId={selectedChannelId}
      savedChannelName={savedChannelName ?? null}
      savedTargetType={savedTargetType ?? null}
      isSaving={isSaving}
      onTargetTypeChange={setTargetType}
      onSelect={setSelectedChannelId}
      onSave={() => void handleSave()}
    />
  )
}
