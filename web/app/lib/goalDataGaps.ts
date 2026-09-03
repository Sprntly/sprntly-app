import type { GoalFinding } from "./api"
import { TYPE_BUCKET_BLOCKER, bucketFor, documentCount, typeBucket } from "./goalMoscow"

/** What we do not know about the finding this report just recommended —
 *  mirroring `backend/app/crucible/data_gaps.py` term for term.
 *
 *  ASSEMBLED, NOT GENERATED. Every line is something the engine already
 *  computed and then dropped: a reach it could not measure, the open questions
 *  the deep pass wrote and the citation gate already cleared, and the `?` on a
 *  thin `MUST`. No model call, no scoring, no choosing (I2).
 *
 *  SCOPED TO THE RECOMMENDED FINDING, NOT THE CORPUS — and that is what makes
 *  it actionable. `plan.cannot_answer` is deliberately excluded: those gaps are
 *  corpus-level and identical on every finding, they are already rendered once
 *  beside the plan, and repeating them here would carry a false promise that
 *  answering them is a precondition of THIS decision.
 *
 *  TWO RENDERERS OF ONE LIST, the discipline `goalMoscow.ts` already
 *  documents: the panel and the exported document must not disagree about what
 *  is still unknown. */
export const DATA_GAPS_HEADING = "Close these before you spend."

/** Past this the list stops being a checklist and starts being a second
 *  findings section. */
export const MAX_DATA_GAPS = 6

function hasDeep(f: GoalFinding): boolean {
  return !!(f.deep_recommendation?.action || "").trim()
    && !!(f.deep_recommendation?.because || "").trim()
}

/** Which finding the report is actually recommending, or `-1`.
 *
 *  THE SAME BINDING `recommend.build_synthesized_recommendation` USES: findings
 *  arrive in the run's frozen rank order (I10) and the single recommendation is
 *  bound to the first of them that KEPT a deep write-up. Not "rank 1" — rank 1
 *  may have lost its deep pass at the citation gate, and the gaps must follow
 *  what was recommended rather than lead it. */
export function recommendedIndex(findings: readonly GoalFinding[]): number {
  return findings.findIndex(hasDeep)
}

function claimTypesOf(f: GoalFinding): string[] {
  return (f as { claim_types?: string[] }).claim_types ?? []
}

function bucketOf(f: GoalFinding): string {
  return bucketFor(claimTypesOf(f), documentCount(f.surfaced_by ?? [])).bucket
}

/** Whether `MUST?` on one finding actually SAYS anything on this run.
 *
 *  It does only when some blocker here is NOT thin. On a corpus where every
 *  blocker is single-document — the normal shape for a small tenant, and the
 *  shape of EVERY call tenant before `pipeline._sources_of` started counting
 *  calls — `MUST?` is a property of the corpus rather than of the finding, and
 *  printing it as a gap sends a reader to confirm something no finding here
 *  could have avoided. Suppressed there; kept where it discriminates. */
export function thinFlagDiscriminates(findings: readonly GoalFinding[]): boolean {
  const blockers = findings.filter(
    (f) => typeBucket(claimTypesOf(f)) === TYPE_BUCKET_BLOCKER,
  )
  if (!blockers.length) return false
  return blockers.some((f) => bucketOf(f) === "MUST")
}

/** `[index of the recommended finding, its gaps]` — `[-1, []]` when nothing
 *  was recommended, `[i, []]` when there is nothing left to name.
 *
 *  Order is fixed and meaningful: what could not be MEASURED, then what the
 *  write-up itself left open, then the corroboration caveat. The first is the
 *  one a reader can most cheaply close. */
export function dataGapsFor(
  findings: readonly GoalFinding[],
): [number, string[]] {
  const i = recommendedIndex(findings)
  if (i < 0) return [-1, []]
  const f = findings[i]
  const gaps: string[] = []

  if (f.impact_value == null) {
    const unit = (f.currency || "accounts").trim() || "accounts"
    const noun = unit === "accounts" ? "accounts" : unit.replace(/_/g, " ")
    gaps.push(
      `Which ${noun} is this about? Nothing connected put a number on how far `
      + `this reaches, so its size here is unknown — which is not the same as `
      + `small.`,
    )
  }

  for (const raw of f.deep_recommendation?.open_questions ?? []) {
    const q = (raw || "").trim()
    if (q) gaps.push(q)
  }

  if (bucketOf(f) === "MUST?" && thinFlagDiscriminates(findings)) {
    gaps.push(
      "One source document backs this blocker, where other blockers on this "
      + "run are backed by more. Confirm it against a second source before "
      + "you commit to it.",
    )
  }

  return [i, gaps.slice(0, MAX_DATA_GAPS)]
}

/** `Option N` for each finding, positionally — `0` for one that is not an
 *  option at all. Mirrors `data_gaps.option_numbers`.
 *
 *  A LABELLING CHANGE, AND ONLY THAT. The options ARE the deep write-ups the
 *  run already produced, numbered in the run's frozen rank order (I10). Nothing
 *  is grouped, merged, scored or chosen, and no model is asked anything (I2).
 *  Option 1 is whichever finding the recommendation is bound to — the same one
 *  `recommendedIndex` returns, by construction.
 *
 *  WHY NUMBER THEM. A column of identically-headed "Recommended — the full
 *  write-up" cards reads as a list to work through. The same cards headed
 *  Option 1 / Option 2 read as a choice with a stated preference between them,
 *  which is what the ranking computed — and the comparison sentence under
 *  Option 1 is the reason for that preference, already written and shown. */
export function optionNumbers(findings: readonly GoalFinding[]): number[] {
  let n = 0
  return findings.map((f) => (hasDeep(f) ? ++n : 0))
}
