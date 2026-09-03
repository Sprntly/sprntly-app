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

/** `pipeline._sources_of`'s COLLAPSED CALL-PROVIDER entry —
 *  `"Fireflies call transcripts (≥ 12 calls, 31 claims)"`. One entry, N
 *  independent calls: a call provider is extracted once per call, so each is
 *  its own source that happens to share a provider label. Counted as `1` —
 *  the fall-through every other shape gets — every call-tenant finding sat at
 *  `docCount === 1` and therefore at `MUST?`, so `MUST` was unreachable there
 *  and the count carried no signal. Mirrors `moscow._CALL_ENTRY_RE` exactly;
 *  the two are changed together or the panel and the document disagree about
 *  what bucket a finding earned. N is a FLOOR (see `_sources_of`). */
const CALL_ENTRY_RE = /\(≥\s*(\d+)\s+calls?,\s*\d+\s+claims?\)$/

/** Said in the output wherever a call count is shown, because "≥" alone is a
 *  symbol a reader has to interpret and the reason for it is not guessable.
 *  Verbatim from `moscow.CALL_COUNT_FLOOR_NOTE` — the panel and the exported
 *  document must caveat the same number the same way. */
export const CALL_COUNT_FLOOR_NOTE =
  "Call counts are a floor (“≥”): one call is extracted as one "
  + "document today, but calls ingested before that changed were batched "
  + "several to a document, so the true number can be higher — never lower."

/** Whether any entry carries a call count, so a renderer knows whether the
 *  floor note applies to what it is about to print. */
export function hasCallCount(surfacedBy: readonly string[]): boolean {
  return surfacedBy.some((s) => CALL_ENTRY_RE.test((s || "").trim()))
}

export function documentCount(surfacedBy: readonly string[]): number {
  let n = 0
  for (const raw of surfacedBy) {
    const s = (raw || "").trim()
    if (!s) continue
    const more = MORE_DOCS_RE.exec(s)
    if (more) { n += Number(more[1]); continue }
    const calls = CALL_ENTRY_RE.exec(s)
    n += calls ? Number(calls[1]) : 1
  }
  return n
}

/** The claim-type buckets as an ORDER, smallest first: a stated blocker
 *  outranks a stated preference outranks neither. Mirrors
 *  `moscow.TYPE_BUCKET_*` / `moscow.type_bucket` — the key `pipeline._rank`
 *  now sorts on, so a renderer explaining the ranking reads the same rule the
 *  ranking used. The `?` on `MUST?` is deliberately NOT part of it: that
 *  comes from `surfaced_by`, a corroboration field, and ordering on it would
 *  let the loudest problem win over the biggest one. */
export const TYPE_BUCKET_BLOCKER = 0
export const TYPE_BUCKET_PREFERENCE = 1
export const TYPE_BUCKET_NEITHER = 2

export function typeBucket(claimTypes: readonly string[]): number {
  const kinds = new Set(claimTypes)
  if (kinds.has("constraint")) return TYPE_BUCKET_BLOCKER
  if (kinds.has("preference")) return TYPE_BUCKET_PREFERENCE
  return TYPE_BUCKET_NEITHER
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
  const kind = typeBucket(claimTypes)
  if (kind === TYPE_BUCKET_BLOCKER) {
    return docCount < THIN_EVIDENCE_DOCS
      ? { bucket: "MUST?", basis: "a stated blocker, but from a single source document — real, and worth confirming before you commit to it" }
      : { bucket: "MUST", basis: `a stated blocker, corroborated across ${docCount} independent source documents` }
  }
  if (kind === TYPE_BUCKET_PREFERENCE) {
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
