"use client"

import { useEffect, useState } from "react"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { updateWorkspace } from "../../../../lib/onboarding/store"
import { SettingsMessage, SettingsSection } from "./SettingsLayout"

export function NotificationsSettings() {
  const { workspace, loading, refresh } = useWorkspace()
  const [slackConnected, setSlackConnected] = useState(false)
  const [channel, setChannel] = useState("#product")
  const [deliveryTime, setDeliveryTime] = useState("07:00")
  const [emailDigest, setEmailDigest] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    const n = workspace.notification_settings ?? {}
    setSlackConnected(Boolean(n.slack_connected))
    setChannel(String(n.slack_channel ?? "#product"))
    setDeliveryTime(String(n.brief_delivery_time ?? "07:00"))
    // `email_enabled` is the key the backend brief-delivery path actually reads
    // (app/synthesis/email_delivery.py). It defaults OFF when never set. We
    // still honor the legacy `email_digest` key for companies saved before this
    // toggle was wired to the delivery path.
    setEmailDigest(
      n.email_enabled != null ? Boolean(n.email_enabled) : n.email_digest === true,
    )
  }, [workspace])

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await updateWorkspace(workspace.id, {
        notification_settings: {
          slack_connected: slackConnected,
          slack_channel: channel,
          brief_delivery_time: deliveryTime,
          // The backend brief-delivery path keys off `email_enabled`; write that
          // so toggling the digest here actually controls whether brief emails
          // are sent. (Previously this wrote `email_digest`, which nothing read.)
          email_enabled: emailDigest,
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

  return (
    <SettingsSection title="Notifications" sub="Brief delivery and digest preferences.">
      <form onSubmit={onSave}>
        <div className="settings-row">
          <div>
            <div className="settings-row-label">Email digest</div>
            <div className="settings-row-sub">Weekly summary when Slack is not connected</div>
          </div>
          <button
            type="button"
            className={`toggle ${emailDigest ? "on" : ""}`}
            onClick={() => setEmailDigest((v) => !v)}
            aria-pressed={emailDigest}
          />
        </div>
        <div className="ob-slack-card" style={{ marginTop: 16 }}>
          <div className="ob-slack-title">Slack</div>
          <p className="ob-slack-sub">OAuth coming soon — toggle simulates connection for now.</p>
          <button type="button" className="btn btn-primary btn-block" onClick={() => setSlackConnected((v) => !v)}>
            {slackConnected ? "Disconnect Slack" : "Connect Slack"}
          </button>
        </div>
        {slackConnected && (
          <>
            <div className="field">
              <label className="field-label">Brief channel</label>
              <select className="input" value={channel} onChange={(e) => setChannel(e.target.value)}>
                <option>#product</option>
                <option>#eng-leadership</option>
                <option>#sprntly-briefs</option>
              </select>
            </div>
            <div className="field">
              <label className="field-label">Delivery time</label>
              <select className="input" value={deliveryTime} onChange={(e) => setDeliveryTime(e.target.value)}>
                <option value="07:00">7:00 AM</option>
                <option value="08:00">8:00 AM</option>
                <option value="09:00">9:00 AM</option>
              </select>
            </div>
          </>
        )}
        {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
        {saved && <SettingsMessage kind="success">Notification settings saved.</SettingsMessage>}
        <button type="submit" className="btn btn-primary" disabled={saving} style={{ marginTop: 16 }}>
          {saving ? "Saving…" : "Save notifications"}
        </button>
      </form>
    </SettingsSection>
  )
}
