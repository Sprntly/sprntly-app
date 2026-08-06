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
// THERE IS ONE LIST. Until 2026-08-05 there were two — a canonical six and a
// selectable three — and the gap between them was invisible state: the backend
// classified findings into all six and promoted them, and this file then
// filtered the extra three out of view, with nothing on either surface saying
// why a promoted finding never appeared. Apurva's ruling on 2026-08-05: a
// backend insight type is either wired through to the web or it does not
// exist. Do not reintroduce a "selectable subset" — put the type in this list
// when it goes live, and not before.
//
// History: the original 6 onboarding chips merged with 3 client-requested
// report types (2026-07-23; all three were duplicates, so the merged set
// stayed six), then narrowed to the three the picker actually offers
// (2026-08-05). The three dropped — user_feedback, reliability_signals, wins —
// had no skill configured to produce them. Two slugs were renamed in the 07-23
// merge as their meaning broadened: drive_metric -> build_priorities,
// emerging_complaints -> user_feedback (since dropped).
//
// Stored rows may still hold a dropped slug: the DB constraint deliberately
// stays wider than the code, and `cleanInsightTypes` drops unknown slugs on
// read. A selection that empties out means "surface everything" — the same
// default as no preference at all.

export type InsightTypeSlug =
  | "top_problems"
  | "competitor_moves"
  | "build_priorities"

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
    value: "competitor_moves",
    label: "Competitor & market moves",
    description: "Competitive and market developments worth reacting to.",
  },
  {
    value: "build_priorities",
    label: "What to build next",
    description:
      "The highest-priority things to build next, weighing every signal — metrics, demand, revenue, strategy.",
  },
]

export const INSIGHT_TYPE_SLUGS: InsightTypeSlug[] = INSIGHT_TYPES.map((t) => t.value)

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
  competitor_moves: { badge: "Competitor moves", accent: "#b07a2e" }, // ochre
  build_priorities: { badge: "What to build", accent: "#1a8a52" }, // green
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

export function insightTypeLabel(slug: string): string {
  return LABEL_BY_SLUG[slug] ?? slug
}
