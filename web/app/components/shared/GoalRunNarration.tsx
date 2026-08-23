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
 * TWO RULES IT MUST NOT BREAK.
 *
 *  1. NEVER RENDER A NUMBER THE RUN HAS NOT MEASURED. The fields arrive in
 *     three writes and a poll can land between any two, so everything is
 *     optional and absent means absent — never zero. A narration that revises
 *     its own numbers teaches a reader to distrust all of them.
 *  2. A CHECK THAT DID NOT RUN IS NOT A CHECK THAT FOUND NOTHING. When the
 *     corpus is dated by ingest, the one-conversation rule is skipped
 *     entirely; rendering "0 set aside" there would claim a check passed that
 *     could not see. `echo_check_skipped` gets a sentence, not a zero.
 */
import type { GoalRunProgress } from "../../lib/api"

/** What each rule is, in a reader's words rather than the engine's.
 *
 *  ORDER IS THE ORDER THE RUN APPLIES THEM, so the list reads as a funnel
 *  rather than a bag of reasons. Mirrors `NARRATED_DROPS` in
 *  `backend/app/crucible/pipeline.py` — one place it can drift, named here. */
const DROP_COPY: { code: string; label: string }[] = [
  { code: "anecdote", label: "a single supporting claim each — anecdotes, not findings" },
  { code: "echo", label: "all their evidence from one document in one window — one conversation, not a pattern" },
  { code: "single_account", label: "every supporting claim from one account — that account's situation, not a pattern across the book" },
  { code: "no_authority", label: "no source allowed to speak to that kind of claim reported it" },
  { code: "uncausal", label: "could not be stated without asserting a cause the evidence does not support" },
]

/** THE UNIT DIFFERS AND THE COPY HAS TO SAY SO. Every rule above sets aside a
 *  GROUP; ungroupable counts individual CLAIMS, because they never reached a
 *  group at all. Listing them in one column under one noun would be a quietly
 *  wrong number, which is the failure this whole screen exists to stop. */
const UNGROUPABLE = "ungroupable"

const n = (v: number) => v.toLocaleString()

export function GoalRunNarration({ progress }: { progress: GoalRunProgress }) {
  const p = progress
  const lines: React.ReactNode[] = []

  // ── what was read ──────────────────────────────────────────────────────
  if (typeof p.claims === "number") {
    lines.push(
      <li key="read">
        Read <b>{n(p.claims)}</b> claims
        {typeof p.sources === "number" && p.sources > 0
          ? ` from ${n(p.sources)} source${p.sources === 1 ? "" : "s"}`
          : ""}
        {typeof p.signals_read === "number" && p.signals_read > p.claims
          ? ` (of ${n(p.signals_read)} signals — the rest carried no usable date)`
          : ""}
      </li>,
    )
  }

  // ── how they were grouped ──────────────────────────────────────────────
  if (typeof p.themed === "number" || typeof p.unthemed === "number") {
    const themed = p.themed ?? 0
    const unthemed = p.unthemed ?? 0
    lines.push(
      <li key="grouped">
        Grouped {typeof p.groups === "number" ? <>into <b>{n(p.groups)}</b> themes </> : null}
        <span className="ga-plan-witness">
          {n(themed)} by your knowledge graph&rsquo;s own themes
          {unthemed ? ` · ${n(unthemed)} by meaning` : ""}
        </span>
      </li>,
    )
  }

  // ── what was set aside, per rule ───────────────────────────────────────
  const dropped = p.dropped || {}
  const shown = DROP_COPY.filter((d) => (dropped[d.code] || 0) > 0)
  const ungroupable = dropped[UNGROUPABLE] || 0

  if (shown.length || ungroupable || p.echo_check_skipped) {
    lines.push(
      <li key="dropped">
        Set aside
        <ul className="ga-narration-drops" data-testid="goal-narration-drops">
          {shown.map((d) => (
            <li key={d.code}>
              <b>{n(dropped[d.code])}</b> {d.label}
            </li>
          ))}
          {ungroupable ? (
            <li key={UNGROUPABLE}>
              <b>{n(ungroupable)}</b> claims never grouped at all — no usable
              embedding, so whether they corroborate anything is unknown rather
              than false
            </li>
          ) : null}
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
