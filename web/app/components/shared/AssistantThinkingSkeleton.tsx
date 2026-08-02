"use client"

import { AssistantWaitState, WAIT_PHASE_WORKING } from "./AssistantWaitState"

type Props = {
  /** Tighter layout for the side AI bar rail */
  compact?: boolean
  /** Overrides the phase line where the caller already has copy specific to the
   *  work it is doing ("Summarizing what got built…", "loading conversation…").
   *  Defaults to the one line that is always true while a job is generating. */
  phase?: string
}

/**
 * The plain waiting indicator: the rung-1 shape (orb + phase + indeterminate bar
 * + skeleton lines) with no clock and no Stop.
 *
 * This used to own a pool of ten generic sentences and pick one at
 * `Math.floor(Math.random() * 10)`, rotating every 15s — so the same 3-second
 * wait could open on "Digging through your workspace context…" for a question
 * that touched no workspace context at all. The pool is gone; `AssistantWaitState`
 * holds the copy and the honesty rule behind each line.
 *
 * Callers that DO have a start time, a Stop handler or stream signals (the chat
 * thread) render `AssistantWaitState` directly to get the full elapsed-time
 * ladder. Everything else — the AI bar rail, the artifact-summary indicator, a
 * hydrating conversation — renders this, which is the same chrome held at rung 1.
 *
 * `gateMs={0}`: the 400ms rung-0 gate belongs to a send, where an indicator that
 * appears and vanishes inside half a second reads as a glitch. These surfaces are
 * mounted only once their work is already underway, so they show immediately.
 */
export function AssistantThinkingSkeleton({ compact, phase }: Props) {
  return <AssistantWaitState compact={compact} gateMs={0} phase={phase ?? WAIT_PHASE_WORKING} />
}
