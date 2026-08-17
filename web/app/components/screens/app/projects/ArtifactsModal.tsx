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
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useRouter } from "next/navigation"
import { ApiError, projectsApi, isProjectArtifactType, type ArtifactItem, type ProjectArtifactType } from "../../../../lib/api"
import { IconClose } from "../../../shared/app-icons"
import { useEscapeToClose } from "./useEscapeToClose"
import { AddArtifactPanel } from "./AddArtifactModal"
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

/** `BADGE`'s fallback for a type outside `ProjectArtifactType` —
 *  unreachable today (a project's own artifacts are DB-constrained to the
 *  five keys above), but `ArtifactItem["type"]` is statically wider, so
 *  every `BADGE[a.type]` lookup below goes through `badgeFor` rather than
 *  assuming the narrower set. */
const UNKNOWN_BADGE = { label: "ARTIFACT", bg: "var(--info-soft)", color: "var(--info)" }
function badgeFor(type: ArtifactItem["type"]): { label: string; bg: string; color: string } {
  return isProjectArtifactType(type) ? BADGE[type] : UNKNOWN_BADGE
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
  // custom_artifact can't reach this modal today (project_artifacts'
  // DB CHECK constraint has no such row to attach), but the type is
  // reachable statically via the shared ArtifactItem union — handled here
  // rather than assuming it away.
  if (a.type === "custom_artifact") return [a.source.conversation_title ? `from ${a.source.conversation_title}` : null, rel].filter(Boolean).join(" · ")
  return [`from Brief ${a.source.week_label || ""}`.trim(), rel].filter(Boolean).join(" · ")
}

function artifactTitle(a: ArtifactItem): string {
  if (a.type === "ticket_set") return a.title.trim() || "Tickets from this conversation"
  return a.title
}

/**
 * The app-side deep-link URL that opens this artifact in the REAL drawer,
 * reusing the machinery `(app)/hooks/useArtifactUrlSync.ts` already mounts in
 * AppShell (`?prd=`/`?evidence=`) and the pre-existing `/prototype` route.
 * We route to `/` (not `/projects?…`): the ContentPanel/drawer only mounts on
 * the workspace root, and `useArtifactUrlSync`'s `openPrdTab` navigates to `/`
 * unconditionally anyway — so landing there directly is the honest target
 * (same posture as ArtifactsScreen's own `openArtifact`).
 *
 * Returns null for types the hook has NO url-param entry point for:
 *   • report     — opens only via NavigationContext.openReportTab / reportFocus
 *                  (no `?report=` param exists).
 *   • ticket_set — a STANDALONE set (no PRD behind it); the hook's `?ticket=`
 *                  param is a PRD-backed `prd-{id}-…` key, which a library
 *                  ticket_set has no way to form (only a ticket_set_id).
 * Those are surfaced with a disabled affordance rather than a fabricated link.
 */
function artifactHref(a: ArtifactItem): string | null {
  switch (a.type) {
    case "prd":
      return `/?prd=${a.open.prd_id}`
    case "evidence":
      return `/?evidence=${a.open.evidence_id}`
    case "prototype":
      return a.open.prd_id != null ? `/prototype?prd=${a.open.prd_id}` : null
    default:
      return null
  }
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

// ── Presentational list ──

export type ArtifactsModalViewProps = {
  open: boolean
  status: "loading" | "forbidden" | "not_found" | "error" | "ready"
  artifacts: ArtifactItem[]
  filter: ArtifactFilter
  onFilterChange: (f: ArtifactFilter) => void
  /** Opens an artifact in the drawer beside the chat AND closes this modal
   *  (wired to a row click — there is no in-modal preview). */
  onOpen: (a: ArtifactItem) => void
  onClose: () => void
  /** Which internal view the single modal shell is showing. "add" swaps the
   *  body/foot to the folded `AddArtifactPanel` at the SAME size — no
   *  close/reopen. */
  view: "list" | "add"
  /** List view → switch to the folded Add-artifact view. */
  onShowAdd: () => void
  /** Add view → "← Back" to the list view (within the same modal). */
  onBackToList: () => void
  /** The folded `AddArtifactPanel` node (its own `modal-body` + `modal-foot`),
   *  rendered by the container so this view stays presentational. */
  addPanel: ReactNode
}

export function ArtifactsModalView({
  open,
  status,
  artifacts,
  filter,
  onFilterChange,
  onOpen,
  onClose,
  view,
  onShowAdd,
  onBackToList,
  addPanel,
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
  // A custom_artifact row can't reach this modal today (see badgeFor's own
  // doc); skipped here rather than counted under a filter chip that has no
  // entry for it.
  for (const a of artifacts) {
    if (!isProjectArtifactType(a.type)) continue
    counts[a.type] = (counts[a.type] ?? 0) + 1
  }
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
        {view === "add" ? (
          <div className="modal-head">
            <div className={styles.addHead}>
              {/* Icon-only back control — reuses the exact back-arrow glyph
                  the top-bar "← All projects" button uses (BackArrowIcon in
                  ProjectDetailScreen.tsx) for cross-product consistency. */}
              <button
                type="button"
                className={styles.backBtn}
                onClick={onBackToList}
                aria-label="Back to artifacts"
                title="Back to artifacts"
                data-testid="artifacts-modal-back"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M15 6l-6 6 6 6" />
                </svg>
              </button>
              <div className="modal-head-text">
                <h2 className="modal-title" id="artifacts-modal-title" data-testid="artifacts-modal-title">
                  Add existing artifact
                </h2>
                <p className="modal-sub">From your company&rsquo;s library — pick one or more to attach to this project.</p>
              </div>
            </div>
            <button type="button" className="modal-close" onClick={onClose} aria-label="Close" data-testid="artifacts-modal-close">
              <IconClose size={16} title="Close" />
            </button>
          </div>
        ) : (
          <div className="modal-head">
            <div className="modal-head-text">
              <h2 className="modal-title" id="artifacts-modal-title" data-testid="artifacts-modal-title">
                Artifacts <span className={styles.count}>{artifacts.length}</span>
              </h2>
              <p className="modal-sub">Everything this project has produced — click a row to open it beside the chat.</p>
            </div>
            <div className={styles.headActions}>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={onShowAdd}
                data-testid="artifacts-modal-add-existing"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
                Add existing artifact
              </button>
              <button type="button" className="modal-close" onClick={onClose} aria-label="Close" data-testid="artifacts-modal-close">
                <IconClose size={16} title="Close" />
              </button>
            </div>
          </div>
        )}

        {view === "add" ? (
          addPanel
        ) : (
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
                // Clicking a row opens the artifact in the drawer beside the
                // chat (via `onOpen`) AND closes this modal in one step — no
                // in-modal preview pane.
                <div className={styles.list} data-testid="artifacts-modal-list">
                  {filtered.map((a) => {
                    const cfg = badgeFor(a.type)
                    return (
                      <div
                        key={artifactKey(a)}
                        role="button"
                        tabIndex={0}
                        className={styles.row}
                        data-testid={`artifacts-row-${artifactKey(a)}`}
                        data-artifact-type={a.type}
                        onClick={() => onOpen(a)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") onOpen(a)
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
              )}
            </>
          )}
        </div>
        )}
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
  onOpenInPlace,
  onArtifactsChanged,
}: {
  projectId: number | string
  open: boolean
  initialFilter?: ProjectArtifactType
  onClose: () => void
  /** AD-HOC: open the artifact IN-PLACE beside the chat (no route change). When
   *  provided, this supersedes the old deep-link `router.push` — the Projects
   *  screen mounts a self-contained `ProjectArtifactDrawer` that renders the
   *  real body. Falls back to the deep-link navigation when absent. */
  onOpenInPlace?: (a: ArtifactItem) => void
  /** Fired after the folded Add-artifact view attaches artifact(s) — the
   *  parent re-fetches so the top-bar "Artifacts(N)" count updates. The modal
   *  refreshes its OWN list internally as well. */
  onArtifactsChanged?: () => void
}) {
  const router = useRouter()
  const [state, setState] = useState<LoadState>({ status: "loading" })
  const [filter, setFilter] = useState<ArtifactFilter>(initialFilter ?? "all")
  // Which internal view the single modal shell shows. Always resets to "list"
  // when the modal (re)opens.
  const [view, setView] = useState<"list" | "add">("list")

  // A row click opens the artifact in the drawer beside the chat AND closes
  // this modal in one step. Prefer the in-place drawer (no route change); only
  // when no in-place handler is wired do we fall back to the app's deep-link
  // viewer (navigates to `/`), and then only for types with a url-param entry.
  const handleOpen = useCallback(
    (a: ArtifactItem) => {
      if (onOpenInPlace) {
        onOpenInPlace(a)
        onClose()
        return
      }
      const href = artifactHref(a)
      if (!href) return
      onClose()
      router.push(href)
    },
    [router, onClose, onOpenInPlace],
  )

  // Reload ONLY the project's artifact list — used on open and after the
  // folded add-view attaches rows, so the list view reflects the addition
  // without a full modal re-mount.
  const reload = useCallback(() => {
    setState({ status: "loading" })
    projectsApi
      .artifacts(projectId)
      .then((artifacts) => setState({ status: "ready", artifacts }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) setState({ status: "forbidden" })
        else if (err instanceof ApiError && err.status === 404) setState({ status: "not_found" })
        else setState({ status: "error" })
      })
  }, [projectId])

  useEffect(() => {
    if (!open) return
    setFilter(initialFilter ?? "all")
    setView("list")
    reload()
    // `initialFilter` intentionally re-applies only when the modal (re)opens
    // — a change to the rail's last-clicked type while already open must
    // not yank the filter out from under someone browsing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectId])

  const artifacts = state.status === "ready" ? state.artifacts : []
  // Rows already on this project — derived from the modal's OWN fetched list,
  // so the folded add-view marks them "On this project" with no extra prop.
  const existingKeys = useMemo(() => new Set(artifacts.map((a) => `${a.type}-${a.id}`)), [artifacts])

  // Refetch own list + bubble to the parent (top-bar count) after an add.
  const handleAdded = useCallback(() => {
    reload()
    onArtifactsChanged?.()
  }, [reload, onArtifactsChanged])

  return (
    <ArtifactsModalView
      open={open}
      status={state.status}
      artifacts={artifacts}
      filter={filter}
      onFilterChange={setFilter}
      onOpen={handleOpen}
      onClose={onClose}
      view={view}
      onShowAdd={() => setView("add")}
      onBackToList={() => setView("list")}
      addPanel={
        <AddArtifactPanel
          projectId={projectId}
          active={open && view === "add"}
          existingKeys={existingKeys}
          onAdded={handleAdded}
          onDone={() => setView("list")}
          onCancel={() => setView("list")}
        />
      }
    />
  )
}
