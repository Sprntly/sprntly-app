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

// ── Icons ──

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
}: ProjectArtifactsDrawerViewProps) {
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

  // ── ADD (secondary) view ──
  if (view === "add") {
    return (
      <aside className={styles.drawer} role="region" aria-label="Add artifacts to project" data-testid="artifacts-drawer">
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
      <div className={styles.head}>
        <div className={styles.titlewrap}>
          <div className={styles.title}>
            Artifacts <span className={styles.count}>{artifacts.length}</span>
          </div>
          <div className={styles.hsub}>Everything this project has produced — click a row to open it.</div>
        </div>
        <button type="button" className={styles.add} onClick={onShowAdd} data-testid="artifacts-drawer-add">
          <IconPlusSmall />
          Add artifact
        </button>
        <button type="button" className={styles.close} onClick={onClose} aria-label="Close artifacts" data-testid="artifacts-drawer-close">
          <IconClose />
        </button>
      </div>

      {status === "ready" && artifacts.length > 0 ? chips : null}

      <div className={styles.body} data-testid="artifacts-drawer-body">
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
        ) : artifacts.length === 0 ? (
          <div className={styles.state} data-testid="artifacts-drawer-empty">
            <div className={styles.stateIc}>
              <FolderStateIcon />
            </div>
            <div className={styles.stateHl}>No artifacts yet</div>
            <div className={styles.stateMsg}>
              Items this project produces — PRDs, evidence, prototypes, reports — show up here. Or attach something from your library.
            </div>
            <button type="button" className={styles.cta} onClick={onShowAdd} data-testid="artifacts-drawer-empty-add">
              Add artifact
            </button>
          </div>
        ) : filtered.length === 0 ? (
          <div className={styles.state} data-testid="artifacts-drawer-empty-filter">
            <div className={styles.stateIc}>
              <FolderStateIcon />
            </div>
            <div className={styles.stateHl}>No {filter} artifacts</div>
            <div className={styles.stateMsg}>Try a different type filter.</div>
          </div>
        ) : (
          <div className={styles.list} data-testid="artifacts-drawer-list">
            {filtered.map((a) => {
              const cfg = badgeFor(a.type)
              const openable = artifactHref(a) != null
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
        )}
      </div>
    </aside>
  )
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
  const addHostRef = useRef<HTMLDivElement | null>(null)

  // Non-modal region: Escape closes the drawer, focus is NOT trapped (the
  // surrounding app stays reachable). Same reliable document-level listener the
  // sibling modals use.
  useEscapeToClose(open, onClose)

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
      // PRD and evidence are IN-PANEL artifacts: they open in the SAME shared
      // side-panel main uses, beside the project chat, via the project surface's
      // in-place seam — which also closes this drawer (it clears the project's
      // rail-modal state). This is a HARD invariant: a PRD/evidence row must NEVER
      // reach the `/?prd=`/`/?evidence=` deep-link, because those land on the MAIN
      // workspace chat (`/`) and open a NEW main chat tab, yanking the user out of
      // the project (the reported defect). So we short-circuit unconditionally —
      // if the in-place seam is somehow absent we no-op rather than fall back to
      // that main deep-link.
      if (a.type === "prd" || a.type === "evidence") {
        onOpenInPlace?.(a)
        return
      }
      // prototype (`/prototype?prd=`) and custom_artifact (`documentPath`) have
      // their OWN standalone routes — not the beside-chat panel — so those keep
      // the deep-link open. Types with no url-param entry (report/ticket_set)
      // aren't rendered as openable rows at all.
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
