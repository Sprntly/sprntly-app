"use client"

// ── ProjectDetailScreen — the group-chat-centric detail shell (v3.6) ──
//
// Flat route (AD-P14): mounts from `web/app/(app)/projects/ProjectsRoute.tsx`
// when `?id=<id>` is present on the one `/projects` route — NO `[id]`
// dynamic segment. `projectId` is threaded in as a prop from that route's
// own `useSearchParams().get("id")` read, so this component stays
// testable in isolation without mocking `next/navigation`.
//
// Scope boundary (this ticket): the SHELL — top bar, a group⇆individual
// chat HOST slot + composer shell, and the collapsible right rail
// (CHATS / ARTIFACTS / PROJECT / MEMBERS), wired to the real
// `GET /v1/projects/{id}`, `/artifacts`, `/memory/summary` endpoints.
//
// AD-P13 (one chat presentation layer): this file renders NO bespoke chat
// bubbles/composer/markdown of its own — `.threadHost` below is a clearly
// labeled placeholder for `<ProjectMainThread>`, mounted by a follow-up
// ticket, and the composer is a static shell whose placeholder/note copy
// swaps on `activeChat`; the actual send/poll wiring and the multi-author
// thread land with that follow-up. This file does NOT import or modify the
// existing single-owner chat monolith container this app already ships
// (AD-P13's "never fork" rule).
//
// Membership (AD-P11): `GET /{id}` is member-gated server-side — 403 for a
// same-tenant non-member, 404 for a foreign-tenant/absent project. Both are
// rendered as an in-chrome "can't open this" state, never a crash or blank
// screen.
import { useCallback, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { AppLayout } from "../AppLayout"
import { EmptyPane } from "../../../shared/EmptyPane"
import { ConfirmDialog } from "../../../shared/ConfirmDialog"
import { useNavigation } from "../../../../context/NavigationContext"
import { useAuth } from "../../../../lib/auth"
import { PROJECTS_PATH } from "../../../../lib/routes"
import {
  ApiError,
  projectsApi,
  type ArtifactItem,
  type ProjectArtifactType,
  type ProjectDetail,
  type ProjectMember,
  type ProjectMemoryInsight,
  type ProjectMemorySummary,
} from "../../../../lib/api"
import { ProjectMainThread } from "./ProjectMainThread"
import { MemoryModal } from "./MemoryModal"
import { ArtifactsModal } from "./ArtifactsModal"
import { TaskModal } from "./TaskModal"
import styles from "./ProjectDetailScreen.module.css"

type HumanMember = Extract<ProjectMember, { kind: "human" }>

/** Copied verbatim from `ArtifactsScreen.tsx`'s `ARTIFACT_BADGE` (same
 *  exception `ProjectsScreen.module.css` already takes) — the app's real
 *  per-type semantic palette, not a new one. Duplicated locally rather than
 *  imported: `ArtifactsScreen` is not a declared Deliverable for this
 *  ticket. */
const TYPE_BADGE: Record<ProjectArtifactType, { label: string; bg: string; color: string }> = {
  prd: { label: "PRD", bg: "var(--accent-soft)", color: "var(--accent-ink)" },
  evidence: { label: "Evidence", bg: "#FEF0E6", color: "#B45309" },
  prototype: { label: "Prototype", bg: "#DBEAFE", color: "#1E40AF" },
  report: { label: "Report", bg: "#EDE9FE", color: "#6D28D9" },
  ticket_set: { label: "Tickets", bg: "var(--info-soft)", color: "var(--info)" },
}

const TYPE_ORDER: ProjectArtifactType[] = ["prd", "prototype", "evidence", "report", "ticket_set"]

/** Same compact relative-time bucketing `ProjectsScreen.tsx`/
 *  `ArtifactsScreen.tsx` use, duplicated locally per the same precedent. */
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

/** Same initials algorithm `TicketsScreen.tsx`/`TicketDetail.tsx` already
 *  duplicate locally — not a shared export in this codebase. */
function initials(name: string | null | undefined): string {
  if (!name) return "?"
  return name.split(" ").filter(Boolean).map((w) => w[0]).join("").toUpperCase().slice(0, 2)
}

/** The teaser text on the Project-memory rail card: the summary's first
 *  sentence (AC7), stripped of any markdown emphasis. Falls back to a
 *  plain "nothing yet" line when no summary has been generated. */
function firstSentence(summaryMd: string | null): string {
  if (!summaryMd) return "Nothing synthesized yet — insights will appear as the team collaborates."
  const stripped = summaryMd.replace(/[#*_`]/g, "").trim()
  const match = stripped.match(/^[^.!?]*[.!?]/)
  return (match ? match[0] : stripped).trim()
}

function groupArtifactsByType(
  artifacts: ArtifactItem[],
): Partial<Record<ProjectArtifactType, ArtifactItem[]>> {
  const out: Partial<Record<ProjectArtifactType, ArtifactItem[]>> = {}
  for (const a of artifacts) {
    const bucket = (out[a.type] ??= [])
    bucket.push(a)
  }
  return out
}

function mostRecent(items: ArtifactItem[]): string {
  let latest = items[0]?.created_at ?? ""
  for (const it of items) {
    if (it.created_at > latest) latest = it.created_at
  }
  return latest
}

// ── Small icons ──

function BackArrowIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 6l-6 6 6 6" />
    </svg>
  )
}

function FolderIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  )
}

function PanelIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <line x1="15" y1="4" x2="15" y2="20" />
    </svg>
  )
}

function GroupChatIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <line x1="4" y1="9" x2="20" y2="9" />
      <line x1="4" y1="15" x2="20" y2="15" />
      <line x1="10" y1="3" x2="8" y2="21" />
      <line x1="16" y1="3" x2="14" y2="21" />
    </svg>
  )
}

function LockIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  )
}

function ClockIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v4l3 2" />
    </svg>
  )
}

function PlusIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  )
}

function ChecklistIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 11l3 3 8-8" />
      <path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9" />
    </svg>
  )
}

function RemoveIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
      <line x1="6" y1="6" x2="18" y2="18" />
      <line x1="18" y1="6" x2="6" y2="18" />
    </svg>
  )
}

function WarnIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true">
      <path d="M13 2 3 14h7l-1 8 10-12h-7z" />
    </svg>
  )
}

function ArtifactTypeIcon({ type }: { type: ProjectArtifactType }) {
  const cfg = TYPE_BADGE[type]
  return (
    <span className={styles.artifactIcon} style={{ background: cfg.bg, color: cfg.color }} aria-hidden="true">
      <FolderIcon />
    </span>
  )
}

// ── Presentational pieces ──

type ActiveChat = "group" | "individual"

export type ProjectDetailViewProps = {
  project: ProjectDetail
  artifacts: ArtifactItem[]
  memory: ProjectMemorySummary
  railCollapsed: boolean
  onToggleRail: () => void
  activeChat: ActiveChat
  onSelectChat: (chat: ActiveChat) => void
  onOpenArtifacts: (type?: ProjectArtifactType) => void
  onCreateArtifact: () => void
  onOpenMemory: () => void
  onAddMemory: () => void
  onOpenTasks: () => void
  onInvite: () => void
  /** The cross-chat INSIGHT turn (design-spec AC7), fed from
   *  `GET /v1/projects/{id}/memory/insight` — `null` when the project has
   *  no agent-promoted memory entry yet, or the fetch failed (best-effort,
   *  AC5). Passed straight through to `ProjectMainThread`'s existing,
   *  unmodified prop chain. */
  insightNote?: ProjectMemoryInsight | null
  /** The signed-in caller's user id (`null` when unresolved) — used ONLY to
   *  withhold the Remove control on the caller's OWN member row (self-
   *  removal/"leave project" is out of scope for this ticket; the backend
   *  rejects it too, this just avoids offering a control that would 400). */
  currentUserId: string | null
  /** Requests removing a human member — opens the confirm dialog. The
   *  CONTAINER owns the confirm/busy/error state and the actual
   *  `removeMember` call (same split as every other rail-triggered action
   *  here). Never invoked for the agent member, the project creator, or
   *  the caller themselves — the View withholds the control on those
   *  rows entirely. */
  onRemoveMember: (member: HumanMember) => void
}

/** Pure presentational shell — the surface a test renders directly, same
 *  View/Screen split as `ProjectsView`/`ProjectsScreen`. */
export function ProjectDetailView({
  project,
  artifacts,
  memory,
  railCollapsed,
  onToggleRail,
  activeChat,
  onSelectChat,
  onOpenArtifacts,
  onCreateArtifact,
  onOpenMemory,
  onAddMemory,
  onOpenTasks,
  onInvite,
  insightNote,
  currentUserId,
  onRemoveMember,
}: ProjectDetailViewProps) {
  const humans = useMemo(() => project.members.filter((m): m is HumanMember => m.kind === "human"), [project.members])
  const agent = useMemo(() => project.members.find((m): m is Extract<ProjectMember, { kind: "agent" }> => m.kind === "agent"), [project.members])
  const byType = useMemo(() => groupArtifactsByType(artifacts), [artifacts])
  const presentTypes = TYPE_ORDER.filter((t) => (byType[t]?.length ?? 0) > 0)

  return (
    <div className={styles.shell}>
      <header className={styles.topBar}>
        <Link className={styles.backLink} href={PROJECTS_PATH} data-testid="back-to-projects">
          <BackArrowIcon />
          All projects
        </Link>
        <span className={styles.topSep} aria-hidden="true" />
        <span className={styles.projName} data-testid="project-name">
          <FolderIcon />
          {project.name}
        </span>
        {humans.length > 0 ? (
          <span className={styles.topAvatars} data-testid="topbar-avatars" aria-label={`${humans.length} member${humans.length === 1 ? "" : "s"}`}>
            {humans.slice(0, 4).map((m) => (
              <span key={m.user_id} className={styles.topAv} title={m.name ?? "Member"} aria-hidden="true">
                {initials(m.name)}
              </span>
            ))}
          </span>
        ) : null}
        <span className={styles.topSpacer} />
        <button
          type="button"
          className={styles.railToggle}
          onClick={onToggleRail}
          aria-pressed={railCollapsed}
          data-testid="rail-toggle"
        >
          <PanelIcon />
          {railCollapsed ? "Show panel" : "Hide panel"}
        </button>
      </header>

      <div className={`${styles.body} ${railCollapsed ? styles.bodyRailCollapsed : ""}`}>
        <main className={styles.main} aria-label="Project chat">
          <div className={styles.chatNote} data-testid="chat-note">
            {activeChat === "group" ? (
              <>
                <ClockIcon />
                Group chat · open to all members. Sprntly uses <b>smart interjection</b> — jumps in when a
                turn is for it, stays out otherwise.
              </>
            ) : (
              <>
                <LockIcon />
                Private · just you + Sprntly. This thread <b>feeds project memory</b> as summaries — never
                transcripts; your chats outside this project stay walled off.
              </>
            )}
          </div>

          {/* Chat thread HOST (AD-P13): ProjectMainThread swaps group ⇆
              individual per `activeChat`; BOTH sides are thin containers
              over the SAME extracted composer — no second composer lives
              at this shell level, and no chat-monolith container is
              imported anywhere in this swap. */}
          <div className={styles.threadHost} data-testid="project-main-thread-host">
            <ProjectMainThread
              projectId={project.id}
              activeChat={activeChat}
              onOpenArtifact={(c) => onOpenArtifacts(c.type)}
              insightNote={insightNote}
            />
          </div>
        </main>

        {!railCollapsed ? (
          <aside className={styles.rail} aria-label="Project panel" data-testid="project-rail">
            <div className={styles.railSectionLabel} data-testid="rail-section-label">
              Chats
            </div>
            <button
              type="button"
              className={`${styles.chatRow} ${activeChat === "group" ? styles.chatRowActive : ""}`}
              onClick={() => onSelectChat("group")}
              aria-pressed={activeChat === "group"}
              data-testid="chat-row-group"
            >
              <span className={styles.chatRowIcon} aria-hidden="true">
                <GroupChatIcon />
              </span>
              <span className={styles.chatRowTitle}>Group chat</span>
              <span className={styles.chatRowDot} title="active now" aria-label="active now" />
            </button>
            <button
              type="button"
              className={`${styles.chatRow} ${activeChat === "individual" ? styles.chatRowActive : ""}`}
              onClick={() => onSelectChat("individual")}
              aria-pressed={activeChat === "individual"}
              data-testid="chat-row-individual"
            >
              <span className={styles.chatRowIcon} aria-hidden="true">
                <LockIcon />
              </span>
              <span className={styles.chatRowTitle}>My chat with Sprntly</span>
              <span className={styles.chatRowPrivate}>private</span>
            </button>

            <div className={styles.railSectionLabel} data-testid="rail-section-label">
              Artifacts
              {artifacts.length > 0 ? <span className={styles.railSectionCount}>{artifacts.length}</span> : null}
            </div>
            {presentTypes.map((t) => {
              const items = byType[t] ?? []
              const cfg = TYPE_BADGE[t]
              return (
                <button
                  type="button"
                  key={t}
                  className={styles.artifactCard}
                  onClick={() => onOpenArtifacts(t)}
                  aria-label={`Open ${cfg.label} artifacts`}
                  data-testid={`artifact-card-${t}`}
                >
                  <ArtifactTypeIcon type={t} />
                  <div className={styles.artifactMain}>
                    <div className={styles.artifactTitle}>{cfg.label}</div>
                    <div className={styles.artifactSub} data-testid={`artifact-card-${t}-sub`}>
                      {items.length} item{items.length === 1 ? "" : "s"} · updated {relativeTime(mostRecent(items))}
                    </div>
                  </div>
                  <span className={styles.artifactOpen} aria-hidden="true">
                    &#8599;
                  </span>
                </button>
              )
            })}
            <button
              type="button"
              className={`${styles.artifactCard} ${styles.artifactCreate}`}
              onClick={onCreateArtifact}
              data-testid="artifact-create"
            >
              <span className={styles.artifactPlus} aria-hidden="true">
                <PlusIcon />
              </span>
              Create new artifact — PRD, ticket, evidence or prototype
            </button>

            <div className={styles.railSectionLabel} data-testid="rail-section-label">
              Project
            </div>
            <div className={styles.card} data-testid="memory-card">
              <div className={styles.cardHead}>
                <h4>
                  <ClockIcon />
                  Project memory
                </h4>
                <span className={styles.cardCount}>{memory.entry_count}</span>
              </div>
              <div className={styles.teaser} data-testid="memory-teaser">
                <div className={styles.teaserSrc}>What this project knows · read-only</div>
                {firstSentence(memory.summary_md)}
              </div>
              <div className={styles.cardActions}>
                <button type="button" className={styles.viewAllBtn} onClick={onOpenMemory} data-testid="memory-view-all">
                  View all {memory.entry_count} insight{memory.entry_count === 1 ? "" : "s"}
                </button>
                <button type="button" className={styles.addBtn} onClick={onAddMemory} title="Add a memory entry" data-testid="memory-add">
                  <PlusIcon /> Add
                </button>
              </div>
            </div>

            <div className={styles.card} data-testid="task-ledger-card">
              <div className={styles.cardHead}>
                <h4>
                  <ChecklistIcon />
                  Task ledger
                </h4>
                <span className={styles.fastFollowBadge} data-testid="task-ledger-fastfollow">
                  Fast-follow
                </span>
              </div>
              <p style={{ margin: "0 0 4px", fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.45 }}>
                Per-person to-do / waiting-on tracking is coming — not wired yet.
              </p>
              <div className={styles.cardActions}>
                <button type="button" className={styles.viewAllBtn} onClick={onOpenTasks} data-testid="task-ledger-view-all">
                  View all tasks
                </button>
              </div>
            </div>

            <div className={styles.railSectionLabel} data-testid="rail-section-label">
              Members
              <span className={styles.railSectionCount}>{project.members.length}</span>
            </div>
            {humans.map((m) => {
              // Removable-row rule (AC3): never the project creator, never
              // the caller themselves. The agent member never reaches this
              // loop at all (it's rendered separately below).
              const removable = m.user_id !== project.created_by && m.user_id !== currentUserId
              return (
                <div className={styles.memberRow} key={m.user_id} data-testid="member-row-human">
                  <span className={styles.memberAv} aria-hidden="true">
                    {initials(m.name)}
                  </span>
                  <div className={styles.memberMain}>
                    <div className={styles.memberName}>{m.name ?? "Unnamed member"}</div>
                    <div className={styles.memberRole}>{m.job_role || "Member"}</div>
                  </div>
                  {removable ? (
                    <button
                      type="button"
                      className={styles.memberRemoveBtn}
                      onClick={() => onRemoveMember(m)}
                      aria-label={`Remove ${m.name ?? "member"} from project`}
                      title="Remove from project"
                      data-testid="member-remove"
                    >
                      <RemoveIcon />
                    </button>
                  ) : null}
                </div>
              )
            })}
            {agent ? (
              <div className={`${styles.memberRow} ${styles.memberRowAgent}`} data-testid="member-row-agent">
                <span className={styles.agentAv} aria-hidden="true">
                  s
                </span>
                <div className={styles.memberMain}>
                  <div className={styles.memberName}>
                    {agent.name} <span className={styles.agentTag}>Agent</span>
                  </div>
                  <div className={styles.memberRole}>{agent.role_label}</div>
                </div>
                <span className={styles.workingPill}>{agent.status}</span>
              </div>
            ) : null}
            <div className={styles.inviteRow}>
              <input className={styles.inviteInput} placeholder="Invite by email…" aria-label="Invite by email" />
              <button type="button" className={styles.inviteBtn} onClick={onInvite} data-testid="invite-button">
                Invite
              </button>
            </div>
            <div className={styles.tagNote}>
              <WarnIcon />
              <div>
                <b>Tag anyone by email — even non-members.</b> They get an invite, create an account, and land
                straight in this project.{" "}
                <span className={styles.fastFollowBadge} style={{ marginLeft: 2 }}>
                  Fast-follow
                </span>
              </div>
            </div>
          </aside>
        ) : null}
      </div>
    </div>
  )
}

// ── Container: fetch + state machine (loading / 403 / 404 / error / ready) ──

type LoadState =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "not_found" }
  | { status: "error" }
  | {
      status: "ready"
      project: ProjectDetail
      artifacts: ArtifactItem[]
      memory: ProjectMemorySummary
      /** Best-effort — a failed/absent insight fetch never blocks or
       *  errors the shell (AC5); `null` renders no insight turn. */
      insight: ProjectMemoryInsight | null
    }

/** Which of this ticket's rail-triggered modals is open, if any. `artifacts`
 *  carries the type the rail card/inline chip was opened FOR (or `undefined`
 *  for the generic "View all"/chip-less opens) — the modal itself owns the
 *  filter-chip state from there. */
type OpenModal = { kind: "memory" } | { kind: "artifacts"; type?: ProjectArtifactType } | { kind: "tasks" } | null

export function ProjectDetailScreen({ projectId }: { projectId: string }) {
  const { openModal } = useNavigation()
  const auth = useAuth()
  const currentUserId = auth.kind === "authed" ? auth.user.id : null
  const [state, setState] = useState<LoadState>({ status: "loading" })
  const [railCollapsed, setRailCollapsed] = useState(false)
  const [activeChat, setActiveChat] = useState<ActiveChat>("group")
  const [railModal, setRailModal] = useState<OpenModal>(null)
  const [removeTarget, setRemoveTarget] = useState<HumanMember | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const [removeError, setRemoveError] = useState<string | null>(null)

  const load = useCallback(() => {
    setState({ status: "loading" })
    Promise.all([
      projectsApi.get(projectId),
      projectsApi.artifacts(projectId),
      projectsApi.memorySummary(projectId),
      // Best-effort (AC5): a failed insight fetch must never fail the
      // whole shell load — caught locally to `null`, same posture as an
      // absent insight, rather than propagating into the outer `.catch`
      // below (which drives the 403/404/error gate states).
      Promise.resolve(projectsApi.memoryInsight(projectId)).catch(() => null),
    ])
      .then(([project, artifacts, memory, insight]) => {
        setState({ status: "ready", project, artifacts, memory, insight })
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) {
          setState({ status: "forbidden" })
        } else if (err instanceof ApiError && err.status === 404) {
          setState({ status: "not_found" })
        } else {
          setState({ status: "error" })
        }
      })
  }, [projectId])

  useEffect(() => {
    load()
  }, [load])

  // Re-fetches ONLY the project row (members + count) after a roster
  // mutation — deliberately not `load()`: that flashes the whole shell back
  // to its "loading" state, wiping the artifacts/memory panes and the
  // active thread for no reason. AC3 ("without a full reload") is best met
  // by updating just the piece that changed.
  const refetchProject = useCallback(() => {
    projectsApi
      .get(projectId)
      .then((project) => {
        setState((prev) => (prev.status === "ready" ? { ...prev, project } : prev))
      })
      .catch(() => {
        // Best-effort: the removal itself already succeeded (this only
        // refreshes the displayed roster); a transient refetch failure
        // leaves the pre-removal roster showing until the next real load.
      })
  }, [projectId])

  const onToggleRail = useCallback(() => setRailCollapsed((v) => !v), [])
  const onInvite = useCallback(() => openModal("invite"), [openModal])
  // The memory/artifacts/task modals below are this ticket's bodies for
  // these rail-card triggers. `onCreateArtifact` ("+ Create new artifact")
  // stays a no-op — that flow routes into the app's EXISTING generation
  // surfaces (per the ticket's explicit scope boundary), not a new modal.
  const onOpenArtifacts = useCallback((type?: ProjectArtifactType) => setRailModal({ kind: "artifacts", type }), [])
  const onCreateArtifact = useCallback(() => {}, [])
  const onOpenMemory = useCallback(() => setRailModal({ kind: "memory" }), [])
  const onAddMemory = useCallback(() => setRailModal({ kind: "memory" }), [])
  const onOpenTasks = useCallback(() => setRailModal({ kind: "tasks" }), [])
  const onCloseRailModal = useCallback(() => setRailModal(null), [])

  const onRemoveMember = useCallback((member: HumanMember) => {
    setRemoveError(null)
    setRemoveTarget(member)
  }, [])
  const onCancelRemove = useCallback(() => {
    if (removeBusy) return
    setRemoveTarget(null)
    setRemoveError(null)
  }, [removeBusy])
  const onConfirmRemove = useCallback(() => {
    if (!removeTarget) return
    setRemoveBusy(true)
    setRemoveError(null)
    projectsApi
      .removeMember(projectId, removeTarget.user_id)
      .then(() => {
        setRemoveBusy(false)
        setRemoveTarget(null)
        refetchProject()
      })
      .catch((err: unknown) => {
        setRemoveBusy(false)
        setRemoveError(
          err instanceof ApiError ? err.message || "Couldn't remove that member." : "Couldn't remove that member.",
        )
      })
  }, [projectId, removeTarget, refetchProject])

  if (state.status === "loading") {
    return (
      <AppLayout hideChromeStrip mainStyle={{ padding: 0, display: "flex", flexDirection: "column", minHeight: 0, flex: "1 1 auto" }}>
        <div className={styles.stateWrap} style={{ padding: 24 }} aria-busy="true" data-testid="project-detail-loading">
          Loading…
        </div>
      </AppLayout>
    )
  }

  if (state.status === "forbidden" || state.status === "not_found" || state.status === "error") {
    const copy =
      state.status === "forbidden"
        ? {
            title: "You're not a member of this project",
            hint: "Ask a project member to add you, then come back.",
          }
        : state.status === "not_found"
          ? { title: "Project not found", hint: "It may have been removed, or the link is wrong." }
          : { title: "Couldn't load this project", hint: "Something went wrong loading this project. Try again." }
    return (
      <AppLayout>
        <div className={styles.stateWrap} data-testid={`project-detail-${state.status}`}>
          <EmptyPane title={copy.title} hint={copy.hint} />
          <p style={{ marginTop: 16 }}>
            <Link href={PROJECTS_PATH} data-testid="back-to-projects-from-error">
              ← Back to all projects
            </Link>
          </p>
        </div>
      </AppLayout>
    )
  }

  return (
    <AppLayout
      hideChromeStrip
      mainStyle={{ maxWidth: "none", padding: 0, display: "flex", flexDirection: "column", minHeight: 0, flex: "1 1 auto" }}
    >
      <ProjectDetailView
        project={state.project}
        artifacts={state.artifacts}
        memory={state.memory}
        insightNote={state.insight}
        railCollapsed={railCollapsed}
        onToggleRail={onToggleRail}
        activeChat={activeChat}
        onSelectChat={setActiveChat}
        onOpenArtifacts={onOpenArtifacts}
        onCreateArtifact={onCreateArtifact}
        onOpenMemory={onOpenMemory}
        onAddMemory={onAddMemory}
        onOpenTasks={onOpenTasks}
        onInvite={onInvite}
        currentUserId={currentUserId}
        onRemoveMember={onRemoveMember}
      />
      <ConfirmDialog
        open={removeTarget != null}
        title={`Remove ${removeTarget?.name ?? "this member"}?`}
        body={removeError ?? "They'll lose access to this project's chats, artifacts, and memory."}
        confirmLabel="Remove"
        busyLabel="Removing…"
        tone="danger"
        busy={removeBusy}
        onConfirm={onConfirmRemove}
        onCancel={onCancelRemove}
      />
      <MemoryModal
        projectId={projectId}
        members={state.project.members}
        open={railModal?.kind === "memory"}
        onClose={onCloseRailModal}
      />
      <ArtifactsModal
        projectId={projectId}
        open={railModal?.kind === "artifacts"}
        initialFilter={railModal?.kind === "artifacts" ? railModal.type : undefined}
        onClose={onCloseRailModal}
      />
      <TaskModal open={railModal?.kind === "tasks"} onClose={onCloseRailModal} />
    </AppLayout>
  )
}
