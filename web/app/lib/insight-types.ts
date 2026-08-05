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

// The subset a PM can actually pick today (2026-07-27). The other three types
// stay in the canonical list on purpose — the compose prompt still classifies
// findings into all six, stored selections still validate, and the DB check
// constraint is unchanged — but no skill is configured to produce them yet, so
// offering them promises insights that never arrive. This is the ONE list to
// edit when a type goes live; the pickers and the Top Insights filter all read
// through it.
export const SELECTABLE_INSIGHT_TYPE_SLUGS: InsightTypeSlug[] = [
  "top_problems",
  "competitor_moves",
  "build_priorities",
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

// The CARD PILL — badge text + accent for a finding, in the reader's own
// vocabulary. Mirrors backend/app/insight_types.py INSIGHT_TYPE_BADGES.
//
// Until 2026-08-05 the pill showed the top-insights skill's separate 8-way
// taxonomy (Reliability, Growth, Demand, Retention, Competitive, Engagement,
// Compliance, Momentum). That vocabulary is not what the picker offers —
// "Growth" is not a preference type at all — so a reader who asked for
// "Reliability & incident signals" could not look at a card and tell whether
// their selection had been honoured. The pill now names the finding's own
// `insight_types`.
//
// `badge` is a short form of `label` above: the chip wording in full does not
// fit an 11px uppercase pill. It lives beside the chip label so the two cannot
// drift — swap these strings for `label` if the full wording is wanted.
// Accents are the skill taxonomy's existing hexes, reassigned by meaning; no
// new colours are introduced.
export const INSIGHT_TYPE_BADGES: Record<InsightTypeSlug, { badge: string; accent: string }> = {
  top_problems: { badge: "Top problem", accent: "#b23b52" }, // rose
  build_priorities: { badge: "What to build", accent: "#1a8a52" }, // green
  user_feedback: { badge: "User feedback", accent: "#5f57a6" }, // iris
  competitor_moves: { badge: "Competitor moves", accent: "#b07a2e" }, // ochre
  reliability_signals: { badge: "Reliability", accent: "#c0473c" }, // clay
  wins: { badge: "Win", accent: "#0f7d70" }, // teal
}

/** Which of a finding's insight types to show on its card.
 *
 *  A finding carries one or two, in the model's own order — the first is its
 *  PRIMARY classification. We walk the finding's types in that order and take
 *  the first one the reader selected, so a card whose primary type was asked
 *  for keeps it, and a card whose primary was NOT asked for surfaces the
 *  secondary type that was. Walking the SELECTION order instead would let a
 *  reader's first chip override every card's primary and collapse distinct
 *  findings to the same label. With no selection, the primary.
 *
 *  Returns null for a legacy finding with no `insight_types` — the caller must
 *  keep its old skill-taxonomy label rather than invent one, since the 8 skill
 *  types have no faithful counterpart for retention, demand, engagement or
 *  compliance. Mirrors backend/app/insight_types.py display_insight_type. */
export function displayInsightType(
  insightTypes: unknown,
  selectedTypes: string[] = [],
): InsightTypeSlug | null {
  const types = cleanInsightTypes(insightTypes)
  if (types.length === 0) return null
  const wanted = new Set(selectedTypes)
  for (const slug of types) {
    if (wanted.has(slug)) return slug
  }
  return types[0]
}

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
