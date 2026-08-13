"use client"

// ── MemoryModal — layered project memory (design-spec v3.1/v3.2/v3.3) ──
//
// AD-P3: memory is layered — a read-only, cached SYNTHESIZED SUMMARY on top
// of discrete, provenance-tagged ENTRIES (the source of truth). This modal
// renders both: the summary block (`.synth`, no edit controls, ever) above
// an add-composer and the entries list, each entry showing whether it is
// user-authored ("Manual"/"Added by <name>") or agent-promoted ("Promoted
// by Sprntly") — a STORED FACT from `author_user_id`/`promoted_by`, never
// inferred (`[[feedback_prefer-inference-over-stored-derived-state]]` cuts
// the other way here: provenance is exactly the kind of fact that must NOT
// be re-derived, because guessing it would blur the privacy boundary this
// modal exists to make visible).
//
// Scope boundary (P1, build spec §5.4/R7): synthesis is a Phase-2 writer —
// `summary_md` renders whatever the cached row holds (seeded/last-good, or
// null → "Synthesis pending"), never regenerated from here. Agent
// promotion is also Phase 2; every entry this modal can ADD is
// user-authored. Edit/remove work on any entry regardless of provenance
// (v1 all-or-nothing membership, AD-P11 — the backend allows it).
import { useCallback, useEffect, useRef, useState } from "react"
import {
  ApiError,
  projectsApi,
  type ProjectMember,
  type ProjectMemoryEntry,
  type ProjectMemorySummary,
} from "../../../../lib/api"
import { IconClose } from "../../../shared/app-icons"
import { useEscapeToClose } from "./useEscapeToClose"
import styles from "./MemoryModal.module.css"

/** Same compact relative-time bucketing every other Projects surface
 *  duplicates locally (`ProjectDetailScreen.tsx`, `ArtifactsScreen.tsx`) —
 *  not a shared export in this codebase. */
/** The provenance chip suffix for an agent-promoted entry — derived from
 *  the ACTUAL source conversation kind, not just whether an id is set
 *  (that alone can't tell a group-chat promotion from an individual-chat
 *  one apart, `entry.source_conversation_kind`, batch-resolved
 *  server-side). Empty string when the kind is unresolved, rather than
 *  guessing "group chat". */
function sourceChip(entry: ProjectMemoryEntry): string {
  if (entry.source_conversation_kind === "group") return " · from the group chat"
  if (entry.source_conversation_kind === "individual") return " · from a chat with Sprntly"
  return ""
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ""
  const diffMs = Date.now() - then
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

/** Resolve a human member's display name + role from the project roster
 *  already loaded by the shell — never a second fetch just to label a
 *  provenance line. Null when the author has left the project / roster
 *  hasn't loaded; the line degrades to "Added by a project member"
 *  rather than erroring. */
function resolveAuthor(members: ProjectMember[], userId: string): { name: string; role: string | null } | null {
  const m = members.find((x): x is Extract<ProjectMember, { kind: "human" }> => x.kind === "human" && x.user_id === userId)
  if (!m) return null
  return { name: m.name ?? "A project member", role: m.job_role }
}

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

function PlusIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

function PencilIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
    </svg>
  )
}

// ── Presentational entry row ──

function MemoryEntryRow({
  entry,
  author,
  editing,
  editValue,
  onEditValueChange,
  onStartEdit,
  onSaveEdit,
  onCancelEdit,
  onRemove,
}: {
  entry: ProjectMemoryEntry
  author: { name: string; role: string | null } | null
  editing: boolean
  editValue: string
  onEditValueChange: (v: string) => void
  onStartEdit: () => void
  onSaveEdit: () => void
  onCancelEdit: () => void
  onRemove: () => void
}) {
  const isUser = entry.author_user_id != null
  return (
    <div
      className={`${styles.item} ${isUser ? styles.itemUser : ""}`}
      data-testid={`memory-entry-${entry.id}`}
      data-provenance={isUser ? "user" : "agent"}
    >
      <div className={styles.itemHead}>
        <span className={styles.itemSrc}>
          {isUser ? (
            <span className={styles.srcTagUser} data-testid="memory-src-user">
              Manual
            </span>
          ) : (
            <span className={styles.srcTagAgent} data-testid="memory-src-agent">
              <SparkleIcon />
              Promoted by Sprntly
            </span>
          )}
        </span>
        <span className={styles.itemActions}>
          <button
            type="button"
            className={styles.itemActBtn}
            onClick={onStartEdit}
            title="Edit this memory"
            aria-label={`Edit memory entry ${entry.id}`}
            data-testid={`memory-edit-${entry.id}`}
          >
            <PencilIcon />
          </button>
          <button
            type="button"
            className={`${styles.itemActBtn} ${styles.itemActDanger}`}
            onClick={onRemove}
            title="Remove from project memory"
            aria-label={`Remove memory entry ${entry.id}`}
            data-testid={`memory-remove-${entry.id}`}
          >
            <TrashIcon />
          </button>
        </span>
      </div>
      {editing ? (
        <div className={styles.editRow}>
          <textarea
            className={styles.editInput}
            rows={2}
            value={editValue}
            onChange={(e) => onEditValueChange(e.target.value)}
            aria-label={`Edit text for memory entry ${entry.id}`}
            data-testid={`memory-edit-input-${entry.id}`}
          />
          <div className={styles.editActions}>
            <button type="button" className={styles.editSaveBtn} onClick={onSaveEdit} data-testid={`memory-edit-save-${entry.id}`}>
              Save
            </button>
            <button type="button" className={styles.editCancelBtn} onClick={onCancelEdit} data-testid={`memory-edit-cancel-${entry.id}`}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className={styles.itemText}>{entry.body}</div>
          <div className={`${styles.itemHow} ${isUser ? styles.itemHowUser : ""}`}>
            {isUser
              ? `Added by ${author?.name ?? "a project member"}${author?.role ? ` · ${author.role}` : ""} · updated ${relativeTime(entry.updated_at)}`
              : `Promoted by Sprntly · ${relativeTime(entry.updated_at)}${sourceChip(entry)}`}
          </div>
        </>
      )}
    </div>
  )
}

// ── View ──

type LoadState =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "not_found" }
  | { status: "error" }
  | { status: "ready"; summary: ProjectMemorySummary; entries: ProjectMemoryEntry[] }

export type MemoryModalViewProps = {
  open: boolean
  members: ProjectMember[]
  state: LoadState
  addValue: string
  onAddValueChange: (v: string) => void
  onAdd: () => void
  adding: boolean
  editingId: number | null
  editValue: string
  onEditValueChange: (v: string) => void
  onStartEdit: (entry: ProjectMemoryEntry) => void
  onSaveEdit: () => void
  onCancelEdit: () => void
  onRemove: (entryId: number) => void
  onClose: () => void
  /** Set when the most recent add/edit/delete mutation rejected — cleared on
   *  the next successful mutation or the next attempt (see `MemoryModal`'s
   *  container). `null` renders nothing; the load-time `state` machine
   *  (loading/forbidden/not_found/error) is untouched by this. */
  mutationError: string | null
}

export function MemoryModalView({
  open,
  members,
  state,
  addValue,
  onAddValueChange,
  onAdd,
  adding,
  editingId,
  editValue,
  onEditValueChange,
  onStartEdit,
  onSaveEdit,
  onCancelEdit,
  onRemove,
  onClose,
  mutationError,
}: MemoryModalViewProps) {
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
  // focus actually is (the panel's own onKeyDown below only ever handles
  // Tab-wrap now; see useEscapeToClose.ts for why). Escape always closes
  // the whole modal, including while an entry is mid-edit — matches this
  // handler's existing intent, not a new sub-state-first behavior.
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

  const count = state.status === "ready" ? state.entries.length : 0

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
              Project memory{state.status === "ready" ? ` · ${count} insight${count === 1 ? "" : "s"}` : ""}
            </h2>
            <p className="modal-sub">
              Every chat in this project writes summaries here — and you can add your own durable entries the
              agent must reason from. Never raw transcripts; a ledger you inspect and correct, not a black box.
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
            <>
              {/* ── Read-only synthesized summary — NO edit/remove controls,
                  ever (AC1). */}
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
                    {state.summary.stale ? (
                      <span className={styles.synthRefreshing} data-testid="memory-synth-refreshing">
                        <RefreshIcon />
                        Updating…
                      </span>
                    ) : null}
                  </span>
                </div>
                {state.summary.summary_md ? (
                  <p className={styles.synthBody} data-testid="memory-synth-body">
                    {state.summary.summary_md}
                  </p>
                ) : (
                  <p className={styles.synthPending} data-testid="memory-synth-pending">
                    Synthesis pending — insights will appear here as the team collaborates.
                  </p>
                )}
                <div className={styles.synthFoot}>
                  <RefreshIcon />
                  Synthesized from {state.summary.entry_count} memor{state.summary.entry_count === 1 ? "y" : "ies"} ·
                  updates as memory changes · not directly edited
                </div>
              </div>

              {/* ── Add-memory composer ── */}
              <div className={styles.add}>
                <p className={styles.addLabel}>
                  <PlusIcon />
                  Add a memory the team should reason from
                </p>
                <p className={styles.addHint}>
                  Durable context every chat in this project starts from — a guardrail, a decision, a constraint.
                  Like project instructions, editable any time.
                </p>
                <div className={styles.addRow}>
                  <textarea
                    className={styles.addInput}
                    rows={2}
                    placeholder="e.g. Decision: mobile-first for the quoting flow."
                    aria-label="New memory entry"
                    value={addValue}
                    onChange={(e) => onAddValueChange(e.target.value)}
                    data-testid="memory-add-input"
                  />
                  <button
                    type="button"
                    className={styles.addBtn}
                    onClick={onAdd}
                    disabled={adding || addValue.trim().length === 0}
                    data-testid="memory-add-submit"
                  >
                    {adding ? "Adding…" : "Add"}
                  </button>
                </div>
              </div>

              {/* ── Mutation-failure surface — a failed add/edit/delete,
                  mirroring the `role="alert"` inline-banner pattern
                  `ProjectGroupChat.tsx`'s `gc-error` / `ProjectIndividualChat.tsx`'s
                  `ic-msg-error` already use. Never touches the load-time
                  `state` machine above. ── */}
              {mutationError ? (
                <div className={styles.mutationError} role="alert" data-testid="memory-mutation-error">
                  {mutationError}
                </div>
              ) : null}

              {/* ── Privacy boundary (AC6) ── */}
              <div className={styles.privacy} data-testid="memory-privacy-strip">
                <LockIcon />
                <div>
                  <b>Personal chats outside this project never feed project memory.</b> Only what happens inside
                  the project is shared — your private context stays walled off.
                </div>
              </div>

              {/* ── Discrete entries ── */}
              <div className={styles.list} data-testid="memory-entries-list">
                {state.entries.length === 0 ? (
                  <p className={styles.empty} data-testid="memory-entries-empty">
                    Nothing added yet — the team&apos;s first durable memory starts here.
                  </p>
                ) : (
                  state.entries.map((entry) => (
                    <MemoryEntryRow
                      key={entry.id}
                      entry={entry}
                      author={entry.author_user_id ? resolveAuthor(members, entry.author_user_id) : null}
                      editing={editingId === entry.id}
                      editValue={editValue}
                      onEditValueChange={onEditValueChange}
                      onStartEdit={() => onStartEdit(entry)}
                      onSaveEdit={onSaveEdit}
                      onCancelEdit={onCancelEdit}
                      onRemove={() => onRemove(entry.id)}
                    />
                  ))
                )}
              </div>

              {/* ── Reversibility note (AC6) ── */}
              <div className={styles.note} data-testid="memory-reversibility-note">
                <b>Every memory setting is reversible.</b> Edit or remove any insight, change what Sprntly
                promotes, or turn project memory off — at any time, not just at creation.
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Container: fetch + mutate ──

export function MemoryModal({
  projectId,
  members,
  open,
  onClose,
}: {
  projectId: number | string
  members: ProjectMember[]
  open: boolean
  onClose: () => void
}) {
  const [state, setState] = useState<LoadState>({ status: "loading" })
  const [addValue, setAddValue] = useState("")
  const [adding, setAdding] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editValue, setEditValue] = useState("")
  // Add/edit/delete mutation failures (surfaced — was a silent swallowed
  // `.catch` before this ticket). Cleared at the start of the next mutation
  // attempt and on a successful mutation; the load-time `state` machine
  // above is untouched by this.
  const [mutationError, setMutationError] = useState<string | null>(null)

  const load = useCallback(() => {
    setState({ status: "loading" })
    Promise.all([projectsApi.memorySummary(projectId), projectsApi.memoryEntries(projectId)])
      .then(([summary, entries]) => setState({ status: "ready", summary, entries }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) setState({ status: "forbidden" })
        else if (err instanceof ApiError && err.status === 404) setState({ status: "not_found" })
        else setState({ status: "error" })
      })
  }, [projectId])

  useEffect(() => {
    if (!open) return
    setAddValue("")
    setEditingId(null)
    setMutationError(null)
    load()
  }, [open, load])

  const onAdd = useCallback(() => {
    const body = addValue.trim()
    if (!body || adding) return
    setAdding(true)
    setMutationError(null)
    projectsApi
      .addMemory(projectId, body)
      .then((entry) => {
        setState((s) => (s.status === "ready" ? { ...s, entries: [entry, ...s.entries] } : s))
        setAddValue("")
        setMutationError(null)
      })
      .catch(() => {
        // The composer text is preserved so nothing typed is lost on a
        // failed submit — the alert (not a silent swallow) is the fix.
        setMutationError("Couldn't save that change. Try again.")
      })
      .finally(() => setAdding(false))
  }, [addValue, adding, projectId])

  const onStartEdit = useCallback((entry: ProjectMemoryEntry) => {
    setEditingId(entry.id)
    setEditValue(entry.body)
  }, [])

  const onCancelEdit = useCallback(() => {
    setEditingId(null)
    setEditValue("")
  }, [])

  const onSaveEdit = useCallback(() => {
    if (editingId == null) return
    const body = editValue.trim()
    if (!body) return
    const id = editingId
    setMutationError(null)
    projectsApi
      .patchMemory(projectId, id, body)
      .then((updated) => {
        setState((s) =>
          s.status === "ready" ? { ...s, entries: s.entries.map((e) => (e.id === id ? updated : e)) } : s,
        )
        setEditingId(null)
        setEditValue("")
        setMutationError(null)
      })
      .catch(() => {
        // Leaves the row in edit mode with the attempted text on failure —
        // no silent data loss; the alert is the fix, not a new behavior.
        setMutationError("Couldn't save that change. Try again.")
      })
  }, [editValue, editingId, projectId])

  const onRemove = useCallback(
    (entryId: number) => {
      setMutationError(null)
      projectsApi
        .deleteMemory(projectId, entryId)
        .then(() => {
          setState((s) => (s.status === "ready" ? { ...s, entries: s.entries.filter((e) => e.id !== entryId) } : s))
          setMutationError(null)
        })
        .catch(() => {
          // Row stays visible on failure — nothing removed client-side
          // unless the server confirmed it; the alert is the fix.
          setMutationError("Couldn't save that change. Try again.")
        })
    },
    [projectId],
  )

  return (
    <MemoryModalView
      open={open}
      members={members}
      state={state}
      addValue={addValue}
      onAddValueChange={setAddValue}
      onAdd={onAdd}
      adding={adding}
      editingId={editingId}
      editValue={editValue}
      onEditValueChange={setEditValue}
      onStartEdit={onStartEdit}
      onSaveEdit={onSaveEdit}
      onCancelEdit={onCancelEdit}
      onRemove={onRemove}
      onClose={onClose}
      mutationError={mutationError}
    />
  )
}
