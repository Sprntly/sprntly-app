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
import { useRouter } from "next/navigation"
import { ApiError, projectsApi, isProjectArtifactType, type ArtifactItem, type ProjectArtifactType } from "../../../../lib/api"
import { IconClose } from "../../../shared/app-icons"
import { useEscapeToClose } from "./useEscapeToClose"
import styles from "./ArtifactsModal.module.css"

// Local extension only — NOT a widen of `ProjectArtifactType` in api.ts (that
// stays narrow; upload is the only path a `custom_artifact` reaches this
// modal through, and it goes through `projectsApi.uploadDocument`, never
// `addArtifact`).
type ArtifactFilter = "all" | ProjectArtifactType | "custom_artifact"

/** Verbatim order from `ArtifactsScreen.tsx`'s `ARTIFACT_FILTERS` — the
 *  app's real filter set (Reports included; the "Tickets" qualifier is the
 *  same one that file uses) — plus a "Documents" chip for uploaded
 *  `custom_artifact` rows. */
const FILTERS: { id: ArtifactFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "report", label: "Reports" },
  { id: "prd", label: "PRDs" },
  { id: "prototype", label: "Prototypes" },
  { id: "evidence", label: "Evidence" },
  { id: "ticket_set", label: "Tickets" },
  { id: "custom_artifact", label: "Documents" },
]

/** The upload strip's accepted formats — copied verbatim from
 *  `shared/ChatComposer.tsx`'s file input, the same extraction pipeline
 *  (`app.ingest.convert`) reads. */
const UPLOAD_ACCEPT = ".txt,.md,.csv,.json,.pdf,.doc,.docx,.pptx"

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

/** `BADGE`'s fallback for a type outside `ProjectArtifactType` — reachable
 *  now for exactly one case: an uploaded document (`custom_artifact`),
 *  special-cased to a neutral DOCUMENT badge below `isProjectArtifactType`
 *  ever sees it. Any OTHER outside type stays a generic fallback. */
const UNKNOWN_BADGE = { label: "ARTIFACT", bg: "var(--info-soft)", color: "var(--info)" }
/** Neutral grey — `UNKNOWN_BADGE`'s own tone, since no dedicated
 *  `--surface-4`/`--ink-2` document token exists in `globals.css`. */
const DOCUMENT_BADGE = { label: "DOCUMENT", bg: "var(--info-soft)", color: "var(--info)" }
function badgeFor(type: ArtifactItem["type"]): { label: string; bg: string; color: string } {
  if (type === "custom_artifact") return DOCUMENT_BADGE
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
  // custom_artifact DOES reach this modal now — an uploaded document,
  // attached via `projectsApi.uploadDocument` (the migration widening
  // project_artifacts' CHECK is what makes the attach write succeed).
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

// ── Canvas (Preview/Spec, presentational) ──

function ArtifactCanvas({ artifact, onOpen }: { artifact: ArtifactItem; onOpen: (a: ArtifactItem) => void }) {
  const [tab, setTab] = useState<"preview" | "spec">("preview")
  const cfg = badgeFor(artifact.type)
  // In-place open handles every type except a standalone ticket set (no single
  // document body to render beside the chat). The old `artifactHref` gate only
  // covered prd/evidence/prototype; the drawer additionally renders reports.
  const openable = artifact.type !== "ticket_set"
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
        <button
          type="button"
          className={styles.openBtn}
          onClick={() => openable && onOpen(artifact)}
          disabled={!openable}
          title={openable ? "Open the full artifact beside the chat" : "Ticket sets open from the Tickets workspace"}
          data-testid="artifact-canvas-open"
        >
          Open
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M7 17 17 7M8 7h9v9" />
          </svg>
        </button>
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

/** The upload strip's tri-state — idle, in flight, or a failed attempt
 *  (mapped to inline copy by the container, keyed off the endpoint's 4xx
 *  status). Owned by the CONTAINER (`ArtifactsModal`), which drives the
 *  actual `projectsApi.uploadDocument` call; this View only renders it. */
export type ArtifactUploadState =
  | { status: "idle" }
  | { status: "uploading"; filename: string }
  | { status: "error"; filename: string; message: string }

export type ArtifactsModalViewProps = {
  open: boolean
  status: "loading" | "forbidden" | "not_found" | "error" | "ready"
  artifacts: ArtifactItem[]
  filter: ArtifactFilter
  onFilterChange: (f: ArtifactFilter) => void
  selected: ArtifactItem | null
  onSelect: (a: ArtifactItem) => void
  onOpen: (a: ArtifactItem) => void
  onClose: () => void
  /** Opens the "Add existing artifact" company-library picker
   *  (`AddArtifactModal`) — the trigger used to live in the top bar; this
   *  ticket relocates it into a 2-item `+ Add ▾` menu, same handler
   *  underneath. */
  onAddExisting: () => void
  /** Upload strip state — see `ArtifactUploadState`. */
  upload: ArtifactUploadState
  /** A file was picked off the "Upload document" menu item's file input. */
  onSelectFile: (file: File) => void
  /** Dismiss the processing row / error state without waiting on the
   *  in-flight request — a soft cancel (the request itself is not
   *  aborted; its result is simply ignored when it resolves, the same
   *  ignore-stale-response pattern this file's own fetch-on-open effect
   *  already uses via its `cancelled` flag). */
  onCancelUpload: () => void
}

export function ArtifactsModalView({
  open,
  status,
  artifacts,
  filter,
  onFilterChange,
  selected,
  onSelect,
  onOpen,
  onClose,
  onAddExisting,
  upload,
  onSelectFile,
  onCancelUpload,
}: ArtifactsModalViewProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<Element | null>(null)
  const [addMenuOpen, setAddMenuOpen] = useState(false)
  const addWrapRef = useRef<HTMLDivElement>(null)

  // Outside-click closes just the menu, not the whole modal (the modal's
  // own overlay-click-to-close only fires for a literal backdrop click).
  useEffect(() => {
    if (!addMenuOpen) return
    const onDocMouseDown = (e: MouseEvent) => {
      if (addWrapRef.current && !addWrapRef.current.contains(e.target as Node)) {
        setAddMenuOpen(false)
      }
    }
    document.addEventListener("mousedown", onDocMouseDown)
    return () => document.removeEventListener("mousedown", onDocMouseDown)
  }, [addMenuOpen])

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
  // custom_artifact DOES reach this modal now (an uploaded document) — its
  // own dedicated increment below, since `isProjectArtifactType` correctly
  // excludes it from the five project-typed keys.
  for (const a of artifacts) {
    if (a.type === "custom_artifact") {
      counts.custom_artifact = (counts.custom_artifact ?? 0) + 1
      continue
    }
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
        <div className="modal-head">
          <div className="modal-head-text">
            <h2 className="modal-title" id="artifacts-modal-title" data-testid="artifacts-modal-title">
              Artifacts <span className={styles.count}>{artifacts.length}</span>
            </h2>
            <p className="modal-sub">Everything this project has produced — click a row to preview it inline.</p>
          </div>
          <div className={styles.headActions}>
            <div className={styles.addWrap} ref={addWrapRef}>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setAddMenuOpen((o) => !o)}
                aria-haspopup="menu"
                aria-expanded={addMenuOpen}
                data-testid="artifacts-modal-add-menu-trigger"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
                Add
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </button>
              {addMenuOpen ? (
                <div
                  className={styles.addMenu}
                  role="menu"
                  aria-label="Add to project"
                  data-testid="artifacts-modal-add-menu"
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      // Close JUST the menu — stop the event before it
                      // reaches the document-level Escape listener the
                      // WHOLE modal uses (useEscapeToClose above).
                      e.stopPropagation()
                      setAddMenuOpen(false)
                    }
                  }}
                >
                  <label className={styles.addMenuItem} data-testid="artifacts-modal-upload-document">
                    Upload document
                    <input
                      type="file"
                      accept={UPLOAD_ACCEPT}
                      style={{ display: "none" }}
                      data-testid="artifacts-modal-file-input"
                      onChange={(e) => {
                        const file = e.target.files?.[0]
                        setAddMenuOpen(false)
                        e.target.value = ""
                        if (file) onSelectFile(file)
                      }}
                    />
                  </label>
                  <button
                    type="button"
                    role="menuitem"
                    className={styles.addMenuItem}
                    onClick={() => {
                      setAddMenuOpen(false)
                      onAddExisting()
                    }}
                    data-testid="artifacts-modal-add-existing"
                  >
                    Add existing artifact
                  </button>
                </div>
              ) : null}
            </div>
            <button type="button" className="modal-close" onClick={onClose} aria-label="Close" data-testid="artifacts-modal-close">
              <IconClose size={16} title="Close" />
            </button>
          </div>
        </div>

        {upload.status === "uploading" ? (
          <div className={styles.uploadRow} data-testid="artifacts-modal-upload-processing">
            <span>Adding {upload.filename} now · Reading &amp; indexing for Sprntly</span>
            <button type="button" className={styles.uploadCancel} onClick={onCancelUpload} data-testid="artifacts-modal-upload-cancel">
              Cancel
            </button>
          </div>
        ) : upload.status === "error" ? (
          <div className={styles.uploadRow} role="alert" data-testid="artifacts-modal-upload-error">
            <span>{upload.message}</span>
            <button type="button" className={styles.uploadCancel} onClick={onCancelUpload} data-testid="artifacts-modal-upload-dismiss">
              Dismiss
            </button>
          </div>
        ) : null}

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
                      const cfg = badgeFor(a.type)
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
                              {a.type === "custom_artifact" ? (
                                <span className={styles.inContextChip} data-testid={`artifacts-row-in-context-${artifactKey(a)}`}>
                                  Sprntly can reference this
                                </span>
                              ) : null}
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  {selected ? <ArtifactCanvas artifact={selected} onOpen={onOpen} /> : null}
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
  onOpenInPlace,
  onAddExisting,
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
  /** Opens the "Add existing artifact" company-library picker — the caller
   *  owns the actual modal swap (a mutually-exclusive `railModal` state on
   *  `ProjectDetailScreen`), this container just forwards the trigger. */
  onAddExisting: () => void
}) {
  const router = useRouter()
  const [state, setState] = useState<LoadState>({ status: "loading" })
  const [filter, setFilter] = useState<ArtifactFilter>(initialFilter ?? "all")
  const [selected, setSelected] = useState<ArtifactItem | null>(null)
  const [upload, setUpload] = useState<ArtifactUploadState>({ status: "idle" })
  // Bumped on every new upload/cancel — a resolving fetch checks its own
  // captured token against this ref before applying state, so a cancelled
  // or superseded upload's late result is silently ignored (no real abort;
  // same ignore-stale-response shape the fetch-on-open effect's own
  // `cancelled` flag uses).
  const uploadTokenRef = useRef(0)

  const handleSelectFile = useCallback(
    (file: File) => {
      const token = ++uploadTokenRef.current
      setUpload({ status: "uploading", filename: file.name })
      projectsApi
        .uploadDocument(projectId, file)
        .then((item) => {
          if (uploadTokenRef.current !== token) return
          setState((s) => (s.status === "ready" ? { status: "ready", artifacts: [item, ...s.artifacts] } : s))
          setUpload({ status: "idle" })
        })
        .catch((err: unknown) => {
          if (uploadTokenRef.current !== token) return
          const status = err instanceof ApiError ? err.status : 0
          const message =
            status === 400
              ? "That file is empty."
              : status === 413
                ? "That file is too large (max 25 MB)."
                : status === 422
                  ? "Couldn't read any text — scanned/image-only PDFs and legacy .ppt aren't supported. Export to PDF or .pptx."
                  : "Couldn't upload that file. Try again."
          setUpload({ status: "error", filename: file.name, message })
        })
    },
    [projectId],
  )

  const handleCancelUpload = useCallback(() => {
    uploadTokenRef.current += 1
    setUpload({ status: "idle" })
  }, [])

  // Prefer the in-place drawer (no route change). Only when no in-place handler
  // is wired do we fall back to the app's deep-link viewer (navigates to `/`),
  // and then only for types with a url-param entry point.
  const handleOpen = useCallback(
    (a: ArtifactItem) => {
      if (onOpenInPlace) {
        onOpenInPlace(a)
        return
      }
      const href = artifactHref(a)
      if (!href) return
      onClose()
      router.push(href)
    },
    [router, onClose, onOpenInPlace],
  )

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
      onOpen={handleOpen}
      onClose={onClose}
      onAddExisting={onAddExisting}
      upload={upload}
      onSelectFile={handleSelectFile}
      onCancelUpload={handleCancelUpload}
    />
  )
}
