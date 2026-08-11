"use client"

// ── TaskModal — task ledger, STUBBED fast-follow (design-spec v3.2) ──
//
// Explicitly out of scope for this ticket (build spec §8, Phase 4+): there
// is no task-ledger backend yet. This modal is PRESENTATIONAL ONLY — a
// fixed Open/Done illustration plus the "Fast-follow · coming" badge, so
// the rail's "Task ledger" card has somewhere real to open without
// promising data this build doesn't have. It fetches nothing and wires no
// callback beyond Close.
import { useCallback, useEffect, useRef } from "react"
import { IconClose } from "../../../shared/app-icons"
import styles from "./TaskModal.module.css"

type StubTask = { id: string; text: string; sub: string; done: boolean }

/** Illustrative only — never real project data. Shown so the stub reads as
 *  "here's the shape this will take", not an empty box. */
const STUB_TASKS: StubTask[] = [
  { id: "t1", text: "Review pricing-latency analysis", sub: "from David · handed off by the agent", done: false },
  { id: "t2", text: "Upload reassurance state", sub: "from the group chat", done: false },
  { id: "t3", text: "Draft quote-summary layout", sub: "your chat with the agent", done: false },
  { id: "t4", text: "Pull pricing p95 benchmark", sub: "from the group chat", done: true },
  { id: "t5", text: "Confirm sub-60s target with Xometry", sub: "from the group chat · David", done: true },
]

function ChecklistIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 11l3 3 8-8" />
      <path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9" />
    </svg>
  )
}

function CheckGlyph() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 12l5 5 9-11" />
    </svg>
  )
}

export type TaskModalViewProps = {
  open: boolean
  onClose: () => void
}

export function TaskModalView({ open, onClose }: TaskModalViewProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<Element | null>(null)

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

  const openTasks = STUB_TASKS.filter((t) => !t.done)
  const doneTasks = STUB_TASKS.filter((t) => t.done)

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
              <ChecklistIcon /> Task ledger{" "}
              <span className={styles.ffBadge} data-testid="task-modal-fastfollow">
                Fast-follow · coming
              </span>
            </h2>
            <p className="modal-sub">
              Who owes what across the project — from the group chat, individual chats and agent hand-offs. Each
              task is simply done or not done.
            </p>
          </div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close" data-testid="task-modal-close">
            <IconClose size={16} title="Close" />
          </button>
        </div>

        <div className="modal-body" data-testid="task-modal-body">
          <div className={styles.sec} data-testid="task-modal-open-heading">
            Open · {openTasks.length}
          </div>
          {openTasks.map((t) => (
            <div className={styles.row} key={t.id} data-testid={`task-row-${t.id}`}>
              <span className={styles.box} aria-hidden="true" />
              <div className={styles.main}>
                <div className={styles.text}>{t.text}</div>
                <div className={styles.sub}>{t.sub}</div>
              </div>
            </div>
          ))}
          <div className={styles.sec} data-testid="task-modal-done-heading">
            Done · {doneTasks.length}
          </div>
          {doneTasks.map((t) => (
            <div className={`${styles.row} ${styles.rowDone}`} key={t.id} data-testid={`task-row-${t.id}`}>
              <span className={`${styles.box} ${styles.boxDone}`} aria-hidden="true">
                <CheckGlyph />
              </span>
              <div className={styles.main}>
                <div className={styles.text}>{t.text}</div>
                <div className={styles.sub}>{t.sub}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export function TaskModal({ open, onClose }: TaskModalViewProps) {
  return <TaskModalView open={open} onClose={onClose} />
}
