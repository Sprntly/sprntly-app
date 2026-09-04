"use client"

// The backlog dock-popup COMPLETION, shared by every chat surface.
//
// The exact twin of `useAssignCompletion`, one surface over, and deliberately
// so: the up-front unambiguous applies + raising the question live in the
// shared `runBacklogAction`; this is the batch's ONE landing. The popup
// collected every pick (the same owner directive — finish all the questions
// before anything is written), and only now do the writes happen, each through
// the ordinary ideation routes the Backlog screen itself uses
// (`applyBacklogOp`), with a single summary turn posted on the flow's
// conversation entry.
//
// Each surface injects the STORE seams around that pure core: reading/clearing
// its one open `pendingBacklog`, toggling its busy state, appending the summary
// turn, and finalizing it (rail + persistence). Main binds them to the ACTIVE
// tab — the only tab a backlog popup is ever open on.
//
// WHY THE OPS COME FROM THE QUESTION, not from a second server call: each
// question carries the half-built operation it completes (`op`, plus `status`
// or `title`) and `fills` names the field the picks supply. So the pick →
// operation mapping is a pure function (`backlogOpsFromAnswers`) over data the
// plan already validated, and answering three ideas into one "mark these done"
// question fans out to three writes without re-resolving anything.

import { useCallback } from "react"

import type { AskResponse, BacklogPlanQuestion } from "../../../../lib/api"
import type { PopupAnswer } from "../../QuestionPopup"
import { applyBacklogOp, backlogOpsFromAnswers } from "./actions"

/** The open backlog batch awaiting completion — the shared shape both the
 *  surface's `pendingBacklog` state and the ConversationView popup prop
 *  carry. */
export type PendingBacklogState = {
  questions: BacklogPlanQuestion[]
  applied: string[]
  turnId: string
}

/** The per-surface store seams the completion addresses. */
export type BacklogCompletionSeams = {
  /** Read the surface's one open backlog batch, if any. */
  getPendingBacklog: () => PendingBacklogState | undefined
  /** Close the batch (the popup is spent). */
  clearPendingBacklog: () => void
  /** Toggle the surface's busy state for the duration of the writes. */
  setBusy: (busy: boolean) => void
  /** Append the batch's summary as its own agent turn (surface mints the id). */
  appendReplyTurn: (reply: AskResponse) => void
  /** Persist the summary against the flow's originating turn (rail + Supabase). */
  finalizeTurn: (turnId: string, reply: AskResponse) => void
  /** Re-read the Backlog screen if it is mounted, so a change made from chat
   *  shows there without a manual refresh. Optional: a surface with no backlog
   *  list on screen supplies nothing. */
  onBacklogChanged?: () => void
}

export function useBacklogCompletion(seams: BacklogCompletionSeams) {
  const {
    getPendingBacklog, clearPendingBacklog, setBusy,
    appendReplyTurn, finalizeTurn, onBacklogChanged,
  } = seams

  const completeBacklog = useCallback(async (
    _tabId: string, answers: PopupAnswer[],
  ) => {
    const pb = getPendingBacklog()
    if (!pb) return
    // Close the batch first — the popup is spent; the writes ride the surface's
    // busy state, not a half-open stepper.
    clearPendingBacklog()
    setBusy(true)
    const applied = [...pb.applied]
    let failed = 0
    let skipped = 0
    try {
      for (let i = 0; i < pb.questions.length; i++) {
        const q = pb.questions[i]
        const a = answers[i]
        // A multi-pick answer carries EVERY tick on `picks` — one operation per
        // pick. A single-pick answer resolves to exactly one option, by stable
        // value first and label second, the same lookup the assign batch uses.
        const values: string[] = []
        if (a && !a.skipped && a.answer) {
          if (q.multi && a.picks?.length) {
            for (const p of a.picks) {
              const opt =
                (p.value != null ? q.options.find((o) => o.value === p.value) : undefined) ??
                q.options.find((o) => o.label === p.label)
              if (opt) values.push(opt.value)
            }
          } else {
            const opt =
              (a.value != null ? q.options.find((o) => o.value === a.value) : undefined) ??
              q.options.find((o) => o.label === a.answer)
            if (opt) values.push(opt.value)
          }
        }
        if (!values.length) { skipped += 1; continue }
        for (const op of backlogOpsFromAnswers(q, values)) {
          const line = await applyBacklogOp(op)
          if (line) applied.push(line)
          else failed += 1
        }
      }
      const lines: string[] = []
      if (applied.length) lines.push(`All set:\n${applied.map((l) => `- ${l}`).join("\n")}`)
      if (skipped) {
        lines.push(
          `${skipped === 1 ? "One question was" : `${skipped} questions were`} skipped, so nothing changed for ${skipped === 1 ? "that idea" : "those"}.`,
        )
      }
      if (failed) {
        lines.push(
          `${failed === 1 ? "One change" : `${failed} changes`} couldn't be saved — try ${failed === 1 ? "it" : "those"} from the Backlog screen.`,
        )
      }
      // Nothing landed and nothing broke → everything was skipped; say that
      // plainly instead of a bare skip count with no verdict.
      const summary = !applied.length && !failed
        ? "Nothing changed on the backlog — everything was skipped."
        : lines.join("\n\n")
      const reply = {
        answer: summary, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
      } as AskResponse
      appendReplyTurn(reply)
      finalizeTurn(pb.turnId, reply)
      if (applied.length) onBacklogChanged?.()
    } finally {
      setBusy(false)
    }
  }, [
    getPendingBacklog, clearPendingBacklog, setBusy,
    appendReplyTurn, finalizeTurn, onBacklogChanged,
  ])

  /** The backlog popup's × — close the stepper. Nothing has been written from
   *  it (the batch only submits on completion), so there is nothing to report:
   *  the operations the plan applied outright are already in the flow's reply. */
  const cancelBacklog = useCallback((_tabId: string) => {
    clearPendingBacklog()
  }, [clearPendingBacklog])

  return { completeBacklog, cancelBacklog }
}
