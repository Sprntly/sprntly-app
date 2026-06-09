"use client"

import type { KpiMetric } from "../../lib/onboarding/types"

type Props = {
  northStar: string
  northStarDescription?: string
  metrics: KpiMetric[]
  hints?: string[]
  onNorthStarChange: (v: string) => void
  onNorthStarDescriptionChange?: (v: string) => void
  onMetricsChange: (metrics: KpiMetric[]) => void
  northStarError?: string
  metricsError?: string
}

/** Drop blank metrics; metrics are equal (no weights anymore). */
export function cleanKpiMetrics(metrics: KpiMetric[]): KpiMetric[] {
  return metrics
    .filter((m) => m.name.trim())
    .map((m) => ({ name: m.name.trim(), description: (m.description ?? "").trim() }))
}

export function KpiTreeEditor({
  northStar,
  northStarDescription = "",
  metrics,
  hints = [],
  onNorthStarChange,
  onNorthStarDescriptionChange,
  onMetricsChange,
  northStarError,
  metricsError,
}: Props) {
  function updateMetric(i: number, patch: Partial<KpiMetric>) {
    onMetricsChange(metrics.map((m, idx) => (idx === i ? { ...m, ...patch } : m)))
  }

  function addMetric() {
    if (metrics.length >= 4) return
    onMetricsChange([...metrics, { name: "", description: "" }])
  }

  return (
    <>
      <div className={`field ${northStarError ? "has-error" : ""}`} data-field="northStar">
        <label className="field-label">North star metric *</label>
        <input
          className="input"
          value={northStar}
          onChange={(e) => onNorthStarChange(e.target.value)}
          placeholder="e.g. Day-30 retention"
        />
        {hints.length > 0 && (
          <div className="ob-hints">Suggestions: {hints.join(" · ")}</div>
        )}
        {northStarError && <p className="field-error">{northStarError}</p>}
        <textarea
          className="input"
          placeholder="Describe what this metric means and why it matters (context for goal-fit scoring)"
          value={northStarDescription}
          rows={2}
          maxLength={400}
          onChange={(e) => onNorthStarDescriptionChange?.(e.target.value)}
        />
      </div>
      <div className={`field ${metricsError ? "has-error" : ""}`} data-field="metrics">
        <label className="field-label">Supporting metrics (2–4) *</label>
        {metrics.map((m, i) => (
          <div key={i} className="ob-metric-block">
            <input
              className="input"
              placeholder="Metric name"
              value={m.name}
              onChange={(e) => updateMetric(i, { name: e.target.value })}
            />
            <textarea
              className="input"
              placeholder="Describe what this metric means and why it matters"
              value={m.description ?? ""}
              rows={2}
              maxLength={400}
              onChange={(e) => updateMetric(i, { description: e.target.value })}
            />
          </div>
        ))}
        {metrics.length < 4 && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={addMetric}>
            + Add metric
          </button>
        )}
        {metricsError && <p className="field-error">{metricsError}</p>}
      </div>
    </>
  )
}
