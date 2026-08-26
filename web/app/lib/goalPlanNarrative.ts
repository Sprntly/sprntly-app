import type { GoalRunPlan } from "./api"

/** The plan, said as a person would say it.
 *
 *  THE PROBLEM THIS SOLVES. The plan card was accurate and unreadable: four
 *  headed sections, a checkbox list, per-source witness clauses and a gap list,
 *  with no sentence anywhere saying what was actually about to happen. The
 *  feedback asked for the opposite shape — "This is the approach that I am
 *  going to use… 1. I am going to analyse… 2. I will review… 3. I will
 *  analyse… 4. Present a list of…" — a short numbered account first, detail
 *  underneath for whoever wants it.
 *
 *  EVERY STEP IS DERIVED, NONE IS WRITTEN. The steps below are composed from
 *  fields the plan already carries: the sources actually kept, the definition
 *  actually adopted, the deliverables the planner actually promised, the gaps
 *  it actually declared. Nothing here describes work the run does not do.
 *
 *  WHAT IS DELIBERATELY ABSENT is a number. The requested copy ended "which if
 *  addressed can drive $230K in revenue" — a forecast. The plan step takes an
 *  INVENTORY: it counts what is connected and reads none of it, which is why
 *  it returns in about a second. It cannot know what the findings will be
 *  worth, and a figure quoted before anything is read would be invention
 *  dressed as a promise. The size of the opportunity is the report's to state,
 *  after the reading, from evidence. */
export function planNarrative(
  plan: Pick<GoalRunPlan,
    "sources" | "definition_text" | "will_produce" | "cannot_answer"
    | "definition_adopted">,
  excluded: ReadonlySet<string>,
): string[] {
  const kept = (plan.sources ?? []).filter((s) => !excluded.has(s.source_type))
  const steps: string[] = []

  // 1. WHAT GETS READ. Named in the reader's own vocabulary — the labels the
  //    checkboxes carry — not in source-type slugs.
  if (kept.length) {
    const signals = kept.reduce((n, s) => n + (s.signal_count || 0), 0)
    steps.push(
      `Read your ${joinWords(kept.map((s) => s.label || s.source_type))}` +
      ` — ${signals.toLocaleString()} ${signals === 1 ? "signal" : "signals"} in all.`,
    )
  } else {
    // NOT SILENCE. A plan with everything dropped still renders, and the
    // narrative saying nothing would read as though a full run were coming.
    steps.push("Read nothing — every source is unchecked below.")
  }

  // 2. HOW IT IS JUDGED. The refutation rules in one sentence, because they
  //    are the reason the answer is short, and a reader who does not know them
  //    reads a short answer as a thin one.
  steps.push(
    "Group what they say into findings, and throw out anything only one" +
    " account raises, anything that is one voice repeated, and anything the" +
    " source is not in a position to know.",
  )

  // 3. WHAT IT IS MEASURED AGAINST. Only when a definition was adopted — the
  //    run never infers one, so neither does this.
  if (plan.definition_text?.trim()) {
    // QUOTED ONLY WHEN IT IS SETTLED. While the definition is still a proposal
    // it is sitting in an editable field a few lines below this, so quoting it
    // here printed the same sentence twice on one card — the "multiple
    // repetitions" the feedback asked us to cut, reintroduced by the fix for
    // the rest of it. Pointing at it is enough while it is still on screen;
    // once it is adopted the field is gone and the words have to be here.
    steps.push(
      plan.definition_adopted
        ? `Judge what survives against your own definition: “${plan.definition_text.trim()}”.`
        : "Judge what survives against your own definition of the metric, which you confirm below.",
    )
  }

  // 4. WHAT COMES BACK. The planner's own promises, verbatim, so the narrative
  //    cannot promise something the run did not.
  const produce = (plan.will_produce ?? []).map((w) => w.trim()).filter(Boolean)
  if (produce.length) {
    steps.push(`Give you ${joinWords(produce.map(lowerFirst))}.`)
  }

  // 5. WHAT IT WILL NOT DO. Last, and stated as a step, because a limit
  //    disclosed up front is part of the approach — not a footnote to it.
  const gaps = plan.cannot_answer ?? []
  if (gaps.length) {
    steps.push(
      `Tell you plainly what this cannot settle — ${gaps.length} ` +
      `${gaps.length === 1 ? "question is" : "questions are"} already out of` +
      ` reach, listed below with what would close ${gaps.length === 1 ? "it" : "them"}.`,
    )
  }
  return steps
}

/** "a, b and c" — Oxford-free, matching the rest of the product's prose. */
function joinWords(xs: string[]): string {
  if (xs.length <= 1) return xs[0] ?? ""
  if (xs.length === 2) return `${xs[0]} and ${xs[1]}`
  return `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`
}

/** Lowercases a promise's first word so it reads inside "Give you …" — but
 *  never an acronym or a proper noun, where the capital is the word. */
function lowerFirst(s: string): string {
  const first = s.split(/\s+/)[0] ?? ""
  if (first.length > 1 && first === first.toUpperCase()) return s
  if (/^[A-Z][a-z]+[A-Z]/.test(first)) return s
  return s.charAt(0).toLowerCase() + s.slice(1)
}
