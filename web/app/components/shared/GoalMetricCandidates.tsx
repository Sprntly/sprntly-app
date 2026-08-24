"use client"

/**
 * The ask, done the way the spec requires. (Engine name Crucible; never shown.)
 *
 * `CRUCIBLE-GOAL-RESOLUTION.md` §5 makes FOUR requirements of an ask and calls
 * all four mandatory. The shipped ask met none of them: it opened with what it
 * could not find ("I can't find X defined anywhere in your systems"), asked an
 * open question ("describe what you'd want to see move"), named no consequence,
 * and handed over an empty box.
 *
 * §5: *"Asking is not a failure state, it is a normal step, and the quality of
 * the ask is what makes it feel competent rather than helpless."*
 *
 *  1. SHOW THE SEARCH BEFORE THE GAP. The first thing rendered is what was
 *     looked at, per source, with counts. Never open with what is missing.
 *  2. CANDIDATES WITH LIVE NUMBERS. Each carries its current value, how long
 *     it has been measured, how fresh it is, and where it lives — so the user
 *     answers by POINTING rather than by composing.
 *  3. NAME THE CONSEQUENCE. Every candidate states what changes about the
 *     analysis if it is chosen. That is what turns a form field into a
 *     decision.
 *  4. LEAVE THE DOOR OPEN. The free-text box is always rendered, never
 *     conditionally — at an enterprise the real definition frequently lives in
 *     a team's head and on no list we can produce.
 *
 * WHAT THIS IS NOT. Picking a candidate does not adopt a definition. §10 of the
 * spec bars inferring one, and I9 bars locking one without a human: a metric
 * observation says "interchange revenue for September was $2,264,810", which is
 * not a statement of what interchange revenue counts, over what population,
 * over what window. So clicking a candidate SEEDS the box with a sentence the
 * user then owns and edits. What gets locked is always their words.
 */
import type { GoalMetricCandidate, GoalAskSearched } from "../../lib/api"

const n = (v: number) => v.toLocaleString()

/** The sentence a pick seeds into the box. Deliberately incomplete — it states
 *  the metric and its live value and then stops, because the parts a
 *  definition needs (what is counted, over what population, over what window)
 *  are exactly the parts an observation cannot supply. The user finishes it. */
export function seedFromCandidate(c: GoalMetricCandidate): string {
  const value =
    typeof c.current_value === "number"
      ? `, currently ${n(c.current_value)} for ${c.current_period}`
      : ""
  return `${c.label}${value}, from ${c.source_label}. `
}

export function GoalMetricCandidates({
  searched,
  candidates,
  onPick,
}: {
  searched?: GoalAskSearched[]
  candidates?: GoalMetricCandidate[]
  onPick: (seed: string) => void
}) {
  const looked = (searched || []).filter((s) => s.signal_count > 0)
  const list = candidates || []

  return (
    <div className="ga-ask-grounded" data-testid="goal-ask-grounded">
      {/* 1. THE SEARCH, FIRST. */}
      {looked.length ? (
        <section className="ga-plan-section" data-testid="goal-ask-searched">
          <h2 className="ga-doc-h3">What I looked at</h2>
          <ul className="ga-doc-list">
            {looked.map((s) => (
              <li key={s.source_type}>
                <b>{n(s.signal_count)}</b> {s.label}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* 2 + 3. CANDIDATES WITH LIVE NUMBERS, EACH NAMING ITS CONSEQUENCE. */}
      {list.length ? (
        <section className="ga-plan-section" data-testid="goal-ask-candidates">
          <h2 className="ga-doc-h3">What you already measure</h2>
          <p className="ga-doc-note">
            Point at one to start from it — you can still change every word
            before this runs.
          </p>
          <ul className="ga-ask-candidates">
            {list.map((c) => (
              <li key={c.key}>
                <button
                  type="button"
                  className="ga-ask-candidate"
                  onClick={() => onPick(seedFromCandidate(c))}
                >
                  <span className="ga-ask-candidate-head">
                    <b>{c.label}</b>{" "}
                    {typeof c.current_value === "number" ? (
                      <span className="ga-doc-source-count">
                        {n(c.current_value)} · {c.current_period}
                      </span>
                    ) : null}
                  </span>
                  <span className="ga-plan-witness">
                    {n(c.observations)} observation
                    {c.observations === 1 ? "" : "s"}
                    {c.first_period && c.last_period &&
                    c.first_period !== c.last_period
                      ? `, ${c.first_period} to ${c.last_period}`
                      : ""}
                    {c.source_label ? ` · from ${c.source_label}` : ""}
                  </span>
                  <span className="ga-ask-consequence">{c.consequence}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}

export default GoalMetricCandidates
