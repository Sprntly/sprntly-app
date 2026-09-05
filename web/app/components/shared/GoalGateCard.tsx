"use client"

/**
 * The two Goal Analysis gates, rendered IN THE CHAT THREAD.
 *
 * WHY THEY MOVED OUT OF THE PANEL. A PM's job is to defend the decision, and
 * both gates are the conversation that makes that possible: what does this goal
 * MEAN, and here is what I will read before I read it. Answering them in a side
 * panel made them look like a form the chat had handed off to — the thread went
 * quiet, and the record of what was asked and what was agreed lived somewhere
 * other than the conversation it belonged to.
 *
 * In the thread they are what they actually are: a question and an answer,
 * scrolled back to later like any other turn. The panel keeps the one thing it
 * is genuinely better at — the finished report, which is a document.
 *
 * SAME CONTRACT AS `ClarifyQuestionsCard`, deliberately. That card already
 * establishes the shape for "an answerable card riding a turn": it renders while
 * the gate is open, flips to a settled summary once `resolved` arrives, and
 * disables itself while busy. A second shape for the same idea would be a second
 * thing to keep right.
 *
 * NOTHING HERE DECIDES ANYTHING. Every button hands the user's own words back to
 * the run — the definition they confirmed (possibly edited), the sources they
 * kept, what they said they already believe. This component computes no score,
 * picks no default the user did not see, and never advances a gate on its own.
 */
import * as React from "react"
import { useEffect, useRef, useState } from "react"

import { goalAnalysisApi, type GoalRunPlan } from "../../lib/api"
import { GoalAnalysisPlan, type PlanDecision } from "./GoalAnalysisPlan"

/** What the thread needs in order to render a gate. Carried on the turn, so a
 *  re-render (or a restore) rebuilds the card from data rather than from a
 *  component that happened to still be mounted. */
export type GoalGate =
  /** Started, not yet asking. A run is born `resolving_goal` and reaches its
   *  first question a moment later; without a gate on the turn for that window
   *  the thread ran the ordinary no-reply ladder and printed "No response was
   *  generated for this message." over a run that was working perfectly. */
  | { kind: "pending"; goalText: string }
  | {
      kind: "definition"
      runId: number
      goalText: string
      /** The engine's question, verbatim. Never paraphrased here: §5 requires
       *  the ask to show the search before the gap, and a tidier version of it
       *  would drop exactly that. */
      ask: string
      proposedDefinition?: string
      proposedSource?: string | null
      /** §6: the calculation being assumed, one sentence, editable. */
      methodNote?: string
    }
  | { kind: "plan"; runId: number; plan: GoalRunPlan }

/** The settled state. Shown in place of the controls once the user has answered,
 *  so the thread keeps a record of WHAT WAS AGREED rather than collapsing to a
 *  bare "done" — the whole point of moving these into the conversation. */
export type GoalGateResolved =
  | { kind: "definition"; definition: string }
  | {
      kind: "plan"
      excludedSources: string[]
      hypotheses: string[]
      /** The plan as approved. CARRIED, not dropped: the settled card used to
       *  collapse to "Reading every connected source", so scrolling back showed
       *  that a plan was approved and not WHAT was approved — which is the
       *  whole reason the gate is in the thread. A PM defending the decision
       *  needs the sources and counts they agreed to, not a receipt. */
      plan?: SettledPlan
    }
  /** The gate could not be answered — the server refused, or the run died.
   *  Carries the REASON: the generic "there was an interruption" the thread
   *  shows for an ordinary failed turn throws that away, and "why can I not
   *  confirm this?" is exactly what the reader needs. */
  | { kind: "failed"; reason: string }

/** What the SETTLED card needs from the plan — the sources and their counts.
 *  Deliberately narrower than `GoalRunPlan`: a record does not need the gaps,
 *  the currency or the will-produce list, and naming only what it reads keeps
 *  a record renderable from a thread persisted by an older build. */
/** THE WHOLE PLAN, not a summary of it. This was narrowed to `sources` when the
 *  settled card was a four-line receipt; it is now re-rendered through
 *  `GoalAnalysisPlan` itself, so the record cannot drift from what was agreed
 *  to. Optional throughout, because a turn persisted by an older build has
 *  whatever it had. */
export type SettledPlan = Partial<GoalRunPlan> & {
  sources?: GoalRunPlan["sources"]
}

/** How often the card asks whether the method has been composed yet, and how
 *  long it keeps asking.
 *
 *  MEASURED, NOT GUESSED: a real composition took 53.8 seconds. The ceiling is
 *  generous against that and exists so a run that never completes cannot leave
 *  a card polling forever — not as a timeout anybody should hit. */
const COMPLETION_POLL_MS = 3_000
const COMPLETION_CEILING_MS = 5 * 60 * 1000

/** The plan to render — the one handed in, or the completed one if this plan
 *  was still being written when it was captured.
 *
 *  WHY THE CARD RE-READS RATHER THAN THE THREAD. The gate lands in two writes:
 *  the deterministic method immediately, then the composed wording ~54s later.
 *  Two things went wrong with that, and they have one cause — whoever held the
 *  plan never looked at it again.
 *
 *    · The poll that attaches the gate returns the moment the run reaches
 *      `awaiting_approval`, which is now phase 1. Nothing was listening when
 *      phase 2 landed.
 *    · The plan is captured into the conversation turn, so the placeholder was
 *      frozen into the transcript — a reload still showed "the wording will
 *      fill in", permanently.
 *
 *  Re-reading HERE fixes both, because both end at this component, and it is
 *  the only place that knows a plan is pending without anything else having to
 *  be told. A completed plan polls not at all; a pending one stops the moment
 *  it completes, or the moment the run leaves the gate.
 *
 *  IT NEVER RE-DRAWS. This reads the stored plan; the composition happens once,
 *  server-side, and `load_steps` is what guarantees that. This is a reader
 *  catching up with a write that already happened.
 */
function usePlanWhenComposed(runId: number, plan: GoalRunPlan): GoalRunPlan {
  const [completed, setCompleted] = useState<GoalRunPlan | null>(null)

  useEffect(() => {
    setCompleted(null)
    if (!plan?.steps_pending) return
    let alive = true
    const deadline = Date.now() + COMPLETION_CEILING_MS
    const tick = async () => {
      // ASKS ONCE IMMEDIATELY, THEN WAITS. The restore case is the common one
      // and the composition finished long ago there — sleeping first would
      // show "the wording will fill in" for three seconds on a plan that has
      // been complete for a week.
      let first = true
      while (alive && Date.now() < deadline) {
        if (!first) {
          await new Promise((r) => setTimeout(r, COMPLETION_POLL_MS))
          if (!alive) return
        }
        first = false
        try {
          const detail = await goalAnalysisApi.get(runId)
          if (!alive) return
          const next = detail.prioritisation?.plan
          if (next && !next.steps_pending) {
            setCompleted(next)
            return
          }
          // THE RUN LEFT THE GATE WITHOUT THE COMPOSITION LANDING. The
          // completing write declines once a run is no longer awaiting
          // approval, so that reader keeps the deterministic method — which is
          // a real method, and final. Nothing more will arrive; stop asking.
          if (detail.status !== "awaiting_approval") return
        } catch {
          // A run we cannot read is not one to keep asking about.
          return
        }
      }
    }
    void tick()
    return () => { alive = false }
  }, [runId, plan?.steps_pending])

  return completed ?? plan
}


/** The plan gate, in its own component so the completion re-read can be a hook.
 *
 *  `GoalGateCard` returns early for every other gate kind, so a hook called
 *  there would run conditionally. This is that rule, not a layer for its own
 *  sake. */
function GoalPlanGate({
  gate, busy, onApprovePlan,
}: {
  gate: { kind: "plan"; runId: number; plan: GoalRunPlan }
  busy?: boolean
  onApprovePlan?: (decision: PlanDecision) => void
}) {
  const plan = usePlanWhenComposed(gate.runId, gate.plan)
  return (
    <GoalAnalysisPlan
      plan={plan}
      approving={!!busy}
      onApprove={(decision) => onApprovePlan?.(decision)}
    />
  )
}


export function GoalGateCard({
  gate,
  resolved,
  busy,
  error,
  onConfirmDefinition,
  onApprovePlan,
}: {
  gate?: GoalGate
  resolved?: GoalGateResolved
  busy?: boolean
  /** A refusal the user can act on. Rendered BESIDE the still-live controls,
   *  never instead of them: the run is usually still sitting at its gate
   *  server-side, so destroying the card turns a retryable error into a dead
   *  end. */
  error?: string
  onConfirmDefinition?: (definition: string) => void
  onApprovePlan?: (decision: PlanDecision) => void
}) {
  if (resolved) return <GoalGateSettled resolved={resolved} error={error} />
  if (!gate) return null
  if (gate.kind === "pending") {
    return (
      <div className="ggc" data-testid="goal-gate-pending">
        <p className="ggc-note">Working out what this goal means…</p>
        {error ? <p className="ggc-error" role="status">{error}</p> : null}
      </div>
    )
  }
  if (gate.kind === "definition") {
    return (
      <GoalDefinitionGate
        gate={gate}
        busy={busy}
        error={error}
        onConfirm={onConfirmDefinition}
      />
    )
  }
  return (
    <div className="ggc" data-testid="goal-gate-plan">
      <GoalPlanGate
        gate={gate}
        busy={busy}
        onApprovePlan={onApprovePlan}
      />
      {error ? <p className="ggc-error" role="status">{error}</p> : null}
    </div>
  )
}

function GoalDefinitionGate({
  gate,
  busy,
  error,
  onConfirm,
}: {
  gate: Extract<GoalGate, { kind: "definition" }>
  busy?: boolean
  error?: string
  onConfirm?: (definition: string) => void
}) {
  const [definition, setDefinition] = useState(gate.proposedDefinition ?? "")
  // Once the user has typed, a later prop change must not overwrite them. The
  // panel version carries the same guard for the same reason: the poll can
  // deliver a fresh row mid-edit.
  const touched = useRef(false)
  useEffect(() => {
    if (!touched.current) setDefinition(gate.proposedDefinition ?? "")
  }, [gate.proposedDefinition])

  return (
    <div className="ggc" data-testid="goal-gate-definition">
      <p className="ggc-ask">{gate.ask}</p>
      {gate.proposedDefinition ? (
        <p className="ggc-provenance">
          Proposed from {gate.proposedSource || "your KPI tree"}. Edit it if that
          is not what you meant.
        </p>
      ) : null}
      {gate.methodNote ? (
        <p className="ggc-note" data-testid="goal-gate-method-note">
          {gate.methodNote}
        </p>
      ) : null}
      <textarea
        className="ggc-definition"
        aria-label="What this goal means"
        value={definition}
        rows={4}
        disabled={busy}
        onChange={(e) => {
          touched.current = true
          setDefinition(e.target.value)
        }}
      />
      <button
        type="button"
        className="ggc-confirm"
        disabled={!definition.trim() || !!busy}
        onClick={() => onConfirm?.(definition.trim())}
      >
        {busy ? "Starting…" : "Confirm and plan"}
      </button>
      {/* BESIDE the button, not instead of it. A refused confirm usually leaves
          the run exactly where it was, so the reader has to be able to try
          again — replacing the card with the error made that impossible. */}
      {error ? <p className="ggc-error" role="status">{error}</p> : null}
    </div>
  )
}

function GoalGateSettled({
  resolved, error,
}: { resolved: GoalGateResolved; error?: string }) {
  // The settled card is the ONLY thing that renders once a gate is answered, so
  // a failure arriving afterwards — the run dying between gates — had nowhere
  // to appear at all. `endGoalTurn` writes exactly that pair, and it was the
  // only case it was ever written for.
  const note = error
    ? <p className="ggc-error" role="status">{error}</p>
    : null

  // WHAT HAPPENS NEXT, SAID IN THE THREAD. Approving used to do two things at
  // once — settle this card and swing the panel open — and say neither of
  // them. The reader's own words for it: "After I approved the plan, there was
  // no comms to me. Just the artifact section opening." The thread is where
  // they are looking, so the thread is where the handoff belongs.
  //
  // Tense is load-bearing. This line is part of a transcript that is re-read
  // long after the run finishes, so anything in the progressive ("we're
  // analysing this now…") is a lie the moment the report lands. Simple present
  // — it *appears* there — reads correctly while the run is going AND a week
  // later. `settledNext` is suppressed on `error` for the same reason: the run
  // that failed between gates has no destination to send anyone to.
  const settledNext = error ? null : (
    <p className="ggc-settled-next" data-testid="goal-gate-plan-next">
      Started. Your Goal Analysis is built from the sources above and appears
      in the <strong>Goal Analysis</strong> panel on the right.
    </p>
  )
  if (resolved.kind === "failed") {
    return (
      <div className="ggc ggc-settled" data-testid="goal-gate-failed">
        <p className="ggc-settled-label">Analysis stopped</p>
        <p className="ggc-settled-body">{resolved.reason}</p>
        {note}
      </div>
    )
  }
  if (resolved.kind === "definition") {
    return (
      <div className="ggc ggc-settled" data-testid="goal-gate-definition-done">
        <p className="ggc-settled-label">Analysing against</p>
        <p className="ggc-settled-body">{resolved.definition}</p>
        {note}
      </div>
    )
  }
  const { excludedSources, hypotheses, plan } = resolved
  // THE PLAN STAYS ON SCREEN. Collapsing it to a receipt threw away which
  // sources were in scope, what each could witness, and what the run said it
  // would NOT answer — the exact things a PM has to point at afterwards. Only
  // a record that actually carries the plan can do that; the terse fallback
  // below is for turns persisted before the plan was carried.
  const fullPlan = plan && plan.sources?.length && plan.goal_text
    ? (plan as GoalRunPlan)
    : null
  if (fullPlan) {
    return (
      <div className="ggc ggc-settled" data-testid="goal-gate-plan-done">
        <GoalAnalysisPlan
          plan={fullPlan}
          approving={false}
          onApprove={() => {}}
          settled={{ excludedSources, hypotheses }}
        />
        {settledNext}
        {note}
      </div>
    )
  }
  const dropped = new Set(excludedSources)
  const kept = (plan?.sources ?? []).filter((x) => !dropped.has(x.source_type))
  const keptSignals = kept.reduce((n, x) => n + (x.signal_count || 0), 0)
  // AN EXCLUSION THE LIST CANNOT SHOW. The list below can only strike through
  // a source it actually renders, so an excluded slug that is not in
  // `plan.sources` would be stated nowhere — and "stated nowhere" is the one
  // outcome this card exists to prevent. Normally the two agree (the
  // checkboxes are rendered FROM those sources), so this is empty; it is not
  // empty for a record persisted before the plan was carried, which is exactly
  // when the fallback line below is the only thing left.
  const listed = new Set((plan?.sources ?? []).map((x) => x.source_type))
  const unlisted = excludedSources.filter((x) => !listed.has(x))
  return (
    <div className="ggc ggc-settled" data-testid="goal-gate-plan-done">
      <p className="ggc-settled-label">Plan approved</p>
      {/* An excluded source is STATED, never merely omitted — a quietly
          narrower run is what the coverage notes exist to prevent. */}
      {/* The list below names the dropped sources with their human labels and
          counts, so repeating them here in raw `source_type` slugs said the
          same fact twice — "Not reading: project_mgmt" directly above "the
          tracker — dropped by you".
          BUT ONLY WHEN THERE IS A LIST. A record persisted before the plan was
          carried has no sources to render, and dropping this line outright
          then left an exclusion stated NOWHERE — which is the one thing this
          card exists to prevent. Slugs are worse than labels; they are much
          better than silence. */}
      {!excludedSources.length ? (
        <p className="ggc-settled-body">Reading every connected source.</p>
      ) : unlisted.length ? (
        <p className="ggc-settled-body">
          Not reading: {unlisted.join(", ")}
        </p>
      ) : null}
      {/* WHAT WAS ACTUALLY APPROVED, kept on screen. Read-only — the decision
          is made — but present, so the reader can check the run against it
          later without reopening anything. */}
      {plan?.sources?.length ? (
        <ul className="ggc-settled-sources" data-testid="goal-gate-plan-done-sources">
          {plan.sources.map((src) => {
            const out = dropped.has(src.source_type)
            return (
              <li key={src.source_type} className={out ? "ggc-src-out" : undefined}>
                <span className={out ? "ggc-src-struck" : undefined}>
                  <strong>{src.signal_count}</strong>{" "}
                  {src.label || src.source_type}
                </span>
                {out ? (
                  <span className="ggc-src-note"> — dropped by you</span>
                ) : null}
              </li>
            )
          })}
        </ul>
      ) : null}
      {/* WHAT THIS NUMBER IS. `signal_count` is an INVENTORY taken by the plan
          step, which reads no content — it is why that step returns in about a
          second rather than minutes. "were read against this goal" was two
          claims the run had not made: that the reading had happened (this card
          renders the moment the plan is approved, before any of it has) and
          that it was done against the goal (claim selection never sees the
          definition — the report's closing section says so in as many words).
          Stated as scope, which is what was actually agreed here. */}
      {plan?.sources?.length ? (
        <p className="ggc-settled-body">
          In scope: {keptSignals} signals across {kept.length} source
          {kept.length === 1 ? "" : "s"} — counted when the plan was made, not a
          record of what has been read.
        </p>
      ) : null}
      {hypotheses.length ? (
        <p className="ggc-settled-body">
          {hypotheses.length === 1 ? "Your expectation" : "Your expectations"}:{" "}
          {hypotheses.join(" · ")}
        </p>
      ) : null}
      {settledNext}
      {note}
    </div>
  )
}
