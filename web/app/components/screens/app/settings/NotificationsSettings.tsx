"use client"

import { useCallback, useEffect, useState } from "react"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import {
  ApiError,
  apiErrorMessage,
  connectorsApi,
  type ConnectionSummary,
} from "../../../../lib/api"
import { updateWorkspace } from "../../../../lib/onboarding/store"
import { SlackChannelPicker } from "../../../connectors/SlackChannelPicker"
import { SettingsMessage, SettingsSection } from "./SettingsLayout"

const DAYS = [
  { value: 0, label: "Monday" },
  { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" },
  { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
]

function hourLabel(h: number): string {
  const period = h < 12 ? "AM" : "PM"
  const display = h % 12 === 0 ? 12 : h % 12
  return `${display}:00 ${period}`
}
const HOURS = Array.from({ length: 24 }, (_, h) => ({ value: h, label: hourLabel(h) }))

function timezones(): string[] {
  // Full IANA list where supported; small sensible fallback otherwise.
  try {
    const fn = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] })
      .supportedValuesOf
    if (fn) return fn("timeZone")
  } catch {
    /* fall through */
  }
  return [
    "UTC",
    "America/Los_Angeles",
    "America/Denver",
    "America/Chicago",
    "America/New_York",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Australia/Sydney",
  ]
}

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  } catch {
    return "UTC"
  }
}

export function NotificationsSettings() {
  const { workspace, loading, refresh } = useWorkspace()

  // "When" (company-wide, persisted on companies.notification_settings).
  const [emailDigest, setEmailDigest] = useState(false)
  const [weekday, setWeekday] = useState(0)
  const [hour, setHour] = useState(6)
  const [timezone, setTimezone] = useState("UTC")
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // "Where" — the per-user Slack connection (separate from notification_settings).
  const [slack, setSlack] = useState<ConnectionSummary | null>(null)
  const [slackLoading, setSlackLoading] = useState(true)
  const [connecting, setConnecting] = useState(false)
  const [slackError, setSlackError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    const n = workspace.notification_settings ?? {}
    setEmailDigest(
      n.email_enabled != null ? Boolean(n.email_enabled) : n.email_digest === true,
    )
    setWeekday(typeof n.brief_weekday === "number" ? n.brief_weekday : 0)
    setHour(typeof n.brief_hour === "number" ? n.brief_hour : 6)
    setTimezone(typeof n.timezone === "string" && n.timezone ? n.timezone : browserTimezone())
  }, [workspace])

  const loadSlack = useCallback(async () => {
    setSlackLoading(true)
    setSlackError(null)
    try {
      const r = await connectorsApi.list()
      setSlack(r.connections.find((c) => c.provider === "slack") ?? null)
    } catch (e) {
      setSlackError(
        e instanceof ApiError ? apiErrorMessage(e.status, e.body)
          : e instanceof Error ? e.message : String(e),
      )
      setSlack(null)
    } finally {
      setSlackLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadSlack()
  }, [loadSlack])

  const onConnectSlack = useCallback(async () => {
    setConnecting(true)
    setSlackError(null)
    try {
      const r = await connectorsApi.startOauth(
        "slack",
        undefined,
        "/settings?section=comms-brief",
      )
      if (r.authorize_url) window.location.href = r.authorize_url
    } catch (e) {
      setSlackError(
        e instanceof ApiError ? apiErrorMessage(e.status, e.body)
          : e instanceof Error ? e.message : String(e),
      )
      setConnecting(false)
    }
  }, [])

  const onDisconnectSlack = useCallback(async () => {
    setConnecting(true)
    setSlackError(null)
    try {
      await connectorsApi.disconnectSlack()
      await loadSlack()
    } catch (e) {
      setSlackError(
        e instanceof ApiError ? apiErrorMessage(e.status, e.body)
          : e instanceof Error ? e.message : String(e),
      )
    } finally {
      setConnecting(false)
    }
  }, [loadSlack])

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      // Merge — don't clobber other notification_settings keys (email_recipients,
      // drip, etc.). Only the fields this page owns are overwritten.
      const existing = workspace.notification_settings ?? {}
      await updateWorkspace(workspace.id, {
        notification_settings: {
          ...existing,
          email_enabled: emailDigest,
          brief_weekday: weekday,
          brief_hour: hour,
          brief_minute: 0,
          timezone,
        },
      })
      await refresh()
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save")
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <p className="settings-loading">Loading…</p>
  if (!workspace) {
    return (
      <SettingsSection title="Notifications" sub="Complete onboarding first.">
        <p className="settings-placeholder">
          <a href="/onboarding/strategy">Finish onboarding →</a>
        </p>
      </SettingsSection>
    )
  }

  const slackActive = slack?.status === "active"

  return (
    <SettingsSection title="Notifications" sub="Choose where your weekly brief is delivered, and when.">
      {/* ───────── WHERE: Slack (per-user) ───────── */}
      <div className="ob-slack-card">
        <div className="ob-slack-title">Slack</div>
        {slackLoading ? (
          <p className="ob-slack-sub">Checking Slack connection…</p>
        ) : slackActive ? (
          <>
            <p className="ob-slack-sub">
              Connected{slack?.account_label ? ` — ${slack.account_label}` : ""}.
              Pick where Sprntly posts your brief below.
            </p>
            <SlackChannelPicker
              savedTargetType={slack?.config?.target_type as "channel" | "dm" | undefined}
              savedChannelId={slack?.config?.channel_id ?? null}
              savedChannelName={slack?.config?.channel_name ?? null}
              onSaved={loadSlack}
            />
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={connecting}
              onClick={onDisconnectSlack}
              style={{ marginTop: 12 }}
            >
              {connecting ? "Working…" : "Disconnect Slack"}
            </button>
          </>
        ) : (
          <>
            {/* One row: description left, action right (was a full-width
                btn-block bar under the text). */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
              <p className="ob-slack-sub" style={{ margin: 0 }}>
                Connect Slack to get your weekly brief in a channel or a DM.
              </p>
              <button
                type="button"
                className="btn btn-primary"
                style={{ flexShrink: 0 }}
                disabled={connecting}
                onClick={onConnectSlack}
              >
                {connecting ? "Opening Slack…" : "Connect Slack"}
              </button>
            </div>
          </>
        )}
        {slackError && <SettingsMessage kind="error">{slackError}</SettingsMessage>}
      </div>

      {/* ───────── WHERE: Email + WHEN: schedule (company-wide) ───────── */}
      <form onSubmit={onSave} style={{ marginTop: 16 }}>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Email digest</div>
            <div className="settings-row-sub">
              Also email the weekly brief to the team
            </div>
          </div>
          <button
            type="button"
            className={`toggle ${emailDigest ? "on" : ""}`}
            onClick={() => setEmailDigest((v) => !v)}
            aria-pressed={emailDigest}
          />
        </div>

        <div className="field">
          <label className="field-label">Delivery day</label>
          <select
            className="input"
            value={weekday}
            onChange={(e) => setWeekday(Number(e.target.value))}
          >
            {DAYS.map((d) => (
              <option key={d.value} value={d.value}>{d.label}</option>
            ))}
          </select>
        </div>

        <div className="field">
          <label className="field-label">Delivery time</label>
          <select
            className="input"
            value={hour}
            onChange={(e) => setHour(Number(e.target.value))}
          >
            {HOURS.map((h) => (
              <option key={h.value} value={h.value}>{h.label}</option>
            ))}
          </select>
        </div>

        <div className="field">
          <label className="field-label">Timezone</label>
          <select
            className="input"
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
          >
            {timezones().map((tz) => (
              <option key={tz} value={tz}>{tz}</option>
            ))}
          </select>
        </div>

        {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
        {saved && <SettingsMessage kind="success">Notification settings saved.</SettingsMessage>}
        <button type="submit" className="btn btn-primary" disabled={saving} style={{ marginTop: 16 }}>
          {saving ? "Saving…" : "Save notifications"}
        </button>
      </form>
    </SettingsSection>
  )
}
