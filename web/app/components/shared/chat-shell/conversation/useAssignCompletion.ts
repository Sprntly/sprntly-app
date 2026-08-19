"use client"

// The assign-tickets dock-popup COMPLETION, shared by every chat surface.
//
// The up-front unambiguous applies + raising the question live in the shared
// `runAssignTicketsAction`. This is the batch's ONE landing: the popup collected
// every pick (owner directive — finish all the questions before anything is
// sent), and only now do the writes happen, each through the ordinary fields
// endpoint (`ticketDataApi.saveFields`), with a single summary turn posted on
// the flow's conversation entry. The loop (per-question value-then-label
// resolution, multi-pick fan-out, the summary lines) was inlined identically on
// the main screen (`ChatScreen`) and copied onto the project conversation host;
// it is pure over the answers + the api and lives here once.
//
// Each surface injects the STORE seams around that pure core: reading/clearing
// its one open `pendingAssign`, toggling its busy state, appending the summary
// turn, and finalizing it (rail + persistence). Main binds them to the ACTIVE
// tab — the only tab an assign popup is ever open on; the project host to its
// single conversation.

import { useCallback } from "react"

import { ticketDataApi, type AskResponse, type TicketAssignQuestion } from "../../../../lib/api"
import type { PopupAnswer } from "../../QuestionPopup"

/** The open assign batch awaiting completion — the shared shape both surfaces'
 *  `pendingAssign` state (and the ConversationView popup prop) carry. */
export type PendingAssignState = {
  questions: TicketAssignQuestion[]
  applied: string[]
  turnId: string
}

/** The per-surface store seams the completion addresses. */
export type AssignCompletionSeams = {
  /** Read the surface's one open assign batch, if any. */
  getPendingAssign: () => PendingAssignState | undefined
  /** Close the batch (the popup is spent). */
  clearPendingAssign: () => void
  /** Toggle the surface's busy state for the duration of the writes. */
  setBusy: (busy: boolean) => void
  /** Append the batch's summary as its own agent turn (surface mints the id). */
  appendReplyTurn: (reply: AskResponse) => void
  /** Persist the summary against the flow's originating turn (rail + Supabase). */
  finalizeTurn: (turnId: string, reply: AskResponse) => void
}

export function useAssignCompletion(seams: AssignCompletionSeams) {
  const { getPendingAssign, clearPendingAssign, setBusy, appendReplyTurn, finalizeTurn } = seams

  const completeAssign = useCallback(async (
    _tabId: string, answers: PopupAnswer[],
  ) => {
    const pa = getPendingAssign()
    if (!pa) return
    // Close the batch first — the popup is spent; the writes ride the surface's
    // busy state, not a half-open stepper.
    clearPendingAssign()
    setBusy(true)
    const applied = [...pa.applied]
    const failed: string[] = []
    let skipped = 0
    try {
      for (let i = 0; i < pa.questions.length; i++) {
        const q = pa.questions[i]
        const a = answers[i]
        // A multi-pick answer carries EVERY tick on `picks` — one option (and
        // one write) per pick. A single-pick answer resolves to exactly one
        // option, same lookup as always: by stable value first, label second.
        const chosen: TicketAssignQuestion["options"] = []
        if (a && !a.skipped && a.answer) {
          if (q.multi && a.picks?.length) {
            for (const p of a.picks) {
              const opt =
                (p.value != null ? q.options.find((o) => o.value === p.value) : undefined) ??
                q.options.find((o) => o.label === p.label)
              if (opt) chosen.push(opt)
            }
          } else {
            const opt =
              (a.value != null ? q.options.find((o) => o.value === a.value) : undefined) ??
              q.options.find((o) => o.label === a.answer)
            if (opt) chosen.push(opt)
          }
        }
        if (!chosen.length) { skipped += 1; continue }
        for (const opt of chosen) {
          const pair = q.fixed.kind === "ticket"
            ? { key: q.fixed.ticket_key, title: q.fixed.ticket_title, assignee: opt.assignee }
            : { key: opt.value, title: opt.label, assignee: q.fixed.assignee }
          if (!pair.assignee) { skipped += 1; continue }
          try {
            await ticketDataApi.saveFields(pair.key, { assignee: pair.assignee })
            applied.push(`“${pair.title}” → ${pair.assignee.display_name || pair.assignee.email || "them"}`)
          } catch {
            failed.push(pair.title)
          }
        }
      }
      const lines: string[] = []
      if (applied.length) lines.push(`All set — assigned:\n${applied.map((l) => `- ${l}`).join("\n")}`)
      if (skipped) lines.push(`${skipped === 1 ? "One ticket was" : `${skipped} tickets were`} left as they are.`)
      if (failed.length) lines.push(`I couldn't save ${failed.map((t) => `“${t}”`).join(", ")} — try those from the ticket itself.`)
      // Nothing landed and nothing broke → everything was skipped; say that
      // plainly instead of a bare skip count with no verdict.
      const summary = !applied.length && !failed.length
        ? "No assignments made — everything was skipped, so the tickets keep their current owners."
        : lines.join("\n\n")
      const reply = {
        answer: summary, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
      } as AskResponse
      appendReplyTurn(reply)
      finalizeTurn(pa.turnId, reply)
    } finally {
      setBusy(false)
    }
  }, [getPendingAssign, clearPendingAssign, setBusy, appendReplyTurn, finalizeTurn])

  /** The assign popup's × — close the stepper. Nothing has been written from
   *  it (the batch only submits on completion), so there is nothing to report:
   *  the explicit pairs the plan applied are already in the flow's reply. */
  const cancelAssign = useCallback((_tabId: string) => {
    clearPendingAssign()
  }, [clearPendingAssign])

  return { completeAssign, cancelAssign }
}
