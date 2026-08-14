"use client"

// ── DelegationActions — one shared, party/state-aware action affordance ──
//
// Renders ONLY the buttons that are BOTH party-appropriate AND a legal edge
// from the current derived `status`. It is reused on the ledger rows (the
// authoritative surface, AD-P28) and inline on the assignee's delivered
// brief turn in individual chat — one component, two render sites.
//
// `LEGAL_ACTIONS` below is a small CLIENT MIRROR of the server's simplified
// transition graph (`backend/app/db/delegation_events.py`'s
// `TRANSITIONS`/`EVENT_PARTY` — no approve/reject; the agent owns
// follow-through once assigned; `cleared` is the assigner's one terminal
// kill switch). It DUPLICATES that graph across the language boundary by
// necessity — the server stays the sole authority (an out-of-sync button
// just 409s); this map exists only so no illegal-edge or wrong-party button
// is ever SHOWN. It is a deliberate SUBSET of the legal edges, never a
// superset: every entry is verified a legal server edge by
// `DelegationActions.dom.test.tsx` against the same edge list. A status not
// present in this map (e.g. a legacy `accepted`/`declined`/`cancelled`/
// `reopened` row from before the simplification) degrades safely to an
// empty action list via the `?? []` fallback below — it renders null, never
// throws. Purely presentational — the parent owns the emit call + the
// refetch.
import { useCallback } from "react"
import styles from "./DelegationActions.module.css"

export type ViewerParty = "assignee" | "assigner"

type Action = { event: string; label: string }

/** The party- and state-appropriate action set. `completed`/`cleared` are
 *  both terminal — no action follows either, for either party. */
export const LEGAL_ACTIONS: Record<ViewerParty, Record<string, Action[]>> = {
  assignee: {
    assigned: [
      { event: "in_progress", label: "Mark in progress" },
      { event: "completed", label: "Mark done" },
    ],
    in_progress: [{ event: "completed", label: "Mark done" }],
    completed: [],
    cleared: [],
  },
  assigner: {
    assigned: [{ event: "cleared", label: "Clear task" }],
    in_progress: [{ event: "cleared", label: "Clear task" }],
    completed: [],
    cleared: [],
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
  const actions = LEGAL_ACTIONS[viewerParty]?.[status] ?? []

  const onClick = useCallback(
    (action: Action) => {
      onEmit(action.event)
    },
    [onEmit],
  )

  if (actions.length === 0) return null

  return (
    <div
      className={`${styles.actions} ${compact ? styles.compact : ""}`}
      data-testid={`delegation-actions-${delegationId}`}
    >
      {actions.map((action) => (
        <button
          key={action.event}
          type="button"
          className={`${styles.btn} ${action.event === "cleared" ? styles.btnDanger : ""} ${
            action.event === "completed" ? styles.btnAccent : ""
          }`}
          onClick={() => onClick(action)}
          data-testid={`delegation-action-${action.event}`}
        >
          {action.label}
        </button>
      ))}
    </div>
  )
}
