"use client"

// ── TaskModal — the project's task ledger (data-bound, AD-P28) ──
//
// The authoritative ledger surface: on open it reads the caller's party-
// filtered delegation views (`projectsApi.ledger`) and unions them into ONE
// flat table — a leading complete-checkbox, Task, Assigned to, Created by.
// Only rows the viewer OWNS (they are the assignee) get a checkbox; rows they
// created but handed off are informational (no checkbox). Ticking an owned row
// completes it AS IF THE ASSIGNEE TYPED A COMPLETION into their chat — routed
// through the project chat's OWN `submitAsk` (the container wires it in via
// `onCompleteTask`), the single ask→persist path. That is the delegation's
// `delivered_conversation_id` (the assignee's individual project chat), so the
// agent runs its normal turn AND `maybe_ingest_status` marks the task
// `completed` and notifies the assigner (in-app "✓ finished" turn + D3 email).
// Reusing `submitAsk` (vs a reproduced start→poll→persist) gives the turn the
// composer's optimistic echo + reply-persist, so the reply renders cleanly with
// no realtime "No response was generated" phantom. The tick is optimistic
// (immediate strike + check), then reconciles from the server, reverting on error.
//
// Presentation chrome (shell, focus-trap, Escape/backdrop close, the
// `task-modal-*` test-ids) is preserved from the shipped modal this replaces.
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { IconClose } from "../../../shared/app-icons"
import { projectsApi, type DelegationLedgerRow } from "../../../../lib/api"
import { type ViewerParty } from "./DelegationActions"
import { personAvatarStyle } from "./avatarColor"
import styles from "./TaskModal.module.css"

/** Human-readable label for a derived delegation status. Legacy labels
 *  (`accepted`/`declined`/`cancelled`/`reopened`) are kept so any
 *  pre-existing derived row from before the state-model simplification
 *  still renders a human label — they are never targets of a fresh
 *  transition (see `DelegationActions.tsx`'s `LEGAL_ACTIONS`). Retained as
 *  an export because a sibling test still imports it. */
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

/** The completion message a tick synthesizes into the assignee's chat — phrased
 *  to classify reliably as a completion by `delegation_status_ingest`. It is
 *  submitted AS IF THE USER TYPED IT (through the ask pipeline), so the agent
 *  runs its normal turn and the whole downstream fires: status → `completed`
 *  AND the assigner is notified (in-app "✓ finished" turn + D3 email). */
function completionMessage(task: string): string {
  return `I've finished this task: "${task}". Please mark it complete.`
}

/** Same initials algorithm the other project surfaces duplicate locally
 *  (`ProjectDetailScreen.tsx`/`TicketsScreen.tsx`) — not a shared export. */
function initials(name: string | null | undefined): string {
  if (!name) return "?"
  return name.split(" ").filter(Boolean).map((w) => w[0]).join("").toUpperCase().slice(0, 2)
}

function ChecklistIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 11l3 3 8-8" />
      <path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9" />
    </svg>
  )
}

function CheckMark() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 12l5 5L20 6" />
    </svg>
  )
}

function UserGlyph() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="8" r="3.4" />
      <path d="M5.5 20a6.5 6.5 0 0 1 13 0" />
    </svg>
  )
}

/** One person cell — an initials/glyph chip + name. The caller's own party
 *  renders a neutral "You" chip; everyone else routes through the SAME
 *  deterministic `personAvatarStyle` tint used on every other project avatar. */
function PersonCell({
  name,
  userId,
  isYou,
}: {
  name?: string | null
  userId?: string | null
  isYou?: boolean
}) {
  if (isYou) {
    return (
      <span className={styles.person}>
        <span className={`${styles.chip} ${styles.chipYou}`} aria-hidden="true">
          <UserGlyph />
        </span>
        <span className={styles.personName}>You</span>
      </span>
    )
  }
  const label = name ?? "Someone"
  return (
    <span className={styles.person}>
      <span className={styles.chip} style={personAvatarStyle(userId, name)} aria-hidden="true">
        {initials(name)}
      </span>
      <span className={styles.personName}>{label}</span>
    </span>
  )
}

type LedgerState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; assigned: DelegationLedgerRow[]; waiting: DelegationLedgerRow[] }

/** One flat table row, derived from whichever party-view it came from — the
 *  DTO's `other_party_*` fields are relative to the caller, so the view fixes
 *  which of Assigned-to / Created-by is "You" and which is the other party. */
type TableRow = {
  delegationId: number
  task: string
  status: string
  bucket: "open" | "done"
  viewerParty: ViewerParty
  otherName: string | null
  otherUserId: string
  /** The conversation the brief was delivered into (the assignee's own
   *  individual project chat) — a tick synthesizes the completion turn INTO
   *  this conversation so `maybe_ingest_status` classifies it. `null` on a
   *  legacy/link-less row: the tick can't be routed, so it is disabled. */
  deliveredConversationId: number | null
}

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
  /** Completes an owned task by sending its completion message through the
   *  project chat's OWN `submitAsk` (the composer's single ask→persist path),
   *  so the turn gets the optimistic echo + reply-persist a typed send does —
   *  no reproduced start→poll→persist, no realtime "No response was generated"
   *  phantom. The container wires this to `ProjectMainThread`'s published
   *  `submitAsk`; the target is the chat's own conversation, which for an
   *  owned row IS the delegation's `delivered_conversation_id` (the assignee's
   *  individual project chat). Resolves when the turn settles; rejects on
   *  failure (or when the chat isn't mounted) so the tick can revert. */
  onCompleteTask?: (text: string) => Promise<void>
}

export function TaskModalView({ open, projectId, onClose, ledgerVersion, onCompleteTask }: TaskModalViewProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<Element | null>(null)
  const [state, setState] = useState<LedgerState>({ status: "loading" })
  const [actionError, setActionError] = useState(false)
  // Delegation ids optimistically closed by a tick — struck/checked at once,
  // cleared once the server refetch confirms the true `done` bucket (or on a
  // failed completion, reverted so the row snaps back to open).
  const [optimisticDone, setOptimisticDone] = useState<Set<number>>(new Set())
  // Ids with an in-flight completion — their checkbox is disabled so a
  // double-tick can't fire a second turn before the reconcile lands.
  const [pending, setPending] = useState<Set<number>>(new Set())

  const fetchViews = useCallback(
    () => Promise.all([projectsApi.ledger(projectId, "assigned_to_me"), projectsApi.ledger(projectId, "waiting_on")]),
    [projectId],
  )

  // Initial load (open / project change): shows the loading state.
  const load = useCallback(() => {
    setState({ status: "loading" })
    fetchViews()
      .then(([assigned, waiting]) => setState({ status: "ready", assigned, waiting }))
      .catch(() => setState({ status: "error" }))
  }, [fetchViews])

  // Post-emit / live re-read: replaces rows IN PLACE (no loading flash) and
  // clears the optimistic overlay now that the server bucket is authoritative.
  const reconcile = useCallback(() => {
    fetchViews()
      .then(([assigned, waiting]) => {
        setState({ status: "ready", assigned, waiting })
        setOptimisticDone(new Set())
      })
      .catch(() => setActionError(true))
  }, [fetchViews])

  useEffect(() => {
    if (!open) {
      setState({ status: "loading" })
      setActionError(false)
      setOptimisticDone(new Set())
      setPending(new Set())
      return
    }
    load()
  }, [open, load])

  // Live re-read on a genuine `ledgerVersion` bump (AD-P22). `open`/`load` are
  // deliberately NOT deps — opening owns the initial load; this only reconciles.
  useEffect(() => {
    if (!open || ledgerVersion === undefined) return
    reconcile()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ledgerVersion])

  // Completing a task runs the FULL agent workflow, as if the assignee typed a
  // completion into their chat — but routed through the project chat's OWN
  // `submitAsk` (via `onCompleteTask`), the single ask→persist path. That gives
  // the turn the optimistic echo + reply-persist a real send has, so the agent's
  // reply renders cleanly on the realtime path with NO "No response was
  // generated" phantom (the reproduced start→poll→persist tripped it because its
  // two racing `turn.created` broadcasts pair by adjacency and can arrive out of
  // order). The server still runs the normal turn AND `maybe_ingest_status`
  // (marks `completed` + notifies the assigner in-app and by the D3 email). Only
  // the ASSIGNEE (an owned `assigned_to_me` row) can complete; assigner rows are
  // informational and never reach here (no checkbox). A row with no delivered
  // conversation can't be routed and is disabled upstream.
  const onToggle = useCallback(
    (row: TableRow) => {
      if (row.viewerParty !== "assignee") return
      if (row.bucket === "done" || optimisticDone.has(row.delegationId) || pending.has(row.delegationId)) return
      if (row.deliveredConversationId == null || !onCompleteTask) return

      const clearPending = (id: number) =>
        setPending((prev) => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })

      setActionError(false)
      setOptimisticDone((prev) => new Set(prev).add(row.delegationId))
      setPending((prev) => new Set(prev).add(row.delegationId))

      onCompleteTask(completionMessage(row.task))
        .then(() => {
          // The turn settled (agent replied). `maybe_ingest_status` runs in the
          // job's post-commit hook — a hair AFTER the answer is marked ready — so
          // we deliberately do NOT reconcile here (an immediate re-read could
          // still see `assigned` and clear the optimistic strike, flickering it
          // open→done). Keep the optimistic strike and let the completion's own
          // realtime `delegation.event` bump `ledgerVersion` → reconcile (the
          // same live path that already moves the requester's ledger). Just drop
          // the in-flight lock so the row isn't stuck disabled if that lands late.
          clearPending(row.delegationId)
        })
        .catch(() => {
          // Revert the optimistic strike, surface the inline note, resync from
          // the server (the completion may have landed; the reload reflects truth).
          setOptimisticDone((prev) => {
            const next = new Set(prev)
            next.delete(row.delegationId)
            return next
          })
          clearPending(row.delegationId)
          setActionError(true)
          reconcile()
        })
    },
    [onCompleteTask, reconcile, optimisticDone, pending],
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

  // Union the two party-views into one flat, deduped row list (a delegation
  // could in theory surface in both; first-seen wins). Open rows sort before
  // done rows; order is otherwise stable (server order preserved per view).
  const rows = useMemo<TableRow[]>(() => {
    if (state.status !== "ready") return []
    const out: TableRow[] = []
    const seen = new Set<number>()
    const push = (r: DelegationLedgerRow, viewerParty: ViewerParty) => {
      if (seen.has(r.delegation_id)) return
      seen.add(r.delegation_id)
      out.push({
        delegationId: r.delegation_id,
        task: r.task_summary,
        status: r.status,
        bucket: r.bucket,
        viewerParty,
        otherName: r.other_party_name,
        otherUserId: r.other_party_user_id,
        deliveredConversationId: r.delivered_conversation_id,
      })
    }
    state.assigned.forEach((r) => push(r, "assignee"))
    state.waiting.forEach((r) => push(r, "assigner"))
    return out.sort((a, b) => (a.bucket === b.bucket ? 0 : a.bucket === "open" ? -1 : 1))
  }, [state])

  const openCount = rows.filter((r) => r.bucket === "open" && !optimisticDone.has(r.delegationId)).length

  if (!open) return null

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
            {state.status === "ready" ? (
              <p className={styles.openSummary} data-testid="ledger-open-summary">
                {openCount} open
              </p>
            ) : null}
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

              {rows.length === 0 ? (
                <div className={styles.empty} data-testid="ledger-empty">
                  No tasks yet — delegations from your chats and agent hand-offs land here.
                </div>
              ) : (
                <table className={styles.table} data-testid="ledger-table">
                  <thead>
                    <tr>
                      <th className={`${styles.th} ${styles.thCheck}`} scope="col">
                        <span className="sr-only">Done</span>
                      </th>
                      <th className={styles.th} scope="col">
                        Task
                      </th>
                      <th className={styles.th} scope="col">
                        Assigned to
                      </th>
                      <th className={styles.th} scope="col">
                        Created by
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const done = row.bucket === "done" || optimisticDone.has(row.delegationId)
                      const busy = pending.has(row.delegationId)
                      // Only tasks the viewer OWNS (they are the assignee) get a
                      // checkbox — completing is the assignee's move. Rows the
                      // viewer created but handed off are informational: no
                      // checkbox. A legacy owned row with no delivered
                      // conversation can't be routed through the chat workflow,
                      // so its checkbox is shown but disabled (with a hint).
                      const owned = row.viewerParty === "assignee"
                      const routable = row.deliveredConversationId != null
                      const tickable = owned && !done && routable
                      return (
                        <tr
                          key={row.delegationId}
                          className={done ? styles.rowDone : undefined}
                          data-testid={`ledger-row-${row.delegationId}`}
                        >
                          <td className={`${styles.td} ${styles.tdCheck}`}>
                            {owned ? (
                              <button
                                type="button"
                                role="checkbox"
                                aria-checked={done}
                                aria-label={done ? "Completed" : "Mark complete"}
                                title={
                                  !done && !routable
                                    ? "Complete this from your chat with the agent"
                                    : undefined
                                }
                                className={`${styles.checkbox} ${done ? styles.checkboxDone : ""}`}
                                onClick={() => onToggle(row)}
                                disabled={done || !tickable || busy}
                                data-testid={`ledger-check-${row.delegationId}`}
                              >
                                {done ? <CheckMark /> : null}
                              </button>
                            ) : (
                              <span className={styles.checkPlaceholder} aria-hidden="true" data-testid={`ledger-nocheck-${row.delegationId}`} />
                            )}
                          </td>
                          <td className={`${styles.td} ${styles.tdTask} ${done ? styles.taskDone : ""}`}>
                            {row.task}
                          </td>
                          <td className={styles.td}>
                            {row.viewerParty === "assignee" ? (
                              <PersonCell isYou />
                            ) : (
                              <PersonCell name={row.otherName} userId={row.otherUserId} />
                            )}
                          </td>
                          <td className={styles.td}>
                            {row.viewerParty === "assignee" ? (
                              <PersonCell name={row.otherName} userId={row.otherUserId} />
                            ) : (
                              <PersonCell isYou />
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export function TaskModal({ open, projectId, onClose, ledgerVersion, onCompleteTask }: TaskModalViewProps) {
  return (
    <TaskModalView
      open={open}
      projectId={projectId}
      onClose={onClose}
      ledgerVersion={ledgerVersion}
      onCompleteTask={onCompleteTask}
    />
  )
}
