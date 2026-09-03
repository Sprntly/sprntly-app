import type { GoalFinding } from "./api"
import { TYPE_BUCKET_BLOCKER, bucketFor, documentCount, typeBucket } from "./goalMoscow"
import { contentTokens, sameTopic } from "./goalTopics"
import { stripClaimRefs } from "./goalProse"

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
 *  ORDER IS THE WHOLE SAFETY PROPERTY HERE, not a matter of taste. Two of
 *  these are ENGINE-DERIVED facts about the evidence — that the recommended
 *  finding could not be sized, and that the blocker it rests on has a single
 *  source document — and the rest are the MODEL's open questions, of which
 *  there can be any number. Sorted measurement/questions/caveat, a real run
 *  put one unsized gap and five questions in front of the caveat, the cap cut
 *  at exactly six, and the page recommended a single-document blocker under a
 *  heading reading "close these before you spend" that never said so.
 *
 *  So engine-derived gaps go first and never count against the cap. Truncating
 *  prose is a presentation decision; truncating "this rests on one document"
 *  is a disclosure failure dressed as one. Mirrors `data_gaps.data_gaps_for`. */
export function dataGapsFor(
  findings: readonly GoalFinding[],
): [number, string[]] {
  const i = recommendedIndex(findings)
  if (i < 0) return [-1, []]
  const f = findings[i]
  // NEVER TRUNCATED — facts about the evidence that nothing else states.
  const engineGaps: string[] = []
  // Truncated to whatever the cap leaves — the model's prose.
  const modelGaps: string[] = []

  if (f.impact_value == null) {
    const unit = (f.currency || "accounts").trim() || "accounts"
    const noun = unit === "accounts" ? "accounts" : unit.replace(/_/g, " ")
    engineGaps.push(
      `Which ${noun} is this about? Nothing connected put a number on how far `
      + `this reaches, so its size here is unknown — which is not the same as `
      + `small.`,
    )
  }

  if (bucketOf(f) === "MUST?" && thinFlagDiscriminates(findings)) {
    engineGaps.push(
      "One source document backs this blocker, where other blockers on this "
      + "run are backed by more. Confirm it against a second source before "
      + "you commit to it.",
    )
  }

  for (const raw of f.deep_recommendation?.open_questions ?? []) {
    const q = stripClaimRefs(raw || "").trim()
    if (q) modelGaps.push(q)
  }

  // THE CAP APPLIES TO THE MODEL'S QUESTIONS ONLY. `Math.max(0, …)` so a run
  // with more engine gaps than the cap drops every question rather than
  // slicing with a negative bound, which would silently keep the last few.
  const room = Math.max(0, MAX_DATA_GAPS - engineGaps.length)
  return [i, [...engineGaps, ...modelGaps.slice(0, room)]]
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
function labelOf(f: GoalFinding): string {
  return (f.label || "").trim() || (f.statement || "").trim()
}

/** Whether the top two write-ups are the SAME topic named twice.
 *
 *  A real run rendered Option 1 "enterprise compliance / citation chains" and
 *  Option 2 "court-admissible citation chains" — and the engine's own
 *  predicate says those are one topic. They reached the reader as separate
 *  options only because the clique builder is greedy first-fit, so which group
 *  a label lands in depends on the order candidates were offered in.
 *  Presenting them as a CHOICE is the worst available outcome: the reader is
 *  asked to pick between two names for one thing, under a "why this over the
 *  next" that has to admit it has no reason, because there is none.
 *
 *  ANSWERED IN THE RENDERER, NOT BY CHANGING THE MERGE — widening the merge
 *  would change which findings exist and which recommendation binds to rank 1.
 *  Mirrors `data_gaps.options_are_one_topic`. */
export function optionsAreOneTopic(findings: readonly GoalFinding[]): boolean {
  const deep = findings.filter(hasDeep)
  if (deep.length < 2) return false
  return sameTopic(contentTokens(labelOf(deep[0])), contentTokens(labelOf(deep[1])))
}

/** What the report says INSTEAD of a second option. Said plainly: an absent
 *  alternative that is not explained reads as a rendering bug. Verbatim from
 *  `data_gaps.ONE_TOPIC_NOTE`. */
export const ONE_TOPIC_NOTE =
  "Only one recommendation is offered here. The next-ranked write-up names "
  + "the same topic as this one rather than a different approach to it, so "
  + "presenting the two as alternatives would be a choice the evidence does "
  + "not actually offer."

export function optionNumbers(findings: readonly GoalFinding[]): number[] {
  // ALL ZEROS WHEN THE TOP TWO ARE ONE TOPIC — the write-ups still render,
  // they simply stop being labelled as a choice. Numbering is a claim that the
  // numbered things differ; making it silently when they do not is what
  // produced "Option 1: enterprise compliance / citation chains" beside
  // "Option 2: court-admissible citation chains".
  if (optionsAreOneTopic(findings)) return findings.map(() => 0)
  let n = 0
  return findings.map((f) => (hasDeep(f) ? ++n : 0))
}
