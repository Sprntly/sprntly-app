"use client"

import { useCallback, useEffect, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import {
  ApiError,
  apiErrorMessage,
  connectorsApi,
  type ConnectionSummary,
} from "../../../../lib/api"
import { updateWorkspace } from "../../../../lib/onboarding/store"
import { SlackChannelPicker } from "../../../connectors/SlackChannelPicker"
import { SettingsMessage, SettingsPaneBar, SettingsSection } from "./SettingsLayout"

const DAYS = [
  { value: 0, label: "Monday" },
  { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" },
  { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
]
// Short weekday names indexed by the DAYS value convention (0 = Monday).
const WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

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

/** "America/Los_Angeles" → "PT" (generic short name), cached per zone. */
const tzShortCache = new Map<string, string>()
function tzShort(tz: string): string {
  let s = tzShortCache.get(tz)
  if (s === undefined) {
    try {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        timeZoneName: "shortGeneric",
      }).formatToParts(new Date())
      s = parts.find((p) => p.type === "timeZoneName")?.value ?? ""
    } catch {
      s = ""
    }
    tzShortCache.set(tz, s)
  }
  return s
}

function tzOptionLabel(tz: string): string {
  const short = tzShort(tz)
  const pretty = tz.replace(/_/g, " ")
  return short ? `${pretty} (${short})` : pretty
}

/** "Monday, June 1 · 7:00 AM PT" — the next moment the brief will land, in
 *  the delivery timezone. Null if the zone is bogus (line simply hides). */
function nextBriefLabel(weekday: number, hour: number, tz: string): string | null {
  const target = WEEKDAY_SHORT[weekday]
  if (!target) return null
  try {
    const now = new Date()
    for (let i = 0; i <= 7; i++) {
      const cand = new Date(now.getTime() + i * 86400000)
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        weekday: "short",
        month: "long",
        day: "numeric",
        hour: "numeric",
        hour12: false,
      }).formatToParts(cand)
      const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ""
      if (get("weekday") !== target) continue
      // Today, but this week's send time already passed → next week's slot.
      if (i === 0 && Number(get("hour")) >= hour) continue
      const wdLong = new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        weekday: "long",
      }).format(cand)
      const short = tzShort(tz)
      return `${wdLong}, ${get("month")} ${get("day")} · ${hourLabel(hour)}${short ? ` ${short}` : ""}`
    }
    return null
  } catch {
    return null
  }
}

type ScheduleFields = {
  emailDigest: boolean
  weekday: number
  hour: number
  timezone: string
}

export function NotificationsSettings() {
  const { workspace, profile, loading, refresh } = useWorkspace()

  // "When" (company-wide, persisted on companies.notification_settings).
  const [emailDigest, setEmailDigest] = useState(false)
  const [weekday, setWeekday] = useState(0)
  const [hour, setHour] = useState(6)
  const [timezone, setTimezone] = useState("UTC")
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The last loaded/saved values — "Discard" restores these, and any deviation
  // from them arms the Save/Discard actions in the top bar.
  const [snapshot, setSnapshot] = useState<ScheduleFields | null>(null)

  // "Where" — the per-user Slack connection (separate from notification_settings).
  const [slack, setSlack] = useState<ConnectionSummary | null>(null)
  const [slackLoading, setSlackLoading] = useState(true)
  const [connecting, setConnecting] = useState(false)
  const [slackError, setSlackError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    const n = workspace.notification_settings ?? {}
    const loaded: ScheduleFields = {
      emailDigest:
        n.email_enabled != null ? Boolean(n.email_enabled) : n.email_digest === true,
      weekday: typeof n.brief_weekday === "number" ? n.brief_weekday : 0,
      hour: typeof n.brief_hour === "number" ? n.brief_hour : 6,
      timezone:
        typeof n.timezone === "string" && n.timezone ? n.timezone : browserTimezone(),
    }
    setEmailDigest(loaded.emailDigest)
    setWeekday(loaded.weekday)
    setHour(loaded.hour)
    setTimezone(loaded.timezone)
    setSnapshot(loaded)
  }, [workspace])

  const dirty =
    snapshot != null &&
    (emailDigest !== snapshot.emailDigest ||
      weekday !== snapshot.weekday ||
      hour !== snapshot.hour ||
      timezone !== snapshot.timezone)

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

  const onSave = useCallback(async () => {
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
      setSnapshot({ emailDigest, weekday, hour, timezone })
      setSaved(true)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save")
    } finally {
      setSaving(false)
    }
  }, [workspace, emailDigest, weekday, hour, timezone, refresh])

  function onDiscard() {
    if (!snapshot) return
    setEmailDigest(snapshot.emailDigest)
    setWeekday(snapshot.weekday)
    setHour(snapshot.hour)
    setTimezone(snapshot.timezone)
    setError(null)
  }

  if (loading) {
    return (
      <div className="pset">
        <div className="pset-body">
          <p className="settings-loading">Loading…</p>
        </div>
      </div>
    )
  }
  if (!workspace) {
    return (
      <div className="pset">
        <div className="pset-body">
          <SettingsSection title="Comms & Brief" sub="Complete onboarding first.">
            <p className="settings-placeholder">
              <a href="/onboarding/strategy">Finish onboarding →</a>
            </p>
          </SettingsSection>
        </div>
      </div>
    )
  }

  const slackActive = slack?.status === "active"
  const displayName = profileDisplayName(profile ?? null, profile?.email)
  const nextLabel = nextBriefLabel(weekday, hour, timezone)

  return (
    <div className="pset">
      <SettingsPaneBar
        title="Comms & Brief"
        meta={[displayName, profile?.email].filter(Boolean).join(" · ") || null}
        saved={saved}
        dirty={dirty}
        saving={saving}
        onDiscard={onDiscard}
        onSave={() => void onSave()}
      />

      <div className="pset-body">
        <h2 className="pset-title">Comms &amp; Brief delivery</h2>
        <p className="pset-sub">
          When the Brief lands, who gets it, and where. All editable — changes
          take effect on the next run.
        </p>

        <div className="pset-stack">
          {/* ───────── WHERE: Slack (per-user) ───────── */}
          <section className="pset-card">
            <div className="pset-card-head">
              <h3 className="pset-card-title">Slack</h3>
              <span className="pset-card-hint">· where the Brief is delivered</span>
            </div>
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
            )}
            {slackError && <SettingsMessage kind="error">{slackError}</SettingsMessage>}
          </section>

          {/* ───────── WHERE: email digest (company-wide) ─────────
              Single line: the card head doubles as the control row — title +
              hint left, toggle right (no inner row restating the same copy). */}
          <section className="pset-card">
            <div className="pset-card-head" style={{ marginBottom: 0 }}>
              <h3 className="pset-card-title">Email digest</h3>
              <span className="pset-card-hint">· also email the Brief to the team</span>
              <button
                type="button"
                className={`toggle ${emailDigest ? "on" : ""}`}
                onClick={() => setEmailDigest((v) => !v)}
                aria-pressed={emailDigest}
                aria-label="Email digest"
                style={{ marginLeft: "auto", alignSelf: "center", flexShrink: 0 }}
              />
            </div>
          </section>

          {/* ───────── WHEN: schedule (company-wide) ───────── */}
          <section className="pset-card">
            <div className="pset-card-head">
              <h3 className="pset-card-title">Schedule</h3>
              <span className="pset-card-hint">· when the Brief is generated and sent</span>
            </div>
            <div className="pset-grid pset-grid--3">
              <div className="pset-field">
                <label className="pset-label" htmlFor="comms-day">Day</label>
                <select
                  id="comms-day"
                  className="input"
                  value={weekday}
                  onChange={(e) => setWeekday(Number(e.target.value))}
                >
                  {DAYS.map((d) => (
                    <option key={d.value} value={d.value}>{d.label}s</option>
                  ))}
                </select>
              </div>
              <div className="pset-field">
                <label className="pset-label" htmlFor="comms-time">Time</label>
                <select
                  id="comms-time"
                  className="input"
                  value={hour}
                  onChange={(e) => setHour(Number(e.target.value))}
                >
                  {HOURS.map((h) => (
                    <option key={h.value} value={h.value}>{h.label}</option>
                  ))}
                </select>
              </div>
              <div className="pset-field">
                <label className="pset-label" htmlFor="comms-tz">Timezone</label>
                <select
                  id="comms-tz"
                  className="input"
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                >
                  {timezones().map((tz) => (
                    <option key={tz} value={tz}>{tzOptionLabel(tz)}</option>
                  ))}
                </select>
              </div>
            </div>
            {nextLabel && (
              <div className="pset-next-line">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <rect x="3" y="4" width="18" height="18" rx="2" />
                  <path d="M16 2v4M8 2v4M3 10h18" />
                </svg>
                <span>Next Brief will land</span>
                <strong>{nextLabel}</strong>
              </div>
            )}
          </section>
        </div>

        {error && (
          <div style={{ marginTop: 14 }}>
            <SettingsMessage kind="error">{error}</SettingsMessage>
          </div>
        )}
      </div>
    </div>
  )
}
