"use client"

// ── ProjectArtifactsDrawer — the project's artifacts view, in a non-blocking
//    side drawer, with an Add-from-library picker as a nested secondary view ──
//
// design_ref: design/specs/add-artifact-drawer-spec.md
// mockup:     design/mockups/add-artifact-drawer/add-artifact-drawer.html
//
// PRIMARY view = the project's existing artifacts (`projectsApi.artifacts`),
// each openable, with per-type filter chips + a count. An "Add artifact"
// header control swaps the drawer IN PLACE to the reused `AddArtifactPanel`
// (search / type chips / multi-select / "Add N" / partial-fail), with a back
// arrow home — the exact list ⇆ add swap `ArtifactsModal` does, re-housed in a
// drawer shell.
//
// Reuse-over-invention (`[[feedback_reuse-over-invention-in-ux-builds]]`):
//   • Add view      → `AddArtifactPanel` (from AddArtifactModal.tsx), wholesale.
//   • Per-type icon  → the `/artifacts` library's `ArtifactTypeIcon` glyphs,
//                      copied verbatim so the drawer's rows match that screen.
//   • Badge palette  → `ARTIFACT_BADGE` from `ArtifactsScreen.tsx`, the app's
//                      one real per-type palette — the SAME documented local-copy
//                      exception `ArtifactsModal`/`AddArtifactModal` already take
//                      (those files are not this component's Deliverable, so the
//                      palette/glyphs are duplicated locally rather than imported).
//   • Row helpers    → `sourceLine`/`artifactTitle`/`artifactHref` copied from
//                      `ArtifactsModal.tsx` (same reason).
//
// Posture (non-blocking region, NOT a modal): `role="region"` + `aria-label`,
// NO `aria-modal`, NO scrim, NO focus trap — the chat to the drawer's left
// stays interactive. Escape closes via `useEscapeToClose`.
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import {
  ApiError,
  projectsApi,
  isProjectArtifactType,
  type ArtifactItem,
  type ProjectArtifactType,
} from "../../../../lib/api"
import { documentPath } from "../../../../(app)/artifacts/doc/DocumentRoute"
import { prototypePath } from "../../../../lib/routes"
import { useEscapeToClose } from "./useEscapeToClose"
import { AddArtifactPanel } from "./AddArtifactModal"
import styles from "./ProjectArtifactsDrawer.module.css"

type ArtifactFilter = "all" | ProjectArtifactType

/** Verbatim order from `ArtifactsScreen.tsx`'s `ARTIFACT_FILTERS` (the same
 *  documented local-copy exception the sibling modals take). */
const FILTERS: { id: ArtifactFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "report", label: "Reports" },
  { id: "prd", label: "PRDs" },
  { id: "prototype", label: "Prototypes" },
  { id: "evidence", label: "Evidence" },
  { id: "ticket_set", label: "Tickets" },
  { id: "custom_artifact", label: "Documents" },
]

type ArtifactBadge = { label: string; bg: string; color: string }

/** Verbatim from `ArtifactsScreen.tsx`'s `ARTIFACT_BADGE` — the app's real
 *  per-type palette (so the drawer's rows match `/artifacts`). Lives here, not
 *  in the .module.css, so the CSS stays tokens-only. */
const ARTIFACT_BADGE: Record<ProjectArtifactType, ArtifactBadge> = {
  prd: { label: "PRD", bg: "#DBF1E7", color: "#0E6E49" },
  prototype: { label: "PROTOTYPE", bg: "#DBEAFE", color: "#1E40AF" },
  evidence: { label: "EVIDENCE", bg: "#FEF0E6", color: "#B45309" },
  report: { label: "REPORT", bg: "#EDE9FE", color: "#6D28D9" },
  ticket_set: { label: "TICKETS", bg: "var(--info-soft)", color: "var(--info)" },
  custom_artifact: { label: "DOC", bg: "var(--surface-2, #F0EDE7)", color: "var(--ink-2, #5A5853)" },
}

/** Fallback badge for a type outside the projectable set — unreachable today
 *  (a project's own artifacts are DB-constrained to the keys above), but
 *  `ArtifactItem["type"]` is statically wider, so every lookup goes through
 *  `badgeFor`. Mirrors `ArtifactsScreen.tsx`'s `UNKNOWN_BADGE`. */
const UNKNOWN_BADGE: ArtifactBadge = { label: "DOC", bg: "var(--surface-2, #F0EDE7)", color: "var(--ink-2, #5A5853)" }

function badgeFor(type: ArtifactItem["type"]): ArtifactBadge {
  return isProjectArtifactType(type) ? ARTIFACT_BADGE[type] : UNKNOWN_BADGE
}

/** Per-type round type-icon — the SVG glyphs copied VERBATIM from
 *  `ArtifactsScreen.tsx`'s `ArtifactTypeIcon`, so the drawer's rows show the
 *  same icon vocabulary as `/artifacts`. Sized 34px to match the drawer row
 *  (the library screen renders 38px). */
function ArtifactTypeIcon({ type }: { type: ArtifactItem["type"] }) {
  const cfg = badgeFor(type)
  const glyph = (() => {
    if (type === "prototype") {
      return (
        <>
          <polyline points="16 18 22 12 16 6" />
          <polyline points="8 6 2 12 8 18" />
        </>
      )
    }
    if (type === "evidence") {
      return (
        <>
          <circle cx="11" cy="11" r="7" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </>
      )
    }
    if (type === "report") {
      return (
        <>
          <line x1="4" y1="20" x2="20" y2="20" />
          <rect x="6" y="11" width="3.4" height="6" rx="1" />
          <rect x="11.4" y="7" width="3.4" height="10" rx="1" />
          <rect x="16.8" y="13" width="3.4" height="4" rx="1" />
        </>
      )
    }
    if (type === "ticket_set") {
      return (
        <>
          <rect x="3" y="6" width="18" height="12" rx="2" />
          <path d="M9 6v12" />
        </>
      )
    }
    if (type === "custom_artifact") {
      return (
        <>
          <rect x="4" y="3" width="16" height="18" rx="2" />
          <line x1="8" y1="8" x2="16" y2="8" />
          <line x1="8" y1="12" x2="16" y2="12" />
          <line x1="8" y1="16" x2="13" y2="16" />
        </>
      )
    }
    // prd (and the unknown fallback)
    return (
      <>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="8" y1="13" x2="16" y2="13" />
        <line x1="8" y1="17" x2="13" y2="17" />
      </>
    )
  })()
  return (
    <span className={styles.glyph} style={{ background: cfg.bg }} aria-hidden="true">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={cfg.color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        {glyph}
      </svg>
    </span>
  )
}

// ── Row helpers — copied from ArtifactsModal.tsx (same duplication precedent) ──

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

function sourceLine(a: ArtifactItem): string {
  const rel = a.created_at ? relativeTime(a.created_at) : ""
  if (a.type === "prototype") return [`from PRD ${a.source.prd_title}`, rel].filter(Boolean).join(" · ")
  if (a.type === "ticket_set") {
    const count = a.status === "generating" ? "Writing tickets" : `${a.ticket_count} tickets`
    return [count, rel].filter(Boolean).join(" · ")
  }
  if (a.type === "report") return [a.source.conversation_title ? `from ${a.source.conversation_title}` : null, rel].filter(Boolean).join(" · ")
  if (a.type === "custom_artifact") return [a.source.conversation_title ? `from ${a.source.conversation_title}` : null, rel].filter(Boolean).join(" · ")
  return [`from Brief ${a.source.week_label || ""}`.trim(), rel].filter(Boolean).join(" · ")
}

function artifactTitle(a: ArtifactItem): string {
  if (a.type === "ticket_set") return a.title.trim() || "Tickets from this conversation"
  if (a.type === "custom_artifact") return a.title.trim() || "Untitled document"
  return a.title
}

/** The app-side deep-link URL that opens this artifact in the shared side
 *  panel — copied verbatim from `ArtifactsModal.tsx`. Returns null for types
 *  with no standalone url-param entry point (`report`, `ticket_set`): those
 *  rows show a non-interactive "Open from chat" affordance rather than a
 *  fabricated/dead link (resolves spec §9 open-Q4). */
function artifactHref(a: ArtifactItem): string | null {
  switch (a.type) {
    case "prd":
      return `/?prd=${a.open.prd_id}`
    case "evidence":
      return `/?evidence=${a.open.evidence_id}`
    case "prototype":
      return a.open.prd_id != null ? `/prototype?prd=${a.open.prd_id}` : null
    case "custom_artifact":
      return documentPath(a.open.custom_artifact_id)
    default:
      return null
  }
}

/** Whether a row is CLICKABLE-TO-OPEN on the project surface. Every artifact
 *  type has an open destination here — PRD/evidence/report/ticket_set into the
 *  shared side panel (`onOpenInPlace`), prototype into a new browser tab, and a
 *  custom document onto its own page — so the only thing that makes a row inert
 *  is a missing target id (a half-written row that can't resolve to anything).
 *  This replaces the earlier `artifactHref(a) != null` gate, which left report
 *  and ticket_set permanently non-interactive. */
function isOpenable(a: ArtifactItem): boolean {
  switch (a.type) {
    case "prd":
      return a.open.prd_id != null
    case "evidence":
      return a.open.evidence_id != null
    case "prototype":
      return a.open.prd_id != null
    case "report":
      return a.open.report_id != null
    case "ticket_set":
      return a.open.ticket_set_id != null
    case "custom_artifact":
      return a.open.custom_artifact_id != null
    default:
      return false
  }
}

// ── Icons ──

/** The upload picker's accepted extensions — the converter's supported
 *  document types (`backend/app/ingest.py::_SUFFIX_TO_CONVERTER`), minus the
 *  data/markup types the drawer's "document" affordance doesn't advertise. A
 *  hint only: the server re-validates and 422s an unreadable file regardless. */
const UPLOAD_ACCEPT = ".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.txt,.md"

/** DOC badge for a transient upload row — the SAME palette entry a finished
 *  custom_artifact row uses (`ARTIFACT_BADGE.custom_artifact`), so a processing
 *  row and its resolved doc row read identically. */
const DOC_BADGE = ARTIFACT_BADGE.custom_artifact

/** One human-readable size, matching the mockup's "3.4 MB" / "812 KB". */
function fileSizeLabel(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

/** ApiError.status → a specific, user-facing upload failure line. Mirrors the
 *  backend's own 400/413/422/403 contract (`routes/projects.py::upload_project_
 *  document`). */
function uploadErrorMessage(status: number | null): string {
  switch (status) {
    case 400:
      return "That file is empty."
    case 413:
      return "That file is too large (max 25 MB)."
    case 422:
      return "Couldn't read any text — scanned/image-only PDFs and unsupported types aren't supported."
    case 403:
      return "You're not a member of this project, so you can't upload here."
    default:
      return "Upload failed. Please try again."
  }
}

function IconPlusSmall() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

function IconClose() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  )
}

function IconBack() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 6l-6 6 6 6" />
    </svg>
  )
}

function IconChevron() {
  return (
    <svg className={styles.chev} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m9 6 6 6-6 6" />
    </svg>
  )
}

function IconCaret() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m6 9 6 6 6-6" />
    </svg>
  )
}

/** Upload glyph (an arrow rising out of a tray) — shared by the "+ Add" menu's
 *  Upload row and the always-present upload strip. */
function IconUpload({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="M12 3v13" />
      <path d="M7 8l5-5 5 5" />
    </svg>
  )
}

/** The "add existing" library glyph (a stacked-layers artifact), matching the
 *  mockup's second menu row. */
function IconLibrary() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l8.5 4.7L12 12.4 3.5 7.7 12 3z" />
      <path d="M3.5 12L12 16.7 20.5 12" />
    </svg>
  )
}

function FolderStateIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  )
}

function ErrorStateIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v4M12 16h.01" />
    </svg>
  )
}

// ── Presentational shell ──

type Status = "loading" | "forbidden" | "not_found" | "error" | "ready"

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 6 }).map((_, i) => (
        <div className={styles.skRow} key={i}>
          <div className={`${styles.sk} ${styles.skIc}`} />
          <div style={{ flex: 1 }}>
            <div className={`${styles.sk} ${styles.skLine}`} style={{ width: `${52 + ((i * 7) % 38)}%` }} />
            <div className={`${styles.sk} ${styles.skLine}`} style={{ width: `${24 + ((i * 5) % 20)}%`, marginTop: 7 }} />
          </div>
          <div className={styles.sk} style={{ width: 56, height: 16, borderRadius: 4 }} />
        </div>
      ))}
    </>
  )
}

/** A transient upload-in-flight row (spinner → resolves into the real DOC row,
 *  or flips to an inline error). Owned by the container's `uploads` state. */
export type UploadRow = {
  id: string
  name: string
  sizeLabel: string
  /** null while uploading; a user-facing message once it has failed. */
  error: string | null
}

export type ProjectArtifactsDrawerViewProps = {
  status: Status
  artifacts: ArtifactItem[]
  filter: ArtifactFilter
  onFilterChange: (f: ArtifactFilter) => void
  onOpenRow: (a: ArtifactItem) => void
  onClose: () => void
  onRetry: () => void
  view: "list" | "add"
  onShowAdd: () => void
  onBackToList: () => void
  addPanel: React.ReactNode
  /** Pointer-down on the left-edge drag handle → begins a resize gesture (the
   *  container owns the width state + persistence). */
  onResizeStart: (e: React.PointerEvent<HTMLDivElement>) => void
  /** Transient upload rows (processing / errored), rendered at the top of the
   *  list. Container-owned. */
  uploads: UploadRow[]
  /** Chosen/dropped files → start uploads (container POSTs each). */
  onUploadFiles: (files: File[]) => void
  /** Dismiss / cancel a transient upload row by id. */
  onCancelUpload: (id: string) => void
}

export function ProjectArtifactsDrawerView({
  status,
  artifacts,
  filter,
  onFilterChange,
  onOpenRow,
  onClose,
  onRetry,
  view,
  onShowAdd,
  onBackToList,
  addPanel,
  onResizeStart,
  uploads,
  onUploadFiles,
  onCancelUpload,
}: ProjectArtifactsDrawerViewProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [dragging, setDragging] = useState(false)
  const addwrapRef = useRef<HTMLDivElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  // Close the "+ Add" menu on an outside click or Escape (a lightweight
  // document listener — the same posture the drawer's own Escape-to-close uses,
  // scoped to while the menu is open).
  useEffect(() => {
    if (!menuOpen) return
    const onDocMouseDown = (e: MouseEvent) => {
      if (!addwrapRef.current?.contains(e.target as Node)) setMenuOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false)
    }
    document.addEventListener("mousedown", onDocMouseDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [menuOpen])

  const openFilePicker = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files ? Array.from(e.target.files) : []
      if (files.length) onUploadFiles(files)
      // Reset so choosing the SAME file again re-fires `change`.
      e.target.value = ""
    },
    [onUploadFiles],
  )

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragging(false)
      const files = e.dataTransfer?.files ? Array.from(e.dataTransfer.files) : []
      if (files.length) onUploadFiles(files)
    },
    [onUploadFiles],
  )

  const counts: Partial<Record<ArtifactFilter, number>> = { all: artifacts.length }
  for (const a of artifacts) {
    if (!isProjectArtifactType(a.type)) continue
    counts[a.type] = (counts[a.type] ?? 0) + 1
  }
  const filtered = filter === "all" ? artifacts : artifacts.filter((a) => a.type === filter)
  // Only surface a type chip that has ≥1 item; the "all" chip always stays.
  const visibleFilters = FILTERS.filter((f) => f.id === "all" || (counts[f.id] ?? 0) > 0)

  const chips = (
    <div className={styles.filters} role="tablist" aria-label="Filter artifacts by type">
      {visibleFilters.map((f) => (
        <button
          key={f.id}
          type="button"
          role="tab"
          aria-selected={filter === f.id}
          className={`${styles.chip} ${filter === f.id ? styles.chipOn : ""}`}
          onClick={() => onFilterChange(f.id)}
          data-testid={`artifacts-drawer-filter-${f.id}`}
        >
          {f.label} <span className={styles.chipN}>{counts[f.id] ?? 0}</span>
        </button>
      ))}
    </div>
  )

  // ── "+ Add ▾" split menu (V2) — replaces the single "Add artifact" pill.
  //    "Upload document" opens the hidden file input; "Add existing artifact"
  //    reuses the EXISTING add-existing behavior (`onShowAdd` → the reused
  //    `AddArtifactPanel`). The hidden input is shared by the menu and the
  //    always-present upload strip below. ──
  const hiddenFileInput = (
    <input
      ref={fileInputRef}
      type="file"
      accept={UPLOAD_ACCEPT}
      multiple
      hidden
      onChange={handleInputChange}
      data-testid="artifacts-drawer-file-input"
    />
  )

  const addMenu = (
    <div className={styles.addwrap} ref={addwrapRef}>
      <button
        type="button"
        className={styles.add}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((o) => !o)}
        data-testid="artifacts-drawer-add"
      >
        <IconPlusSmall />
        Add
        <IconCaret />
      </button>
      {menuOpen ? (
        <div className={styles.addmenu} role="menu" data-testid="artifacts-drawer-add-menu">
          <button
            type="button"
            role="menuitem"
            className={styles.amRow}
            onClick={() => {
              setMenuOpen(false)
              openFilePicker()
            }}
            data-testid="artifacts-drawer-menu-upload"
          >
            <span className={styles.amIc}>
              <IconUpload />
            </span>
            <span>
              <span className={styles.amT}>Upload document</span>
              <span className={styles.amS}>PDF, DOCX, MD, TXT — read into context</span>
            </span>
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.amRow}
            onClick={() => {
              setMenuOpen(false)
              onShowAdd()
            }}
            data-testid="artifacts-drawer-menu-existing"
          >
            <span className={styles.amIc}>
              <IconLibrary />
            </span>
            <span>
              <span className={styles.amT}>Add existing artifact</span>
              <span className={styles.amS}>Link a PRD, report or prototype</span>
            </span>
          </button>
        </div>
      ) : null}
    </div>
  )

  // The slim always-present upload strip (below the filter band, top of body).
  const uploadStrip = (
    <button
      type="button"
      className={`${styles.upstrip} ${dragging ? styles.upstripDrag : ""}`}
      onClick={openFilePicker}
      data-testid="artifacts-drawer-upload-strip"
    >
      <span className={styles.upIc}>
        <IconUpload size={15} />
      </span>
      <span className={styles.upMain}>
        <span className={styles.upT}>
          Drop documents here, or <b>browse</b>
        </span>
        <span className={styles.upS}>PDF · DOCX · MD · TXT — read into Sprntly&rsquo;s context</span>
      </span>
    </button>
  )

  // Transient upload rows (processing spinner or inline error), above the list.
  const uploadRows = uploads.map((u) => (
    <div
      key={u.id}
      className={`${styles.arow} ${styles.proc}`}
      data-testid={`artifacts-drawer-upload-${u.id}`}
      data-upload-state={u.error ? "error" : "uploading"}
    >
      {u.error ? (
        <span className={`${styles.glyph} ${styles.glyphError}`} aria-hidden="true">
          <ErrorStateIcon />
        </span>
      ) : (
        <span className={`${styles.glyph} ${styles.ring}`} aria-hidden="true" />
      )}
      <span className={styles.rowMain}>
        <span className={styles.rowTitle}>{u.name}</span>
        <span className={styles.rowMeta}>
          <span className={styles.badge} style={{ background: DOC_BADGE.bg, color: DOC_BADGE.color }}>
            {DOC_BADGE.label}
          </span>
          <span className={styles.rowSrc}>
            {u.error ? u.error : `Indexing for Sprntly · ${u.sizeLabel}`}
          </span>
        </span>
        {u.error ? null : (
          <div className={styles.prog} aria-hidden="true">
            <i />
          </div>
        )}
      </span>
      <button
        type="button"
        className={styles.cancelBtn}
        onClick={() => onCancelUpload(u.id)}
        aria-label={u.error ? "Dismiss" : "Cancel upload"}
        title={u.error ? "Dismiss" : "Cancel upload"}
        data-testid={`artifacts-drawer-upload-cancel-${u.id}`}
      >
        <IconClose />
      </button>
    </div>
  ))

  // ── ADD (secondary) view ──
  if (view === "add") {
    return (
      <aside className={styles.drawer} role="region" aria-label="Add artifacts to project" data-testid="artifacts-drawer">
        <div
          className={styles.resizeHandle}
          onPointerDown={onResizeStart}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize artifacts panel"
          data-testid="artifacts-drawer-resize"
        />
        <div className={styles.head}>
          <button
            type="button"
            className={styles.back}
            onClick={onBackToList}
            aria-label="Back to artifacts"
            title="Back to artifacts"
            data-testid="artifacts-drawer-back"
          >
            <IconBack />
          </button>
          <div className={styles.titlewrap}>
            <div className={styles.title}>Add artifacts</div>
            <div className={styles.hsub}>From your company&rsquo;s library — pick one or more to attach.</div>
          </div>
          <button type="button" className={styles.close} onClick={onClose} aria-label="Close" data-testid="artifacts-drawer-close">
            <IconClose />
          </button>
        </div>
        <div className={styles.addHost} data-testid="artifacts-drawer-add-host">
          {addPanel}
        </div>
      </aside>
    )
  }

  // ── LIST (primary) view ──
  return (
    <aside className={styles.drawer} role="region" aria-label="Project artifacts" data-testid="artifacts-drawer">
      <div
        className={styles.resizeHandle}
        onPointerDown={onResizeStart}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize artifacts panel"
        data-testid="artifacts-drawer-resize"
      />
      <div className={styles.head}>
        <div className={styles.titlewrap}>
          <div className={styles.title}>
            Artifacts <span className={styles.count}>{artifacts.length}</span>
          </div>
          <div className={styles.hsub}>Everything this project has produced — click a row to open it.</div>
        </div>
        {hiddenFileInput}
        {addMenu}
        <button type="button" className={styles.close} onClick={onClose} aria-label="Close artifacts" data-testid="artifacts-drawer-close">
          <IconClose />
        </button>
      </div>

      {status === "ready" && artifacts.length > 0 ? chips : null}

      <div
        className={styles.body}
        data-testid="artifacts-drawer-body"
        {...(status === "ready"
          ? {
              onDragOver: (e: React.DragEvent) => {
                e.preventDefault()
                if (!dragging) setDragging(true)
              },
              onDragLeave: (e: React.DragEvent) => {
                // Only clear when the pointer actually left the body, not on a
                // child-boundary crossing.
                if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false)
              },
              onDrop: handleDrop,
            }
          : {})}
      >
        {status === "loading" ? (
          <div aria-busy="true" data-testid="artifacts-drawer-loading">
            <SkeletonRows />
          </div>
        ) : status === "forbidden" ? (
          <div className={styles.state} data-testid="artifacts-drawer-forbidden">
            <div className={`${styles.stateIc} ${styles.stateIcError}`}>
              <ErrorStateIcon />
            </div>
            <div className={styles.stateHl}>Artifacts aren&rsquo;t visible</div>
            <div className={styles.stateMsg}>You&rsquo;re not a member of this project, so its artifacts aren&rsquo;t visible to you.</div>
          </div>
        ) : status === "not_found" ? (
          <div className={styles.state} data-testid="artifacts-drawer-not-found">
            <div className={`${styles.stateIc} ${styles.stateIcError}`}>
              <ErrorStateIcon />
            </div>
            <div className={styles.stateHl}>Couldn&rsquo;t find these artifacts</div>
            <div className={styles.stateMsg}>This project&rsquo;s artifacts couldn&rsquo;t be found.</div>
          </div>
        ) : status === "error" ? (
          <div className={styles.state} data-testid="artifacts-drawer-error">
            <div className={`${styles.stateIc} ${styles.stateIcError}`}>
              <ErrorStateIcon />
            </div>
            <div className={styles.stateHl}>Couldn&rsquo;t load this project&rsquo;s artifacts</div>
            <div className={styles.stateMsg}>Something went wrong. Check your connection and try again.</div>
            <button type="button" className={styles.cta} onClick={onRetry} data-testid="artifacts-drawer-retry">
              Try again
            </button>
          </div>
        ) : artifacts.length === 0 && uploads.length === 0 ? (
          <>
            {uploadStrip}
            <div className={styles.state} data-testid="artifacts-drawer-empty">
              <div className={styles.stateIc}>
                <FolderStateIcon />
              </div>
              <div className={styles.stateHl}>No artifacts yet</div>
              <div className={styles.stateMsg}>
                Drop a document above, or attach something from your library — everything this project produces shows up here.
              </div>
              <button type="button" className={styles.cta} onClick={onShowAdd} data-testid="artifacts-drawer-empty-add">
                Add artifact
              </button>
            </div>
          </>
        ) : filtered.length === 0 && uploads.length === 0 ? (
          <>
            {uploadStrip}
            <div className={styles.state} data-testid="artifacts-drawer-empty-filter">
              <div className={styles.stateIc}>
                <FolderStateIcon />
              </div>
              <div className={styles.stateHl}>No {filter} artifacts</div>
              <div className={styles.stateMsg}>Try a different type filter.</div>
            </div>
          </>
        ) : (
          <>
            {uploadStrip}
            <div className={styles.list} data-testid="artifacts-drawer-list">
              {uploadRows}
              {filtered.map((a) => {
              const cfg = badgeFor(a.type)
              const openable = isOpenable(a)
              const common = (
                <>
                  <ArtifactTypeIcon type={a.type} />
                  <span className={styles.rowMain}>
                    <span className={styles.rowTitle}>{artifactTitle(a)}</span>
                    <span className={styles.rowMeta}>
                      <span className={styles.badge} style={{ background: cfg.bg, color: cfg.color }}>
                        {cfg.label}
                      </span>
                      <span className={styles.rowSrc}>{sourceLine(a)}</span>
                    </span>
                  </span>
                </>
              )
              if (!openable) {
                // report / ticket_set — no standalone deep-link; opened from
                // chat. Non-interactive affordance (spec §9 open-Q4).
                return (
                  <div
                    key={artifactKey(a)}
                    className={`${styles.arow} ${styles.arowStatic}`}
                    data-testid={`artifacts-drawer-row-${artifactKey(a)}`}
                    data-artifact-type={a.type}
                  >
                    {common}
                    <span className={styles.openHint}>Opens from chat</span>
                  </div>
                )
              }
              return (
                <button
                  key={artifactKey(a)}
                  type="button"
                  className={styles.arow}
                  data-testid={`artifacts-drawer-row-${artifactKey(a)}`}
                  data-artifact-type={a.type}
                  onClick={() => onOpenRow(a)}
                >
                  {common}
                  <IconChevron />
                </button>
              )
            })}
            </div>
          </>
        )}
      </div>
    </aside>
  )
}

// ── Drawer resize (drag-to-widen) ──
// The drawer is a grid COLUMN (ProjectDetailScreen.module.css's `.bodyDrawerOpen`),
// its width driven by `--proj-drawer-w` on :root — set here, persisted, so a
// wider drawer narrows the chat column rather than overlaying it. Mirrors the
// content panel's own drag handle; the list has no iframes, so this is the
// simpler pointer-capture + rAF form without the frame-swallowing guards.
const DRAWER_WIDTH_KEY = "sprntly-proj-artifacts-drawer-width"
// The drawer holds the artifacts LIST (a column of one-line rows), so it is
// capped — matching the CSS default band `clamp(240px, 30vw, 450px)` in
// ProjectDetailScreen.module.css. A too-wide list drawer was the root cause of
// the three-panel squeeze (chat + list + content-panel couldn't fit), so this
// cap is enforced here too: any persisted/dragged width is clamped into
// [240, 450] on restore, so an old wide value can never re-widen it past 450.
const DRAWER_WIDTH_MIN = 240
const DRAWER_WIDTH_MAX = 450
const DRAWER_VAR = "--proj-drawer-w"
// Below this the layout drops to a full-width drawer (no chat column to trade
// against), matching ProjectDetailScreen.module.css's <=960px rule.
const DRAWER_RESIZE_MIN_VIEWPORT = 960

function clampDrawerWidth(px: number): number {
  return Math.min(DRAWER_WIDTH_MAX, Math.max(DRAWER_WIDTH_MIN, Math.round(px)))
}

// ── Container: fetch on open + list ⇆ add state ──

type LoadState =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "not_found" }
  | { status: "error" }
  | { status: "ready"; artifacts: ArtifactItem[] }

export function ProjectArtifactsDrawer({
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
  /** Open a PRD/evidence row IN-PLACE in the shared side-panel beside the
   *  project chat (the project surface's own seam), instead of the deep-link
   *  `artifactHref` — which for a PRD is `/?prd=` = MAIN chat, navigating the
   *  user OUT of the project. PRD/evidence route here; prototype/document rows
   *  (their own standalone routes, not the beside-chat panel) still deep-link. */
  onOpenInPlace?: (a: ArtifactItem) => void
  /** Fired after the add view attaches artifact(s) — the parent re-fetches so
   *  the top-bar "Artifacts(N)" count updates. The drawer refreshes its OWN
   *  list internally as well. */
  onArtifactsChanged?: () => void
}) {
  const router = useRouter()
  const [state, setState] = useState<LoadState>({ status: "loading" })
  const [filter, setFilter] = useState<ArtifactFilter>(initialFilter ?? "all")
  const [view, setView] = useState<"list" | "add">("list")
  const [uploads, setUploads] = useState<UploadRow[]>([])
  const uploadSeq = useRef(0)
  const addHostRef = useRef<HTMLDivElement | null>(null)

  // Non-modal region: Escape closes the drawer, focus is NOT trapped (the
  // surrounding app stays reachable). Same reliable document-level listener the
  // sibling modals use.
  useEscapeToClose(open, onClose)

  // ── Drag-to-resize: live width + persistence ──
  // widthRef holds the current px width (null = the CSS default band). On open
  // it restores the saved width, applies it to :root, and re-clamps on window
  // resize; on close it clears the var so a re-open starts from the default.
  const widthRef = useRef<number | null>(null)
  useEffect(() => {
    if (!open) return
    const root = document.documentElement
    const saved = Number(window.localStorage.getItem(DRAWER_WIDTH_KEY))
    widthRef.current = Number.isFinite(saved) && saved >= DRAWER_WIDTH_MIN ? saved : null
    const apply = () => {
      if (window.innerWidth <= DRAWER_RESIZE_MIN_VIEWPORT || widthRef.current == null) {
        root.style.removeProperty(DRAWER_VAR)
        return
      }
      const next = clampDrawerWidth(widthRef.current)
      widthRef.current = next
      root.style.setProperty(DRAWER_VAR, `${next}px`)
    }
    apply()
    window.addEventListener("resize", apply)
    return () => {
      window.removeEventListener("resize", apply)
      root.style.removeProperty(DRAWER_VAR)
    }
  }, [open])

  const handleResizeStart = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || window.innerWidth <= DRAWER_RESIZE_MIN_VIEWPORT) return
    e.preventDefault()
    const handle = e.currentTarget
    const drawerEl = handle.closest<HTMLElement>("[data-testid='artifacts-drawer']")
    const root = document.documentElement
    const { pointerId } = e
    let latestX = e.clientX
    let frame = 0
    const flush = () => {
      frame = 0
      // The drawer is anchored to the viewport's RIGHT edge, so its width is the
      // gap between the pointer and that edge — dragging LEFT widens it.
      const next = clampDrawerWidth(window.innerWidth - latestX)
      widthRef.current = next
      root.style.setProperty(DRAWER_VAR, `${next}px`)
    }
    drawerEl?.setAttribute("data-resizing", "true")
    try { handle.setPointerCapture(pointerId) } catch { /* jsdom / unsupported */ }
    const onMove = (ev: PointerEvent) => {
      if (ev.pointerId !== pointerId) return
      latestX = ev.clientX
      if (!frame) frame = window.requestAnimationFrame(flush)
    }
    const end = () => {
      if (frame) { window.cancelAnimationFrame(frame); flush() }
      if (widthRef.current != null) {
        window.localStorage.setItem(DRAWER_WIDTH_KEY, String(widthRef.current))
      }
      drawerEl?.removeAttribute("data-resizing")
      try { handle.releasePointerCapture(pointerId) } catch { /* already gone */ }
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onUp)
      window.removeEventListener("pointercancel", onUp)
    }
    const onUp = (ev: PointerEvent) => { if (ev.pointerId === pointerId) end() }
    window.addEventListener("pointermove", onMove)
    window.addEventListener("pointerup", onUp)
    window.addEventListener("pointercancel", onUp)
  }, [])

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
    setUploads([])
    reload()
    // `initialFilter` re-applies only when the drawer (re)opens — a change to
    // the trigger's last-clicked type while already open must not yank the
    // filter out from under someone browsing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectId])

  // On entering the add view, move focus to its first control (the search
  // input) — the region's a11y contract (spec AC9). Done from the host without
  // modifying the reused panel. `AddArtifactPanel` renders a "Loading…"
  // placeholder first (the search input is NOT in the DOM yet), so focusing
  // synchronously on the `view` flip races the panel's own fetch and usually
  // misses. Focus eagerly if the input is already present, otherwise watch the
  // host subtree and focus once the panel reaches its ready state (the input
  // mounts), then stop observing.
  useEffect(() => {
    if (view !== "add") return
    const host = addHostRef.current
    if (!host) return
    const focusSearch = () => {
      const input = host.querySelector<HTMLInputElement>(
        "input[data-testid='add-artifact-search']",
      )
      if (input) {
        input.focus()
        return true
      }
      return false
    }
    if (focusSearch()) return
    const observer = new MutationObserver(() => {
      if (focusSearch()) observer.disconnect()
    })
    observer.observe(host, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [view])

  const artifacts = state.status === "ready" ? state.artifacts : []
  const existingKeys = useMemo(() => new Set(artifacts.map((a) => `${a.type}-${a.id}`)), [artifacts])

  const handleOpenRow = useCallback(
    (a: ArtifactItem) => {
      // PRD, evidence, report, ticket_set AND an uploaded/team document
      // (custom_artifact) are IN-PANEL artifacts: they open in the SAME shared
      // side-panel main uses, beside the project chat, via the project surface's
      // in-place seam — which also closes this drawer (it clears the project's
      // rail-modal state). This is a HARD invariant: these rows must NEVER reach
      // the `/?prd=`/`/?evidence=` deep-link or the full-page document route,
      // because those land on a DIFFERENT page and yank the user out of the
      // project (the reported defect — an uploaded document opened the full-page
      // `DocumentRoute`). So we short-circuit unconditionally — if the in-place
      // seam is somehow absent we no-op rather than fall back to a navigation.
      if (
        a.type === "prd" ||
        a.type === "evidence" ||
        a.type === "report" ||
        a.type === "ticket_set" ||
        a.type === "custom_artifact"
      ) {
        onOpenInPlace?.(a)
        return
      }
      // A prototype has its OWN standalone canvas route (`/prototype?prd=`), which
      // is a full-page surface — routing to it in-place would navigate the user
      // out of the project. Open it in a NEW browser tab so the project chat stays
      // put behind it. `noopener` keeps the opened tab from reaching back into
      // this window.
      if (a.type === "prototype") {
        if (a.open.prd_id == null) return
        window.open(prototypePath(a.open.prd_id), "_blank", "noopener")
        return
      }
      // Defensive fallback for any future artifact type with only a deep-link
      // entry point — no current type reaches here.
      const href = artifactHref(a)
      if (!href) return
      router.push(href)
    },
    [router, onOpenInPlace],
  )

  const handleAdded = useCallback(() => {
    reload()
    onArtifactsChanged?.()
  }, [reload, onArtifactsChanged])

  // ── Upload flow ──
  // A chosen/dropped file → an optimistic processing row (spinner) is inserted
  // immediately; the POST runs; on success the row is removed and the returned
  // custom_artifact DTO is merged into the list (the EXISTING row renderer then
  // draws it) + the parent count refreshes; on failure the row flips to an
  // inline, status-mapped error (kept until dismissed). No full reload — the
  // returned DTO is authoritative and the optimistic insert avoids a flash
  // (`add_artifact`'s realtime `artifact.added` broadcast still reconciles any
  // other open surface).
  const cancelUpload = useCallback((id: string) => {
    setUploads((prev) => prev.filter((u) => u.id !== id))
  }, [])

  const uploadFiles = useCallback(
    (files: File[]) => {
      for (const file of files) {
        const id = `upload-${++uploadSeq.current}`
        setUploads((prev) => [
          ...prev,
          { id, name: file.name || "document", sizeLabel: fileSizeLabel(file.size), error: null },
        ])
        projectsApi
          .uploadDocument(projectId, file)
          .then((item) => {
            setUploads((prev) => prev.filter((u) => u.id !== id))
            setState((prev) =>
              prev.status === "ready"
                ? {
                    status: "ready",
                    // Prepend the new doc; dedupe by key in case a concurrent
                    // reload already surfaced it.
                    artifacts: [
                      item,
                      ...prev.artifacts.filter((a) => `${a.type}-${a.id}` !== `${item.type}-${item.id}`),
                    ],
                  }
                : prev,
            )
            onArtifactsChanged?.()
          })
          .catch((err: unknown) => {
            const status = err instanceof ApiError ? err.status : null
            setUploads((prev) =>
              prev.map((u) => (u.id === id ? { ...u, error: uploadErrorMessage(status) } : u)),
            )
          })
      }
    },
    [projectId, onArtifactsChanged],
  )

  if (!open) return null

  return (
    <div ref={addHostRef} style={{ display: "contents" }}>
      <ProjectArtifactsDrawerView
        status={state.status}
        artifacts={artifacts}
        filter={filter}
        onFilterChange={setFilter}
        onOpenRow={handleOpenRow}
        onClose={onClose}
        onRetry={reload}
        view={view}
        onShowAdd={() => setView("add")}
        onBackToList={() => setView("list")}
        onResizeStart={handleResizeStart}
        uploads={uploads}
        onUploadFiles={uploadFiles}
        onCancelUpload={cancelUpload}
        addPanel={
          <AddArtifactPanel
            projectId={projectId}
            active={open && view === "add"}
            existingKeys={existingKeys}
            onAdded={handleAdded}
            onDone={() => setView("list")}
            onCancel={() => setView("list")}
            renderIcon={(type) => <ArtifactTypeIcon type={type} />}
          />
        }
      />
    </div>
  )
}
