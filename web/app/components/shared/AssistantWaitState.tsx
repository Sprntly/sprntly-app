"use client"

// The chat wait surface — everything a person sees between hitting send and
// reading an answer.
//
// SHAPE (owner redesign, 2026-08-11): one small line — orb, a phase that TYPES
// itself out, holds with a soft shimmer, erases, and types the next — plus the
// pacing note and Stop from 10s. The indeterminate bar and the skeleton lines
// that used to sit under the status are gone: they made the wait read as a
// heavy block when the whole point of the status line is that ONE line carries
// the state. (The bar's CSS went with it; `.assistant-skel-line` survives — the
// onboarding ReviewStep shares it.)
//
// The typed cycle is visual pacing, not new claims. The rotating lines are all
// generically true of a job that is `generating` — which is the only state this
// component mounts in — and the moment a REAL signal exists it takes over as a
// fixed line and the cycle stops:
//
//   rung 0  0–400ms   nothing at all — under Cloudscape's 1s floor a spinner
//                     only flickers, so the user bubble + agent head stand alone
//   rung 1  0.4–10s   the typed cycle, opening on "Working on your question"
//   rung 2  10–30s    + pacing note + inline Stop. NN/g's 10-second limit is
//                     where attention leaves the task; that is where an escape
//                     hatch earns its place, not before
//   fixed   streaming "Writing the answer" — a `delta` frame provably arrived
//   fixed   dropped   "Finishing the answer" — SSE died, poll still `generating`
//   fixed   resumed   "Picking up where this left off" — re-attached, not POSTed
//   fixed   phase     the caller's own copy ("Summarizing what got built…")
//
// Accessibility: the typed characters are aria-hidden garnish. A visually
// hidden `.cw-phase-sr` span is the ONE live region, and it announces the
// stable STATE line (the fixed phrase, or "Working on your question" while the
// cycle plays) — per state change, never per keystroke and never per cycle
// word. Tests read the same span, so they assert state, not animation frames.
// Reduced motion: no typing, no shimmer — the current line renders whole.

import { useEffect, useRef, useState } from "react"
import { SprntlyThinkingMark } from "./SprntlyMark"
import { GROUNDED_PROGRESS_ENABLED } from "../../lib/friendlyPhase"

// ── Copy ────────────────────────────────────────────────────────────────────
export const WAIT_PHASE_WORKING = "Working on your question"
export const WAIT_PHASE_WRITING = "Writing the answer"
export const WAIT_PHASE_FINISHING = "Finishing the answer"
export const WAIT_PHASE_RESUMED = "Picking up where this left off"

/** The typed rotation while the only known state is "generating". It always
 *  OPENS on the honest state line, then wanders through Claude-style spinner
 *  words (owner request, 2026-08-11). The whimsy is deliberate honesty: a
 *  single gerund claims nothing about which step is running, where the old
 *  ten-sentence pool claimed steps that weren't. The pool is shuffled per ask
 *  (see `cyclePool` below) so repeat askers don't watch the same loop. */
export const WAIT_CYCLE_PHRASES: readonly string[] = [
  WAIT_PHASE_WORKING,
  "Thinking…",
  "Processing…",
  "Pondering…",
  "Brewing…",
  "Percolating…",
  "Noodling…",
  "Musing…",
  "Simmering…",
  "Frolicking…",
  "Ruminating…",
  "Connecting dots…",
  "Synthesizing…",
]

/** The honest opener, then the whimsy in a fresh order each mount. */
function shuffledCycle(): string[] {
  const [opener, ...rest] = WAIT_CYCLE_PHRASES
  for (let i = rest.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[rest[i], rest[j]] = [rest[j], rest[i]]
  }
  return [opener, ...rest]
}

// ── Grounded progress (flagged) ─────────────────────────────────────────────
// GROUNDED_PROGRESS_ENABLED is the build-time flag, OFF by default (imported from
// lib/friendlyPhase so the ask runner and this component share one source). OFF
// is byte-identical to the whimsy cycle above.
//
// ON, the wait line is driven by, in order of preference:
//   1. a fixed real signal (streaming / dropped / resumed / caller phase),
//   2. `livePhase` — the REAL backend pipeline-leg phase event, curated to
//      user-facing copy (e.g. "Looking through your connected sources…"),
//   3. the TIME-BASED grounded beats below, the fallback for the ~6.5s planner
//      preamble which emits no event at all.
// So a real signal always wins over the timed beat once one has arrived.

/** The time-based fallback beats, timed to the measured pipeline: an ask spends
 *  ~6.5s in the planner preamble (one Sonnet call that classifies the question
 *  and picks the approach) BEFORE any phase event fires, so these narrate that
 *  window. Past ~12s with still no real phase, it settles on the claim-free
 *  generic line rather than falsely holding "Pulling in your data…" for a
 *  minute. None of these claims a step that isn't happening; the boundary times
 *  are the honest soft part — the known AVERAGE, not a per-request event. */
const GROUNDED_PROGRESS_STAGES: readonly { untilMs: number; label: string }[] = [
  { untilMs: 3000, label: "Understanding your question…" },
  { untilMs: 6500, label: "Planning the best approach…" },
  { untilMs: 12000, label: "Pulling in your data…" },
  { untilMs: Number.POSITIVE_INFINITY, label: "Working on your answer…" },
]

/** The grounded beat for `elapsedMs` — the first stage whose window it's in. */
function groundedStageLabel(elapsedMs: number): string {
  for (const stage of GROUNDED_PROGRESS_STAGES) {
    if (elapsedMs < stage.untilMs) return stage.label
  }
  return GROUNDED_PROGRESS_STAGES[GROUNDED_PROGRESS_STAGES.length - 1].label
}

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
// One line for EVERY failure kind — a dropped connection, a backend 500, a
// tenant-gate 404. The person cannot act on the difference between them, and
// the old copy guessed wrong about the common case: it blamed attachments when
// what usually happened is the connection cutting out mid-answer.
export const WAIT_FAILED = "There was an interruption, try again."

// ── Rung thresholds ─────────────────────────────────────────────────────────
/** Rung 0 → 1. Below this an indicator only flickers on a fast answer. */
export const WAIT_RUNG1_MS = 400
/** Rung 1 → 2. NN/g's 10-second attention limit. */
export const WAIT_RUNG2_MS = 10_000
/** Rung 2 → 3. */
export const WAIT_RUNG3_MS = 30_000
const TICK_MS = 1000

// Typewriter pacing. Erase runs ~2× type speed — the eye reads deletion as one
// gesture, and a slow erase makes the cycle feel stuck rather than alive. The
// hold is the pace that actually registers: 2.4s read as flickery in review
// ("switching too fast" — owner, 2026-08-11), so a settled word now stays for
// ~4.8s before it makes way.
const TYPE_MS = 28
const ERASE_MS = 14
const HOLD_MS = 4800

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

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  )
}

/** Types `target` character by character; when `cycle` is on, holds the
 *  finished line, erases it and advances `onAdvance` so the caller can hand
 *  over the next one. A `target` change mid-word erases back to the shared
 *  prefix and types forward — the same motion a person correcting themselves
 *  makes, and it means a fixed phrase (streaming, dropped) takes the stage
 *  without a hard cut. Reduced motion renders `target` whole immediately. */
function useTypedPhrase(target: string, cycle: boolean, onAdvance: () => void) {
  const [reduced] = useState(prefersReducedMotion)
  const [typed, setTyped] = useState(() => (reduced ? target : ""))
  const typedRef = useRef(typed)
  const advanceRef = useRef(onAdvance)
  advanceRef.current = onAdvance

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    if (reduced) {
      typedRef.current = target
      setTyped(target)
      // The words still rotate — content change without motion is fine — the
      // hold is just longer, since there is no typing time to absorb.
      if (cycle) timer = setTimeout(() => advanceRef.current(), HOLD_MS + 1400)
      return () => clearTimeout(timer)
    }
    let mode: "type" | "erase" = "type"
    const tick = () => {
      const cur = typedRef.current
      if (mode === "type") {
        if (cur === target) {
          if (cycle) {
            timer = setTimeout(() => {
              mode = "erase"
              tick()
            }, HOLD_MS)
          }
          return
        }
        const forward = target.startsWith(cur)
        const next = forward ? target.slice(0, cur.length + 1) : cur.slice(0, -1)
        typedRef.current = next
        setTyped(next)
        timer = setTimeout(tick, forward ? TYPE_MS : ERASE_MS)
        return
      }
      // erase to empty, then hand over
      if (cur.length === 0) {
        advanceRef.current()
        return
      }
      const next = cur.slice(0, -1)
      typedRef.current = next
      setTyped(next)
      timer = setTimeout(tick, ERASE_MS)
    }
    tick()
    return () => clearTimeout(timer)
  }, [target, cycle, reduced])

  return { typed, settled: typed === target }
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
  /** The real backend pipeline-leg phase, ALREADY curated to user-facing copy
   *  ("Looking through your connected sources…"). Consulted ONLY when the
   *  grounded-progress flag is on, where it outranks the time-based beat but not
   *  a fixed signal (streaming/dropped/resumed/phase). Ignored flag-off. */
  livePhase?: string
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
  livePhase,
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

  // A real signal fixes the line and stops the cycle; otherwise the cycle owns
  // the stage. Priority mirrors the old phase resolution exactly.
  const fixedPhrase = phase
    ? phase
    : streamDropped
      ? WAIT_PHASE_FINISHING
      : streaming
        ? WAIT_PHASE_WRITING
        : resumed
          ? WAIT_PHASE_RESUMED
          : null
  const [cyclePool] = useState(shuffledCycle)
  const [cycleIdx, setCycleIdx] = useState(0)
  // Flag ON: the wait line is a REAL curated phase (`livePhase`) the moment one
  // has arrived, else a grounded, time-driven beat — either way replacing the
  // whimsy cycle until a fixed signal (streaming) takes over. `now` ticks every
  // second, so the timed beat advances at the stage boundaries on its own — no
  // self-advancing cycle. Flag OFF: `groundedLabel` is null and everything below
  // is byte-identical to the shuffled cycle.
  const groundedLabel = GROUNDED_PROGRESS_ENABLED
    ? (livePhase ?? groundedStageLabel(Math.max(0, now - start)))
    : null
  const targetPhrase =
    fixedPhrase ?? groundedLabel ?? cyclePool[cycleIdx % cyclePool.length]
  // The typewriter only self-advances for the whimsy cycle; the grounded beat is
  // clocked by `now`, and a fixed phrase never advances.
  const cycleActive = fixedPhrase === null && groundedLabel === null
  const { typed, settled } = useTypedPhrase(
    targetPhrase,
    cycleActive,
    () => setCycleIdx((i) => i + 1),
  )
  // What the live region (and the tests) read: the STATE, not the animation. The
  // grounded beat IS a state change worth announcing (real progress), so it is
  // the announced line when the flag is on.
  const srLine = fixedPhrase ?? groundedLabel ?? WAIT_PHASE_WORKING

  const elapsed = Math.max(0, now - start)

  // Rung 0 — nothing at all. The turn still carries aria-busy, so a screen
  // reader knows the surface is working even with no visible indicator.
  if (!gatePassed && !streaming && !children) return null

  // Rung 2 onward: the pacing note and the inline Stop start here. Once shown
  // they STAY — including through the first streamed token, which is where the
  // old surface yanked the whole status row away the instant `partial` arrived.
  const pastRung2 = elapsed >= WAIT_RUNG2_MS
  const showStop = pastRung2 && !!onStop

  return (
    <div className={`cw${compact ? " cw--compact" : ""}`}>
      <div className="cw-status">
        <span className="cw-orb" aria-hidden>
          <SprntlyThinkingMark size={15} />
        </span>
        <span className="cw-phase">
          <span
            className={`cw-phase-typed${settled ? " cw-phase-held" : ""}`}
            aria-hidden
          >
            {typed}
            <span className="cw-caret" />
          </span>
          {/* The single announcement point. Its text changes ONLY when the
              STATE changes — never per keystroke, never per cycle word. */}
          <span className="cw-phase-sr" role="status" aria-live="polite" aria-atomic="true">
            {srLine}
          </span>
        </span>
        {skillLabel ? <span className="cw-skill">{skillLabel}</span> : null}
        {showStop ? (
          <button
            type="button"
            className="cw-btn cw-btn--stop"
            aria-label="Stop generating"
            onClick={onStop}
          >
            Stop
          </button>
        ) : null}
      </div>

      {children ? (
        <div className={streamDropped ? "cw-partial" : undefined}>{children}</div>
      ) : null}

      {/* A dropped live preview is NOT a failure — the poll is still authoritative
          and will deliver the finished answer — so it degrades to a note, never
          an error. It takes precedence over the generic pacing note. */}
      {streamDropped ? (
        <div className="cw-long">
          <span className="cw-long-mark" aria-hidden>◐</span>
          <div>{WAIT_NOTE_STREAM_DROPPED}</div>
        </div>
      ) : pastRung2 ? (
        <div className="cw-note">{longSkill ? WAIT_NOTE_LONG_SKILL : WAIT_NOTE_GENERIC}</div>
      ) : null}

      {resumed && !streamDropped ? <div className="cw-note">{WAIT_NOTE_RESUMED}</div> : null}
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
 *  The copy is FIXED BY DEFAULT. The raw error is deliberately not rendered: a
 *  backend detail string is meaningless to the person reading it, and the 404
 *  the tenant gate raises must read as an interruption with no hint that the
 *  row exists somewhere else.
 *
 *  A PROVIDER NOTICE IS THE ONE EXCEPTION, and it is not a raw error — it is a
 *  TYPED, server-authored, deliberately user-safe sentence produced for exactly
 *  this purpose ("the account is out of credits or rate limited"). Suppressing
 *  it left the durable turn saying "There was an interruption" while a toast —
 *  transient, and gone by the time anyone scrolled back — carried the truth.
 *  Observed on staging with `credit_balance: 0`: two toasts fired for one
 *  event, saying different things, and the turn kept the less useful one.
 *
 *  AND THE RETRY GOES WITH IT when an admin has to act. "Ask again" on an
 *  out-of-credits account is a control that cannot work, which is worse than
 *  no control: it reads as though the failure were transient. A transient
 *  overload (`needsAdmin: false`) keeps it, because there retrying is exactly
 *  right. */
export function WaitFailedState({
  onAskAgain,
  notice,
}: {
  onAskAgain?: () => void
  notice?: { message: string; needsAdmin: boolean } | null
}) {
  const canRetry = onAskAgain && !(notice && notice.needsAdmin)
  return (
    <div className="cw">
      <div className="bc-error" role="alert">
        {notice ? notice.message : WAIT_FAILED}
      </div>
      {canRetry ? (
        <div className="cw-actions">
          <button type="button" className="cw-btn cw-btn--primary" onClick={onAskAgain}>Ask again</button>
        </div>
      ) : null}
    </div>
  )
}
