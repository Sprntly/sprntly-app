// Canonical top-insights skill taxonomy — the single source of truth for the
// 7 finding types, their accent hexes, and display labels. Mirrors
// backend/skills/top-insights/SKILL.md step 3 (and top_insights_skill.py's
// SKILL_TYPE_ACCENTS). Every brief surface (BriefChat, BriefV2, email) resolves
// type/accent through here so colors stay consistent and correct.
//
// IMPORTANT: accent is derived from `type`, never read from the card's own
// `accent` field — the model can (and does) emit an accent that mismatches the
// type (e.g. competitive carrying the retention rose). Type is the source of
// truth; accent follows from it.
import type { BriefSkillType, Insight } from "./api"

export const SKILL_TYPE_ACCENTS: Record<BriefSkillType, string> = {
  reliability: "#c0473c", // clay
  retention: "#b23b52", // rose
  competitive: "#b07a2e", // ochre
  growth: "#1a8a52", // green
  demand: "#5f57a6", // iris
  engagement: "#3f63a0", // slate blue
  compliance: "#4f5675", // deep slate
  momentum: "#0f7d70", // teal — a win worth celebrating / favorable movement
}

export const SKILL_TYPE_LABELS: Record<BriefSkillType, string> = {
  reliability: "Reliability",
  retention: "Retention",
  competitive: "Competitive",
  growth: "Growth",
  demand: "Demand",
  engagement: "Engagement",
  compliance: "Compliance",
  momentum: "Momentum",
}

const SKILL_TYPES = Object.keys(SKILL_TYPE_ACCENTS) as BriefSkillType[]

// Legacy 3-tag → skill type fallback, for briefs generated before the skill
// sweep (no `_card`/`type`). Matches top_insights_skill._TAG_TO_TYPE.
const TAG_TO_TYPE: Record<string, BriefSkillType> = {
  something_broken: "reliability",
  something_new: "demand",
  something_better: "growth",
}

function isSkillType(v: unknown): v is BriefSkillType {
  return typeof v === "string" && (SKILL_TYPES as string[]).includes(v)
}

/** Resolve an insight's skill type, preferring the backend card, then the
 *  hoisted top-level field, then a derivation from the legacy tag. Always
 *  returns one of the 8 canonical types. */
export function resolveSkillType(insight: Pick<Insight, "type" | "tag" | "_card">): BriefSkillType {
  const raw = insight._card?.type ?? insight.type
  if (isSkillType(raw)) return raw
  return TAG_TO_TYPE[insight.tag] ?? "reliability"
}

/** The canonical accent hex for an insight, derived from its type (NOT from the
 *  possibly-mismatched card accent). */
export function accentForInsight(insight: Pick<Insight, "type" | "tag" | "_card">): string {
  return SKILL_TYPE_ACCENTS[resolveSkillType(insight)]
}

/** The category pill label (type name only — the skill forbids P0/P1 here). */
export function labelForInsight(insight: Pick<Insight, "type" | "tag" | "_card">): string {
  return SKILL_TYPE_LABELS[resolveSkillType(insight)]
}
