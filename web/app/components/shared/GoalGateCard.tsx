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
  | { kind: "plan"; excludedSources: string[]; hypotheses: string[] }

export function GoalGateCard({
  gate,
  resolved,
  busy,
  onConfirmDefinition,
  onApprovePlan,
}: {
  gate: GoalGate
  resolved?: GoalGateResolved
  busy?: boolean
  onConfirmDefinition?: (definition: string) => void
  onApprovePlan?: (decision: PlanDecision) => void
}) {
  if (resolved) return <GoalGateSettled resolved={resolved} />
  if (gate.kind === "definition") {
    return (
      <GoalDefinitionGate
        gate={gate}
        busy={busy}
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
    </div>
  )
}

function GoalDefinitionGate({
  gate,
  busy,
  onConfirm,
}: {
  gate: Extract<GoalGate, { kind: "definition" }>
  busy?: boolean
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
    </div>
  )
}

function GoalGateSettled({ resolved }: { resolved: GoalGateResolved }) {
  if (resolved.kind === "definition") {
    return (
      <div className="ggc ggc-settled" data-testid="goal-gate-definition-done">
        <p className="ggc-settled-label">Analysing against</p>
        <p className="ggc-settled-body">{resolved.definition}</p>
      </div>
    )
  }
  const { excludedSources, hypotheses } = resolved
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
      {hypotheses.length ? (
        <p className="ggc-settled-body">
          {hypotheses.length === 1 ? "Your expectation" : "Your expectations"}:{" "}
          {hypotheses.join(" · ")}
        </p>
      ) : null}
    </div>
  )
}
