"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { OptionalDisclosure } from "../../onboarding/OptionalDisclosure"
import { useOnboarding } from "../../../context/OnboardingContext"
import { useContent } from "../../../context/ContentContext"
import { updateWorkspace } from "../../../lib/onboarding/store"
import {
  SELECTABLE_INSIGHT_TYPES,
  selectableInsightTypes,
} from "../../../lib/insight-types"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { connectorsApi, type ConnectionSummary } from "../../../lib/api"
import { hasLiveAnalyticsConnection } from "../../../lib/onboarding/connectorsWizard"
import { hasDataSourceConnection } from "../../../lib/connectorsCatalog"
import { prefetchMetricDefinitions } from "../../../lib/onboarding/draftPrefetch"
import { stepForSlug } from "../../../lib/onboarding/types"
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
import { ConnectorConnectModal } from "../../connectors/ConnectorConnectModal"
import { useConnectorConnectedSignal } from "../../../lib/useConnectorConnectedSignal"
import { Check } from "../../auth/icons"

const DRAFT_KEY = "personalize-step"

// The insight-type chips come from the shared list of SELECTABLE types
// (lib/insight-types) so onboarding and Settings → Comms & Brief always offer
// the same set — currently the three that have a skill behind them. The
// selection is WORKSPACE-level — persisted on
// companies.notification_settings.brief_insight_types — so the whole
// workspace's brief is filtered to what the admin picks here.

/** Where the brief lands. Teams has no backend delivery path yet, so it is
 *  left out entirely (2026-09-03) rather than shown disabled — a "coming
 *  soon" chip nobody can act on is still a chip taking up a decision. */
const DESTINATIONS: { value: string; label: string }[] = [
  { value: "slack", label: "Slack" },
  { value: "email", label: "Email" },
]

/**
 * Onboarding step 09 — "Personalize your workspace" (2026-07-21 spec).
 *
 * Two halves:
 *   - What the workspace should surface: the insight-type chips. Persisted as
 *     notification_settings.brief_insight_types, NOT a new table — every other
 *     brief-delivery preference already lives in that blob and the schedule
 *     migration explicitly argues for keeping it that way.
 *   - Delivery: frequency / destination / day / time / timezone, OPEN by
 *     default (2026-09-03 — a PM should not have to click to discover there is
 *     a schedule to set) rather than hidden behind the disclosure it still
 *     visually is (still collapsible, just not collapsed on arrival). These
 *     are the SAME keys Settings → Comms & Brief writes, and the option
 *     vocabularies come from the shared briefSchedule module, so the two
 *     surfaces cannot drift.
 *
 * SLACK IS NEVER PRE-SELECTED (2026-09-03). A chip showing "selected" the
 * moment the screen loads reads as "already connected" — it isn't, for a
 * brand-new signup. Destination starts unset; picking Slack while it isn't
 * connected opens the SAME connect modal Connectors uses (OAuth, provider
 * "slack"), so choosing it and connecting it are one motion instead of a
 * chip that quietly does nothing until a trip to Settings.
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
    (draft?.surfaces as string[]) ?? ["top_problems", "build_priorities"],
  )

  const [frequency, setFrequency] = useState<BriefFrequency>("weekly")
  // Unset until the PM actually picks one — see the module doc for why Slack
  // must never be the pre-selected chip. `save()` below falls back to email
  // only at the point of persisting, never as something shown selected here.
  const [destination, setDestination] = useState<string | null>(null)
  const [modalProvider, setModalProvider] = useState<string | null>(null)
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
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  // Seed the insight-type selection from the workspace's saved default
  // (notification_settings.brief_insight_types), so returning to the step (or
  // having set it in Settings) doesn't reset it. A local draft wins; an empty
  // saved selection keeps the sensible defaults above rather than blanking the
  // chips. Narrowed to the offered types — a saved slug with no chip would be
  // invisible state the PM can't see or clear.
  useEffect(() => {
    if (!workspace || draft) return
    const n = workspace.notification_settings ?? {}
    const saved = selectableInsightTypes(n.brief_insight_types)
    if (saved.length) setSurfaces(saved)
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onHide = () => {
      if (document.hidden) saveDraft(DRAFT_KEY, { surfaces })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [surfaces])

  function reloadConnections() {
    return connectorsApi.list().then(
      (r) => {
        setConnections(r.connections)
        setHasAnalytics(hasLiveAnalyticsConnection(r.connections))
      },
      // Same fail-open rule as the old ReviewStep gate: a connector list we
      // can't confirm counts as "no analytics", because stranding the PM on a
      // spinner at the last step is worse than finishing one screen early.
      () => setHasAnalytics(false),
    )
  }

  useEffect(() => {
    void reloadConnections()
  }, [])

  // The OAuth tab signals back via BroadcastChannel / localStorage the moment
  // Slack connects (see /connectors/return) — refresh so the picker replaces
  // the "connect it" hint without a manual reload. Mirrors Connectors.tsx.
  useConnectorConnectedSignal(() => void reloadConnections())

  // Belt-and-suspenders: OAuth opens Slack in a sibling tab. If the
  // return-page signal is missed (e.g. that tab closed before posting), a
  // refresh on tab focus still picks up the new connection while the modal
  // is open.
  useEffect(() => {
    if (modalProvider == null) return
    const onVisible = () => {
      if (document.visibilityState === "visible") void reloadConnections()
    }
    document.addEventListener("visibilitychange", onVisible)
    return () => document.removeEventListener("visibilitychange", onVisible)
  }, [modalProvider])

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
    // Delivery is per-user: the company's SHARED Slack connection (surfaced
    // for the voice-of-customer view) is not YOUR delivery target — only a
    // row you installed yourself counts here.
    () => connections.find(
      (c) =>
        c.provider === "slack"
        && c.status === "active"
        && !c.config?.company_connection,
    ) ?? null,
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
      // Workspace-level: the insight types the whole workspace's brief should
      // surface, plus delivery (one scheduled digest per workspace). All merged
      // into notification_settings — never clobber the other keys
      // (email_recipients, drip, Slack target, …).
      const existing = workspace.notification_settings ?? {}
      const updated = await updateWorkspace(workspace.id, {
        notification_settings: {
          ...existing,
          // Top Insights filter for the workspace. Cleaned to the offered slugs
          // so a stale client can't violate the companies_brief_insight_types
          // check. brief_insight_note is deliberately not written — the
          // free-text override was removed from both pickers; any value already
          // stored survives in `existing`.
          brief_insight_types: selectableInsightTypes(surfaces),
          // Unset in the UI reads as "email" here — the same graceful
          // fallback already promised by the unconnected-Slack hint below,
          // just applied whether or not a chip was ever clicked.
          brief_channel: destination ?? "email",
          email_enabled: destination == null || destination === "email",
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

      // ANALYTICS **AND** METRICS. define-metrics confirms a definition and an
      // analytics mapping for each metric already picked, and the step that
      // picked them was removed on 2026-09-03 — so for a fresh signup the list
      // is empty and the sub-flow would open on nothing to confirm. Metrics are
      // chosen in Settings → KPI Settings now; someone who has picked some and
      // has analytics connected still gets the mapping screen.
      if (hasAnalytics && workspace.kpi_tree.metrics.length > 0) {
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
      step={stepForSlug("personalize") ?? 4}
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
          <span className="opt">— pick any</span>
        </div>
      </div>

      <div className="metric-chips" data-field="surfaces">
        {SELECTABLE_INSIGHT_TYPES.map((opt) => {
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

      <OptionalDisclosure
        label="Delivery — when & where your brief lands (optional)"
        defaultOpen
      >
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

        {/* `.onb-section` only spaces itself BELOW (margin-bottom: 20px), so a
            heading straight after a chip row sits flush against it. Match that
            same 20px above so the two delivery sections read as one rhythm. */}
        <div className="onb-section" style={{ marginTop: 20 }}>
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
                onClick={() => {
                  setDestination(opt.value)
                  // Picking Slack while it isn't connected asks for it right
                  // here — the SAME modal Connectors uses — instead of
                  // selecting a chip that quietly does nothing until a trip to
                  // Settings. Already connected: just select, as normal.
                  if (opt.value === "slack" && !slack) setModalProvider("slack")
                }}
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

        {/* Only a real, connected Slack can be targeted — the picker writes the
            channel id the backend needs. Without one, offer the SAME connect
            flow rather than only describing it — "click it, we ask you to
            connect" is the point, not a paragraph pointing at Settings. */}
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
            <div className="onb-field-hint" style={{ marginTop: 10 }}>
              <p style={{ margin: "0 0 8px" }}>
                Slack isn&apos;t connected yet — we&apos;ll email your brief
                until you do.
              </p>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setModalProvider("slack")}
              >
                Connect Slack
              </button>
            </div>
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

      <ConnectorConnectModal
        providerId={modalProvider}
        activeCompany={workspace.slug}
        connection={connections.find((c) => c.provider === modalProvider) ?? null}
        returnTo="/onboarding/personalize"
        onClose={() => setModalProvider(null)}
        onConnected={() => {
          setModalProvider(null)
          void reloadConnections()
        }}
        onSkipForLater={() => setModalProvider(null)}
      />
    </OnboardingChrome>
  )
}
