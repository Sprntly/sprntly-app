"use client"

// ── Projects — list (the "Recently edited" + search home) ──
//
// Flat route: `web/app/(app)/projects/page.tsx` renders this when there is no
// `?id=` query param (AD-P14 — the `/prototype?prd=<id>` pattern; no `[id]`
// dynamic segment). The detail view (`?id=<id>`) is a follow-up ticket.
//
// Data: `projectsApi.list()` → `GET /v1/projects`, already MEMBER-scoped and
// recency-ordered by the backend (build spec §5.1/AD-P11) — this screen does
// not re-filter by membership, it renders exactly what the API returns.
//
// No status tabs/filters (build spec §2 — status is out of v1, and if it
// returns it must be derived at read time, never a stored field). Controls
// are a "Recently edited" section label + a client-side search-by-name field
// only (design-spec AC16's status tabs are explicitly not reintroduced).
//
// Card content mirrors ArtifactsScreen's badge/relativeTime patterns (reuse
// over invention, `[[feedback_reuse-over-invention-in-ux-builds]]`):
//  - the type-count badges reuse ARTIFACT_BADGE's exact per-type colors
//    (ArtifactsScreen.tsx:56) — duplicated locally rather than importing,
//    since ArtifactsScreen is not a declared Deliverable for this ticket;
//  - `relativeTime` is the same compact bucketing ArtifactsScreen ships.
//
// Member avatar stack — KNOWN DATA GAP (flagged for the planner): `GET
// /v1/projects` (`list_projects_for_workspace`, backend/app/db/projects.py)
// returns only `member_count` on the list row, not member identity/initials
// (those live on `GET /v1/projects/{id}`, out of scope for a list fetch).
// The design-spec/ticket AC call for "initials legible" avatars, which this
// endpoint cannot supply without an N+1 fan-out this ticket does not add.
// `renderAvatarStack` below renders `member_count` generic placeholder
// avatars (no fabricated initials) so the stack still communicates "how many
// people", and is built so a future `list_projects_for_workspace` extension
// (compact member initials on the list row) drops in without a reshape.
import { useCallback, useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { AppLayout } from "../AppLayout"
import { EmptyPane } from "../../../shared/EmptyPane"
import { projectPath } from "../../../../lib/routes"
import { projectsApi, type ProjectArtifactType, type ProjectListItem } from "../../../../lib/api"
import { CreateProjectModal } from "./CreateProjectModal"
import styles from "./ProjectsScreen.module.css"

/** Mirrors `ArtifactsScreen.tsx`'s `ARTIFACT_BADGE` (line 56) exactly — same
 *  per-type semantic colors, not a new palette. PRD/Evidence/Tickets resolve
 *  to existing `globals.css` tokens; Prototype/Report keep the same
 *  pre-token hex ArtifactsScreen already ships app-wide (its own comment:
 *  "predate the design tokens... changing them is a visual-consistency pass
 *  of its own, not this feature's" — reusing verbatim, not introducing new
 *  hex). Do NOT use `#634AB0` (the design mockup's purple) for prototype —
 *  the real app's prototype badge is `#DBEAFE`/`#1E40AF`. */
const TYPE_BADGE: Record<ProjectArtifactType, { label: string; bg: string; color: string }> = {
  prd: { label: "PRD", bg: "var(--accent-soft)", color: "var(--accent-ink)" },
  evidence: { label: "evidence", bg: "#FEF0E6", color: "#B45309" },
  prototype: { label: "prototype", bg: "#DBEAFE", color: "#1E40AF" },
  report: { label: "report", bg: "#EDE9FE", color: "#6D28D9" },
  ticket_set: { label: "tickets", bg: "var(--info-soft)", color: "var(--info)" },
}

// Stable render order for the per-type badges (matches ARTIFACT_FILTERS'
// ordering intent: reports/PRD lead, tickets last).
const TYPE_ORDER: ProjectArtifactType[] = ["prd", "prototype", "evidence", "report", "ticket_set"]

/** Compact relative time, e.g. "just now", "3h ago", "2d ago", "May 3".
 *  Mirrors `ArtifactsScreen.tsx`'s `relativeTime` (same bucketing) so a
 *  project's "updated" time reads identically to an artifact's. */
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

const MAX_AVATARS = 4

/** See the module-level "Member avatar stack — KNOWN DATA GAP" note above:
 *  `member_count` is the only member data the list endpoint carries, so
 *  each circle is a generic placeholder (no fabricated initials). */
function AvatarStack({ memberCount }: { memberCount: number }) {
  const shown = Math.min(memberCount, MAX_AVATARS)
  const overflow = memberCount - shown
  if (memberCount <= 0) return null
  return (
    <div className={styles.avStack} data-testid="av-stack" aria-label={`${memberCount} member${memberCount === 1 ? "" : "s"}`}>
      {Array.from({ length: shown }, (_, i) => (
        <span key={i} className={styles.av} aria-hidden="true">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
        </span>
      ))}
      {overflow > 0 ? (
        <span className={`${styles.av} ${styles.avOverflow}`} aria-hidden="true">
          +{overflow}
        </span>
      ) : null}
    </div>
  )
}

function ChatIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" strokeLinejoin="round" />
    </svg>
  )
}

/** One project card. A real `<button>` so it is keyboard-reachable with a
 *  native, visible focus ring (AC12/AC8) without reinventing tab semantics. */
function ProjectCard({ project, onOpen }: { project: ProjectListItem; onOpen: (id: number) => void }) {
  const nonEmptyTypes = TYPE_ORDER.filter((t) => (project.artifact_counts[t] ?? 0) > 0)
  return (
    <button
      type="button"
      className={styles.card}
      data-testid="project-card"
      onClick={() => onOpen(project.id)}
    >
      <div className={styles.cardTop}>
        <h3 className={styles.cardTitle}>{project.name}</h3>
        {project.origin === "prd_auto" ? (
          <span className={styles.autoBadge} title="Project was created automatically when a PRD was generated">
            Auto · from PRD
          </span>
        ) : null}
      </div>
      {nonEmptyTypes.length > 0 ? (
        <div className={styles.badgeRow}>
          {nonEmptyTypes.map((t) => {
            const cfg = TYPE_BADGE[t]
            const count = project.artifact_counts[t] ?? 0
            return (
              <span
                key={t}
                className={styles.countBadge}
                style={{ background: cfg.bg, color: cfg.color }}
              >
                <b>{count}</b> {cfg.label}
              </span>
            )
          })}
        </div>
      ) : null}
      <div className={styles.footer}>
        <AvatarStack memberCount={project.member_count} />
        <div className={styles.metaRow}>
          <span className={styles.metaItem} title={project.has_group_chat ? "Group chat" : "No group chat yet"}>
            <ChatIcon />
            {project.has_group_chat ? "Group chat" : "No group chat yet"}
          </span>
          <span className={styles.metaItem} title="Summarized project memory">
            <span className={styles.memDot} aria-hidden="true" />
            {project.memory_count} insight{project.memory_count === 1 ? "" : "s"}
          </span>
          <span className={styles.metaItem}>{relativeTime(project.updated_at)}</span>
        </div>
      </div>
    </button>
  )
}

export type ProjectsViewProps = {
  projects: ProjectListItem[]
  loading: boolean
  search: string
  onSearchChange: (value: string) => void
  onOpen: (id: number) => void
  onNewProject: () => void
}

/** Pure presentational list — the surface a test/screenshot renders, same
 *  container/view split as `ArtifactsView`/`ArtifactsScreen`. */
export function ProjectsView({ projects, loading, search, onSearchChange, onOpen, onNewProject }: ProjectsViewProps) {
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    if (!needle) return projects
    return projects.filter((p) => p.name.toLowerCase().includes(needle))
  }, [projects, search])

  return (
    <div>
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>Projects</h1>
          <p className={styles.sub}>
            Everything on a topic in one place — the artifacts and the shared context behind them.
          </p>
        </div>
        <button type="button" className={styles.newBtn} onClick={onNewProject} data-testid="new-project-button">
          + New project
        </button>
      </div>

      {loading ? (
        <div className={styles.skeletonGrid} aria-busy="true">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} className={styles.skeletonCard} />
          ))}
        </div>
      ) : projects.length === 0 ? (
        <EmptyPane title="No projects yet — your first PRD will start one automatically." placeholders={2} />
      ) : (
        <>
          <div className={styles.searchRow}>
            <label className={styles.search}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <circle cx="11" cy="11" r="7" />
                <path d="M21 21l-4-4" strokeLinecap="round" />
              </svg>
              <input
                type="text"
                placeholder="Search projects"
                aria-label="Search projects"
                data-testid="projects-search"
                value={search}
                onChange={(e) => onSearchChange(e.target.value)}
              />
            </label>
          </div>
          <p className={styles.sectionLabel}>Recently edited</p>
          {filtered.length === 0 ? (
            <p className={styles.noMatch}>No projects match &ldquo;{search}&rdquo;.</p>
          ) : (
            <div className={styles.grid}>
              {filtered.map((p) => (
                <ProjectCard key={p.id} project={p} onOpen={onOpen} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

/** Container: fetches `projectsApi.list()`, wires card-open navigation to the
 *  flat `?id=` route (AD-P14), and mounts the app chrome. */
export function ProjectsScreen() {
  const router = useRouter()
  const [projects, setProjects] = useState<ProjectListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState("")
  const [createOpen, setCreateOpen] = useState(false)

  const refresh = useCallback(() => {
    setLoading(true)
    projectsApi
      .list()
      .then(setProjects)
      .catch(() => setProjects([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const onOpen = useCallback(
    (id: number) => {
      router.push(projectPath(id))
    },
    [router],
  )

  // "New project" opens the create-project flow (tabs + invite rows).
  const onNewProject = useCallback(() => {
    setCreateOpen(true)
  }, [])

  // A successful create navigates away from this screen before onClose
  // fires (CreateProjectModal calls router.push then closes itself), and a
  // cancel changes nothing — so closing never needs a re-fetch here.
  const onCloseCreate = useCallback(() => {
    setCreateOpen(false)
  }, [])

  return (
    <AppLayout>
      <div style={{ maxWidth: 1220, margin: "0 auto", padding: "0 4px" }}>
        <ProjectsView
          projects={projects}
          loading={loading}
          search={search}
          onSearchChange={setSearch}
          onOpen={onOpen}
          onNewProject={onNewProject}
        />
      </div>
      <CreateProjectModal open={createOpen} onClose={onCloseCreate} />
    </AppLayout>
  )
}
