"use client"

// The chat wait surface — everything a person sees between hitting send and
// reading an answer.
//
// It replaces a 10-message pool that was picked at `Math.floor(Math.random() *
// 10)` and rotated every 15s, so one user's 3-second answer opened on "Almost
// there — shaping the answer…" while another's opened on "Double-checking the
// facts…" when nothing had been checked. That violated the rule stated at the
// top of `generationPhases.ts`: phase copy must describe work the job REALLY
// does.
//
// So every line below is defensible against an event we can observe:
//
//   rung 0  0–400ms   nothing at all — under Cloudscape's 1s floor a spinner
//                     only flickers, so the user bubble + agent head stand alone
//   rung 1  0.4–10s   "Working on your question" — the job is `generating`,
//                     which is always true while this component is mounted
//   rung 2  10–30s    + elapsed + note + inline Stop. NN/g's 10-second limit is
//                     where attention leaves the task; that is where a clock
//                     earns its place, not before
//   rung 3  30s+      + "you can leave" — the job genuinely survives navigation
//                     and reload (jobResume persists ask_id per tab)
//   rung 4  streaming "Writing the answer" — a `delta` frame provably arrived,
//                     so the model is emitting answer text right now
//   rung 5  dropped   "Finishing the answer" — the SSE preview errored while the
//                     poll still reports `generating`
//   rung 6  resumed   "Picking up where this left off" — resumeAskGeneration
//                     re-attached to a persisted ask_id instead of POSTing
//
// Where a line would need a signal we do not have — which leg of the agent is
// running, or which skill the ROUTER picked — it degrades to the weaker line
// that is still true rather than inventing one. Naming the router's skill and
// naming the live leg both need backend work (a `routed_skill` column and SSE
// phase frames); until that lands, the skill chip appears only for a
// slash-invoked ask, where the trigger deterministically selects the skill.

import { useEffect, useMemo, useState } from "react"

// ── Copy ────────────────────────────────────────────────────────────────────
export const WAIT_PHASE_WORKING = "Working on your question"
export const WAIT_PHASE_WRITING = "Writing the answer"
export const WAIT_PHASE_FINISHING = "Finishing the answer"
export const WAIT_PHASE_RESUMED = "Picking up where this left off"

export const WAIT_NOTE_GENERIC = "This one usually takes under a minute."
export const WAIT_NOTE_LONG_SKILL =
  "A competitive report reads sources across the web — this one usually takes a few minutes."
export const WAIT_NOTE_RUNNING_LONG =
  "This one is running long. You can switch tabs or close Sprntly — the answer keeps generating and will be here when you come back."
export const WAIT_NOTE_STREAM_DROPPED =
  "The live preview dropped out. The answer is still generating and will appear complete in a moment."
export const WAIT_NOTE_RESUMED = "This answer was already running before you reloaded."

export const WAIT_STOPPED = "You stopped this response."
export const WAIT_TIMED_OUT =
  "This is taking longer than expected. It's still running on our side — reload and it will pick up where it left off."
export const WAIT_FAILED_TITLE = "That answer didn't come through."
export const WAIT_FAILED_BODY =
  "Nothing was saved. Send the question again, or try it with fewer files attached."

// ── Rung thresholds ─────────────────────────────────────────────────────────
/** Rung 0 → 1. Below this an indicator only flickers on a fast answer. */
export const WAIT_RUNG1_MS = 400
/** Rung 1 → 2. NN/g's 10-second attention limit. */
export const WAIT_RUNG2_MS = 10_000
/** Rung 2 → 3. */
export const WAIT_RUNG3_MS = 30_000
const TICK_MS = 1000

/** Skills whose slash-pinned ask legitimately runs for minutes, so the 10s note
 *  must not promise "under a minute". The competitive-intelligence report kicks
 *  off a staged web-research sweep (see `app/skill_router.py`, which narrows its
 *  own routing for exactly this reason). */
const LONG_RUNNING_SKILL_IDS: ReadonlySet<string> = new Set([
  "competitive-intelligence-review",
])

/** True when a slash-pinned skill is one of the multi-minute research skills. */
export function isLongRunningSkill(skillId: string | null | undefined): boolean {
  return !!skillId && LONG_RUNNING_SKILL_IDS.has(skillId)
}

/** `m:ss`, tabular-nums in CSS so the row never jitters as digits change. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const mins = Math.floor(total / 60)
  const secs = total % 60
  return `${mins}:${String(secs).padStart(2, "0")}`
}

type Props = {
  /** Tighter layout for the side AI bar rail. */
  compact?: boolean
  /** Wall-clock start of THIS ask. Passing it keeps the ladder continuous across
   *  the pending-send → real-turn handoff (two mounts, one wait). */
  startedAt?: number
  /** Rung 0 gate. Surfaces that already showed an indicator pass 0. */
  gateMs?: number
  /** Overrides the phase line for surfaces whose own copy is already specific
   *  and honest ("Summarizing what got built…", "loading conversation…"). */
  phase?: string
  /** Rung 4 — a `delta` frame arrived on the SSE channel. */
  streaming?: boolean
  /** Rung 5 — the SSE preview dropped while the poll still says `generating`. */
  streamDropped?: boolean
  /** Rung 6 — this ask was re-attached rather than POSTed. */
  resumed?: boolean
  /** Deterministic slash-pinned skill label, e.g. "Competitive intelligence
   *  report". Never a guess: only set when the draft began with a known trigger. */
  skillLabel?: string | null
  /** The pinned skill is one of the multi-minute research skills. */
  longSkill?: boolean
  /** Inline Stop. Omit to render no Stop button (surfaces with nothing to stop). */
  onStop?: () => void
  /** Rung 4/5 — the live answer body, rendered in place of the skeleton. */
  children?: React.ReactNode
}

export function AssistantWaitState({
  compact,
  startedAt,
  gateMs = WAIT_RUNG1_MS,
  phase,
  streaming,
  streamDropped,
  resumed,
  skillLabel,
  longSkill,
  onStop,
  children,
}: Props) {
  // Own clock only when the caller has no start to hand us (the AI bar rail,
  // the artifact-summary indicator — surfaces with a single mount).
  const [mountedAt] = useState(() => Date.now())
  const start = startedAt ?? mountedAt

  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(id)
  }, [])

  // A separate short timer for the rung-0 gate, so the first indicator lands at
  // 400ms rather than waiting for the 1s tick.
  const [gatePassed, setGatePassed] = useState(() => Date.now() - start >= gateMs)
  useEffect(() => {
    if (gatePassed) return
    const wait = Math.max(0, gateMs - (Date.now() - start))
    const id = setTimeout(() => setGatePassed(true), wait)
    return () => clearTimeout(id)
  }, [gatePassed, gateMs, start])

  const elapsed = Math.max(0, now - start)
  const phaseLine = useMemo(() => {
    if (phase) return phase
    if (streamDropped) return WAIT_PHASE_FINISHING
    if (streaming) return WAIT_PHASE_WRITING
    if (resumed) return WAIT_PHASE_RESUMED
    return WAIT_PHASE_WORKING
  }, [phase, resumed, streamDropped, streaming])

  // Rung 0 — nothing at all. The turn still carries aria-busy, so a screen
  // reader knows the surface is working even with no visible indicator.
  if (!gatePassed && !streaming && !children) return null

  // The clock and the inline Stop both start at rung 2. Once shown they STAY —
  // including through the first streamed token, which is where the old surface
  // yanked the whole status row away the instant `partial` arrived.
  const showElapsed = elapsed >= WAIT_RUNG2_MS
  const showStop = showElapsed && !!onStop
  const runningLong = elapsed >= WAIT_RUNG3_MS

  return (
    <div className={`cw${compact ? " cw--compact" : ""}`}>
      <div className="cw-status">
        <span className="cw-orb" aria-hidden />
        {/* The single announcement point. Its text changes ONLY when the phase
            changes, so a polite live region here announces per phase — not per
            token and not per elapsed tick (which is why the clock beside it is
            aria-hidden and why role="timer" is deliberately not used). */}
        <span className="cw-phase" role="status" aria-live="polite" aria-atomic="true">
          {phaseLine}
        </span>
        {skillLabel ? <span className="cw-skill">{skillLabel}</span> : null}
        {showElapsed ? (
          <span className="cw-elapsed" aria-hidden>
            {formatElapsed(elapsed)}
          </span>
        ) : null}
      </div>

      {children ? (
        <div className={streamDropped ? "cw-partial" : undefined}>{children}</div>
      ) : (
        <>
          {/* Indeterminate bar. It carries no role and no accessible name: the
              status line above owns the announcement, and an unnamed
              role="progressbar" (what shipped here before) is worse than none. */}
          <div className="cw-bar" aria-hidden>
            <div className="cw-bar-pill" />
          </div>
          <div className="cw-skel" aria-hidden>
            <span className="assistant-skel-line" />
            <span className="assistant-skel-line" />
            {showElapsed && !compact ? <span className="assistant-skel-line" /> : null}
          </div>
        </>
      )}

      {/* A dropped live preview is NOT a failure — the poll is still authoritative
          and will deliver the finished answer — so it degrades to a note, never
          an error. It takes precedence over the generic pacing note. */}
      {streamDropped ? (
        <div className="cw-long">
          <span className="cw-long-mark" aria-hidden>◐</span>
          <div>{WAIT_NOTE_STREAM_DROPPED}</div>
        </div>
      ) : runningLong ? (
        <div className="cw-long">
          <span className="cw-long-mark" aria-hidden>↻</span>
          <div>{WAIT_NOTE_RUNNING_LONG}</div>
        </div>
      ) : showElapsed ? (
        <div className="cw-note">{longSkill ? WAIT_NOTE_LONG_SKILL : WAIT_NOTE_GENERIC}</div>
      ) : null}

      {resumed && !streamDropped ? <div className="cw-note">{WAIT_NOTE_RESUMED}</div> : null}

      {showStop ? (
        <div className="cw-actions">
          <button type="button" className="cw-btn" aria-label="Stop generating" onClick={onStop}>
            Stop
          </button>
        </div>
      ) : null}
    </div>
  )
}

/** The user stopped this ask. Muted, never red — and no longer a dead end. */
export function WaitStoppedState({ onAskAgain }: { onAskAgain?: () => void }) {
  return (
    <div className="cw">
      <div className="cw-stopped">{WAIT_STOPPED}</div>
      {onAskAgain ? (
        <div className="cw-actions">
          <button type="button" className="cw-btn" onClick={onAskAgain}>Ask again</button>
        </div>
      ) : null}
    </div>
  )
}

/** The 12-minute client budget ran out. The server job may still finish and the
 *  pending ask_id is deliberately LEFT in place, so this promises a reload will
 *  pick it up — and it must not be dressed as a failure. */
export function WaitTimedOutState({
  onReload,
  onAskAgain,
}: {
  onReload?: () => void
  onAskAgain?: () => void
}) {
  return (
    <div className="cw">
      <div className="cw-long cw-long--warn">
        <span className="cw-long-mark" aria-hidden>⏱</span>
        <div>{WAIT_TIMED_OUT}</div>
      </div>
      <div className="cw-actions">
        {onReload ? (
          <button type="button" className="cw-btn cw-btn--primary" onClick={onReload}>Reload</button>
        ) : null}
        {onAskAgain ? (
          <button type="button" className="cw-btn" onClick={onAskAgain}>Ask again</button>
        ) : null}
      </div>
    </div>
  )
}

/** The ask failed.
 *
 *  role="alert" because the chat surface had NO alert, status or live region at
 *  all — a screen-reader user got total silence on failure.
 *
 *  The copy is FIXED. The raw error is deliberately not rendered: a backend
 *  detail string is meaningless to the person reading it, and the 404 the tenant
 *  gate raises must read as "didn't come through" with no hint that the row
 *  exists somewhere else. */
export function WaitFailedState({ onAskAgain }: { onAskAgain?: () => void }) {
  return (
    <div className="cw">
      <div className="cw-err bc-error" role="alert">
        <b>{WAIT_FAILED_TITLE}</b>
        {WAIT_FAILED_BODY}
      </div>
      {onAskAgain ? (
        <div className="cw-actions">
          <button type="button" className="cw-btn cw-btn--primary" onClick={onAskAgain}>Ask again</button>
        </div>
      ) : null}
    </div>
  )
}
