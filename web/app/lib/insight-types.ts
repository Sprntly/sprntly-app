// Canonical user-facing insight types — the categories a PM picks to say which
// findings they want as their Top Insights. Single source of truth on the
// frontend for the onboarding chips, the settings pane, and the inline picker
// on the Top Insights tab.
//
// Mirrors backend/app/insight_types.py on the SLUGS — those are the contract
// (stored preference, DB check constraint, the vocabulary the compose prompt
// classifies into), so adding or removing a type means changing BOTH sides AND
// the DB constraint(s). The `label` is product wording for the chips and is
// deliberately allowed to differ from the backend's: the backend label is
// prompt text the classifier reads, and rewording it there would shift how
// findings get categorised.
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
    label: "Top Customer Problem",
    description:
      "The most pressing user and product problems, and the biggest opportunities across your signals.",
  },
  {
    value: "build_priorities",
    label: "What to build next",
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

// The subset a PM can actually pick. This is the ONE list to edit when a type
// goes live; the pickers and the Top Insights filter all read through it.
//
// All six as of 2026-08-04. It was narrowed to three on 2026-07-27 on the
// grounds that "no skill is configured to produce" the other three — no longer
// true: measured across the live briefs, the compose step classifies findings
// into reliability_signals (26% of findings), user_feedback (20%), wins (10%)
// and competitor_moves (8%) every week. Withholding them was the opposite
// problem — top_problems tags 67% of all findings, so the two types a PM could
// still pick were catch-alls that steered nothing, and the types they'd
// actually want to prioritise were unpickable AND stripped from a stored
// selection on read (see selectableInsightTypes).
export const SELECTABLE_INSIGHT_TYPE_SLUGS: InsightTypeSlug[] = [
  "top_problems",
  "competitor_moves",
  "build_priorities",
  "user_feedback",
  "reliability_signals",
  "wins",
]

const SELECTABLE_SET = new Set<string>(SELECTABLE_INSIGHT_TYPE_SLUGS)

/** The pickable types. Display order follows the slug list above — the order
 *  the chips were specified in — not the canonical order of INSIGHT_TYPES. */
export const SELECTABLE_INSIGHT_TYPES: InsightType[] = SELECTABLE_INSIGHT_TYPE_SLUGS
  .map((slug) => INSIGHT_TYPES.find((t) => t.value === slug))
  .filter((t): t is InsightType => t != null)

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

/** Like cleanInsightTypes, but also drops types that are no longer offered.
 *  Used everywhere a stored selection is read back — a slug the pickers can't
 *  render would otherwise be invisible state that still filters the brief with
 *  no way to see or clear it. The stored row is left alone until the next save,
 *  so re-listing a slug above restores the original choice. */
export function selectableInsightTypes(values: unknown): InsightTypeSlug[] {
  return cleanInsightTypes(values).filter((v) => SELECTABLE_SET.has(v))
}

export function insightTypeLabel(slug: string): string {
  return LABEL_BY_SLUG[slug] ?? slug
}
