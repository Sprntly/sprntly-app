"use client"

/**
 * The SECOND gate: the run says what it will do, before it does it.
 * (Engine name Crucible; that word never appears on screen.)
 *
 * WHY THIS SCREEN EXISTS. A run reads a company's whole knowledge graph and
 * takes minutes. Until this step, the first thing a user learned about its
 * limits was the coverage notes at the BOTTOM of the finished output — after
 * the wait, and phrased as an apology. The same facts beforehand are a
 * decision instead: connect the missing source, drop one that would only add
 * noise, or accept a qualitative answer knowingly.
 *
 * IT IS AN INVENTORY, NOT A PREVIEW. The counts come from counting rows, not
 * from reading them, so this arrives in about a second and promises nothing
 * about what the findings will say.
 *
 * THREE THINGS THE USER CAN DO HERE, and each one changes the run:
 *  - See what will be read, per source, with what that source can witness.
 *  - Drop a source. Honoured by the engine, and recorded on the finished
 *    report — a quietly narrower run is exactly what coverage notes exist to
 *    prevent, so an excluded source is stated, not just omitted.
 *  - Say what they already believe. A run can only report what it found; a run
 *    that knows what you expected can also tell you when it did not find it.
 *
 * The approve button is DISABLED when every source has been excluded. A run
 * with nothing to read produces a confident-looking empty report, which is the
 * worst output this feature has.
 */
import * as React from "react"
import { useMemo, useState } from "react"
import { planNarrative } from "../../lib/goalPlanNarrative"
import type { GoalRunPlan } from "../../lib/api"

export type PlanDecision = {
  excluded_sources: string[]
  hypotheses: string[]
  /** The definition this click adopts, sent ONLY when the reader edited the
   *  proposal. Absent means "as shown", which the server reads off the plan it
   *  stored — so an untouched approve cannot round-trip the definition through
   *  the client, where a stale card could overwrite it with old words. */
  definition_text?: string
  /** ── ANSWERS TO WHAT THE RUN CANNOT KNOW. ────────────────────────────
   *  All optional. Skipping them yields exactly the document you got before,
   *  with the affected sections stating what is missing rather than guessing.
   *  A value given here is an ASSUMPTION, not evidence, and the document says
   *  so where it uses it. */
  account_value?: number
  decision_owner?: string
  needed_by?: string
}

/** Mirrors the API's per-hypothesis cap. One place it can drift, stated here
 *  rather than discovered as a 422. */
export const MAX_HYPOTHESIS_CHARS = 2_000

export function GoalAnalysisPlan({
  plan,
  approving,
  onApprove,
  settled,
}: {
  plan: GoalRunPlan
  approving: boolean
  onApprove: (decision: PlanDecision) => void
  /** THE PLAN STAYS IN THE THREAD after it is approved, read-only.
   *
   *  It used to collapse into a four-line receipt — "Plan approved", the source
   *  counts, and nothing else. That threw away the entire thing a PM has to be
   *  able to point at later: which sources were in scope, what each one can
   *  actually witness, what the run said up front it would NOT be able to
   *  answer. Scrolling back showed that a plan was approved, not what was
   *  approved, which is the whole reason the gate is in the conversation and
   *  not in a modal.
   *
   *  Same component, so the record cannot drift from the thing that was agreed
   *  to — two renderers of one plan is the mistake #1325 had to come back for. */
  settled?: { excludedSources: string[]; hypotheses: string[] }
}) {
  // Excluded, not included: the default is "read everything", so an empty set
  // is the untouched state and no source can be dropped by an off-by-one.
  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [hypothesesText, setHypothesesText] = useState("")
  // THE DEFINITION, EDITABLE IN PLACE. `null` is untouched — distinct from a
  // string equal to the proposal, because only `null` may be omitted from the
  // approve body. A reader who selects the text, retypes it identically and
  // approves has still adopted it; they just have not changed it.
  const [definitionEdit, setDefinitionEdit] = useState<string | null>(null)
  const [accountValue, setAccountValue] = useState("")
  const [decisionOwner, setDecisionOwner] = useState("")
  const [neededBy, setNeededBy] = useState("")

  // A settled record reads its exclusions from what was actually posted, not
  // from local state a re-mount would have thrown away.
  const effectiveExcluded = settled ? new Set(settled.excludedSources) : excluded
  const kept = useMemo(
    () => plan.sources.filter((s) => !effectiveExcluded.has(s.source_type)),
    [plan.sources, effectiveExcluded],
  )
  const keptSignals = kept.reduce((n, s) => n + s.signal_count, 0)
  const nothingLeft = plan.sources.length > 0 && kept.length === 0

  const toggle = (sourceType: string) => {
    setExcluded((prev) => {
      const next = new Set(prev)
      if (next.has(sourceType)) next.delete(sourceType)
      else next.add(sourceType)
      return next
    })
  }

  // Mirrors `HYPOTHESIS_MAX_CHARS` on `/v1/crucible/{id}/approve`. The API
  // REJECTS an over-long line rather than truncating it, and a 422 there is
  // unrecoverable from the panel: the run stays `awaiting_approval`, so the
  // user retries the same text forever. Caught here, where the offending line
  // can actually be named.
  const tooLong = hypothesesText
    .split("\n")
    .map((h) => h.trim())
    .filter((h) => h.length > MAX_HYPOTHESIS_CHARS)

  const submit = () => {
    if (approving || nothingLeft || tooLong.length) return
    const editedDefinition =
      definitionEdit !== null && definitionEdit.trim() &&
      definitionEdit.trim() !== (plan.definition_text || "").trim()
        ? definitionEdit.trim()
        : undefined
    // EMPTY MEANS UNANSWERED, never zero. A blank account value must not
    // reach the arithmetic as 0 — that would render a stake of nothing and
    // read as a measurement.
    const value = Number.parseFloat(accountValue.replace(/[^0-9.]/g, ""))
    onApprove({
      ...(Number.isFinite(value) && value > 0 ? { account_value: value } : {}),
      ...(decisionOwner.trim() ? { decision_owner: decisionOwner.trim() } : {}),
      ...(neededBy.trim() ? { needed_by: neededBy.trim() } : {}),
      ...(editedDefinition ? { definition_text: editedDefinition } : {}),
      excluded_sources: [...excluded],
      // One per line. Blank lines are dropped rather than sent as empty
      // hypotheses, which would be counted and reported back as things the
      // user believed.
      hypotheses: hypothesesText
        .split("\n")
        .map((h) => h.trim())
        .filter(Boolean),
    })
  }

  return (
    <div className="ga-plan" data-testid="goal-plan">
      <header className="ga-doc-header">
        <p className="ga-doc-eyebrow">
          {settled ? "Plan approved" : "Before this runs"}
        </p>
        <h1 className="ga-doc-title">{plan.goal_text}</h1>
      </header>

      {/* THE APPROACH, IN FIVE SENTENCES. Everything below this block was
          already true and already on screen — and unreadable as an approach,
          because it was four headed sections and a checkbox list with no
          sentence anywhere saying what was about to happen. The feedback asked
          for a numbered account first and the detail underneath, which is what
          this is: what we SAY, then what we DO.
          RECOMPUTED FROM `effectiveExcluded`, so unticking a source rewrites
          step 1 under the reader's hand rather than leaving the narrative
          describing a run they have just changed. */}
      <section className="ga-plan-section ga-plan-approach" data-testid="goal-plan-approach">
        <p className="ga-doc-note">
          {settled
            ? "This is the approach you approved."
            : "This is the approach I am going to use. Approve it, or change it below."}
        </p>
        <ol className="ga-plan-steps">
          {planNarrative(plan, effectiveExcluded).map((step, i) => (
            <li key={i}>
              {step.text}
              {/* A LIST STAYS A LIST. Several full sentences folded into one
                  with commas and an "and" is what made this step 487
                  characters on a real run. */}
              {step.items?.length ? (
                <ul className="ga-plan-step-items">
                  {step.items.map((it, j) => <li key={j}>{it}</li>)}
                </ul>
              ) : null}
            </li>
          ))}
        </ol>
      </section>

      {/* THE DEFINITION IS CONFIRMED HERE NOW, not one screen earlier.
          It used to have its own gate: a bare question, asked before the
          reader had seen a single thing the run intended to do. The answers
          showed what that costs — one run's definition of its own metric was
          recorded as the literal words "that is accurate", because that is
          what somebody typed at a prompt that was not, to them, asking for a
          definition.
          I9 is unchanged: a definition is adopted or elicited, never inferred.
          It is shown, attributed, and editable in place, and approving is the
          act of adopting it. What changed is only that the question now has
          the plan around it to give it meaning. */}
      {plan.definition_text ? (
        <section className="ga-plan-section" data-testid="goal-plan-definition">
          <h2 className="ga-doc-h3">
            {settled || plan.definition_adopted
              ? "What this was asked to establish"
              : "Confirm what this means"}
          </h2>
          {settled || plan.definition_adopted ? (
            <blockquote className="ga-doc-quote">{plan.definition_text}</blockquote>
          ) : (
            <>
              <p className="ga-doc-note">
                {/* WHERE IT CAME FROM AND THAT YOU MAY CHANGE IT — nothing
                    else. This line used to end "I work to this sentence
                    exactly as it stands", which is the first clause of the
                    server's note rendered immediately below it: the same
                    promise twice, in consecutive paragraphs. */}
                {plan.definition_source
                  ? <>Taken from {plan.definition_source}. Change it if that is not what you meant.</>
                  : <>Change it if that is not what you meant.</>}
              </p>
              <textarea
                className="ga-plan-definition-edit"
                aria-label="What this goal means"
                rows={3}
                value={definitionEdit ?? plan.definition_text}
                onChange={(e) => setDefinitionEdit(e.target.value)}
              />
              {/* WHAT IS DONE WITH THE SENTENCE, from the server, so §6's
                  wording lives in one place. It says the part the sentence
                  itself cannot: that it is taken literally, and what gets read
                  against it. The CONVENTION is not repeated here — it is the
                  text in the box above, and saying it twice is the repetition
                  this whole change was asked to remove. */}
              {plan.definition_note ? (
                <p className="ga-doc-note" data-testid="goal-plan-definition-note">
                  {plan.definition_note}
                </p>
              ) : null}
            </>
          )}
        </section>
      ) : null}

      <section className="ga-plan-section">
        <h2 className="ga-doc-h3">Where I will look</h2>
        <p className="ga-doc-note">
          {settled ? (
            <>
              {keptSignals.toLocaleString()} signal
              {keptSignals === 1 ? "" : "s"} in scope across {kept.length} source
              {kept.length === 1 ? "" : "s"} — counted when the plan was made,
              not a record of what has been read.
            </>
          ) : (
            // NOT THE TOTAL AGAIN. The narrative's first step opens with it;
            // repeating it as this section's lede put the same number twice on
            // one card. The per-source counts below are what this section is
            // for, and they are not a restatement of anything.
            <>
              Uncheck anything you do not want read — the report will say that
              you dropped it.
            </>
          )}
        </p>
        {plan.sources.length ? (
          <ul className="ga-plan-sources">
            {plan.sources.map((s) => {
              // READ FROM WHAT WAS POSTED on a settled plan. Reading the
              // local checkbox state here rendered every source as kept, so
              // the record silently agreed with a wider run than the one that
              // actually happened — the single thing this card exists to
              // prevent.
              const on = !effectiveExcluded.has(s.source_type)
              return (
                <li key={s.source_type}>
                  <label className="ga-plan-source">
                    {/* A SETTLED PLAN IS A RECORD, NOT A CONTROL. The checkbox
                        is gone rather than disabled: a disabled control invites
                        a click and says nothing about why it will not move. */}
                    {settled ? null : (
                      <input
                        type="checkbox"
                        checked={on}
                        aria-label={`Read ${s.label}`}
                        onChange={() => toggle(s.source_type)}
                      />
                    )}
                    <span>
                      <span className={settled && !on ? "ggc-src-struck" : undefined}>
                        <b>{s.label}</b>{" "}
                        <span className="ga-doc-source-count">
                          {s.signal_count.toLocaleString()}
                        </span>
                      </span>
                      {settled && !on ? (
                        <span className="ggc-src-note"> — dropped by you</span>
                      ) : null}
                      <br />
                      <span className="ga-plan-witness">
                        Can witness {s.witnesses}
                      </span>
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="ga-empty" data-testid="goal-plan-no-sources">
            Nothing is connected for this to read.
          </p>
        )}
      </section>

      {plan.cannot_answer?.length ? (
        <section className="ga-plan-section" data-testid="goal-plan-gaps">
          <h2 className="ga-doc-h3">What I will not be able to answer</h2>
          <ul className="ga-doc-gaps">
            {plan.cannot_answer.map((g, i) => (
              <li key={i}>
                <p className="ga-doc-gap-q">{g.question}</p>
                <p className="ga-doc-gap-why">Because {g.because}.</p>
                <p className="ga-doc-gap-fix">
                  <span className="ga-sources-label">To close it</span>{" "}
                  {g.remedy}
                </p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* "What you will get" LIVED HERE and is gone: `planNarrative` composes
          its fourth step from these same strings, so the section printed the
          deliverables a second time, verbatim, three inches below the first.
          The narrative is the summary AND the only statement of them now. The
          gap list below stays, because it carries `because` and `remedy` —
          detail the one-line summary of it genuinely does not have. */}


      {/* ── WHAT I CANNOT KNOW. ─────────────────────────────────────────
          Apurva: "the plan gate can start asking questions it doesn't know
          answers to." Until now the gate asked one thing — what the metric
          means — and everything else it lacked was reported afterwards as a
          limit. These three are the difference between a document that says
          "no revenue is mapped to accounts" and one that sizes the work.
          OPTIONAL, AND SAID TO BE. A reader who skips them gets the document
          they got before; nothing here is required to run. */}
      <section className="ga-plan-section" data-testid="goal-plan-unknowns">
        <h2 className="ga-doc-h3">What I cannot know</h2>
        <p className="ga-doc-note">
          None of this is in your connected sources, and I will not guess at
          it. Answer what you can — anything you leave blank stays stated as
          missing rather than filled in.
        </p>
        <label className="ga-plan-ask">
          <span>What is one account worth to you, per year?</span>
          <input
            type="text" inputMode="decimal" placeholder="e.g. 12000"
            aria-label="What is one account worth to you, per year?"
            value={accountValue}
            onChange={(e) => setAccountValue(e.target.value)}
          />
          <em>Lets the findings be sized in money instead of account counts.
            Used as your estimate, and labelled as one.</em>
        </label>
        <label className="ga-plan-ask">
          <span>Who decides this?</span>
          <input
            type="text" placeholder="e.g. VP Product"
            aria-label="Who decides this?"
            value={decisionOwner}
            onChange={(e) => setDecisionOwner(e.target.value)}
          />
        </label>
        <label className="ga-plan-ask">
          <span>When do you need the decision?</span>
          <input
            type="text" placeholder="e.g. before the Q3 review"
            aria-label="When do you need the decision?"
            value={neededBy}
            onChange={(e) => setNeededBy(e.target.value)}
          />
        </label>
      </section>

      {settled ? (
        settled.hypotheses.length ? (
          <section className="ga-plan-section" data-testid="goal-plan-settled-hypotheses">
            <h2 className="ga-doc-h3">
              What you already believed
            </h2>
            <ul className="ga-doc-list">
              {settled.hypotheses.map((h, i) => <li key={i}>{h}</li>)}
            </ul>
          </section>
        ) : null
      ) : (
      <section className="ga-plan-section">
        <h2 className="ga-doc-h3">What do you already believe?</h2>
        <p className="ga-doc-note">
          Optional, one per line. A run can always tell you what it found; told
          what you expected, it can also record what it did not find.
        </p>
        <textarea
          className="ga-definition"
          aria-label="What you already believe"
          rows={3}
          value={hypothesesText}
          placeholder={"onboarding is where they drop off\npricing is the blocker"}
          onChange={(e) => setHypothesesText(e.target.value)}
        />
      </section>
      )}

      {!settled && tooLong.length ? (
        <p className="ga-doc-note" data-testid="goal-plan-hypothesis-too-long">
          {tooLong.length === 1
            ? `One hypothesis is ${tooLong[0].length} characters long. `
            : `${tooLong.length} of these are over ${MAX_HYPOTHESIS_CHARS} characters. `}
          Each one has to be under {MAX_HYPOTHESIS_CHARS} characters — shorten
          it, or split it across lines.
        </p>
      ) : null}

      {!settled && nothingLeft ? (
        <p className="ga-doc-note" data-testid="goal-plan-empty-warning">
          Every source is unchecked, so there is nothing left to read.
        </p>
      ) : null}

      {/* NO BUTTON ON A SETTLED PLAN. The decision is made; a control that
          cannot act is worse than none, because it reads as one that can. */}
      {settled ? null : (
        <button
          type="button"
          className="ga-confirm"
          disabled={approving || nothingLeft}
          onClick={submit}
        >
          {approving ? "Starting…" : "Approve and run"}
        </button>
      )}
    </div>
  )
}

export default GoalAnalysisPlan
