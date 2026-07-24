// Canonical user-facing insight types — the categories a PM picks to say which
// findings they want as their Top Insights. Single source of truth on the
// frontend for the onboarding chips, the settings pane, and the inline picker
// on the Top Insights tab.
//
// Mirrors backend/app/insight_types.py. Adding, removing, or renaming a type
// means changing BOTH sides AND the DB constraint(s).
//
// History: merged from the original 6 onboarding chips + 3 client-requested
// report types (2026-07-23). All three requested types were duplicates of an
// existing chip, so the merged set is still six. Two slugs were renamed as
// their meaning broadened: drive_metric -> build_priorities,
// emerging_complaints -> user_feedback (see the accompanying migration).

export type InsightTypeSlug =
  | "top_problems"
  | "build_priorities"
  | "user_feedback"
  | "competitor_moves"
  | "reliability_signals"
  | "wins"

export interface InsightType {
  value: InsightTypeSlug
  label: string
  /** Short helper copy for the chip's tooltip / settings row description. */
  description: string
}

// Order here is display order everywhere the chips render.
export const INSIGHT_TYPES: InsightType[] = [
  {
    value: "top_problems",
    label: "Top user problems & opportunities",
    description:
      "The most pressing user and product problems, and the biggest opportunities across your signals.",
  },
  {
    value: "build_priorities",
    label: "Most important to build",
    description:
      "The highest-priority things to build next, weighing every signal — metrics, demand, revenue, strategy.",
  },
  {
    value: "user_feedback",
    label: "User feedback & complaints",
    description:
      "What users are saying: emerging complaints, recurring themes, and frequent requests.",
  },
  {
    value: "competitor_moves",
    label: "Competitor & market moves",
    description: "Competitive and market developments worth reacting to.",
  },
  {
    value: "reliability_signals",
    label: "Reliability & incident signals",
    description: "Reliability problems, incidents, errors, and stability risks.",
  },
  {
    value: "wins",
    label: "Wins to celebrate",
    description: "Positive movements, milestones, and wins worth recognizing.",
  },
]

export const INSIGHT_TYPE_SLUGS: InsightTypeSlug[] = INSIGHT_TYPES.map((t) => t.value)

const INSIGHT_TYPE_SET = new Set<string>(INSIGHT_TYPE_SLUGS)
const LABEL_BY_SLUG: Record<string, string> = Object.fromEntries(
  INSIGHT_TYPES.map((t) => [t.value, t.label]),
)

export function isInsightTypeSlug(v: unknown): v is InsightTypeSlug {
  return typeof v === "string" && INSIGHT_TYPE_SET.has(v)
}

/** Keep only known slugs, order-preserving + de-duplicated. Unknown/garbage
 *  input degrades to [] — an empty selection means "surface everything", the
 *  same default the readers use when no preference is stored. */
export function cleanInsightTypes(values: unknown): InsightTypeSlug[] {
  if (!Array.isArray(values)) return []
  const out: InsightTypeSlug[] = []
  for (const v of values) {
    if (isInsightTypeSlug(v) && !out.includes(v)) out.push(v)
  }
  return out
}

export function insightTypeLabel(slug: string): string {
  return LABEL_BY_SLUG[slug] ?? slug
}
