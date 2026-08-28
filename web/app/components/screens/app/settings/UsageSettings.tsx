"use client"

import { useEffect, useMemo, useState } from "react"
import {
  ApiError,
  apiErrorMessage,
  usageApi,
  type LlmProvider,
  type UsageBucket,
  type UsageSummary,
} from "../../../../lib/api"
import { SettingsSection, SettingsMessage } from "./SettingsLayout"

/**
 * Usage — LLM spend and token usage for this workspace, for ONE provider.
 *
 * Rendered inside the Admin pane, directly beneath the provider + API key it
 * reports on (see AdminSettings): the key and what ran on it are one question,
 * so they share a screen rather than a nav entry each.
 *
 * Scoped to the ACTIVE provider, and it follows the cards above: picking Claude
 * or OpenAI switches this dashboard with it, so the two never blend. They are
 * separate accounts with separate invoices — a combined figure reconciles
 * against neither, and captioning one provider's chart with the other's numbers
 * is the specific failure this scoping exists to prevent. The provider is
 * therefore in the section TITLE, not a footnote: it is the first thing read.
 *
 * The consequence, accepted deliberately: there is no way to read OpenAI's
 * numbers while running on Claude. Seeing the other provider's history means
 * switching to it above, which also switches what the workspace runs on.
 *
 * Restricted to owners/admins (the backend returns 403; AdminSettings skips
 * this component entirely when it already knows the caller is restricted, so
 * the message is never shown twice).
 *
 * Every money figure is ESTIMATED. The provider APIs return token counts, never
 * dollars, so the backend prices tokens against the published rate card. The UI
 * says so in three places (hero caption, section hint, footnote) because the
 * single worst outcome here is someone reconciling this against a provider
 * invoice and concluding one of them is broken.
 *
 * Charts are inline SVG rather than a charting dependency — two forms, one
 * series each, on a constrained settings column. The marks use the brand accent
 * as a single hue: both charts encode MAGNITUDE, not identity, so no
 * categorical palette is involved and no series is distinguished by colour
 * alone.
 *
 * The View is pure (props in, JSX out) so it can be unit-tested without the
 * network; the default-exported UsageSettings wraps it with the API wiring.
 */

const RANGES = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
]

/** Which breakdown the chart area is showing. The two tab groups are
 *  independent: the range filters the data, the view reshapes it, so any
 *  combination is valid and switching one never resets the other.
 *
 *  There is deliberately no "by provider" view: every figure on the panel is
 *  already one provider's, so that breakdown could only ever draw a single bar
 *  equal to the total beside it. Reading the other provider means selecting it
 *  above. */
export type UsageView = "daily" | "feature" | "model"

const VIEWS: { id: UsageView; label: string; heading: string }[] = [
  { id: "daily", label: "Daily", heading: "Estimated daily spend" },
  { id: "feature", label: "By feature", heading: "Estimated spend by feature" },
  { id: "model", label: "By model", heading: "Estimated spend by model" },
]

/** Product-facing provider names. The UI says "Claude", not "Anthropic" — the
 *  same wording the provider chooser above uses. */
const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Claude",
  openai: "OpenAI",
}

export function providerLabel(slug: string): string {
  return PROVIDER_LABELS[slug] ?? slug.replace(/^\w/, (c) => c.toUpperCase())
}

/** The provider a reader would have to switch to in order to see the rest of
 *  their spend. Drives the hint on an empty panel: landing on "$0" with no idea
 *  where the money you know you spent actually went is the worst outcome of
 *  scoping, and one sentence removes it. Null once there is nothing else to
 *  point at, so this stays correct if a third provider is ever added. */
export function otherProviderLabel(provider: string): string | null {
  const others = Object.keys(PROVIDER_LABELS).filter((p) => p !== provider)
  return others.length === 1 ? providerLabel(others[0]) : null
}

const EMPTY_BUCKET: UsageBucket = {
  calls: 0,
  failed_calls: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_creation_input_tokens: 0,
  cache_read_input_tokens: 0,
  est_cost_usd: 0,
}

/** Human labels for the backend's feature slugs. Unknown slugs fall through
 *  de-slugified rather than being dropped — a new feature ships with a readable
 *  label before anyone updates this map. */
const FEATURE_LABELS: Record<string, string> = {
  prd: "PRD generation",
  design_agent: "Prototype generation",
  chat: "Chat",
  ask: "Ask",
  ideation: "Backlog",
  synthesis: "Synthesis",
  top_insights: "Top Insights",
  // Pre-rename rows persisted `weekly_brief`; keep them labeled under the
  // product's current name rather than surfacing the legacy key.
  weekly_brief: "Top Insights",
  evidence: "Evidence",
  research: "Research",
  documents: "PRD documents",
  stories: "Stories & tickets",
  onboarding: "Onboarding",
  kg_ingest: "Data ingest",
  embeddings: "Embeddings",
  qa: "QA agent",
  oncall: "On-call",
  data_science: "Data science",
  unattributed: "Other",
}

export function featureLabel(slug: string): string {
  if (FEATURE_LABELS[slug]) return FEATURE_LABELS[slug]
  return slug.replace(/[_:]+/g, " ").replace(/^\w/, (c) => c.toUpperCase())
}

/** Money, at a precision that stays honest for very small amounts.
 *  Rounding $0.004 to "$0.00" reads as "free" and is the wrong story on a page
 *  whose whole job is showing what things cost. */
export function formatUsd(v: number): string {
  const n = Number(v) || 0
  if (n === 0) return "$0.00"
  if (n >= 1) return `$${n.toFixed(2)}`
  if (n >= 0.01) return `$${n.toFixed(3)}`
  return `$${n.toFixed(5)}`
}

export function formatCount(v: number): string {
  const n = Number(v) || 0
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatDay(day: string): string {
  // `day` is a plain YYYY-MM-DD calendar date from the server; parse the parts
  // rather than `new Date(day)`, which would treat it as UTC midnight and can
  // render as the previous day for viewers west of Greenwich.
  const [y, m, d] = day.split("-").map(Number)
  if (!y || !m || !d) return day
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  })
}

// --- daily chart -------------------------------------------------------------

const CHART_W = 720
const CHART_H = 180
const PAD_L = 8
const PAD_R = 8
const PAD_T = 12
const PAD_B = 22

export type ChartPoint = { x: number; y: number; day: string; value: number }

/** Map a daily series onto chart coordinates. Exported for unit tests. */
export function buildChartPoints(
  daily: { day: string; est_cost_usd: number }[],
  max: number,
): ChartPoint[] {
  const plotW = CHART_W - PAD_L - PAD_R
  const plotH = CHART_H - PAD_T - PAD_B
  const denom = max > 0 ? max : 1
  const step = daily.length > 1 ? plotW / (daily.length - 1) : 0
  return daily.map((d, i) => {
    const value = Number(d.est_cost_usd) || 0
    return {
      day: d.day,
      value,
      x: PAD_L + (daily.length > 1 ? i * step : plotW / 2),
      y: PAD_T + plotH - (value / denom) * plotH,
    }
  })
}

function DailyChart({
  daily,
  hovered,
  onHover,
}: {
  daily: UsageSummary["daily"]
  hovered: number | null
  onHover: (i: number | null) => void
}) {
  const max = Math.max(0, ...daily.map((d) => Number(d.est_cost_usd) || 0))
  const points = useMemo(() => buildChartPoints(daily, max), [daily, max])
  if (points.length === 0) return null

  const line = points.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ")
  const baseline = CHART_H - PAD_B
  const area = `${PAD_L},${baseline} ${line} ${points[points.length - 1].x.toFixed(2)},${baseline}`
  const active = hovered != null ? points[hovered] : null

  return (
    <div className="usage-chart">
      <svg
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        className="usage-chart-svg"
        role="img"
        aria-label={`Estimated daily spend over ${daily.length} days`}
      >
        {/* Recessive gridlines: baseline + midpoint only. */}
        <line
          x1={PAD_L} y1={baseline} x2={CHART_W - PAD_R} y2={baseline}
          className="usage-grid"
        />
        <line
          x1={PAD_L} y1={PAD_T + (baseline - PAD_T) / 2}
          x2={CHART_W - PAD_R} y2={PAD_T + (baseline - PAD_T) / 2}
          className="usage-grid usage-grid-soft"
        />
        <polyline points={area} className="usage-area" />
        <polyline points={line} className="usage-line" />
        {active && (
          <>
            <line
              x1={active.x} y1={PAD_T} x2={active.x} y2={baseline}
              className="usage-crosshair"
            />
            {/* 2px surface ring so the marker reads against the area fill. */}
            <circle cx={active.x} cy={active.y} r={5} className="usage-dot" />
          </>
        )}
        {/* One transparent hit target per day — bigger than the mark, and it
            keeps hover working without measuring the DOM (which jsdom can't).
            Clamped to the plot: the svg is `overflow: visible`, so an
            unclamped band at either end would sit outside the chart and steal
            hover from whatever is next to it. */}
        {points.map((p, i) => {
          const band = (CHART_W - PAD_L - PAD_R) / Math.max(points.length, 1)
          const x = Math.max(PAD_L, p.x - band / 2)
          const right = Math.min(CHART_W - PAD_R, p.x + band / 2)
          return (
            <rect
              key={p.day}
              x={x}
              y={0}
              width={Math.max(0, right - x)}
              height={CHART_H}
              fill="transparent"
              onMouseEnter={() => onHover(i)}
              onMouseLeave={() => onHover(null)}
            />
          )
        })}
      </svg>
      <div className="usage-chart-axis">
        <span>{formatDay(daily[0].day)}</span>
        <span>{formatDay(daily[daily.length - 1].day)}</span>
      </div>
      {active && (
        <div className="usage-tooltip" role="status">
          <strong>{formatDay(active.day)}</strong>
          <span>{formatUsd(active.value)} estimated</span>
          <span className="usage-tooltip-sub">
            {formatCount(daily[hovered as number].calls)} calls
          </span>
        </div>
      )}
    </div>
  )
}

// --- breakdown bars ----------------------------------------------------------

export type BreakdownRow = {
  key: string
  label: string
  sub: string
  value: number
}

/** Ranked horizontal bars — the form for "magnitude across categories".
 *
 *  One hue for every bar, deliberately: the bars encode HOW MUCH, and the
 *  category is already named in the label beside each one. Colouring them
 *  individually would imply an identity encoding the data doesn't have, and
 *  would need a validated categorical palette to stay legible. */
function BreakdownBars({ rows }: { rows: BreakdownRow[] }) {
  if (rows.length === 0) {
    return (
      <p className="settings-placeholder">Nothing recorded in this period.</p>
    )
  }
  const max = Math.max(1, ...rows.map((r) => r.value))
  return (
    <ul className="usage-bars">
      {rows.map((r) => (
        <li key={r.key} className="usage-bar-row">
          <span className="usage-bar-label">
            <span className="usage-bar-name">{r.label}</span>
            <span className="usage-bar-sub">{r.sub}</span>
          </span>
          <span className="usage-bar-track">
            <span
              className="usage-bar-fill"
              // Floor of 2% so a real-but-tiny value stays visible as a mark
              // rather than collapsing to nothing and reading as zero.
              style={{ width: `${Math.max(2, (r.value / max) * 100)}%` }}
            />
          </span>
          <span className="usage-bar-value">{formatUsd(r.value)}</span>
        </li>
      ))}
    </ul>
  )
}

// --- view --------------------------------------------------------------------

export type UsageSettingsViewProps = {
  data: UsageSummary | null
  days: number
  view: UsageView
  /** Whether this workspace has its own key saved for the ACTIVE provider.
   *  Drives the empty state: with no key there is nothing to bill and nothing
   *  to show, and the reader needs telling why rather than being left with a
   *  blank chart. */
  keyConfigured: boolean
  /** The active provider, so the empty state and the footnote name the console
   *  the reader should actually go and check. */
  provider: LlmProvider
  restricted: boolean
  loading: boolean
  error: string | null
  onRangeChange: (days: number) => void
  onViewChange: (view: UsageView) => void
}

export function UsageSettingsView({
  data,
  days,
  view,
  keyConfigured,
  provider,
  restricted,
  loading,
  error,
  onRangeChange,
  onViewChange,
}: UsageSettingsViewProps) {
  const [hovered, setHovered] = useState<number | null>(null)

  if (restricted) {
    return (
      <SettingsSection title="Usage" sub="LLM usage and estimated cost.">
        <p className="settings-placeholder">
          Usage is restricted to owners and admins.
        </p>
      </SettingsSection>
    )
  }

  const totals = data?.totals ?? EMPTY_BUCKET
  const hasUsage = totals.calls > 0
  const heading =
    VIEWS.find((v) => v.id === view)?.heading ?? VIEWS[0].heading

  const featureRows: BreakdownRow[] = (data?.by_feature ?? []).map((f) => ({
    key: f.feature,
    label: featureLabel(f.feature),
    sub: `${formatCount(f.calls)} calls · ${formatCount(
      f.input_tokens + f.output_tokens,
    )} tokens`,
    value: Number(f.est_cost_usd) || 0,
  }))

  const modelRows: BreakdownRow[] = (data?.by_model ?? []).map((m) => ({
    key: m.model ?? "unknown",
    label: m.model ?? "unknown",
    sub: `${formatCount(m.calls)} calls · ${formatCount(
      m.input_tokens,
    )} in · ${formatCount(m.output_tokens)} out`,
    value: Number(m.est_cost_usd) || 0,
  }))

  const breakdownRows: Record<Exclude<UsageView, "daily">, BreakdownRow[]> = {
    feature: featureRows,
    model: modelRows,
  }

  const providerName = providerLabel(provider)
  const otherName = otherProviderLabel(provider)

  return (
    <>
      <SettingsSection
        title={`${providerName} usage`}
        sub={`What Sprntly has run on your own ${providerName} key, and what it cost. Choosing a provider above switches this dashboard with it — ${providerName} and ${
          otherName ?? "the other provider"
        } bill separately, so their spend is never combined. Costs are estimated from token counts, not billed amounts.`}
      >
        {/* Two independent filter groups on one row: WHEN on the left, WHAT on
            the right. Both use the same pill control so it reads as one bar. */}
        <div className="usage-toolbar">
          <div className="usage-ranges" role="group" aria-label="Date range">
            {RANGES.map((r) => (
              <button
                key={r.days}
                type="button"
                className={`usage-range${r.days === days ? " is-active" : ""}`}
                aria-pressed={r.days === days}
                onClick={() => onRangeChange(r.days)}
              >
                {r.label}
              </button>
            ))}
          </div>
          <div className="usage-ranges" role="group" aria-label="Breakdown">
            {VIEWS.map((v) => (
              <button
                key={v.id}
                type="button"
                className={`usage-range${v.id === view ? " is-active" : ""}`}
                aria-pressed={v.id === view}
                onClick={() => onViewChange(v.id)}
              >
                {v.label}
              </button>
            ))}
          </div>
        </div>

        {error && <SettingsMessage kind="error">{error}</SettingsMessage>}

        {loading && !data ? (
          <p className="settings-placeholder">Loading usage…</p>
        ) : error && !data ? (
          // The error banner above already says what went wrong. Falling
          // through to the empty state would add "no usage in this period"
          // underneath it — a claim about data we failed to load.
          null
        ) : !keyConfigured ? (
          <p className="settings-placeholder">
            No API key saved, so everything currently runs on Sprntly&apos;s key
            at our cost — there is nothing billed to you to show. Add your own{" "}
            {providerName} key above and usage on it will appear here.
          </p>
        ) : !hasUsage ? (
          <p className="settings-placeholder">
            No {providerName} usage on your key in this period. Tracking starts
            the moment a key is saved — anything Sprntly ran before that was on
            our key and is not counted here.
            {otherName && (
              <>
                {" "}
                Spend on {otherName} is counted separately: select it above to
                see it.
              </>
            )}
          </p>
        ) : (
          <>
            <div className="usage-hero">
              <div className="usage-hero-main">
                <div className="usage-hero-value">
                  {formatUsd(totals.est_cost_usd)}
                </div>
                <div className="usage-hero-label">
                  Estimated spend on your {providerName} key · last {days} days
                </div>
              </div>
              <div className="usage-stats">
                <div className="usage-stat">
                  <span className="usage-stat-value">
                    {formatCount(totals.calls)}
                  </span>
                  <span className="usage-stat-label">Calls</span>
                </div>
                <div className="usage-stat">
                  <span className="usage-stat-value">
                    {formatCount(totals.input_tokens)}
                  </span>
                  <span className="usage-stat-label">Input tokens</span>
                </div>
                <div className="usage-stat">
                  <span className="usage-stat-value">
                    {formatCount(totals.output_tokens)}
                  </span>
                  <span className="usage-stat-label">Output tokens</span>
                </div>
                <div className="usage-stat">
                  <span className="usage-stat-value">
                    {formatCount(totals.failed_calls)}
                  </span>
                  <span className="usage-stat-label">Failed</span>
                </div>
              </div>
            </div>

            <h4 className="usage-h">{heading}</h4>
            {view === "daily" ? (
              <DailyChart
                daily={data!.daily}
                hovered={hovered}
                onHover={setHovered}
              />
            ) : (
              <BreakdownBars rows={breakdownRows[view]} />
            )}

            <p className="usage-foot">
              Costs are estimated by pricing token counts against the
              provider&apos;s published rates — the API returns token counts,
              not charges. Use your {providerName} console for billed amounts.
            </p>
          </>
        )}
      </SettingsSection>
    </>
  )
}

export function UsageSettings({
  keyConfigured,
  provider,
}: {
  /** Passed down from AdminSettings, which has already fetched the config —
   *  avoids a second request for something the parent pane already knows. */
  keyConfigured: boolean
  provider: LlmProvider
}) {
  // The payload is held together with the provider it was fetched FOR. A
  // provider switch re-fetches, and until the new payload lands the old one is
  // still in state — rendering it would put Claude's numbers under an "OpenAI
  // usage" heading for as long as the request takes. Pairing them makes that
  // unrepresentable: the view is handed data only when the two agree.
  //
  // The pair is the source of truth rather than the server's echoed `provider`
  // because it is also correct against a backend that predates the filter and
  // ignores the param — that one returns blended figures with no echo, and
  // captioning THOSE with a provider name is the same lie.
  const [loaded, setLoaded] = useState<{
    provider: LlmProvider
    summary: UsageSummary
  } | null>(null)
  const [days, setDays] = useState(30)
  // The view is pure presentation — every breakdown ships in the same payload,
  // so switching tabs reshapes what's already loaded and never refetches.
  const [view, setView] = useState<UsageView>("daily")
  const [restricted, setRestricted] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    ;(async () => {
      try {
        const s = await usageApi.summary(days, provider)
        if (!cancelled) {
          setLoaded({ provider, summary: s })
          setError(null)
        }
      } catch (e) {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 403) {
          setRestricted(true)
        } else {
          setError(
            e instanceof ApiError
              ? apiErrorMessage(e.status, e.body)
              : "Could not load usage.",
          )
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [days, provider])

  return (
    <UsageSettingsView
      data={loaded?.provider === provider ? loaded.summary : null}
      days={days}
      view={view}
      keyConfigured={keyConfigured}
      provider={provider}
      restricted={restricted}
      loading={loading}
      error={error}
      onRangeChange={setDays}
      onViewChange={setView}
    />
  )
}
