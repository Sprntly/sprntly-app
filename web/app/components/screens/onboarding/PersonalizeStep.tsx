"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { OptionalDisclosure } from "../../onboarding/OptionalDisclosure"
import { useOnboarding } from "../../../context/OnboardingContext"
import { useContent } from "../../../context/ContentContext"
import { updateWorkspace } from "../../../lib/onboarding/store"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { connectorsApi, type ConnectionSummary } from "../../../lib/api"
import { hasLiveAnalyticsConnection } from "../../../lib/onboarding/connectorsWizard"
import { hasDataSourceConnection } from "../../../lib/connectorsCatalog"
import { prefetchMetricDefinitions } from "../../../lib/onboarding/draftPrefetch"
import {
  POST_ONBOARDING_PATH,
  finishOnboardingAndEnterApp,
} from "../../../lib/onboarding/finishOnboarding"
import {
  BRIEF_DAYS,
  BRIEF_FREQUENCIES,
  BRIEF_HOURS,
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
} from "../../../lib/briefSchedule"
import { SlackChannelPicker } from "../../connectors/SlackChannelPicker"
import { Check } from "../../auth/icons"

const DRAFT_KEY = "personalize-step"

/**
 * What the workspace surfaces. The slugs are the contract with
 * companies_brief_insight_types_check (migration 20260721140000) — adding one
 * here needs the constraint widened in the same change.
 */
export const INSIGHT_TYPES: { value: string; label: string }[] = [
  { value: "top_problems", label: "Top user problems & opportunities" },
  { value: "drive_metric", label: "What I should work on to drive my metric" },
  { value: "emerging_complaints", label: "Emerging user complaints" },
  { value: "competitor_moves", label: "Competitor & market moves" },
  { value: "reliability_signals", label: "Reliability & incident signals" },
  { value: "wins", label: "Wins to celebrate" },
]

/** Where the brief lands. Teams has no backend delivery path yet. */
const DESTINATIONS: { value: string; label: string; disabled?: boolean }[] = [
  { value: "slack", label: "Slack" },
  { value: "teams", label: "Microsoft Teams", disabled: true },
  { value: "email", label: "Email" },
]

/**
 * Onboarding step 09 — "Personalize your workspace" (2026-07-21 spec).
 *
 * Two halves:
 *   - What the workspace should surface: insight-type chips plus a free-text
 *     override. Persisted as notification_settings.brief_insight_types (+
 *     brief_insight_note), NOT a new table — every other brief-delivery
 *     preference already lives in that blob and the schedule migration
 *     explicitly argues for keeping it that way.
 *   - Delivery, behind a disclosure: frequency / destination / day / time /
 *     timezone. These are the SAME keys Settings → Comms & Brief writes, and
 *     the option vocabularies come from the shared briefSchedule module, so the
 *     two surfaces cannot drift.
 *
 * This is also where the define-metrics gate now lives. It used to sit on
 * ReviewStep, but personalize was inserted between review and the sub-flow, so
 * the branch moved with the hand-off: with a live analytics connection we go on
 * to confirm each metric's event mapping; without one there is nothing to map
 * against, so this screen runs the closer and enters the app directly.
 */
export function PersonalizeStep() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const { setContent } = useContent()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [surfaces, setSurfaces] = useState<string[]>(
    (draft?.surfaces as string[]) ?? ["top_problems", "drive_metric"],
  )
  const [note, setNote] = useState((draft?.note as string) ?? "")

  const [frequency, setFrequency] = useState<BriefFrequency>("weekly")
  const [destination, setDestination] = useState("slack")
  const [weekday, setWeekday] = useState(0)
  const [hour, setHour] = useState(9)
  const [timezone, setTimezone] = useState(browserTimezone())

  const [connections, setConnections] = useState<ConnectionSummary[]>([])
  const [hasAnalytics, setHasAnalytics] = useState<boolean | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Seed the schedule from whatever is already persisted, so a PM who set this
  // up in Settings before finishing onboarding doesn't get it silently reset.
  useEffect(() => {
    if (!workspace) return
    const n = workspace.notification_settings ?? {}
    setFrequency(resolveFrequency(n))
    setWeekday(coerceWeekday(typeof n.brief_weekday === "number" ? n.brief_weekday : 0))
    setHour(typeof n.brief_hour === "number" ? n.brief_hour : 9)
    setTimezone(
      typeof n.timezone === "string" && n.timezone ? n.timezone : browserTimezone(),
    )
    if (typeof n.brief_channel === "string") setDestination(n.brief_channel)
    if (draft) return
    if (Array.isArray(n.brief_insight_types) && n.brief_insight_types.length) {
      setSurfaces(n.brief_insight_types as string[])
    }
    if (typeof n.brief_insight_note === "string") setNote(n.brief_insight_note)
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onHide = () => {
      if (document.hidden) saveDraft(DRAFT_KEY, { surfaces, note })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [surfaces, note])

  // Same fail-open rule as the old ReviewStep gate: a connector list we can't
  // confirm counts as "no analytics", because stranding the PM on a spinner at
  // the last step is worse than finishing one screen early.
  useEffect(() => {
    let cancelled = false
    connectorsApi
      .list()
      .then((r) => {
        if (cancelled) return
        setConnections(r.connections)
        setHasAnalytics(hasLiveAnalyticsConnection(r.connections))
      })
      .catch(() => {
        if (!cancelled) setHasAnalytics(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Warm the metric-definition drafts while they pick chips, so define-metrics
  // opens pre-filled rather than spinning. Skipped without analytics.
  useEffect(() => {
    if (!workspace || !hasAnalytics) return
    if (workspace.metric_definitions.length) return
    const names = workspace.kpi_tree.metrics.map((m) => m.name.trim()).filter(Boolean)
    if (!names.length) return
    prefetchMetricDefinitions(workspace.id, names).catch(() => {})
  }, [workspace, hasAnalytics])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  const slack = useMemo(
    () => connections.find((c) => c.provider === "slack" && c.status === "active") ?? null,
    [connections],
  )

  const preview = useMemo(
    () => nextBriefLabel(new Date(), timezone, { weekday, hour, frequency }),
    [timezone, weekday, hour, frequency],
  )

  function toggleSurface(value: string) {
    setSurfaces((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
    )
  }

  async function save() {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    setSaving(true)
    try {
      // Merge — never clobber the other notification_settings keys
      // (email_recipients, drip, Slack target, …).
      const existing = workspace.notification_settings ?? {}
      const updated = await updateWorkspace(workspace.id, {
        notification_settings: {
          ...existing,
          brief_insight_types: surfaces,
          brief_insight_note: note.trim() || null,
          brief_channel: destination,
          email_enabled: destination === "email",
          brief_frequency: frequency,
          brief_anchor_date: anchorForSave(new Date(), timezone, { weekday, hour }),
          brief_weekday: weekday,
          brief_hour: hour,
          brief_minute: 0,
          timezone,
        },
      })
      setWorkspace({ ...updated, product: workspace.product })
      clearDraft(DRAFT_KEY)

      if (hasAnalytics) {
        router.push("/onboarding/define-metrics")
        return
      }
      // No analytics connector — nothing to map metrics onto, so this is the
      // last screen. Run the same closer define-metrics would have. Only kick
      // the first brief if a real data source is connected (a non-analytics one
      // can still qualify — e.g. Zendesk/HubSpot); otherwise the brief would be
      // built from onboarding info alone, which we avoid.
      await finishOnboardingAndEnterApp(
        { ...updated, product: workspace.product },
        auth.user.id,
        setContent,
        hasDataSourceConnection(connections),
      )
      router.replace(POST_ONBOARDING_PATH)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your preferences.")
      setSaving(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={9}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Personalize your <em>workspace.</em>
        </>
      }
      subtitle="Your workspace is where Sprntly sends insights about how your product is performing, how users are using it, and what to build next. Tell us what you want to surface."
      footerMeta="Personalize your workspace"
      onBack={() => router.push("/onboarding/review")}
      onContinue={() => void save()}
      continueLabel={
        hasAnalytics ? "Next · define metrics" : "Looks right · enter Sprntly"
      }
      continueDisabled={saving || hasAnalytics === null}
      loading={saving}
    >
      {error && <div className="onb-form-error">{error}</div>}

      <div className="onb-section">
        <div className="onb-section-h">
          What should your workspace surface?{" "}
          <span className="opt">— pick any, or add your own</span>
        </div>
      </div>

      <div className="metric-chips" data-field="surfaces">
        {INSIGHT_TYPES.map((opt) => {
          const isSel = surfaces.includes(opt.value)
          return (
            <button
              type="button"
              key={opt.value}
              className={`metric ${isSel ? "sel" : ""}`}
              aria-pressed={isSel}
              onClick={() => toggleSurface(opt.value)}
            >
              {isSel && (
                <span className="mt-ic" aria-hidden>
                  <Check style={{ width: 11, height: 11 }} />
                </span>
              )}
              {opt.label}
            </button>
          )
        })}
      </div>

      <div className="field full" style={{ marginTop: 12 }}>
        <textarea
          className="inp"
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={1000}
          placeholder={
            'Or describe it in your words — e.g. "I want my workspace to show the top user problems and the one thing I should ship this week to move MAU."'
          }
          aria-label="Describe what your workspace should surface"
        />
      </div>

      <OptionalDisclosure label="Delivery — when & where your brief lands (optional)">
        <div className="onb-section">
          <div className="onb-section-h">Frequency</div>
        </div>
        <div className="metric-chips" data-field="frequency">
          {BRIEF_FREQUENCIES.map((opt) => {
            const isSel = frequency === opt.value
            return (
              <button
                type="button"
                key={opt.value}
                className={`metric ${isSel ? "sel" : ""}`}
                aria-pressed={isSel}
                onClick={() => setFrequency(opt.value)}
              >
                {isSel && (
                  <span className="mt-ic" aria-hidden>
                    <Check style={{ width: 11, height: 11 }} />
                  </span>
                )}
                {opt.label}
              </button>
            )
          })}
        </div>

        <div className="onb-section">
          <div className="onb-section-h">Where should we send it?</div>
        </div>
        <div className="metric-chips" data-field="destination">
          {DESTINATIONS.map((opt) => {
            const isSel = destination === opt.value
            return (
              <button
                type="button"
                key={opt.value}
                className={`metric ${isSel ? "sel" : ""}`}
                aria-pressed={isSel}
                disabled={opt.disabled}
                title={opt.disabled ? "Coming soon" : undefined}
                onClick={() => setDestination(opt.value)}
              >
                {isSel && (
                  <span className="mt-ic" aria-hidden>
                    <Check style={{ width: 11, height: 11 }} />
                  </span>
                )}
                {opt.label}
                {opt.disabled && <span className="opt"> — soon</span>}
              </button>
            )
          })}
        </div>

        {/* Only a real, connected Slack can be targeted — the picker writes the
            channel id the backend needs. Without one, say so rather than
            accepting a channel name that would route nowhere. */}
        {destination === "slack" &&
          (slack ? (
            <div style={{ marginTop: 12 }}>
              <SlackChannelPicker
                savedTargetType={null}
                savedChannelId={null}
                savedChannelName={null}
                // The picker writes the Slack target itself (POST
                // /v1/connectors/slack/config); nothing to reconcile here.
                onSaved={() => {}}
              />
            </div>
          ) : (
            <p className="onb-field-hint" style={{ marginTop: 10 }}>
              Slack isn&apos;t connected yet — we&apos;ll email your brief until
              you connect it in Settings → Connectors, where you can also pick
              the channel.
            </p>
          ))}

        <div className="form-grid" style={{ marginTop: 14 }}>
          {frequencyUsesDay(frequency) && (
            <div className="field" data-field="weekday">
              <div className="field-l">Day</div>
              <select
                className="inp"
                value={weekday}
                onChange={(e) => setWeekday(Number(e.target.value))}
                aria-label="Day"
              >
                {BRIEF_DAYS.map((d) => (
                  <option key={d.value} value={d.value}>
                    {dayOptionLabel(d.label, frequency)}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="field" data-field="hour">
            <div className="field-l">Time</div>
            <select
              className="inp"
              value={hour}
              onChange={(e) => setHour(Number(e.target.value))}
              aria-label="Time"
            >
              {BRIEF_HOURS.map((h) => (
                <option key={h.value} value={h.value}>
                  {h.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field full" data-field="timezone">
            <div className="field-l">Time zone</div>
            <select
              className="inp"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              aria-label="Time zone"
            >
              {timezones().map((tz) => (
                <option key={tz} value={tz}>
                  {tzOptionLabel(tz)}
                </option>
              ))}
            </select>
          </div>
        </div>

        {preview && (
          <p className="onb-field-hint" role="status">
            Next brief will land {preview}.
          </p>
        )}
      </OptionalDisclosure>
    </OnboardingChrome>
  )
}
