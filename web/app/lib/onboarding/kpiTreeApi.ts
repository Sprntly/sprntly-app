/**
 * Client + builder for the backend KPI tree (design-v4 onboarding page 05).
 *
 * The backend owns the canonical KPI-tree schema (backend/app/kpi_tree.py):
 *   north_star      { metric, description }
 *   primary_metrics [ ≤4, { metric, description } ]
 *   secondary_signals [ ≤6, { metric, description } ]
 *
 * Each metric is a name plus a free-text description that feeds the goal-fit
 * classifier as richer context. There are no weights / current / target values.
 *
 * Page 05 captures a North Star (metric + description) plus up to 6 supporting
 * metrics (each metric + description) the PM picks/writes. We map the first ≤4
 * supporting metrics onto primary_metrics and any remainder (≤6) onto
 * secondary_signals.
 */
import { api } from "../api"

export type KpiMetricEntry = {
  metric: string
  description: string
}

export type KpiTreePayload = {
  north_star: KpiMetricEntry
  primary_metrics: KpiMetricEntry[]
  secondary_signals: KpiMetricEntry[]
}

/** A supporting metric in page-05 form state: a name + free-text description. */
export type SupportingMetric = {
  name: string
  description: string
}

export const MAX_PRIMARY_METRICS = 4
export const MAX_SECONDARY_SIGNALS = 6

/**
 * Build the backend payload from page-05 form state.
 *
 * @param northStar             the North Star metric name
 * @param northStarDescription  free-text context for the North Star
 * @param supporting            ordered supporting metrics (name + description)
 */
export function buildKpiTreePayload(
  northStar: string,
  northStarDescription: string,
  supporting: SupportingMetric[],
): KpiTreePayload {
  const ns = northStar.trim()
  // Drop blanks and anything whose name duplicates the North Star / an earlier
  // entry (case-insensitive on the metric name).
  const seen = new Set<string>([ns.toLowerCase()])
  const unique: KpiMetricEntry[] = []
  for (const s of supporting) {
    const metric = s.name.trim()
    if (!metric) continue
    const key = metric.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    unique.push({ metric, description: s.description.trim() })
  }

  return {
    north_star: { metric: ns, description: northStarDescription.trim() },
    primary_metrics: unique.slice(0, MAX_PRIMARY_METRICS),
    secondary_signals: unique.slice(
      MAX_PRIMARY_METRICS,
      MAX_PRIMARY_METRICS + MAX_SECONDARY_SIGNALS,
    ),
  }
}

/** Has the form captured enough to persist? (North Star + ≥1 supporting.) */
export function canSaveKpiTree(
  northStar: string,
  supporting: SupportingMetric[],
): boolean {
  if (!northStar.trim()) return false
  return supporting.some((s) => s.name.trim().length > 0)
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
