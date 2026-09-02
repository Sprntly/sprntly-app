import type { GoalFinding } from "./api"

/** MoSCoW, mirroring `backend/app/crucible/moscow.py` term for term — the
 *  ranking this run used when nothing connected carries a number, so RICE's
 *  Reach and Impact would both come back unmeasured on every row.
 *
 *  TWO RENDERERS OF ONE RANKING, same discipline `goalRice.ts` already
 *  documents: the panel and the saved document must not disagree about which
 *  bucket a finding earned, so both read the same `surfaced_by`/`claim_types`
 *  fields and apply the same rule. */
export const MAX_MOSCOW_ROWS = 10

//: Below this many independent source documents, a MUST is real but thin.
const THIN_EVIDENCE_DOCS = 2

export type MoscowRow = {
  label: string
  bucket: "MUST" | "MUST?" | "SHOULD" | "COULD" | "unranked"
  bucketBasis: string
  docCount: number
  reach: number | null
  reachUnit: string
}

/** `surfaced_by` is PRE-FORMATTED for display (`pipeline._sources_of`): up to
 *  four real `"doc (n)"` entries, most-cited first, plus — only once there
 *  are more documents than that — one trailing `"+K more documents"`
 *  summary. Counting `surfacedBy.length` directly would count that summary
 *  as ONE more document instead of the K it actually stands for. */
const MORE_DOCS_RE = /^\+(\d+) more documents?$/

export function documentCount(surfacedBy: readonly string[]): number {
  let n = 0
  for (const raw of surfacedBy) {
    const s = (raw || "").trim()
    if (!s) continue
    const m = MORE_DOCS_RE.exec(s)
    n += m ? Number(m[1]) : 1
  }
  return n
}

/** The bucket a finding earns from its STRONGEST claim type, and why — same
 *  "strongest, not the average" rule as `goalRice.impactFor`. `?` marks a
 *  thin MUST rather than demoting it: a single-document blocker is still
 *  real evidence of something stopping an account, and I1 forbids letting
 *  corroboration change how big a finding is scored — this flags it for a
 *  human to confirm instead. */
export function bucketFor(
  claimTypes: readonly string[], docCount: number,
): { bucket: MoscowRow["bucket"]; basis: string } {
  const kinds = new Set(claimTypes)
  if (kinds.has("constraint")) {
    return docCount < THIN_EVIDENCE_DOCS
      ? { bucket: "MUST?", basis: "a stated blocker, but from a single source document — real, and worth confirming before you commit to it" }
      : { bucket: "MUST", basis: `a stated blocker, corroborated across ${docCount} independent source documents` }
  }
  if (kinds.has("preference")) {
    return docCount >= THIN_EVIDENCE_DOCS
      ? { bucket: "SHOULD", basis: `a stated preference, seen in ${docCount} documents` }
      : { bucket: "COULD", basis: "a stated preference, seen in a single source document" }
  }
  return {
    bucket: "unranked",
    basis: "neither a stated blocker nor a stated preference — describes the world rather than asking for or blocking something",
  }
}

export function moscowFor(f: GoalFinding): MoscowRow {
  const claimTypes = (f as { claim_types?: string[] }).claim_types ?? []
  const docCount = documentCount(f.surfaced_by ?? [])
  const { bucket, basis } = bucketFor(claimTypes, docCount)
  return {
    label: (f.label || "").trim() || f.statement,
    bucket, bucketBasis: basis, docCount,
    reach: f.impact_value ?? null,
    reachUnit: f.currency || "accounts",
  }
}
