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

/** How much of two ACTIONS' combined vocabulary must be shared before they
 *  are one build described twice rather than two builds in one domain.
 *
 *  PROPORTIONAL, NOT AN ABSOLUTE COUNT, because the input changed.
 *  `sameTopic` qualifies on two shared content words, calibrated for the 2-4
 *  word theme LABELS it was written for. An action is a sentence: two shared
 *  words out of thirty-five is noise, and the label rule applied to prose
 *  collapsed two materially different builds.
 *
 *  JACCARD, NOT THE OVERLAP COEFFICIENT — Jaccard is symmetric, where overlap
 *  divides by the shorter side and would collapse pairs for being terse.
 *  0.6 MEASURED: the two different builds score 0.129, a true restatement
 *  0.727. Mirrors `data_gaps.ACTION_TOPIC_OVERLAP`. */
export const ACTION_TOPIC_OVERLAP = 0.6

function actionsAreOneBuild(a: string, b: string): boolean {
  const ta = contentTokens(a)
  const tb = contentTokens(b)
  if (!ta.size || !tb.size) return false
  let shared = 0
  for (const t of ta) if (tb.has(t)) shared += 1
  const union = new Set([...ta, ...tb]).size
  return union > 0 && shared / union >= ACTION_TOPIC_OVERLAP
}

/** Whether the top two write-ups are one thing described twice.
 *
 *  REQUIRES BOTH THE LABELS AND THE ACTIONS TO AGREE. The labels alone were
 *  the original test and they are the wrong question: what the page presents
 *  as a choice is the ACTIONS. A real run offered "Build a multi-vendor,
 *  compliance-grade provenance layer…" beside "Build a court-admissible
 *  citation chain feature as a distinct, first-class capability separate
 *  from RAG retrieval quality improvements" — different builds whose labels
 *  shared exactly two content words, so the label test collapsed them.
 *
 *  The two errors are not symmetric: collapsing two different builds hides a
 *  real choice and cannot be recovered from the page, while declining to
 *  collapse two similar ones costs a duplicated card — and the comparison
 *  paragraph now always renders, so the reader is still told which comes
 *  first and why. Mirrors `data_gaps.options_are_one_topic`. */
export function optionsAreOneTopic(findings: readonly GoalFinding[]): boolean {
  const deep = findings.filter(hasDeep)
  if (deep.length < 2) return false
  if (!sameTopic(contentTokens(labelOf(deep[0])), contentTokens(labelOf(deep[1])))) {
    return false
  }
  return actionsAreOneBuild(
    deep[0].deep_recommendation?.action ?? "",
    deep[1].deep_recommendation?.action ?? "",
  )
}

/** What the report says instead of a second OPTION LABEL. It never replaces
 *  the comparison: "why this one first" is the question the reader came with.
 *  Verbatim from `data_gaps.ONE_TOPIC_NOTE`. */
export const ONE_TOPIC_NOTE =
  "These two write-ups describe the same build rather than a choice between "
  + "approaches, so they are not offered as alternatives. The comparison "
  + "below still says which comes first, and why."

/** The heading for one deep write-up's card.
 *
 *  EXACTLY ONE CARD MAY BE HEADED AS THE RECOMMENDATION. Returning all-zero
 *  option numbers under one-topic made every deep card fall back to the same
 *  "Recommended — the full write-up" header, so a real page carried that
 *  phrase twice. The numbering never disappears — it decides which card is
 *  first — and only its PRESENTATION changes. Mirrors
 *  `data_gaps.option_header`. */
export function optionHeader(
  option: number, total: number, oneTopic: boolean,
): string {
  if (option <= 0) return ""
  if (oneTopic) {
    return option === 1
      ? "Recommended — the full write-up."
      : "Also written up — the same build, not an alternative."
  }
  if (total <= 1) return "Recommended — the full write-up."
  return option === 1 ? "Option 1 — recommended." : `Option ${option} — alternative.`
}

export function optionNumbers(findings: readonly GoalFinding[]): number[] {
  // ALWAYS NUMBERS, even when the two are one build described twice. The
  // number decides which card is FIRST and which is subordinate; whether it
  // shows as "Option 1" or is absorbed into a plainer header is
  // `optionHeader`'s decision. Returning all zeros here sent both cards down
  // the single-write-up path and headed both "Recommended".
  let n = 0
  return findings.map((f) => (hasDeep(f) ? ++n : 0))
}
