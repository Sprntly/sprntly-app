"use client"

import { useCallback, useEffect, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import {
  ApiError,
  apiErrorMessage,
  connectorsApi,
  type ConnectionSummary,
} from "../../../../lib/api"
import {
  BRIEF_DAYS as DAYS,
  BRIEF_FREQUENCIES,
  BRIEF_HOURS as HOURS,
  type BriefFrequency,
  anchorForSave,
  browserTimezone,
  coerceWeekday,
  dayOptionLabel,
  frequencyUsesDay,
  nextBriefLabel,
  resolveFrequency,
  timezones,
  tzOptionLabel,
} from "../../../../lib/briefSchedule"
import { updateWorkspace } from "../../../../lib/onboarding/store"
import { INSIGHT_TYPES, cleanInsightTypes } from "../../../../lib/insight-types"
import { SlackChannelPicker } from "../../../connectors/SlackChannelPicker"
import { SettingsMessage, SettingsPaneBar, SettingsSection } from "./SettingsLayout"


type ScheduleFields = {
  emailDigest: boolean
  frequency: BriefFrequency
  weekday: number
  hour: number
  timezone: string
  // Workspace-level Top Insights filter — which insight types the brief should
  // surface for everyone in the workspace (companies.notification_settings.
  // brief_insight_types / brief_insight_note). Empty = surface everything.
  insightTypes: string[]
  insightNote: string
}

/** Stable order-insensitive key for comparing an insight-type selection. */
function typesKey(types: string[]): string {
  return [...types].sort().join(",")
}

export function NotificationsSettings() {
  const { workspace, profile, loading, refresh } = useWorkspace()

  // "When" (company-wide, persisted on companies.notification_settings).
  const [emailDigest, setEmailDigest] = useState(false)
  const [frequency, setFrequency] = useState<BriefFrequency>("weekly")
  const [weekday, setWeekday] = useState(0)
  const [hour, setHour] = useState(6)
  const [timezone, setTimezone] = useState("UTC")
  // Workspace-level insight-type filter + free-text note.
  const [insightTypes, setInsightTypes] = useState<string[]>([])
  const [insightNote, setInsightNote] = useState("")
  // The persisted "every other week" anchor. Kept out of ScheduleFields on
  // purpose: it is derived, never edited directly, so it must not arm Save.
  const [storedAnchor, setStoredAnchor] = useState<string | null>(null)
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
      // Absent/unknown → weekly, so a company saved before this control existed
      // keeps firing exactly as it does today.
      frequency: resolveFrequency(n),
      // Coerced to Mon–Fri: the Day picker no longer offers a weekend, and a
      // <select> holding an unofferable value renders a lie.
      weekday: coerceWeekday(typeof n.brief_weekday === "number" ? n.brief_weekday : 0),
      hour: typeof n.brief_hour === "number" ? n.brief_hour : 6,
      timezone:
        typeof n.timezone === "string" && n.timezone ? n.timezone : browserTimezone(),
      insightTypes: cleanInsightTypes(n.brief_insight_types),
      insightNote: typeof n.brief_insight_note === "string" ? n.brief_insight_note : "",
    }
    setEmailDigest(loaded.emailDigest)
    setFrequency(loaded.frequency)
    setWeekday(loaded.weekday)
    setHour(loaded.hour)
    setTimezone(loaded.timezone)
    setInsightTypes(loaded.insightTypes)
    setInsightNote(loaded.insightNote)
    setStoredAnchor(typeof n.brief_anchor_date === "string" ? n.brief_anchor_date : null)
    setSnapshot(loaded)
  }, [workspace])

  const dirty =
    snapshot != null &&
    (emailDigest !== snapshot.emailDigest ||
      frequency !== snapshot.frequency ||
      weekday !== snapshot.weekday ||
      hour !== snapshot.hour ||
      timezone !== snapshot.timezone ||
      typesKey(insightTypes) !== typesKey(snapshot.insightTypes) ||
      insightNote.trim() !== snapshot.insightNote.trim())

  function toggleInsightType(value: string) {
    setInsightTypes((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
    )
    setSaved(false)
  }

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
      const anchor = anchorForSave(new Date(), timezone, { weekday, hour })
      await updateWorkspace(workspace.id, {
        notification_settings: {
          ...existing,
          email_enabled: emailDigest,
          brief_frequency: frequency,
          // "Every other week" needs an anchor to be deterministic. Stamp it on
          // every save (cheap, and harmless for the other cadences, which
          // ignore it): the anchor is the date of the FIRST run after this
          // save, so the next brief the user sees is always an ON week and the
          // alternation counts from there.
          brief_anchor_date: anchor,
          brief_weekday: weekday,
          brief_hour: hour,
          brief_minute: 0,
          timezone,
          // Workspace-level Top Insights filter. Cleaned to known slugs so a
          // stale client can't violate the companies_brief_insight_types check
          // constraint; note is stored as null when blank.
          brief_insight_types: cleanInsightTypes(insightTypes),
          brief_insight_note: insightNote.trim() || null,
        },
      })
      setStoredAnchor(anchor)
      setSnapshot({ emailDigest, frequency, weekday, hour, timezone, insightTypes, insightNote })
      setSaved(true)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save")
    } finally {
      setSaving(false)
    }
  }, [workspace, emailDigest, frequency, weekday, hour, timezone, insightTypes, insightNote, refresh])

  function onDiscard() {
    if (!snapshot) return
    setEmailDigest(snapshot.emailDigest)
    setFrequency(snapshot.frequency)
    setWeekday(snapshot.weekday)
    setHour(snapshot.hour)
    setTimezone(snapshot.timezone)
    setInsightTypes(snapshot.insightTypes)
    setInsightNote(snapshot.insightNote)
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
              <a href="/onboarding/workspace">Finish onboarding →</a>
            </p>
          </SettingsSection>
        </div>
      </div>
    )
  }

  const slackActive = slack?.status === "active"
  const displayName = profileDisplayName(profile ?? null, profile?.email)
  const showDay = frequencyUsesDay(frequency)
  // Preview the anchor that will actually be IN EFFECT. While the form is
  // clean that's the persisted one (so a saved biweekly schedule correctly
  // shows its off-week skip); an unsaved edit re-anchors from now, which is
  // precisely what Save is about to store.
  const previewAnchor = dirty
    ? anchorForSave(new Date(), timezone, { weekday, hour })
    : storedAnchor
  const nextLabel = nextBriefLabel(new Date(), timezone, {
    weekday,
    hour,
    frequency,
    anchor: previewAnchor,
  })

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
        <h2 className="pset-title">Communications to you on the Top Product Insights</h2>
        <p className="pset-sub">
          We send you notifications when we find insights about your business,
          select the channel and the cadence.
        </p>

        <div className="pset-stack">
          {/* ───────── WHAT: workspace Top Insights filter ─────────
              Workspace-level — the admin picks which insight types the brief
              surfaces for everyone. Empty = surface everything. */}
          <section className="pset-card">
            <div className="pset-card-head">
              <h3 className="pset-card-title">Top Insights</h3>
              <span className="pset-card-hint">
                · what your workspace should surface — pick any, or leave empty for everything
              </span>
            </div>
            <div className="metric-chips" data-field="insight-types">
              {INSIGHT_TYPES.map((opt) => {
                const isSel = insightTypes.includes(opt.value)
                return (
                  <button
                    type="button"
                    key={opt.value}
                    className={`metric ${isSel ? "sel" : ""}`}
                    aria-pressed={isSel}
                    title={opt.description}
                    onClick={() => toggleInsightType(opt.value)}
                  >
                    {opt.label}
                  </button>
                )
              })}
            </div>
            <div className="pset-field" style={{ marginTop: 14 }}>
              <label className="pset-label" htmlFor="comms-insight-note">
                Or describe it in your words (optional)
              </label>
              <textarea
                id="comms-insight-note"
                className="input"
                rows={3}
                value={insightNote}
                maxLength={1000}
                onChange={(e) => {
                  setInsightNote(e.target.value)
                  setSaved(false)
                }}
                placeholder='e.g. "Show the top user problems and the one thing we should ship this week to move activation."'
              />
            </div>
          </section>

          {/* ───────── WHERE: Slack (per-user) ───────── */}
          <section className="pset-card">
            <div className="pset-card-head">
              <h3 className="pset-card-title">Slack</h3>
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
                  Connect your slack and we will send your product comms through it
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
              <h3 className="pset-card-title">
                Email Digest: Send communication to me via email.
              </h3>
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
              <h3 className="pset-card-title">
                Schedule, select a day and time to receive insights about your product.
              </h3>
            </div>
            <div className="pset-grid pset-grid--3">
              <div className="pset-field">
                <label className="pset-label" htmlFor="comms-frequency">Frequency</label>
                <select
                  id="comms-frequency"
                  className="input"
                  value={frequency}
                  onChange={(e) => setFrequency(e.target.value as BriefFrequency)}
                >
                  {BRIEF_FREQUENCIES.map((f) => (
                    <option key={f.value} value={f.value}>{f.label}</option>
                  ))}
                </select>
              </div>
              {/* Daily (weekdays) fires Mon–Fri, so the Day picker would be a
                  control that does nothing — hide it rather than show it inert. */}
              {showDay && (
                <div className="pset-field">
                  <label className="pset-label" htmlFor="comms-day">Day</label>
                  <select
                    id="comms-day"
                    className="input"
                    value={weekday}
                    onChange={(e) => setWeekday(Number(e.target.value))}
                  >
                    {DAYS.map((d) => (
                      <option key={d.value} value={d.value}>
                        {dayOptionLabel(d.label, frequency)}
                      </option>
                    ))}
                  </select>
                </div>
              )}
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
