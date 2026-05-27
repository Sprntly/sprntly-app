"use client"

import type { KpiMetric } from "../../lib/onboarding/types"

type Props = {
  northStar: string
  metrics: KpiMetric[]
  hints?: string[]
  onNorthStarChange: (v: string) => void
  onMetricsChange: (metrics: KpiMetric[]) => void
}

export function normalizeKpiWeights(metrics: KpiMetric[]): KpiMetric[] {
  const filled = metrics.filter((m) => m.name.trim())
  const sum = filled.reduce((a, m) => a + (m.weight || 0), 0)
  if (sum <= 0) {
    const even = filled.length ? 1 / filled.length : 0
    return filled.map((m) => ({ ...m, weight: even }))
  }
  return filled.map((m) => ({ ...m, weight: (m.weight || 0) / sum }))
}

export function KpiTreeEditor({
  northStar,
  metrics,
  hints = [],
  onNorthStarChange,
  onMetricsChange,
}: Props) {
  function updateMetric(i: number, patch: Partial<KpiMetric>) {
    onMetricsChange(metrics.map((m, idx) => (idx === i ? { ...m, ...patch } : m)))
  }

  function addMetric() {
    if (metrics.length >= 4) return
    onMetricsChange([
      ...metrics,
      { name: "", current_value: "", target_value: "", weight: 0.25 },
    ])
  }

  return (
    <>
      <div className="field">
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
      </div>
      <div className="field">
        <label className="field-label">Supporting metrics (2–4) *</label>
        {metrics.map((m, i) => (
          <div key={i} className="ob-metric-block">
            <input
              className="input"
              placeholder="Metric name"
              value={m.name}
              onChange={(e) => updateMetric(i, { name: e.target.value })}
            />
            <div className="ob-metric-row">
              <input
                className="input"
                placeholder="Current (optional)"
                value={m.current_value ?? ""}
                onChange={(e) => updateMetric(i, { current_value: e.target.value })}
              />
              <input
                className="input"
                placeholder="Target (optional)"
                value={m.target_value ?? ""}
                onChange={(e) => updateMetric(i, { target_value: e.target.value })}
              />
              <input
                className="input"
                type="number"
                min={0}
                max={1}
                step={0.05}
                placeholder="Weight"
                value={m.weight}
                onChange={(e) => updateMetric(i, { weight: Number(e.target.value) })}
              />
            </div>
          </div>
        ))}
        {metrics.length < 4 && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={addMetric}>
            + Add metric
          </button>
        )}
      </div>
    </>
  )
}
