"use client"

// "Ask again" on a stopped / timed-out / failed turn, shared by every chat
// surface. All three used to be a dead end at those three states.
//
// Attachments are NOT re-sent: their bytes left component state on the original
// send, and quietly re-asking the same words WITHOUT the files the user attached
// is a different question. So a turn that carried files hands its text back to
// the composer (and focuses it) instead — which is also what the failure copy
// ("try it with fewer files attached") tells the reader to do. A plain turn
// re-submits verbatim.
//
// Pure over its injected surface seams: `submit` (the surface's ask entry),
// `setDraft` (its composer text), and `composerRef` (its composer element).

import type { ThreadTurn } from "../../../screens/app/ChatScreen"

export type AskAgainDeps = {
  /** The surface's ask entry point — re-runs the query as a fresh send. */
  submit: (query: string) => void
  /** Set the composer's draft text (used for the files-present hand-back). */
  setDraft: (value: string) => void
  /** The composer element, focused after a hand-back. */
  composerRef: { current: { focus: () => void } | null }
}

export function askAgain(turn: ThreadTurn, deps: AskAgainDeps): void {
  const q = turn.query.trim()
  if (!q) return
  if (turn.attachments?.length) {
    deps.setDraft(turn.query)
    deps.composerRef.current?.focus()
    return
  }
  deps.submit(q)
}
