"use client"

/**
 * The run, narrating itself. (Engine name Crucible; never on screen.)
 *
 * WHY THIS REPLACED A SPINNER. `running` used to be one state showing
 * "Reading N claims…" for minutes, so the first thing a reader learned about
 * how the answer was reached was the finished report. But this pipeline is
 * deterministic and every number in its funnel is already computed — the only
 * reason they were invisible is that nothing wrote them down.
 *
 * That matters beyond decoration. A reader who watched 1,744 groups become 168
 * findings knows what the ranking IS, and can defend it. A reader handed 168
 * findings has to take them on authority, and the first wrong call costs all of
 * it. Watching the funnel is how the mechanism gets learned.
 *
 * IT IS ALSO RENDERED AFTER THE RUN FINISHES, and that is not a nicety. The
 * gap between the final progress write and `status="ready"` is about a second
 * against a 3s poll, so a reader who could only see it live would usually see
 * nothing at all — the drop rows, which are the entire feature, would render on
 * a minority of runs and never once the report was on screen.
 *
 * FOUR RULES IT MUST NOT BREAK.
 *
 *  1. NEVER RENDER A NUMBER THE RUN HAS NOT MEASURED. The fields arrive in
 *     three writes and a poll can land between any two, so everything is
 *     optional and absent means absent — never zero. A narration that revises
 *     its own numbers teaches a reader to distrust all of them.
 *  2. A CHECK THAT DID NOT RUN IS NOT A CHECK THAT FOUND NOTHING. When the
 *     corpus is dated by ingest, the one-conversation rule is skipped
 *     entirely; rendering "0 set aside" there would claim a check passed that
 *     could not see.
 *  3. EVERY NUMBER CARRIES ITS UNIT. `claims_themed`/`claims_unthemed` are
 *     CLAIM counts summing to `claims`. `themes` is what a reader calls a
 *     theme. `groups` is the BALANCING total and includes one pseudo-group per
 *     ungroupable claim, so it is never rendered as a theme count. The drop
 *     rules count GROUPS except `ungroupable`, which counts CLAIMS. Printing
 *     any two of those as parts of one whole is a quietly wrong number — and it
 *     is the defect this screen exists to avoid, committed twice already.
 *  4. DRIFT MUST BE VISIBLE. If the engine adds a drop rule this file does not
 *     know, the row renders with its raw code rather than vanishing — a funnel
 *     that silently omits a rule stops adding up with nothing going red.
 */
import type { GoalRunProgress } from "../../lib/api"

/** What each rule is, in a reader's words rather than the engine's.
 *
 *  ORDER IS THE ORDER THE RUN APPLIES THEM, so the list reads as a funnel.
 *  `ungroupable` leads because those claims never entered the funnel at all.
 *  Mirrors `NARRATED_DROPS` in `backend/app/crucible/pipeline.py`; a code
 *  missing from here still renders (see rule 4), so drift degrades to ugly
 *  rather than to invisible. */
const DROP_COPY: Record<string, string> = {
  ungroupable:
    "claims never grouped at all — no usable embedding, so whether they corroborate anything is unknown rather than false",
  anecdote: "a single supporting claim each — anecdotes, not findings",
  echo: "all their evidence from one document in one window — one conversation, not a pattern",
  single_account:
    "every supporting claim from one account — that account's situation, not a pattern across the book",
  no_authority: "no source allowed to speak to that kind of claim reported it",
  uncausal:
    "could not be stated without asserting a cause the evidence does not support",
}

const DROP_ORDER = [
  "ungroupable", "anecdote", "echo", "single_account", "no_authority", "uncausal",
]

const n = (v: number) => v.toLocaleString()

export function GoalRunNarration({ progress }: { progress: GoalRunProgress }) {
  const p = progress
  const lines: React.ReactNode[] = []

  // ── what was read ──────────────────────────────────────────────────────
  if (typeof p.claims === "number") {
    // EACH DROP REASON NAMED. `signals_read - claims` is retired PLUS undated,
    // and calling all of it "undated" prints a number the run's own coverage
    // note contradicts.
    const skipped: string[] = []
    if (p.retired) skipped.push(`${n(p.retired)} superseded`)
    if (p.undated) skipped.push(`${n(p.undated)} undated`)
    lines.push(
      <li key="read">
        Read <b>{n(p.claims)}</b> claims
        {typeof p.sources === "number" && p.sources > 0
          ? ` from ${n(p.sources)} source${p.sources === 1 ? "" : "s"}`
          : ""}
        {skipped.length && typeof p.signals_read === "number"
          ? ` (of ${n(p.signals_read)} signals — ${skipped.join(", ")})`
          : ""}
      </li>,
    )
  }

  // ── how they were grouped ──────────────────────────────────────────────
  //
  // THE HEADLINE IS `themes`, NOT `groups`. `groups` is the balancing total and
  // counts one pseudo-group per ungroupable claim, so calling it a theme count
  // would put "Grouped into 2,410 themes" directly above "2,410 claims never
  // grouped at all" on a tenant with no usable embeddings.
  //
  // EACH HALF IS GATED ON ITSELF (rule 1). The claim split and the theme count
  // come from DIFFERENT writes, and `_progress` swallows a failed write by
  // design — so gating the headline on the split means one lost update silently
  // drops the one number this whole screen exists to publish.
  //
  // BOTH HALVES OF THE SPLIT SAY "claims". They sum to the claim count, never
  // to the theme count, so the sentence must not invite that addition.
  const hasSplit =
    typeof p.claims_themed === "number" || typeof p.claims_unthemed === "number"
  if (typeof p.themes === "number" || hasSplit) {
    const themed = p.claims_themed ?? 0
    const unthemed = p.claims_unthemed ?? 0
    lines.push(
      <li key="grouped">
        {typeof p.themes === "number" ? (
          <>
            Grouped into <b>{n(p.themes)}</b> theme{p.themes === 1 ? "" : "s"}
            {hasSplit ? " — from " : ""}
          </>
        ) : (
          <>Grouping </>
        )}
        {hasSplit ? (
          <span className="ga-plan-witness">
            {n(themed)} claim{themed === 1 ? "" : "s"} your knowledge graph had
            already themed
            {unthemed ? `, plus ${n(unthemed)} it had not` : ""}
          </span>
        ) : null}
      </li>,
    )
  }

  // ── what was set aside, per rule ───────────────────────────────────────
  const dropped = p.dropped || {}
  // Known rules in funnel order, then anything the engine sent that this file
  // does not recognise — visible rather than silently discarded (rule 4).
  const codes = [
    ...DROP_ORDER.filter((c) => (dropped[c] || 0) > 0),
    ...Object.keys(dropped).filter(
      (c) => !DROP_ORDER.includes(c) && (dropped[c] || 0) > 0,
    ),
  ]

  if (codes.length || p.echo_check_skipped) {
    lines.push(
      <li key="dropped">
        Set aside
        <ul className="ga-narration-drops" data-testid="goal-narration-drops">
          {codes.map((code) => (
            <li key={code}>
              {/* `ungroupable`'s copy names its own unit (CLAIMS) because
                  every other rule here counts GROUPS. */}
              <b>{n(dropped[code])}</b> {DROP_COPY[code] ?? code}
            </li>
          ))}
        </ul>
        {p.echo_check_skipped ? (
          <p className="ga-doc-note" data-testid="goal-narration-echo-skipped">
            The one-conversation check did not run: your evidence is dated by
            when we read it, not by when it happened, so it cannot tell a
            pattern from an echo.
          </p>
        ) : null}
      </li>,
    )
  }

  // ── what survived ──────────────────────────────────────────────────────
  if (typeof p.findings === "number") {
    lines.push(
      <li key="findings">
        <b>{n(p.findings)}</b> finding{p.findings === 1 ? "" : "s"}
        {typeof p.conflicts === "number" && p.conflicts > 0
          ? ` · ${n(p.conflicts)} where your sources disagree`
          : ""}
        {typeof p.deep === "number" && p.deep > 0
          ? ` · top ${n(p.deep)} written up in full`
          : ""}
      </li>,
    )
  }

  if (!lines.length) return null

  return (
    <ol className="ga-narration" data-testid="goal-narration">
      {lines}
    </ol>
  )
}

export default GoalRunNarration
