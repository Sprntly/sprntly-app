"use client"

// ── MemoryModal — read-only synthesized project memory (design-spec v3.1/v3.2/v3.3, reduced) ──
//
// AD-P3: memory is layered — a read-only, cached SYNTHESIZED SUMMARY on top
// of discrete, provenance-tagged entries (the source of truth, written by
// chat + agent promotion elsewhere). This modal surfaces ONLY the summary:
// no manual add composer, no per-entry list, no edit/remove — "what this
// project knows," read-only, full stop. The underlying entries still exist
// server-side and still feed the synthesis; this modal just no longer gives
// anyone a hand-curation surface over them.
//
// Scope boundary (P1, build spec §5.4/R7): synthesis is a Phase-2 writer —
// `summary_md` renders whatever the cached row holds (seeded/last-good, or
// null → "Synthesis pending"), never regenerated from here.
import { useCallback, useEffect, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ApiError, projectsApi, type ProjectMember, type ProjectMemorySummary } from "../../../../lib/api"
import { IconClose } from "../../../shared/app-icons"
import { useEscapeToClose } from "./useEscapeToClose"
import styles from "./MemoryModal.module.css"

function SparkleIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l1.9 4.6L18.5 9.5 13.9 11.4 12 16l-1.9-4.6L5.5 9.5l4.6-1.9z" />
      <path d="M18 15l.7 1.8L20.5 17.5l-1.8.7L18 20l-.7-1.8L15.5 17.5l1.8-.7z" />
    </svg>
  )
}

function LockIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  )
}

function RefreshIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12a9 9 0 1 1-2.6-6.4" />
      <path d="M21 4v4h-4" />
    </svg>
  )
}

// ── Read-only summary body — extracted so the layout redesign's Settings ›
// Memory tab can render the SAME markup (DRY — do not re-implement) without
// pulling in the modal chrome or fetch/state-machine below. `MemoryModalView`
// keeps rendering it for its own (unchanged) ready branch. ──

export function MemorySummaryBody({ summary }: { summary: ProjectMemorySummary }) {
  return (
    <>
      {/* ── Read-only synthesized summary — NO edit/remove controls,
          ever. */}
      <div className={styles.synth} data-testid="memory-synth-block">
        <div className={styles.synthHead}>
          <span className={styles.synthTitle}>
            <SparkleIcon />
            What this project knows
          </span>
          <span className={styles.synthTagGroup}>
            <span className={styles.synthTag} data-testid="memory-synth-readonly-tag">
              <LockIcon />
              Read-only · synthesized
            </span>
            {summary.stale ? (
              <span className={styles.synthRefreshing} data-testid="memory-synth-refreshing">
                <RefreshIcon />
                Updating…
              </span>
            ) : null}
          </span>
        </div>
        {summary.summary_md ? (
          <div className={styles.synthBody} data-testid="memory-synth-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary.summary_md}</ReactMarkdown>
          </div>
        ) : (
          <p className={styles.synthPending} data-testid="memory-synth-pending">
            Synthesis pending — insights will appear here as the team collaborates.
          </p>
        )}
        <div className={styles.synthFoot}>
          <RefreshIcon />
          Synthesized from {summary.entry_count} memor{summary.entry_count === 1 ? "y" : "ies"} · updates as
          memory changes · not directly edited
        </div>
      </div>

      {/* ── Privacy boundary ── */}
      <div className={styles.privacy} data-testid="memory-privacy-strip">
        <LockIcon />
        <div>
          <b>Personal chats outside this project never feed project memory.</b> Only what happens inside the
          project is shared — your private context stays walled off.
        </div>
      </div>
    </>
  )
}

// ── View ──

type LoadState =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "not_found" }
  | { status: "error" }
  | { status: "ready"; summary: ProjectMemorySummary }

export type MemoryModalViewProps = {
  open: boolean
  state: LoadState
  onClose: () => void
}

export function MemoryModalView({ open, state, onClose }: MemoryModalViewProps) {
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

  // Document-level listener — reliable Escape-to-close regardless of where
  // focus actually is.
  useEscapeToClose(open, onClose)

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key !== "Tab") return
      const focusables = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), [href], input:not([disabled]), select, textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
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
    [],
  )

  if (!open) return null

  return (
    <div className="modal-overlay open" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={dialogRef}
        className={`modal modal-md ${styles.panel}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="memory-modal-title"
        onKeyDown={onKeyDown}
      >
        <div className="modal-head">
          <div className="modal-head-text">
            <h2 className="modal-title" id="memory-modal-title" data-testid="memory-modal-title">
              Memory
            </h2>
            <p className="modal-sub">
              What this project knows, synthesized from every chat in it — a read-only summary, never raw
              transcripts.
            </p>
          </div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close" data-testid="memory-modal-close">
            <IconClose size={16} title="Close" />
          </button>
        </div>

        <div className="modal-body" data-testid="memory-modal-body">
          {state.status === "loading" ? (
            <div className={styles.stateWrap} data-testid="memory-modal-loading" aria-busy="true">
              Loading…
            </div>
          ) : state.status === "forbidden" ? (
            <div className={styles.stateWrap} data-testid="memory-modal-forbidden">
              You&apos;re not a member of this project, so its memory isn&apos;t visible to you.
            </div>
          ) : state.status === "not_found" ? (
            <div className={styles.stateWrap} data-testid="memory-modal-not-found">
              This project&apos;s memory couldn&apos;t be found.
            </div>
          ) : state.status === "error" ? (
            <div className={styles.stateWrap} data-testid="memory-modal-error">
              Couldn&apos;t load project memory. Try again.
            </div>
          ) : (
            <MemorySummaryBody summary={state.summary} />
          )}
        </div>
      </div>
    </div>
  )
}

// ── Container: fetch (summary-only, read-only) ──

export function MemoryModal({
  projectId,
  members: _members,
  open,
  onClose,
}: {
  projectId: number | string
  /** Accepted for call-site compatibility with the other Projects surfaces
   *  that already thread the loaded roster through here — no longer used
   *  now that the modal is summary-only (no per-entry author line to
   *  resolve). */
  members: ProjectMember[]
  open: boolean
  onClose: () => void
}) {
  const [state, setState] = useState<LoadState>({ status: "loading" })

  const load = useCallback(() => {
    setState({ status: "loading" })
    projectsApi
      .memorySummary(projectId)
      .then((summary) => setState({ status: "ready", summary }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) setState({ status: "forbidden" })
        else if (err instanceof ApiError && err.status === 404) setState({ status: "not_found" })
        else setState({ status: "error" })
      })
  }, [projectId])

  useEffect(() => {
    if (!open) return
    load()
  }, [open, load])

  return <MemoryModalView open={open} state={state} onClose={onClose} />
}
