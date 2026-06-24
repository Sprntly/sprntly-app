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

/** Onboarding metrics step: pick at least this many metrics (the minimum). */
export const MIN_METRIC_PICKS = 3
/**
 * …and at most this many. 5 maps cleanly onto the KPI-tree schema
 * (1 North Star + up to 4 primary metrics). The picker enforces the ceiling at
 * selection time (and warns past it); this is the upper bound for validation.
 */
export const MAX_METRIC_PICKS = 5

/** The onboarding metrics step is satisfiable iff between MIN and MAX metrics
 *  (inclusive) are picked. */
export function canSavePickedMetrics(picked: SupportingMetric[]): boolean {
  const named = picked.filter((m) => m.name.trim().length > 0)
  return named.length >= MIN_METRIC_PICKS && named.length <= MAX_METRIC_PICKS
}

/** Request body for the metric-selection endpoint. */
export type MetricSelectionPayload = {
  metrics: KpiMetricEntry[]
}

/** Response from the metric-selection endpoint (echoes the inferred North Star). */
export type MetricSelectionResult = {
  ok: true
  version: number
  north_star: string
}

/**
 * Build the selection payload from the onboarding picks. Trims names +
 * descriptions, drops blanks, and de-dupes by name (case-insensitive) so the
 * backend gets a clean set. The server picks which of the 3–5 metrics is the
 * North Star (PUT /v1/company/kpi-tree/from-selection) — the client no longer
 * designates one or send a placeholder.
 */
export function buildSelectionPayload(
  picked: SupportingMetric[],
): MetricSelectionPayload {
  const seen = new Set<string>()
  const metrics: KpiMetricEntry[] = []
  for (const m of picked) {
    const metric = m.name.trim()
    if (!metric) continue
    const key = metric.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    metrics.push({ metric, description: m.description.trim() })
  }
  return { metrics }
}

export const kpiTreeApi = {
  /** Current tree, or null if onboarding step 05 hasn't been completed (404). */
  get: () =>
    api
      .get<KpiTreePayload>("/v1/company/kpi-tree")
      .catch(() => null as KpiTreePayload | null),
  /** Settings KPI editor: persist a fully-specified tree (explicit North Star). */
  put: (tree: KpiTreePayload) =>
    api.put<{ ok: true; version: number }>("/v1/company/kpi-tree", tree),
  /** Onboarding metrics step: send the PM's picks; the server infers the
   *  North Star and persists the tree. */
  putFromSelection: (payload: MetricSelectionPayload) =>
    api.put<MetricSelectionResult>(
      "/v1/company/kpi-tree/from-selection",
      payload,
    ),
}
