/**
 * Client + builder for the backend KPI tree (design-v4 onboarding page 05).
 *
 * The backend owns the canonical KPI-tree schema (backend/app/kpi_tree.py):
 *   north_star      { metric, current_value?, target_value?, target_window_days? }
 *   primary_metrics [ ≤4, weight in (0,1], weights sum to 1.0 ]
 *   secondary_signals [ ≤6, direction higher|lower_is_better ]
 *
 * Page 05 captures a North Star (just the metric string) plus up to 6
 * supporting metrics the PM picks/writes. We map them onto the backend
 * shape: the first ≤4 supporting metrics become weighted primary_metrics
 * (evenly weighted so they sum to 1.0); any remainder (≤6) become
 * secondary_signals (default higher_is_better). This keeps the picker UI
 * simple while satisfying the backend validator.
 */
import { api } from "../api"

export type KpiNorthStar = {
  metric: string
  current_value?: number | null
  target_value?: number | null
  target_window_days?: number | null
}

export type KpiPrimaryMetric = {
  metric: string
  current_value?: number | null
  target_value?: number | null
  weight: number
}

export type KpiSecondarySignal = {
  metric: string
  current_value?: number | null
  direction: "higher_is_better" | "lower_is_better"
}

export type KpiTreePayload = {
  north_star: KpiNorthStar
  primary_metrics: KpiPrimaryMetric[]
  secondary_signals: KpiSecondarySignal[]
}

export const MAX_PRIMARY_METRICS = 4
export const MAX_SECONDARY_SIGNALS = 6

/** Even weights for n metrics that sum to exactly 1.0 (last absorbs rounding). */
export function evenWeights(n: number): number[] {
  if (n <= 0) return []
  const base = Math.floor((1 / n) * 100) / 100
  const weights = Array.from({ length: n }, () => base)
  const drift = Math.round((1 - base * n) * 100) / 100
  weights[n - 1] = Math.round((weights[n - 1] + drift) * 100) / 100
  return weights
}

/**
 * Build the backend payload from page-05 form state.
 *
 * @param northStar   the North Star metric name
 * @param supporting  ordered supporting-metric names (deduped, trimmed)
 */
export function buildKpiTreePayload(
  northStar: string,
  supporting: string[],
): KpiTreePayload {
  const ns = northStar.trim()
  const clean = supporting.map((s) => s.trim()).filter(Boolean)
  // Drop duplicates and anything equal to the North Star.
  const seen = new Set<string>([ns.toLowerCase()])
  const unique: string[] = []
  for (const s of clean) {
    const key = s.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    unique.push(s)
  }

  const primaryNames = unique.slice(0, MAX_PRIMARY_METRICS)
  const secondaryNames = unique.slice(
    MAX_PRIMARY_METRICS,
    MAX_PRIMARY_METRICS + MAX_SECONDARY_SIGNALS,
  )
  const weights = evenWeights(primaryNames.length)

  return {
    north_star: { metric: ns },
    primary_metrics: primaryNames.map((metric, i) => ({
      metric,
      weight: weights[i],
    })),
    secondary_signals: secondaryNames.map((metric) => ({
      metric,
      direction: "higher_is_better" as const,
    })),
  }
}

/** Has the form captured enough to persist? (North Star + ≥1 supporting.) */
export function canSaveKpiTree(northStar: string, supporting: string[]): boolean {
  if (!northStar.trim()) return false
  return supporting.some((s) => s.trim().length > 0)
}

export const kpiTreeApi = {
  /** Current tree, or null if onboarding step 05 hasn't been completed (404). */
  get: () =>
    api
      .get<KpiTreePayload>("/v1/company/kpi-tree")
      .catch(() => null as KpiTreePayload | null),
  put: (tree: KpiTreePayload) =>
    api.put<{ ok: true; version: number }>("/v1/company/kpi-tree", tree),
}
