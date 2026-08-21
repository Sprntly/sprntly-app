"use client"

// ── TaskModal — the project's task ledger (data-bound, AD-P28) ──
//
// The authoritative ledger surface: on open it reads the caller's party-
// filtered delegation views (`projectsApi.ledger`) and renders three
// sections — Assigned to me / Waiting on / Done — each row carrying the
// shared `<DelegationActions>` for the party- and state-appropriate moves.
// Every action calls the one gated `projectsApi.emitDelegationEvent`; the
// affected view refetches after a successful emit (no realtime yet).
//
// Presentation chrome (shell, focus-trap, Escape/backdrop close, the
// `task-modal-*` test-ids) is preserved from the shipped stub this replaces.
import { useCallback, useEffect, useRef, useState } from "react"
import { IconClose } from "../../../shared/app-icons"
import { projectsApi, type DelegationLedgerRow } from "../../../../lib/api"
import { DelegationActions, type ViewerParty } from "./DelegationActions"
import styles from "./TaskModal.module.css"

/** Human-readable label for a derived delegation status. Legacy labels
 *  (`accepted`/`declined`/`cancelled`/`reopened`) are kept so any
 *  pre-existing derived row from before the state-model simplification
 *  still renders a human label — they are never targets of a fresh
 *  transition (see `DelegationActions.tsx`'s `LEGAL_ACTIONS`). */
export const STATUS_LABEL: Record<string, string> = {
  assigned: "Assigned",
  accepted: "Accepted",
  in_progress: "In progress",
  completed: "Done",
  cleared: "Cleared",
  declined: "Declined",
  cancelled: "Cancelled",
  reopened: "Reopened",
}

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status
}

function ChecklistIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 11l3 3 8-8" />
      <path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9" />
    </svg>
  )
}

type LedgerState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; assigned: DelegationLedgerRow[]; waiting: DelegationLedgerRow[] }

export type TaskModalViewProps = {
  open: boolean
  projectId: number | string
  onClose: () => void
  /** Bumped by the parent's per-user realtime subscription on a live
   *  `delegation.event` or a reconnect reconcile. While open, a change
   *  re-reads the party-filtered views so a live status change lands
   *  without a reopen (AD-P22). Absent/unchanged on the degraded path —
   *  the open-fetch + post-emit refetch stay the authority, and no new
   *  poll loop is introduced. */
  ledgerVersion?: number
}

function LedgerRow({
  row,
  viewerParty,
  onEmit,
}: {
  row: DelegationLedgerRow
  viewerParty: ViewerParty
  onEmit: (delegationId: number, event: string, note?: string) => void
}) {
  return (
    <div className={styles.row} data-testid={`ledger-row-${row.delegation_id}`}>
      <div className={styles.main}>
        <div className={styles.text}>{row.task_summary}</div>
        <div className={styles.sub}>
          {row.other_party_name ?? "Someone"} · <span className={styles.statusLabel}>{statusLabel(row.status)}</span>
        </div>
        <DelegationActions
          delegationId={row.delegation_id}
          status={row.status}
          viewerParty={viewerParty}
          onEmit={(event, note) => onEmit(row.delegation_id, event, note)}
        />
      </div>
    </div>
  )
}

export function TaskModalView({ open, projectId, onClose, ledgerVersion }: TaskModalViewProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<Element | null>(null)
  const [state, setState] = useState<LedgerState>({ status: "loading" })
  const [actionError, setActionError] = useState(false)

  const load = useCallback(() => {
    setState({ status: "loading" })
    Promise.all([
      projectsApi.ledger(projectId, "assigned_to_me"),
      projectsApi.ledger(projectId, "waiting_on"),
    ])
      .then(([assigned, waiting]) => setState({ status: "ready", assigned, waiting }))
      .catch(() => setState({ status: "error" }))
  }, [projectId])

  // Fetch on open (and whenever the project changes while open). Closing
  // resets to loading so a re-open always shows fresh reads, never stale rows.
  useEffect(() => {
    if (!open) {
      setState({ status: "loading" })
      setActionError(false)
      return
    }
    load()
  }, [open, load])

  // Live re-read: the parent's per-user subscription bumps `ledgerVersion` on
  // a `delegation.event` or a reconnect reconcile; while open, re-read the
  // affected views so a status change lands without a reopen (AD-P22). Fires
  // ONLY on a genuine bump — `open`/`load` are deliberately NOT deps here, so
  // opening the modal doesn't double-fetch (the open-fetch effect above owns
  // the initial load).
  useEffect(() => {
    if (!open || ledgerVersion === undefined) return
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ledgerVersion])

  const onEmit = useCallback(
    (delegationId: number, event: string, note?: string) => {
      setActionError(false)
      projectsApi
        .emitDelegationEvent(projectId, delegationId, event, note)
        .then(() => load())
        .catch(() => {
          // Non-blocking: surface an inline note and resync from the server
          // (the emit may have landed; the reload reflects the true state).
          setActionError(true)
          load()
        })
    },
    [projectId, load],
  )

  useEffect(() => {
    if (!open) return
    openerRef.current = document.activeElement
    const first = dialogRef.current?.querySelector<HTMLElement>(
      "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
    )
    first?.focus()
    const opener = openerRef.current
    return () => {
      if (opener instanceof HTMLElement) opener.focus()
    }
  }, [open])

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key !== "Tab") return
      const focusables = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex='-1'])",
        ) ?? [],
      )
      if (focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const active = document.activeElement
      if (e.shiftKey && active === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      }
    },
    [onClose],
  )

  if (!open) return null

  const assignedOpen = state.status === "ready" ? state.assigned.filter((r) => r.bucket === "open") : []
  const waitingOpen = state.status === "ready" ? state.waiting.filter((r) => r.bucket === "open") : []
  const done =
    state.status === "ready"
      ? [
          ...state.assigned.filter((r) => r.bucket === "done").map((r) => ({ row: r, viewerParty: "assignee" as const })),
          ...state.waiting.filter((r) => r.bucket === "done").map((r) => ({ row: r, viewerParty: "assigner" as const })),
        ]
      : []

  return (
    <div className="modal-overlay open" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={dialogRef}
        className={`modal modal-md ${styles.panel}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="task-modal-title"
        onKeyDown={onKeyDown}
      >
        <div className="modal-head">
          <div className="modal-head-text">
            <h2 className="modal-title" id="task-modal-title" data-testid="task-modal-title">
              <ChecklistIcon /> Task ledger
            </h2>
            <p className="modal-sub">
              Who owes what across the project — from individual chats and agent hand-offs. Each
              task is simply done or not done.
            </p>
          </div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close" data-testid="task-modal-close">
            <IconClose size={16} title="Close" />
          </button>
        </div>

        <div className="modal-body" data-testid="task-modal-body">
          {state.status === "loading" ? (
            <div className={styles.stateNote} data-testid="ledger-loading" aria-busy="true">
              Loading tasks…
            </div>
          ) : state.status === "error" ? (
            <div className={styles.stateNote} role="alert" data-testid="ledger-error">
              Couldn't load tasks. Try reopening this in a moment.
            </div>
          ) : (
            <>
              {actionError ? (
                <div className={styles.actionError} role="alert" data-testid="ledger-action-error">
                  Couldn't update that task. It may have already changed — this list is now refreshed.
                </div>
              ) : null}

              <div className={styles.sec} data-testid="ledger-section-assigned">
                Assigned to me · {assignedOpen.length}
              </div>
              {assignedOpen.length === 0 ? (
                <div className={styles.emptyNote} data-testid="ledger-empty-assigned">
                  Nothing here yet
                </div>
              ) : (
                assignedOpen.map((row) => (
                  <LedgerRow key={row.delegation_id} row={row} viewerParty="assignee" onEmit={onEmit} />
                ))
              )}

              <div className={styles.sec} data-testid="ledger-section-waiting">
                Waiting on · {waitingOpen.length}
              </div>
              {waitingOpen.length === 0 ? (
                <div className={styles.emptyNote} data-testid="ledger-empty-waiting">
                  Nothing here yet
                </div>
              ) : (
                waitingOpen.map((row) => (
                  <LedgerRow key={row.delegation_id} row={row} viewerParty="assigner" onEmit={onEmit} />
                ))
              )}

              <div className={styles.sec} data-testid="ledger-section-done">
                Done · {done.length}
              </div>
              {done.length === 0 ? (
                <div className={styles.emptyNote} data-testid="ledger-empty-done">
                  Nothing here yet
                </div>
              ) : (
                done.map(({ row, viewerParty }) => (
                  <LedgerRow
                    key={`${viewerParty}-${row.delegation_id}`}
                    row={row}
                    viewerParty={viewerParty}
                    onEmit={onEmit}
                  />
                ))
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export function TaskModal({ open, projectId, onClose, ledgerVersion }: TaskModalViewProps) {
  return <TaskModalView open={open} projectId={projectId} onClose={onClose} ledgerVersion={ledgerVersion} />
}
