"use client"

// ── DelegationActions — one shared, party/state-aware action affordance ──
//
// Renders ONLY the buttons that are BOTH party-appropriate AND a legal edge
// from the current derived `status`. It is reused on the ledger rows (the
// authoritative surface, AD-P28) and inline on the assignee's delivered
// brief turn in individual chat — one component, two render sites.
//
// `LEGAL_ACTIONS` below is a small CLIENT MIRROR of the server's transition
// graph (`backend/app/db/delegation_events.py`'s `TRANSITIONS`/`EVENT_PARTY`).
// It DUPLICATES that graph across the language boundary by necessity — the
// server stays the sole authority (an out-of-sync button just 409s); this map
// exists only so no illegal-edge or wrong-party button is ever SHOWN. It is a
// deliberate SUBSET of the legal edges (a UX choice — e.g. an assignee starts
// with Accept, then progresses), never a superset: every entry is verified a
// legal server edge by `DelegationActions.dom.test.tsx` against the same edge
// list. Purely presentational — the parent owns the emit call + the refetch.
import { useCallback, useState } from "react"
import styles from "./DelegationActions.module.css"

export type ViewerParty = "assignee" | "assigner"

type Action = { event: string; label: string; needsNote?: boolean }

/** The party- and state-appropriate action set. A `reopened` delegation
 *  behaves like a freshly-`assigned` one (same outgoing edges), mirroring the
 *  server's own `TRANSITIONS`. Closed states an assignee can't act on map to
 *  an empty list (assignee never reopens — assigner-only). */
export const LEGAL_ACTIONS: Record<ViewerParty, Record<string, Action[]>> = {
  assignee: {
    assigned: [
      { event: "accepted", label: "Accept" },
      { event: "declined", label: "Decline", needsNote: true },
    ],
    reopened: [
      { event: "accepted", label: "Accept" },
      { event: "declined", label: "Decline", needsNote: true },
    ],
    accepted: [
      { event: "in_progress", label: "In progress" },
      { event: "completed", label: "Mark done" },
      { event: "declined", label: "Decline", needsNote: true },
    ],
    in_progress: [
      { event: "completed", label: "Mark done" },
      { event: "declined", label: "Decline", needsNote: true },
    ],
    completed: [],
    declined: [],
    cancelled: [],
  },
  assigner: {
    assigned: [{ event: "cancelled", label: "Cancel" }],
    accepted: [{ event: "cancelled", label: "Cancel" }],
    in_progress: [{ event: "cancelled", label: "Cancel" }],
    reopened: [{ event: "cancelled", label: "Cancel" }],
    completed: [{ event: "reopened", label: "Reopen" }],
    cancelled: [{ event: "reopened", label: "Reopen" }],
    declined: [
      { event: "reopened", label: "Reopen" },
      { event: "cancelled", label: "Cancel" },
    ],
  },
}

export type DelegationActionsProps = {
  delegationId: number
  status: string
  viewerParty: ViewerParty
  /** The parent performs the actual `projectsApi.emitDelegationEvent` call
   *  and the post-emit refetch (no realtime yet — a later live-update pass). */
  onEmit: (event: string, note?: string) => void
  /** Tighter layout for the inline brief-turn render site. */
  compact?: boolean
}

export function DelegationActions({ delegationId, status, viewerParty, onEmit, compact }: DelegationActionsProps) {
  const [declining, setDeclining] = useState(false)
  const [note, setNote] = useState("")

  const actions = LEGAL_ACTIONS[viewerParty]?.[status] ?? []

  const onClick = useCallback(
    (action: Action) => {
      if (action.needsNote) {
        setDeclining(true)
        return
      }
      onEmit(action.event)
    },
    [onEmit],
  )

  const onConfirmDecline = useCallback(() => {
    const trimmed = note.trim()
    onEmit("declined", trimmed.length > 0 ? trimmed : undefined)
    setDeclining(false)
    setNote("")
  }, [note, onEmit])

  const onCancelDecline = useCallback(() => {
    setDeclining(false)
    setNote("")
  }, [])

  if (actions.length === 0) return null

  return (
    <div
      className={`${styles.actions} ${compact ? styles.compact : ""}`}
      data-testid={`delegation-actions-${delegationId}`}
    >
      {declining ? (
        <div className={styles.declineWrap} data-testid="delegation-decline-form">
          <input
            className={styles.declineNote}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Add a note (optional)"
            aria-label="Decline note"
            data-testid="delegation-decline-note"
          />
          <button
            type="button"
            className={`${styles.btn} ${styles.btnDanger}`}
            onClick={onConfirmDecline}
            data-testid="delegation-decline-confirm"
          >
            Decline
          </button>
          <button
            type="button"
            className={styles.btn}
            onClick={onCancelDecline}
            data-testid="delegation-decline-cancel"
          >
            Cancel
          </button>
        </div>
      ) : (
        actions.map((action) => (
          <button
            key={action.event}
            type="button"
            className={`${styles.btn} ${action.event === "declined" ? styles.btnDanger : ""} ${
              action.event === "accepted" || action.event === "completed" ? styles.btnAccent : ""
            }`}
            onClick={() => onClick(action)}
            data-testid={`delegation-action-${action.event}`}
          >
            {action.label}
          </button>
        ))
      )}
    </div>
  )
}
