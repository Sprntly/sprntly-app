"use client"

// ── AddArtifactModal — "Add existing artifact" (company-library picker) ──
//
// Reuse-over-invention (`[[feedback_reuse-over-invention-in-ux-builds]]`):
// REUSES `projectsApi` — `artifactsApi.list(activeCompany)` to load the
// company's artifact library, `projectsApi.addArtifact(projectId, type, id)`
// per pick — NO new artifact API. The filter-chip row + badge palette
// reproduce `ArtifactsScreen.tsx`'s `ARTIFACT_FILTERS`/`ARTIFACT_BADGE`
// VERBATIM, the same documented exception `ArtifactsModal.tsx`/
// `ProjectDetailScreen.tsx`'s own local badge copies already take
// (`ArtifactsScreen.tsx` is not a declared Deliverable for this ticket).
// The modal chrome (`modal-overlay`/`modal`/`modal-head`/`modal-foot`/
// `btn btn-ghost`/`btn btn-primary`) reuses the SAME global classes every
// other project modal (`ArtifactsModal`, `CreateProjectModal`) already
// renders with — no bespoke dialog shell.
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { KeyboardEvent as ReactKeyboardEvent } from "react"
import { useCompany } from "../../../../context/CompanyContext"
import { artifactsApi, projectsApi, type ArtifactItem, type ProjectArtifactType } from "../../../../lib/api"
import { IconClose } from "../../../shared/app-icons"
import { useEscapeToClose } from "./useEscapeToClose"
import styles from "./AddArtifactModal.module.css"

type ArtifactFilter = "all" | ProjectArtifactType

/** Verbatim from `ArtifactsScreen.tsx`'s `ARTIFACT_FILTERS` (the documented
 *  local-copy exception). */
const FILTERS: { id: ArtifactFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "report", label: "Reports" },
  { id: "prd", label: "PRDs" },
  { id: "prototype", label: "Prototypes" },
  { id: "evidence", label: "Evidence" },
  { id: "ticket_set", label: "Tickets" },
]

/** Verbatim from `ArtifactsScreen.tsx`'s `ARTIFACT_BADGE` — the app's real
 *  per-type palette, copied (same exception as `ArtifactsModal.tsx`'s
 *  `BADGE`). Lives in this .tsx file, not the .module.css, so the CSS module
 *  itself stays tokens-only (no new palette there). */
const BADGE: Record<ProjectArtifactType, { label: string; bg: string; color: string }> = {
  prd: { label: "PRD", bg: "#DBF1E7", color: "#0E6E49" },
  prototype: { label: "PROTOTYPE", bg: "#DBEAFE", color: "#1E40AF" },
  evidence: { label: "EVIDENCE", bg: "#FEF0E6", color: "#B45309" },
  report: { label: "REPORT", bg: "#EDE9FE", color: "#6D28D9" },
  ticket_set: { label: "TICKETS", bg: "var(--info-soft)", color: "var(--info)" },
}

function artifactKey(a: ArtifactItem): string {
  return `${a.type}-${a.id}`
}

function artifactTitle(a: ArtifactItem): string {
  return a.type === "ticket_set" ? (a.title.trim() || "Tickets from this conversation") : a.title
}

export type AddArtifactModalProps = {
  projectId: number | string
  open: boolean
  /** `${type}-${id}` keys already on the project (`ProjectDetailScreen`
   *  derives this from `state.artifacts`) — those rows render disabled
   *  ("On this project") and cannot be selected/added. */
  existingKeys: Set<string>
  onClose: () => void
  /** Fired once the confirm write(s) settle with AT LEAST one success — the
   *  caller re-fetches the project's artifact list (`refetchArtifacts`). */
  onAdded: () => void
}

type LoadState = "loading" | "ready" | "error"

export function AddArtifactModal({ projectId, open, existingKeys, onClose, onAdded }: AddArtifactModalProps) {
  const { activeCompany } = useCompany()
  const [status, setStatus] = useState<LoadState>("loading")
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [filter, setFilter] = useState<ArtifactFilter>("all")
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const dialogRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<Element | null>(null)

  useEffect(() => {
    if (!open) return
    setStatus("loading")
    setSelected(new Set())
    setQuery("")
    setFilter("all")
    setSaveError(null)
    artifactsApi
      .list(activeCompany)
      .then((rows) => {
        setArtifacts(rows)
        setStatus("ready")
      })
      .catch(() => setStatus("error"))
  }, [open, activeCompany])

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

  // Document-level listener — same reliable Escape-to-close posture every
  // other rail modal in this directory shares (`useEscapeToClose.ts`).
  useEscapeToClose(open, onClose)

  const onKeyDown = useCallback((e: ReactKeyboardEvent) => {
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
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return artifacts.filter((a) => {
      if (filter !== "all" && a.type !== filter) return false
      if (!q) return true
      return artifactTitle(a).toLowerCase().includes(q)
    })
  }, [artifacts, filter, query])

  const toggle = useCallback(
    (a: ArtifactItem) => {
      const key = artifactKey(a)
      if (existingKeys.has(key)) return // already on this project — non-toggleable
      setSelected((prev) => {
        const next = new Set(prev)
        if (next.has(key)) next.delete(key)
        else next.add(key)
        return next
      })
    },
    [existingKeys],
  )

  const onConfirm = useCallback(() => {
    if (selected.size === 0 || saving) return
    setSaving(true)
    setSaveError(null)
    const picks = artifacts.filter((a) => selected.has(artifactKey(a)))
    Promise.allSettled(picks.map((a) => projectsApi.addArtifact(projectId, a.type, a.id))).then(
      (results) => {
        setSaving(false)
        const failedKeys = new Set<string>()
        let anySuccess = false
        results.forEach((r, i) => {
          if (r.status === "rejected") failedKeys.add(artifactKey(picks[i]))
          else anySuccess = true
        })
        if (failedKeys.size === 0) {
          onAdded()
          onClose()
          return
        }
        if (anySuccess) {
          // Some picks landed — refetch so those rows flip to "on this
          // project" (existingKeys) even while the modal stays open on the
          // failed rest.
          onAdded()
        }
        setSelected(failedKeys)
        setSaveError(
          anySuccess
            ? "Some artifacts couldn't be added — the rest were attached."
            : "Couldn't add those artifacts. Try again.",
        )
      },
    )
  }, [selected, saving, artifacts, projectId, onAdded, onClose])

  if (!open) return null

  const counts: Partial<Record<ArtifactFilter, number>> = { all: artifacts.length }
  for (const a of artifacts) counts[a.type] = (counts[a.type] ?? 0) + 1

  return (
    <div className="modal-overlay open" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={dialogRef}
        className={`modal modal-lg ${styles.panel}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-artifact-modal-title"
        onKeyDown={onKeyDown}
        data-testid="add-artifact-modal"
      >
        <div className="modal-head">
          <div className="modal-head-text">
            <h2 className="modal-title" id="add-artifact-modal-title" data-testid="add-artifact-modal-title">
              Add existing artifact
            </h2>
            <p className="modal-sub">From your company&rsquo;s library — pick one or more to attach to this project.</p>
          </div>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
            data-testid="add-artifact-modal-close"
          >
            <IconClose size={16} title="Close" />
          </button>
        </div>

        <div className="modal-body" data-testid="add-artifact-modal-body">
          {status === "loading" ? (
            <div className={styles.stateWrap} data-testid="add-artifact-modal-loading" aria-busy="true">
              Loading…
            </div>
          ) : status === "error" ? (
            <div className={styles.stateWrap} data-testid="add-artifact-modal-error">
              Couldn&rsquo;t load your company&rsquo;s artifacts. Try again.
            </div>
          ) : (
            <>
              <input
                className={styles.search}
                placeholder="Search artifacts…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search artifacts"
                data-testid="add-artifact-search"
              />
              <div className={styles.chips} role="tablist" aria-label="Filter artifacts by type">
                {FILTERS.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    role="tab"
                    aria-selected={filter === f.id}
                    className={`${styles.chip} ${filter === f.id ? styles.chipOn : ""}`}
                    onClick={() => setFilter(f.id)}
                    data-testid={`add-artifact-filter-${f.id}`}
                  >
                    {f.label} <span className={styles.chipN}>{counts[f.id] ?? 0}</span>
                  </button>
                ))}
              </div>

              {filtered.length === 0 ? (
                <div className={styles.stateWrap} data-testid="add-artifact-modal-empty">
                  No artifacts match.
                </div>
              ) : (
                <div className={styles.list} data-testid="add-artifact-modal-list">
                  {filtered.map((a) => {
                    const key = artifactKey(a)
                    const cfg = BADGE[a.type]
                    const isExisting = existingKeys.has(key)
                    const isSelected = selected.has(key)
                    return (
                      <button
                        type="button"
                        key={key}
                        className={`${styles.row} ${isSelected ? styles.rowSel : ""} ${isExisting ? styles.rowExisting : ""}`}
                        onClick={() => toggle(a)}
                        disabled={isExisting}
                        aria-pressed={isSelected}
                        aria-disabled={isExisting}
                        data-testid={`add-artifact-row-${key}`}
                        data-existing={isExisting ? "true" : undefined}
                      >
                        <span className={styles.icon} style={{ background: cfg.bg, color: cfg.color }} aria-hidden="true" />
                        <div className={styles.rowMain}>
                          <div className={styles.rowTitle}>{artifactTitle(a)}</div>
                          <span className={styles.badge} style={{ background: cfg.bg, color: cfg.color }}>
                            {cfg.label}
                          </span>
                        </div>
                        {isExisting ? (
                          <span className={styles.onProject} data-testid={`add-artifact-existing-${key}`}>
                            On this project
                          </span>
                        ) : (
                          <span
                            className={styles.checkbox}
                            aria-hidden="true"
                            data-checked={isSelected ? "true" : undefined}
                          />
                        )}
                      </button>
                    )
                  })}
                </div>
              )}

              {saveError ? (
                <div className={styles.error} role="alert" data-testid="add-artifact-modal-save-error">
                  {saveError}
                </div>
              ) : null}
            </>
          )}
        </div>

        <div className="modal-foot">
          <button type="button" className="btn btn-ghost" onClick={onClose} data-testid="add-artifact-modal-cancel">
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={onConfirm}
            disabled={selected.size === 0 || saving}
            data-testid="add-artifact-modal-confirm"
          >
            {saving
              ? "Adding…"
              : selected.size > 0
                ? `Add ${selected.size} artifact${selected.size === 1 ? "" : "s"}`
                : "Add"}
          </button>
        </div>
      </div>
    </div>
  )
}
