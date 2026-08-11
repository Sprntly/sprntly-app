"use client"

// ── ArtifactsModal — the artifacts library, app-faithful, in a modal ──
//
// Reuse-over-invention (`[[feedback_reuse-over-invention-in-ux-builds]]`):
// the filter-chip row + single-column row list below reproduce
// `ArtifactsScreen.tsx`'s `ARTIFACT_FILTERS` order and `ARTIFACT_BADGE`
// palette VERBATIM — the same exception `ProjectDetailScreen.tsx`'s own
// `TYPE_BADGE` already takes (`ArtifactsScreen.tsx` is not a declared
// Deliverable for this ticket, so the markup/palette is duplicated locally
// rather than imported, matching that file's own precedent). This is NOT a
// second badge palette — it is the app's one real palette, copied.
//
// Scope boundary (P1): the inline Preview/Spec canvas is PRESENTATIONAL —
// `ArtifactItem` carries no version-history or rendered-preview payload
// yet, so the canvas shows the selected artifact's real title/type/recency
// plus a static two-chip version affordance (build spec §8 Phase 2+ for a
// real version history endpoint). It never fabricates document content.
import { useCallback, useEffect, useRef, useState } from "react"
import { ApiError, projectsApi, type ArtifactItem, type ProjectArtifactType } from "../../../../lib/api"
import { IconClose } from "../../../shared/app-icons"
import { useEscapeToClose } from "./useEscapeToClose"
import styles from "./ArtifactsModal.module.css"

type ArtifactFilter = "all" | ProjectArtifactType

/** Verbatim order from `ArtifactsScreen.tsx`'s `ARTIFACT_FILTERS` — the
 *  app's real filter set (Reports included; the "Tickets" qualifier is the
 *  same one that file uses). */
const FILTERS: { id: ArtifactFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "report", label: "Reports" },
  { id: "prd", label: "PRDs" },
  { id: "prototype", label: "Prototypes" },
  { id: "evidence", label: "Evidence" },
  { id: "ticket_set", label: "Tickets" },
]

/** Verbatim from `ArtifactsScreen.tsx`'s `ARTIFACT_BADGE` — the app's real
 *  per-type palette (same duplication precedent as
 *  `ProjectDetailScreen.tsx`'s `TYPE_BADGE`). */
const BADGE: Record<ProjectArtifactType, { label: string; bg: string; color: string }> = {
  prd: { label: "PRD", bg: "#DBF1E7", color: "#0E6E49" },
  prototype: { label: "PROTOTYPE", bg: "#DBEAFE", color: "#1E40AF" },
  evidence: { label: "EVIDENCE", bg: "#FEF0E6", color: "#B45309" },
  report: { label: "REPORT", bg: "#EDE9FE", color: "#6D28D9" },
  ticket_set: { label: "TICKETS", bg: "var(--info-soft)", color: "var(--info)" },
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

function artifactKey(a: ArtifactItem): string {
  return `${a.type}-${a.id}`
}

/** The row's meta/source clause — a compact version of
 *  `ArtifactsScreen.tsx`'s `artifactSourceLine`, sufficient for a modal row
 *  (full per-type provenance strings are that file's job, not duplicated
 *  here beyond what's needed to read the row). */
function sourceLine(a: ArtifactItem): string {
  const rel = a.created_at ? relativeTime(a.created_at) : ""
  if (a.type === "prototype") return [`from PRD ${a.source.prd_title}`, rel].filter(Boolean).join(" · ")
  if (a.type === "ticket_set") {
    const count = a.status === "generating" ? "Writing tickets" : `${a.ticket_count} tickets`
    return [count, rel].filter(Boolean).join(" · ")
  }
  if (a.type === "report") return [a.source.conversation_title ? `from ${a.source.conversation_title}` : null, rel].filter(Boolean).join(" · ")
  return [`from Brief ${a.source.week_label || ""}`.trim(), rel].filter(Boolean).join(" · ")
}

function artifactTitle(a: ArtifactItem): string {
  if (a.type === "ticket_set") return a.title.trim() || "Tickets from this conversation"
  return a.title
}

function FolderGlyph({ cfg }: { cfg: { bg: string; color: string } }) {
  return (
    <span className={styles.icon} style={{ background: cfg.bg, color: cfg.color }} aria-hidden="true">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      </svg>
    </span>
  )
}

// ── Canvas (Preview/Spec, presentational) ──

function ArtifactCanvas({ artifact }: { artifact: ArtifactItem }) {
  const [tab, setTab] = useState<"preview" | "spec">("preview")
  const cfg = BADGE[artifact.type]
  return (
    <div className={styles.canvas} data-testid="artifact-canvas">
      <div className={styles.canvasBar}>
        <div className={styles.canvasTabs} role="tablist" aria-label="Preview or spec">
          <button
            type="button"
            className={`${styles.canvasTab} ${tab === "preview" ? styles.canvasTabOn : ""}`}
            role="tab"
            aria-selected={tab === "preview"}
            onClick={() => setTab("preview")}
            data-testid="artifact-canvas-tab-preview"
          >
            Preview
          </button>
          <button
            type="button"
            className={`${styles.canvasTab} ${tab === "spec" ? styles.canvasTabOn : ""}`}
            role="tab"
            aria-selected={tab === "spec"}
            onClick={() => setTab("spec")}
            data-testid="artifact-canvas-tab-spec"
          >
            Spec
          </button>
        </div>
        {/* Version history is a Phase 2+ endpoint (build spec §8) — these
            two chips are a presentational placeholder, never fabricated
            document content, so the canvas still satisfies "recent
            versions at a glance" without claiming real history. */}
        <div className={styles.canvasVers} title="Version history" data-testid="artifact-canvas-versions">
          <span className={`${styles.vchip} ${styles.vchipOn}`}>v2</span>
          <span className={styles.vchip}>v1</span>
        </div>
      </div>
      <div className={styles.canvasTitle}>
        <span className={styles.badge} style={{ background: cfg.bg, color: cfg.color }}>
          {cfg.label}
        </span>
        <b>{artifactTitle(artifact)}</b>
        {artifact.created_at ? <span> · updated {relativeTime(artifact.created_at)}</span> : null}
      </div>
      <div className={styles.canvasBody}>
        {tab === "preview" ? (
          <div className={styles.pane} data-testid="artifact-canvas-preview">
            <p>{sourceLine(artifact) || "No preview details available yet."}</p>
          </div>
        ) : (
          <div className={styles.pane} data-testid="artifact-canvas-spec">
            <p>{artifactTitle(artifact)}</p>
            <p className={styles.paneMuted}>Open the full artifact to see its complete spec.</p>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Presentational list + canvas ──

export type ArtifactsModalViewProps = {
  open: boolean
  status: "loading" | "forbidden" | "not_found" | "error" | "ready"
  artifacts: ArtifactItem[]
  filter: ArtifactFilter
  onFilterChange: (f: ArtifactFilter) => void
  selected: ArtifactItem | null
  onSelect: (a: ArtifactItem) => void
  onClose: () => void
}

export function ArtifactsModalView({
  open,
  status,
  artifacts,
  filter,
  onFilterChange,
  selected,
  onSelect,
  onClose,
}: ArtifactsModalViewProps) {
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
  // Tab-wrap now; see useEscapeToClose.ts for why).
  useEscapeToClose(open, onClose)

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
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
    [],
  )

  if (!open) return null

  const counts: Partial<Record<ArtifactFilter, number>> = { all: artifacts.length }
  for (const a of artifacts) counts[a.type] = (counts[a.type] ?? 0) + 1
  const filtered = filter === "all" ? artifacts : artifacts.filter((a) => a.type === filter)

  return (
    <div className="modal-overlay open" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={dialogRef}
        className={`modal modal-lg ${styles.panel}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="artifacts-modal-title"
        onKeyDown={onKeyDown}
      >
        <div className="modal-head">
          <div className="modal-head-text">
            <h2 className="modal-title" id="artifacts-modal-title" data-testid="artifacts-modal-title">
              Artifacts <span className={styles.count}>{artifacts.length}</span>
            </h2>
            <p className="modal-sub">Everything this project has produced — click a row to preview it inline.</p>
          </div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close" data-testid="artifacts-modal-close">
            <IconClose size={16} title="Close" />
          </button>
        </div>

        <div className="modal-body" data-testid="artifacts-modal-body">
          {status === "loading" ? (
            <div className={styles.stateWrap} data-testid="artifacts-modal-loading" aria-busy="true">
              Loading…
            </div>
          ) : status === "forbidden" ? (
            <div className={styles.stateWrap} data-testid="artifacts-modal-forbidden">
              You&apos;re not a member of this project, so its artifacts aren&apos;t visible to you.
            </div>
          ) : status === "not_found" ? (
            <div className={styles.stateWrap} data-testid="artifacts-modal-not-found">
              This project&apos;s artifacts couldn&apos;t be found.
            </div>
          ) : status === "error" ? (
            <div className={styles.stateWrap} data-testid="artifacts-modal-error">
              Couldn&apos;t load artifacts. Try again.
            </div>
          ) : (
            <>
              <div className={styles.chips} role="tablist" aria-label="Filter artifacts by type">
                {FILTERS.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    role="tab"
                    aria-selected={filter === f.id}
                    className={`${styles.chip} ${filter === f.id ? styles.chipOn : ""}`}
                    onClick={() => onFilterChange(f.id)}
                    data-testid={`artifacts-filter-${f.id}`}
                  >
                    {f.label} <span className={styles.chipN}>{counts[f.id] ?? 0}</span>
                  </button>
                ))}
              </div>

              {filtered.length === 0 ? (
                <div className={styles.stateWrap} data-testid="artifacts-modal-empty">
                  No artifacts yet — items this project produces will show up here.
                </div>
              ) : (
                <div className={styles.canvasGrid}>
                  <div className={styles.list} data-testid="artifacts-modal-list">
                    {filtered.map((a) => {
                      const cfg = BADGE[a.type]
                      const isSel = selected != null && artifactKey(selected) === artifactKey(a)
                      return (
                        <div
                          key={artifactKey(a)}
                          role="button"
                          tabIndex={0}
                          className={`${styles.row} ${isSel ? styles.rowSel : ""}`}
                          data-testid={`artifacts-row-${artifactKey(a)}`}
                          data-artifact-type={a.type}
                          data-active={isSel ? "true" : undefined}
                          aria-current={isSel ? "true" : undefined}
                          onClick={() => onSelect(a)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") onSelect(a)
                          }}
                        >
                          <FolderGlyph cfg={cfg} />
                          <div className={styles.rowMain}>
                            <div className={styles.rowTitle}>{artifactTitle(a)}</div>
                            <div className={styles.rowMeta}>
                              <span className={styles.badge} style={{ background: cfg.bg, color: cfg.color }}>
                                {cfg.label}
                              </span>
                              <span className={styles.rowSrc}>{sourceLine(a)}</span>
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  {selected ? <ArtifactCanvas artifact={selected} /> : null}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Container: fetch on open ──

type LoadState =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "not_found" }
  | { status: "error" }
  | { status: "ready"; artifacts: ArtifactItem[] }

export function ArtifactsModal({
  projectId,
  open,
  initialFilter,
  onClose,
}: {
  projectId: number | string
  open: boolean
  initialFilter?: ProjectArtifactType
  onClose: () => void
}) {
  const [state, setState] = useState<LoadState>({ status: "loading" })
  const [filter, setFilter] = useState<ArtifactFilter>(initialFilter ?? "all")
  const [selected, setSelected] = useState<ArtifactItem | null>(null)

  useEffect(() => {
    if (!open) return
    setFilter(initialFilter ?? "all")
    setSelected(null)
    setState({ status: "loading" })
    projectsApi
      .artifacts(projectId)
      .then((artifacts) => setState({ status: "ready", artifacts }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) setState({ status: "forbidden" })
        else if (err instanceof ApiError && err.status === 404) setState({ status: "not_found" })
        else setState({ status: "error" })
      })
    // `initialFilter` intentionally re-applies only when the modal (re)opens
    // — a change to the rail's last-clicked type while already open must
    // not yank the filter out from under someone browsing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectId])

  return (
    <ArtifactsModalView
      open={open}
      status={state.status}
      artifacts={state.status === "ready" ? state.artifacts : []}
      filter={filter}
      onFilterChange={setFilter}
      selected={selected}
      onSelect={setSelected}
      onClose={onClose}
    />
  )
}
