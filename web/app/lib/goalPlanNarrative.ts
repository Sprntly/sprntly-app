import { frameworkDisplayName } from "./goalFrameworkDisplay"
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
export type PlanStep = {
  text: string
  /** Rendered as a nested list under `text` rather than folded into it.
   *
   *  MEASURED AGAINST REAL DATA, NOT THE FIXTURE. `will_produce` reads like a
   *  list of short noun phrases in every test here; on a live run its entries
   *  are full sentences carrying their own em-dashes and subclauses. Joined
   *  with commas and an "and", five of them produced a single 487-character
   *  step sitting between four others of 87 to 173 — one unreadable paragraph
   *  in the middle of the thing whose whole purpose is being readable. */
  items?: string[]
}

export function planNarrative(
  plan: Pick<GoalRunPlan,
    "sources" | "definition_text" | "will_produce" | "cannot_answer"
    | "definition_adopted" | "framework" | "framework_reason">,
  excluded: ReadonlySet<string>,
): PlanStep[] {
  const kept = (plan.sources ?? []).filter((s) => !excluded.has(s.source_type))
  const steps: PlanStep[] = []

  // 0. WHAT IT WILL BE JUDGED AGAINST, CONFIRMED FIRST. Everything after this
  //    step — what counts as a finding, what a source is trusted to witness,
  //    what gets ranked — depends on it, so it is named before any of them
  //    rather than surfacing three steps in as a qualifier on "judge what
  //    survives". David: "Confirm what revenue means here. I work to your
  //    definition exactly as written." Only when a definition exists — the
  //    run never infers one (I9), so neither does this.
  //
  //    THE WORDS THEMSELVES LIVE HERE, ONCE. A later step still judges
  //    survivors against this definition, but it points back at this one
  //    instead of quoting it again — see the note there for why printing the
  //    same sentence twice was exactly the failure this file already fixed
  //    once (the "QUOTED ONLY WHEN IT IS SETTLED" note below).
  if (plan.definition_text?.trim()) {
    steps.push({
      text: plan.definition_adopted
        ? `Confirm what this means — I work to your definition of it exactly` +
          ` as written: “${plan.definition_text.trim()}”.`
        : "Confirm what this means — I work to your definition of it exactly" +
          " as written, which you confirm below.",
    })
  }

  // 1. WHAT GETS READ. Named in the reader's own vocabulary — the labels the
  //    checkboxes carry — not in source-type slugs.
  if (kept.length) {
    const signals = kept.reduce((n, s) => n + (s.signal_count || 0), 0)
    steps.push({
      text:
        `Read your ${joinWords(kept.map((s) => s.label || s.source_type))}` +
        ` — ${signals.toLocaleString()} ${signals === 1 ? "signal" : "signals"} in all.`,
    })
  } else {
    // NOT SILENCE. A plan with everything dropped still renders, and the
    // narrative saying nothing would read as though a full run were coming.
    steps.push({ text: "Read nothing — every source is unchecked below." })
  }

  // 2. HOW IT IS JUDGED. The refutation rules in one sentence, because they
  //    are the reason the answer is short, and a reader who does not know them
  //    reads a short answer as a thin one.
  steps.push({
    text:
      "Group what they say into findings, and throw out anything only one" +
      " account raises, anything that is one voice repeated, and anything the" +
      " source is not in a position to know.",
  })

  // 3. WHAT IT IS MEASURED AGAINST. Only when a definition exists — same
  //    gating as step 0. THE WORDS THEMSELVES sit there, not here: quoting
  //    them again on this step printed the same sentence twice on one card —
  //    the "multiple repetitions" the feedback asked us to cut, and exactly
  //    the failure mode the "QUOTED ONLY WHEN IT IS SETTLED" note used to warn
  //    about at this spot. This step now only points back at step 0.
  if (plan.definition_text?.trim()) {
    steps.push({
      text: plan.definition_adopted
        ? "Judge what survives against that definition."
        : "Judge what survives against the definition you confirm above.",
    })
  }

  // 3b. HOW THE SURVIVORS GET ORDERED, named before the run — AND WHY THAT
  //     FRAMEWORK, not another.
  //
  // Apurva: "in the initial plan that we are going to output, we should
  // highlight that we are using the RICE framework." A ranking method
  // discovered in the output is a convention; one stated in the plan is a
  // choice, and the reader can say no to it while the gate is still open.
  //
  // CHOSEN BY CODE OVER WHAT IS CONNECTED, never by a model (I2) — see
  // `app.crucible.framework.select_framework`. RICE needs a numeric source
  // (analytics/revenue/measured outcome) to size Reach and Impact; without
  // one it renders every row unmeasured rather than ranking badly (measured
  // on a real corpus: 26/26 findings scored `None`). MoSCoW needs only a
  // stated blocker or a stated preference, which any corpus with either
  // already carries — so the terms spelled out below differ by which
  // framework this run actually picked.
  const framework = (plan.framework || "").trim()
  if (framework) {
    const isMoscow = framework.trim().toLowerCase() === "moscow"
    const items = isMoscow
      ? [
          "MUST — a stated blocker: something is stopping an account today",
          "SHOULD or COULD — a stated preference: something an account asked for",
          "Graded by how many independent source documents back each one, not by raw claim count",
        ]
      : [
          "Reach — how many of your accounts the theme touches, counted",
          "Impact — how directly it bears on the metric, read from the kind of claim behind it",
          "Confidence — the band the evidence earns, not a guess",
          "Effort — not in your connected data, so the ranking is by reach × impact × confidence until you supply one",
        ]
    const reason = (plan.framework_reason || "").trim()
    steps.push({
      // THE READER'S WORD, NOT THE STORED VALUE. `plan.framework` is the
      // storage/comparison value ("rice", "moscow") — a real run never sends
      // pre-cased text, so interpolating it directly here printed "moscow"
      // in the middle of a sentence. `frameworkDisplayName` is the frontend
      // mirror of `app.crucible.framework.display_name`; keep the two in
      // step if either changes.
      text: `Rank what survives with ${frameworkDisplayName(framework)}:`,
      items: reason ? [...items, reason] : items,
    })
  }

  // 4. WHAT COMES BACK. The planner's own promises, verbatim, so the narrative
  //    cannot promise something the run did not.
  const produce = (plan.will_produce ?? []).map((w) => w.trim()).filter(Boolean)
  if (produce.length === 1) {
    steps.push({ text: `Give you ${lowerFirst(produce[0])}.` })
  } else if (produce.length) {
    // A LIST, BECAUSE IT IS A LIST. Folding several full sentences into one
    // with commas and an "and" is exactly what produced the 487-character step
    // this branch exists to prevent; see `PlanStep.items`.
    steps.push({ text: "Give you:", items: produce })
  }

  // 4b. WHAT GETS DROPPED, SHOWN RATHER THAN HIDDEN. Stated as a promise here
  //     because otherwise the reader only meets it later, as a section in the
  //     finished report — a commitment discovered after the fact reads as an
  //     apology, not a design choice. Unconditional, like step 2's
  //     corroboration rules: this is how the run always works, not something
  //     read off this particular plan.
  //
  //     NO COUNT HERE. This step runs before the run reads anything, so it
  //     cannot say how many findings will end up set aside — only that it
  //     will show them, and why, when it does. The number itself belongs to
  //     the finished report, from evidence, same as the deliverables above.
  steps.push({
    text: "Show you what got set aside — every finding that did not survive, with the reason it was dropped.",
  })

  // 5. WHAT IT WILL NOT DO. Last, and stated as a step, because a limit
  //    disclosed up front is part of the approach — not a footnote to it.
  const gaps = plan.cannot_answer ?? []
  if (gaps.length) {
    steps.push({
      text:
        `Tell you plainly what this cannot settle — ${gaps.length} ` +
        `${gaps.length === 1 ? "question is" : "questions are"} already out of` +
        ` reach, listed below with what would close ${gaps.length === 1 ? "it" : "them"}.`,
    })
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
