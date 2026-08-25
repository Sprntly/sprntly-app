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
import type { GoalRunPlan } from "../../lib/api"

export type PlanDecision = {
  excluded_sources: string[]
  hypotheses: string[]
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
    onApprove({
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

      {plan.definition_text ? (
        <section className="ga-plan-section">
          <h2 className="ga-doc-h3">What I am trying to establish</h2>
          <blockquote className="ga-doc-quote">{plan.definition_text}</blockquote>
        </section>
      ) : null}

      <section className="ga-plan-section">
        <h2 className="ga-doc-h3">Where I will look</h2>
        <p className="ga-doc-note">
          {keptSignals.toLocaleString()} signal{keptSignals === 1 ? "" : "s"}
          {settled ? (
            <>
              {" "}in scope across {kept.length} source
              {kept.length === 1 ? "" : "s"} — counted when the plan was made,
              not a record of what has been read.
            </>
          ) : (
            <>
              . Uncheck anything you do not want read — the report will say that
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

      {plan.will_produce?.length ? (
        <section className="ga-plan-section" data-testid="goal-plan-produce">
          <h2 className="ga-doc-h3">What you will get</h2>
          <ul className="ga-doc-list">
            {plan.will_produce.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </section>
      ) : null}

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
