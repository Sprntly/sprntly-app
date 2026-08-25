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

import type { GoalRunPlan } from "../../lib/api"
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
export type SettledPlan = {
  sources?: { source_type: string; label?: string; signal_count: number }[]
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
      <GoalAnalysisPlan
        plan={gate.plan}
        approving={!!busy}
        onApprove={(decision) => onApprovePlan?.(decision)}
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
  const dropped = new Set(excludedSources)
  const kept = (plan?.sources ?? []).filter((x) => !dropped.has(x.source_type))
  const keptSignals = kept.reduce((n, x) => n + (x.signal_count || 0), 0)
  return (
    <div className="ggc ggc-settled" data-testid="goal-gate-plan-done">
      <p className="ggc-settled-label">Plan approved</p>
      {/* An excluded source is STATED, never merely omitted — a quietly
          narrower run is what the coverage notes exist to prevent. */}
      {excludedSources.length ? (
        <p className="ggc-settled-body">
          Not reading: {excludedSources.join(", ")}
        </p>
      ) : (
        <p className="ggc-settled-body">Reading every connected source.</p>
      )}
      {/* WHAT WAS ACTUALLY APPROVED, kept on screen. Read-only — the decision
          is made — but present, so the reader can check the run against it
          later without reopening anything. */}
      {plan?.sources?.length ? (
        <ul className="ggc-settled-sources" data-testid="goal-gate-plan-done-sources">
          {plan.sources.map((src) => {
            const out = dropped.has(src.source_type)
            return (
              <li key={src.source_type} className={out ? "ggc-src-out" : undefined}>
                <strong>{src.signal_count}</strong>{" "}
                {src.label || src.source_type}
                {out ? " — dropped by you" : ""}
              </li>
            )
          })}
        </ul>
      ) : null}
      {plan?.sources?.length ? (
        <p className="ggc-settled-body">
          {keptSignals} signals across {kept.length} source
          {kept.length === 1 ? "" : "s"} were read against this goal.
        </p>
      ) : null}
      {hypotheses.length ? (
        <p className="ggc-settled-body">
          {hypotheses.length === 1 ? "Your expectation" : "Your expectations"}:{" "}
          {hypotheses.join(" · ")}
        </p>
      ) : null}
      {note}
    </div>
  )
}
