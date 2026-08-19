"use client"

/**
 * Goal Analysis — the run panel. (Engine name Crucible; that word never
 * appears on screen.)
 *
 * WHAT THIS SURFACE IS FOR, and why it is shaped this way:
 *
 *  - A run STOPS and asks what the goal means. That is not a loading state and
 *    it must not look like one. It is the product's central claim — that the
 *    analysis is about the thing you actually meant — so the question gets the
 *    panel, prefilled and editable.
 *  - An unsized finding is rendered as "could not size", never as 0. They lead
 *    to opposite decisions, and a dash where a number goes is the only honest
 *    rendering of "we do not know" (I3).
 *  - The CONSIDERED list renders. A ranking whose rejections are invisible is
 *    a ranking you have to take on faith; every drop carries the reason it
 *    died and the claims it died on.
 *  - Coverage notes render beside the findings, not in a footer. A quietly
 *    thinner run is indistinguishable from a complete one, which is worse than
 *    the failure it replaced.
 */
import { useCallback, useEffect, useRef, useState } from "react"
import {
  goalAnalysisApi,
  type GoalFinding,
  type GoalRunDetail,
} from "../../lib/api"

/** How often to poll a live run. A run is minutes long, so a tight poll buys
 *  nothing but load; the row is durable, so a missed tick costs nothing. */
const POLL_MS = 3000

/** Consecutive failed polls before the panel stops trying. Three, because a
 *  deploy restart drops one or two ticks and the run itself survives it. */
const MAX_CONSECUTIVE_FAILURES = 3

/** Statuses that will never change on their own. `awaiting_confirmation` is
 *  here because only a click moves it — but the click has to RE-ARM the poll,
 *  which is what `pollKey` below is for. Getting that wrong meant every user
 *  confirmed and then watched "Reading 0 claims…" forever while the run
 *  finished on the server. */
const TERMINAL = new Set(["ready", "failed", "cancelled", "awaiting_confirmation"])

/** Error codes the backend may return, in the user's language. Anything not
 *  listed falls through to a generic line — the raw `error` is never sent. */
const ERROR_COPY: Record<string, string> = {
  no_evidence:
    "There is nothing connected yet for this to read. Connect a source and try again.",
  goal_unresolved: "We could not work out which metric this goal is about.",
  interrupted: "This run stopped before it finished. Nothing was saved from it.",
  cancelled: "This run was cancelled.",
  llm_error: "A model call failed partway through.",
  internal: "Something went wrong on our side partway through this run.",
}

function money(v: number | null, currency: string | null): string {
  if (v == null) return "—"
  if (currency === "accounts") return `${v} account${v === 1 ? "" : "s"}`
  return String(v)
}

function Finding({ f }: { f: GoalFinding }) {
  const unsized = f.impact_value == null
  return (
    <li className="ga-finding" data-testid="goal-finding">
      <p className="ga-finding-statement">{f.statement}</p>
      <div className="ga-finding-meta">
        <span
          className={`ga-size${unsized ? " ga-size--unknown" : ""}`}
          data-testid={unsized ? "goal-unsized" : "goal-sized"}
        >
          {unsized ? "Could not be sized" : money(f.impact_value, f.currency)}
        </span>
        {f.confidence_band ? (
          <span className="ga-band">{f.confidence_band} confidence</span>
        ) : null}
        {f.adjudication === "conflict" ? (
          <span className="ga-conflict" title="Two sources that may both speak to this disagree">
            sources disagree
          </span>
        ) : null}
      </div>
      {/* The weakest leg is the actionable half of a confidence score: it says
          what to go and find out, which a band on its own never does. */}
      {f.confidence?.weakest_leg_reason ? (
        <p className="ga-weakest">Weakest link: {f.confidence.weakest_leg_reason}</p>
      ) : null}
      {f.confidence?.cap_reason ? (
        <p className="ga-cap">{f.confidence.cap_reason}</p>
      ) : null}
      {/* I8: every assumed parameter is disclosed where the number is read,
          not in a methodology page nobody opens. */}
      {f.assumed_params?.length ? (
        <ul className="ga-assumed">
          {f.assumed_params.map((p) => (
            <li key={p.name}>
              <b>{p.name}</b>: {p.basis}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  )
}

export function GoalAnalysisTab({ runId }: { runId: number }) {
  const [run, setRun] = useState<GoalRunDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [definition, setDefinition] = useState("")
  const [confirming, setConfirming] = useState(false)
  // Bumped by confirm to restart the poll. `load` is keyed on `runId`, which
  // has not changed, so without this the effect never re-runs and the panel
  // stays on the last status it saw.
  const [pollKey, setPollKey] = useState(0)
  // How many consecutive polls failed. One 502 on one tick of a multi-minute
  // run must not brick the panel — a transient error is a retry, not a state.
  const failures = useRef(0)
  // The user's edit must survive a poll landing underneath it. Without this
  // the textarea is reset every three seconds and a long definition is
  // impossible to type.
  const touched = useRef(false)

  const load = useCallback(async () => {
    try {
      const detail = await goalAnalysisApi.get(runId)
      failures.current = 0
      setError(null)              // a recovered poll clears the warning
      setRun(detail)
      if (!touched.current) {
        setDefinition(detail.prioritisation?.proposed_definition ?? "")
      }
      return detail.status
    } catch {
      failures.current += 1
      if (failures.current >= MAX_CONSECUTIVE_FAILURES) {
        setError("Lost contact with this analysis. It may still be running.")
        return "failed"           // stop polling; the run row survives
      }
      // Keep polling. Returning a non-terminal status is the point: a single
      // blip during a run that takes minutes is not a reason to give up on it.
      return "running"
    }
  }, [runId])

  useEffect(() => {
    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      const status = await load()
      if (!live || TERMINAL.has(String(status))) return
      timer = setTimeout(tick, POLL_MS)
    }
    void tick()
    return () => {
      live = false
      if (timer) clearTimeout(timer)
    }
  }, [load, pollKey])

  const confirm = async () => {
    if (!definition.trim() || confirming) return
    setConfirming(true)
    try {
      await goalAnalysisApi.confirm(runId, definition.trim())
      touched.current = false
      failures.current = 0
      // Re-arm. The run has just left `awaiting_confirmation`, and nothing
      // else would ever ask it again.
      setPollKey((k) => k + 1)
    } catch {
      setError("Could not confirm that definition.")
    } finally {
      setConfirming(false)
    }
  }

  if (error && !run) return <p className="ga-error">{error}</p>
  if (!run) return <p className="ga-loading">Loading…</p>

  // ── The I9 gate. Not a loading state — a question. ───────────────────────
  if (run.status === "awaiting_confirmation") {
    const proposed = run.prioritisation?.proposed_definition
    return (
      <div className="ga" data-testid="goal-confirm">
        <p className="ga-goal">{run.goal_text}</p>
        <p className="ga-ask">
          {run.prioritisation?.ask ||
            "Before this runs, confirm what this goal means."}
        </p>
        {proposed ? (
          <p className="ga-provenance">
            Proposed from {run.prioritisation?.proposed_source || "your KPI tree"}.
            Edit it if that is not what you meant.
          </p>
        ) : null}
        <textarea
          className="ga-definition"
          aria-label="What this goal means"
          value={definition}
          rows={4}
          onChange={(e) => {
            touched.current = true
            setDefinition(e.target.value)
          }}
        />
        <button
          type="button"
          className="ga-confirm"
          disabled={!definition.trim() || confirming}
          onClick={confirm}
        >
          {confirming ? "Starting…" : "Confirm and analyse"}
        </button>
      </div>
    )
  }

  if (run.status === "failed") {
    return (
      <div className="ga" data-testid="goal-failed">
        <p className="ga-goal">{run.goal_text}</p>
        <p className="ga-error">
          {ERROR_COPY[run.error_code ?? ""] || "This run did not finish."}
        </p>
      </div>
    )
  }

  if (run.status !== "ready") {
    return (
      <div className="ga" data-testid="goal-running">
        <p className="ga-goal">{run.goal_text}</p>
        <p className="ga-loading">
          Reading {run.claim_count || 0} claims…
        </p>
      </div>
    )
  }

  return (
    <div className="ga" data-testid="goal-ready">
      <p className="ga-goal">{run.goal_text}</p>

      {/* Degradations first. A note explaining that a third of the evidence
          was undated changes how every number below it should be read, so it
          cannot sit under them. */}
      {run.coverage_notes?.length ? (
        <ul className="ga-coverage" data-testid="goal-coverage">
          {run.coverage_notes.map((n, i) => (
            <li key={i}>
              <b>{n.reason}</b> — {n.actual}
            </li>
          ))}
        </ul>
      ) : null}

      {run.findings?.length ? (
        <ul className="ga-findings">
          {run.findings.map((f) => <Finding key={f.id} f={f} />)}
        </ul>
      ) : (
        <p className="ga-empty">
          Nothing survived verification. Everything considered is listed below,
          with why it was dropped.
        </p>
      )}

      {run.considered?.length ? (
        <details className="ga-considered" data-testid="goal-considered">
          <summary>Considered and dropped ({run.considered.length})</summary>
          <ul>
            {run.considered.map((r) => (
              <li key={r.id}>
                <b>{r.label}</b> — {r.reason}
                {r.stopped_at_stage ? (
                  <em> (stopped at {r.stopped_at_stage})</em>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  )
}

export default GoalAnalysisTab
