import type { GoalFinding } from "./api"

/** RICE, mirroring `backend/app/crucible/rice.py` term for term.
 *
 *  TWO RENDERERS OF ONE RANKING is a mistake this feature has already made
 *  once — the panel and the document must not disagree about what a finding
 *  scored. The arithmetic and the thresholds live in both files because the
 *  panel renders from stored rows without asking the server to format them,
 *  and the tests on each side assert the same properties.
 *
 *  WHAT IS DERIVED AND WHAT IS ASSUMED:
 *    Reach       counted accounts. Derived.
 *    Impact      read from the claim type. ASSUMED — the ordering is ours.
 *    Confidence  the band `scoring.py` computed. Derived; the mapping is not.
 *    Effort      NOT IN THE DATA. Never invented; the score is R×I×C until a
 *                reader supplies one, and an effort applied equally to every
 *                row cannot change their order anyway.
 */
export const IMPACT_BY_CLAIM_TYPE: Record<string, number> = {
  constraint: 3,
  preference: 2,
  mechanism: 1,
  existence: 1,
  attempt: 1,
  direction: 1,
  magnitude: 1,
}

export const CONFIDENCE_BY_BAND: Record<string, number> = {
  high: 0.8, medium: 0.5, low: 0.25,
}

export const EFFORT_ABSENT = "Unquantified"
export const RICE_INPUT_COUNT = 4
export const MAX_RICE_ROWS = 10

export type RiceRow = {
  label: string
  reach: number | null
  reachUnit: string
  impact: number
  confidence: number
  confidenceBand: string
  /** How many of RICE's four terms this row actually has. */
  inputsPresent: number
  score: number | null
}

/** The STRONGEST claim type decides, never the average — a theme carrying one
 *  blocked deal among ten descriptions is still about a blocked deal, and
 *  averaging lets volume of commentary dilute it. */
export function impactFor(claimTypes: readonly string[]): number {
  let best = 1
  for (const t of claimTypes) {
    const v = IMPACT_BY_CLAIM_TYPE[t] ?? 1
    if (v > best) best = v
  }
  return best
}

export function riceFor(f: GoalFinding): RiceRow {
  const claimTypes = (f as { claim_types?: string[] }).claim_types ?? []
  const reach = f.impact_value ?? null
  const impact = impactFor(claimTypes)
  const band = (f.confidence_band ?? "").trim()
  const confidence = CONFIDENCE_BY_BAND[band] ?? CONFIDENCE_BY_BAND.low
  return {
    label: (f.label || "").trim() || f.statement,
    reach,
    reachUnit: f.currency || "accounts",
    impact,
    confidence,
    confidenceBand: band || "low",
    // impact and confidence always resolve; reach may not; effort never does.
    inputsPresent: reach === null ? 2 : 3,
    // NULL, NEVER ZERO. An unsized finding has no RICE — printing 0 says "we
    // sized this and it is nothing", which leads to the opposite decision.
    score: reach === null ? null : reach * impact * confidence,
  }
}
