"use client"

// ── ProjectDetailScreen — the group-chat-centric detail shell (v4.0) ──
//
// Flat route (AD-P14): mounts from `web/app/(app)/projects/ProjectsRoute.tsx`
// when `?id=<id>` is present on the one `/projects` route — NO `[id]`
// dynamic segment. `projectId` is threaded in as a prop from that route's
// own `useSearchParams().get("id")` read, so this component stays
// testable in isolation without mocking `next/navigation`.
//
// Layout (redesign): full-bleed chat is first-class — there is no standing
// right rail. Settings/artifacts/members/memory/invite are demoted behind a
// top-bar gear (`ProjectSettingsModal`, a 4-tab modal) and an "Artifacts (N)"
// button (`ArtifactsModal`); a chat-driven PRD open lands in the SAME global
// side-panel main uses (`ContentPanel`, mounted at the app root via AppShell) —
// the `<main>` is full-bleed chat and the panel slides in as an overlay,
// identical to main. Wired to the real
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
import { useRouter, useSearchParams } from "next/navigation"
import { AppLayout } from "../AppLayout"
import { Spinner } from "../../../auth/icons"
import { EmptyPane } from "../../../shared/EmptyPane"
import { ConfirmDialog } from "../../../shared/ConfirmDialog"
import { useAuth } from "../../../../lib/auth"
import { PROJECTS_PATH } from "../../../../lib/routes"
import {
  ApiError,
  projectsApi,
  type ArtifactItem,
  type DelegationCounts,
  type DelegationLedgerRow,
  type OpenArtifactCandidate,
  type ProjectArtifactType,
  type ProjectDetail,
  type ProjectMember,
  type ProjectMemoryInsight,
  type ProjectMemorySummary,
} from "../../../../lib/api"
import { openArtifactDestination } from "../../../shared/chat-shell/openArtifactDestination"
import { openArtifactCandidateAsItem } from "./artifactCandidates"
import { ProjectMainThread } from "./ProjectMainThread"
import { ArtifactsModal } from "./ArtifactsModal"
import { ProjectSettingsModal, type SettingsTab } from "./ProjectSettingsModal"
import { useContent } from "../../../../context/ContentContext"
import { useNavigation } from "../../../../context/NavigationContext"
import { loadPrdById } from "../../../../lib/runPrdGeneration"
import { TaskModal } from "./TaskModal"
import { useRealtimeChannel } from "./useRealtimeChannel"
import { personAvatarStyle } from "./avatarColor"
import { IconPlus } from "../../../shared/app-icons"
import styles from "./ProjectDetailScreen.module.css"

// Focus-gated FALLBACK poll interval for the unread badge (AD-P22) — same
// cadence `ProjectGroupChat.tsx`'s own `POLL_MS` uses (AD-P4), duplicated
// locally per this file's existing precedent (`initials`, etc.). Demoted
// below: the live per-user channel + its reconnect reconcile carry the
// badge while connected; this interval only arms when degraded.
const UNREAD_POLL_MS = 4000

type HumanMember = Extract<ProjectMember, { kind: "human" }>

/** Same initials algorithm `TicketsScreen.tsx`/`TicketDetail.tsx` already
 *  duplicate locally — not a shared export in this codebase. */
function initials(name: string | null | undefined): string {
  if (!name) return "?"
  return name.split(" ").filter(Boolean).map((w) => w[0]).join("").toUpperCase().slice(0, 2)
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

function GearIcon() {
  // A recognizable toothed cog (Feather/Lucide "settings" style) — the
  // prior center-dot-plus-spokes glyph read as a sun/asterisk next to the
  // theme toggle.
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  )
}

function ArtifactsIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l8.5 4.7L12 12.4 3.5 7.7 12 3z" />
      <path d="M3.5 12L12 16.7 20.5 12" />
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

// ── Presentational pieces ──

type ActiveChat = "group" | "individual"

export type ProjectDetailViewProps = {
  project: ProjectDetail
  artifacts: ArtifactItem[]
  memory: ProjectMemorySummary
  activeChat: ActiveChat
  onSelectChat: (chat: ActiveChat) => void
  /** Derived (AD-P3/AD-P20 — never a stored boolean upstream, this is the
   *  server's `unread` field passed straight through): true when the
   *  caller's individual chat has a turn beyond their read cursor. Renders
   *  a dot on the top-bar Private tab; clears when the tab is selected
   *  (the container POSTs `/individual/read` on that transition). */
  individualUnread: boolean
  /** Open-only delegation counts for the Task-ledger rail card, mirrored
   *  from `GET /v1/projects/{id}/delegations/counts` on fetch/poll the same
   *  way `individualUnread` is — `null` before the first fetch resolves or
   *  when it failed (a best-effort convenience; the modal is the authority). */
  ledgerCounts: DelegationCounts | null
  /** A small preview of OPEN ledger rows for the rail card's checklist — the
   *  real party-filtered reads (`assigned_to_me` + `waiting_on`), capped for
   *  the rail. Empty before the first fetch resolves or when none are open;
   *  the modal (`onOpenTasks`) stays the authority. */
  ledgerRows: DelegationLedgerRow[]
  onOpenArtifacts: (type?: ProjectArtifactType) => void
  /** Open ONE artifact IN-PLACE in the drawer beside the chat (no route
   *  change, no modal). The rail's per-type card uses this for a type that
   *  maps to a single openable artifact; a multi-artifact type falls back to
   *  `onOpenArtifacts` (the browse modal). Same handler the ArtifactsModal's
   *  "Open ↗" routes through. */
  onOpenArtifactInPlace: (artifact: ArtifactItem) => void
  /** The PRD open in the shared side-panel (`content.prd` when the panel is on
   *  its "prd" tab), threaded to both chat surfaces as the edit target — parity
   *  with main chat's open-tab `prd_id`. `null` when no PRD is open. */
  openPrdId: number | null
  onOpenTasks: () => void
  /** Opens the top-bar "Project settings" gear (`ProjectSettingsModal`),
   *  landing on its default Instructions tab — the container owns the
   *  `settingsTab` state and mounts the modal; navigating to a specific tab
   *  from there happens inside the modal itself (its own tab buttons). */
  onOpenSettings: () => void
  /** Opens the project settings modal directly on the INVITE tab — wired to
   *  the "+" invite affordance next to the top-bar member avatars. Same
   *  modal/open mechanism the gear uses, just targeting a specific tab. */
  onOpenInvite: () => void
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
  /** Re-fetches ONLY the project's artifact list (#9-count) — threaded into
   *  `ProjectMainThread` -> `ProjectPrivateChat` so a CLIENT-driven generate
   *  (the sender's own `runGeneratePrd`/`runGenerateTickets`) refreshes the
   *  list + count immediately, without waiting on the realtime echo. */
  refetchArtifacts: () => void
}

/** Pure presentational shell — the surface a test renders directly, same
 *  View/Screen split as `ProjectsView`/`ProjectsScreen`. */
export function ProjectDetailView({
  project,
  artifacts,
  memory: _memory,
  activeChat,
  onSelectChat,
  individualUnread,
  ledgerCounts: _ledgerCounts,
  ledgerRows: _ledgerRows,
  onOpenArtifacts,
  onOpenArtifactInPlace,
  openPrdId,
  onOpenTasks,
  onOpenSettings,
  onOpenInvite,
  insightNote,
  currentUserId: _currentUserId,
  onRemoveMember: _onRemoveMember,
  refetchArtifacts,
}: ProjectDetailViewProps) {
  const humans = useMemo(() => project.members.filter((m): m is HumanMember => m.kind === "human"), [project.members])
  // The SAME decision main chat uses for "open the PRD" — the evidence-vs-PRD
  // branch, resume-conversation-first, reuse-by-prd-id and null-id guards all
  // live in `openArtifactDestination`; this project surface supplies the
  // DRAWER as its terminal action instead of main's panel-tab. `resumeConversation`
  // returns false (a project's chat has no cross-conversation resume path),
  // which lets the shared decision fall through to `openPrd` — the one real
  // difference from main, not a fork of the decision itself. A candidate with
  // no openable id (the shared function's own null-id guard) keeps today's
  // browse-modal-by-type fallback rather than opening an empty drawer.
  const onOpenArtifactCandidate = (candidate: OpenArtifactCandidate) => {
    const opened = openArtifactDestination(candidate, {
      // Evidence — exactly like main: scope the shared panel's Evidence tab to
      // this insight (by-context, not by-id), and it self-loads the existing
      // evidence. The shared decision only routes here when the candidate is
      // brief-anchored, so the (briefId, insightIndex) pair is real.
      openEvidence: (c) => {
        onOpenArtifactInPlace(openArtifactCandidateAsItem(c))
        return true
      },
      resumeConversation: () => false,
      // The PRIMARY path — exactly like main: load the PRD by id into the global
      // content store and open the shared side-panel.
      openPrd: (c, prdId) => {
        onOpenArtifactInPlace(openArtifactCandidateAsItem(c, prdId))
        return true
      },
    })
    if (!opened) onOpenArtifacts(candidate.type)
  }

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
              <span
                key={m.user_id}
                className={styles.topAv}
                title={m.name ?? "Member"}
                aria-hidden="true"
                style={personAvatarStyle(m.user_id, m.name)}
              >
                {initials(m.name)}
              </span>
            ))}
          </span>
        ) : null}

        {/* "+" invite affordance — sits next to the member avatars and opens
            the project settings modal directly on the Invite tab. */}
        <button
          type="button"
          className={styles.avInviteBtn}
          onClick={onOpenInvite}
          aria-label="Invite members"
          title="Invite members"
          data-testid="topbar-invite"
        >
          <IconPlus size={14} />
        </button>

        <span className={styles.topSpacer} />

        {/* Group ⇆ Private chat switch — sits in the RIGHT cluster (after the
            spacer), immediately left of the Artifacts button. Same
            testids/behaviour; placement-only move from the left cluster. */}
        <div className={styles.topChatToggle} role="tablist" aria-label="Chat" data-testid="topbar-chat-toggle">
          <button
            type="button"
            role="tab"
            className={`${styles.chatToggleBtn} ${activeChat === "group" ? styles.chatToggleBtnActive : ""}`}
            onClick={() => onSelectChat("group")}
            aria-selected={activeChat === "group"}
            data-testid="chat-row-group"
          >
            <GroupChatIcon />
            Group
            <span className={styles.chatToggleDot} title="active now" aria-label="active now" />
          </button>
          <button
            type="button"
            role="tab"
            className={`${styles.chatToggleBtn} ${activeChat === "individual" ? styles.chatToggleBtnActive : ""}`}
            onClick={() => onSelectChat("individual")}
            aria-selected={activeChat === "individual"}
            data-testid="chat-row-individual"
          >
            <LockIcon />
            Private
            {individualUnread ? (
              <span
                className={styles.chatRowUnreadDot}
                title="unread"
                aria-label="unread"
                data-testid="individual-chat-unread-dot"
              />
            ) : null}
          </button>
        </div>

        {/* Top-bar "See all tasks" affordance removed — the task ledger is
            reached conversationally (backend `get_task_ledger`, rendered
            inline in chat). The `onOpenTasks` → TaskModal open-state chain
            below is deliberately retained (not orphaned) so the modal stays
            mountable for a future/other trigger without a re-wire. */}
        <button
          type="button"
          className={styles.railToggle}
          onClick={() => onOpenArtifacts()}
          data-testid="topbar-artifacts"
        >
          <ArtifactsIcon />
          Artifacts
          <span className={styles.topbarCount}>{artifacts.length}</span>
        </button>
        <button
          type="button"
          className={styles.gearBtn}
          onClick={onOpenSettings}
          aria-label="Project settings"
          data-testid="project-settings-gear"
        >
          <GearIcon />
        </button>
      </header>

      <div className={styles.body}>
        <main className={styles.main} aria-label="Project chat">
          <div className={styles.chatNote} data-testid="chat-note">
            {activeChat === "group" ? (
              humans.length > 1 ? (
                <>
                  Open to all members. Tag <b>@Sprntly</b> to bring the agent in.
                </>
              ) : (
                <>
                  Open to all members. Sprntly <b>replies to every message</b> here.
                </>
              )
            ) : (
              <>
                Just you + Sprntly. This thread <b>feeds project memory</b> as summaries — never
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
              // Project-switch isolation (an adversarial review): a flat-route
              // project A→B `?id=` change with no remount would carry over the
              // shell + BOTH engines + the picker; keying the whole thread on
              // `project.id` resets them together. Latent/defensive — no current
              // nav path does a direct A→B without an unmount, so this makes the
              // asserted flat-route premise hold rather than patching a live bug.
              key={project.id}
              projectId={project.id}
              projectName={project.name}
              humanMemberCount={humans.length}
              activeChat={activeChat}
              onOpenArtifact={onOpenArtifactCandidate}
              insightNote={insightNote}
              onArtifactsChanged={refetchArtifacts}
              openPrdId={openPrdId}
            />
          </div>
        </main>

        {/* No in-place artifact column: the project chat opens artifacts in the
            SAME global side-panel (`ContentPanel`) main uses, mounted at the app
            root (`AppShell`). The `<main>` is full-bleed chat; the panel slides
            in as an overlay via `app--cpanel-open`, identical to main. */}
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

/** Which of this ticket's top-bar-triggered modals is open, if any.
 *  `artifacts` carries the type the top-bar chip was opened FOR (or
 *  `undefined` for the generic "View all"/chip-less opens) — the modal
 *  itself owns the filter-chip state from there. Memory no longer has its
 *  own variant here — it's a tab inside `ProjectSettingsModal` now (a
 *  separate `settingsTab` state below), reached via the top-bar gear. */
type OpenModal =
  | { kind: "artifacts"; type?: ProjectArtifactType }
  | { kind: "tasks" }
  | null

export function ProjectDetailScreen({
  projectId,
  initialChat,
}: {
  projectId: string
  /** Which chat tab to land on when the shell first mounts — set by the
   *  main-chat PRD fork nav via `?chat=individual` (`ProjectsRoute.tsx`).
   *  Defaults to the shell's own `"group"` default when absent. */
  initialChat?: ActiveChat
}) {
  const auth = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const currentUserId = auth.kind === "authed" ? auth.user.id : null
  const [state, setState] = useState<LoadState>({ status: "loading" })
  const [activeChat, setActiveChat] = useState<ActiveChat>(initialChat ?? "group")
  const [railModal, setRailModal] = useState<OpenModal>(null)
  // The top-bar gear's "Project settings" modal — `null` = closed, a tab
  // value = open on that tab. Replaces the standalone `MemoryModal`/
  // `ProjectInviteModal` mounts (folded into this modal's Memory/Invite
  // tabs) AND the old rail's collapse toggle (there is no rail to collapse
  // any more — the chat is full-bleed whenever this is closed).
  const [settingsTab, setSettingsTab] = useState<SettingsTab | null>(null)
  const [removeTarget, setRemoveTarget] = useState<HumanMember | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const [removeError, setRemoveError] = useState<string | null>(null)
  // Derived unread signal for the caller's OWN individual chat
  // (AD-P3/AD-P20 — never stored client-side either, just mirrored from the
  // server's derived `unread` field on each fetch/poll tick).
  const [individualUnread, setIndividualUnread] = useState(false)
  // Open-only delegation counts for the Task-ledger rail card — mirrored from
  // the server's derived counts on each fetch/poll tick, exactly like
  // `individualUnread` above (a convenience readout; the modal is the
  // authority). `null` until the first fetch resolves, or when it failed.
  const [ledgerCounts, setLedgerCounts] = useState<DelegationCounts | null>(null)
  // A small preview of OPEN ledger rows for the rail card's checklist. Real,
  // party-filtered reads (`assigned_to_me` + `waiting_on`), deduped and capped
  // — best-effort (a dropped fetch just leaves the counts line showing). The
  // TaskModal stays the authority on the full ledger.
  const [ledgerRows, setLedgerRows] = useState<DelegationLedgerRow[]>([])
  // The artifact opened IN-PLACE beside the chat (AD-HOC: no route change).
  // `null` = drawer closed. Driven purely by local state, never the URL.
  // The SAME global content store + side-panel main uses (mounted via AppShell).
  const { content, setContent } = useContent()
  const { openContentPanel, contentPanelTab } = useNavigation()
  // The PRD open in that panel — parity with main chat's open-tab `prd_id`,
  // threaded to both chat surfaces as the edit target. `null` when no PRD is open.
  const openPrdId =
    contentPanelTab === "prd" && content.prd?.prd_id != null ? content.prd.prd_id : null
  // Monotonic signal bumped whenever a live `delegation.event` (or a reconnect
  // reconcile) lands on the caller's per-user channel — passed to `TaskModal`
  // so an OPEN modal refetches its party-filtered reads without a reopen
  // (AD-P22). A pure signal, not the data: the modal owns its own reads.
  const [ledgerVersion, setLedgerVersion] = useState(0)

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

  // Live individual-unread signal (AD-P25 S2): the caller's OWN, member+
  // owner-gated per-user channel (`project:{id}:user:{uid}`) flips the badge
  // the instant a `brief.delivered` broadcast lands — no poll wait. `onReconcile`
  // fires exactly once per (re)subscribe (the hook's own guarantee) and
  // re-derives the same `individualUnread` signal the poll below uses,
  // closing any at-most-once Broadcast gap (AD-P22). `currentUserId` is the
  // same session-derived id already resolved above (`useAuth()`) — no new
  // fetch; a `null` id (unresolved auth) yields a `null` topic, the hook
  // reports `degraded: true`, and the poll below carries the badge exactly
  // as it always has.
  // Refetch the open-only rail counts (best-effort) — shared by the live
  // `delegation.event` path and the reconnect reconcile below, the same
  // convenience readout the fallback poll tick also drives.
  const refetchLedgerCounts = useCallback(() => {
    projectsApi
      .ledgerCounts(projectId)
      .then((counts) => setLedgerCounts(counts))
      .catch(() => {
        /* best-effort — a dropped read leaves the last-known counts on the card */
      })
  }, [projectId])
  // The rail-card checklist preview: both party-filtered OPEN reads, merged
  // (dedup by delegation_id; assigned-to-me first). Best-effort — a dropped
  // read leaves the last-known rows (or the counts line) showing.
  const refetchLedgerRows = useCallback(() => {
    Promise.all([
      projectsApi.ledger(projectId, "assigned_to_me").catch(() => [] as DelegationLedgerRow[]),
      projectsApi.ledger(projectId, "waiting_on").catch(() => [] as DelegationLedgerRow[]),
    ])
      .then(([mine, waiting]) => {
        const seen = new Set<number>()
        const open: DelegationLedgerRow[] = []
        for (const row of [...mine, ...waiting]) {
          if (row.bucket !== "open" || seen.has(row.delegation_id)) continue
          seen.add(row.delegation_id)
          open.push(row)
        }
        setLedgerRows(open)
      })
      .catch(() => {
        /* best-effort — leave the last-known rows */
      })
  }, [projectId])
  // The caller's OWN per-user channel carries BOTH `brief.delivered` (R1-05,
  // the unread badge) and `delegation.event` (the ledger status change): the
  // latter refetches the rail counts and bumps `ledgerVersion` so an open
  // modal re-reads. Any other event is ignored — one subscription, one topic.
  const handleUnreadEvent = useCallback(
    (event: string) => {
      if (event === "brief.delivered") {
        setIndividualUnread(true)
        return
      }
      if (event === "delegation.event") {
        refetchLedgerCounts()
        refetchLedgerRows()
        setLedgerVersion((v) => v + 1)
      }
    },
    [refetchLedgerCounts, refetchLedgerRows],
  )
  const handleUnreadReconcile = useCallback(() => {
    projectsApi
      .individualUnread(projectId)
      .then((status) => setIndividualUnread(Boolean(status.unread)))
      .catch(() => {
        /* best-effort — the next reconnect or fallback poll tick retries */
      })
    // Ledger surfaces reconcile on reconnect too (AD-P22 reconcile authority):
    // refetch the counts and bump the modal so an open ledger re-reads once.
    refetchLedgerCounts()
    refetchLedgerRows()
    setLedgerVersion((v) => v + 1)
  }, [projectId, refetchLedgerCounts, refetchLedgerRows])
  const unreadTopic = currentUserId ? `project:${projectId}:user:${currentUserId}` : null
  const { degraded: unreadDegraded } = useRealtimeChannel(unreadTopic, {
    onEvent: handleUnreadEvent,
    onReconcile: handleUnreadReconcile,
  })

  // Unread badge: fetch on mount, then a focus-gated poll while the tab has
  // focus — same cadence + focus-gate posture `ProjectGroupChat.tsx`'s own
  // poll uses (AD-P4). Demoted to a fallback by AD-P22: while the realtime
  // channel above is live, the broadcast + reconnect reconcile cover the
  // badge and this interval does not arm; when the channel errors/drops,
  // this re-arms exactly as it always has. Best-effort: a failed fetch/poll
  // tick just leaves the badge in its last-known state, never an error
  // surface.
  useEffect(() => {
    let cancelled = false
    const fetchUnread = () => {
      projectsApi
        .individualUnread(projectId)
        .then((status) => {
          if (!cancelled) setIndividualUnread(Boolean(status.unread))
        })
        .catch(() => {
          /* best-effort — a dropped tick leaves the badge as-is */
        })
      // Ledger counts ride the same fetch/poll tick, mirrored the same way —
      // best-effort: a dropped tick leaves the last-known counts on the card.
      projectsApi
        .ledgerCounts(projectId)
        .then((counts) => {
          if (!cancelled) setLedgerCounts(counts)
        })
        .catch(() => {
          /* best-effort — a dropped tick leaves the counts as-is */
        })
      if (!cancelled) refetchLedgerRows()
    }

    fetchUnread()

    let intervalId: ReturnType<typeof setInterval> | null = null
    const start = () => {
      if (!unreadDegraded) return
      if (intervalId != null) return
      intervalId = setInterval(fetchUnread, UNREAD_POLL_MS)
    }
    const stop = () => {
      if (intervalId == null) return
      clearInterval(intervalId)
      intervalId = null
    }

    if (typeof document !== "undefined" && document.hasFocus()) start()
    const onFocus = () => start()
    const onBlur = () => stop()
    window.addEventListener("focus", onFocus)
    window.addEventListener("blur", onBlur)

    return () => {
      cancelled = true
      stop()
      window.removeEventListener("focus", onFocus)
      window.removeEventListener("blur", onBlur)
    }
  }, [projectId, unreadDegraded, refetchLedgerRows])

  // Selecting the individual row clears the badge: POST /individual/read
  // advances the caller's own cursor server-side, then the local dot state
  // is cleared to match (best-effort — a failed POST just leaves the badge
  // showing until the next successful poll tick re-derives it).
  const onSelectChat = useCallback(
    (chat: ActiveChat) => {
      setActiveChat(chat)
      // Persist the active surface to the URL (`?chat=group|individual`) so a
      // refresh restores it — `ProjectsRoute` reads this back as `initialChat`
      // on mount. Preserve every other param (`id`, `company`) rather than
      // rebuilding the path, and skip the scroll reset (same idiom as
      // `SettingsScreen`'s section sync). Without this, a reload defaults to
      // the shell's own `"group"` and snaps a private chat back to group.
      const params = new URLSearchParams(searchParams?.toString() ?? "")
      params.set("chat", chat)
      router.replace(`${PROJECTS_PATH}?${params.toString()}`, { scroll: false })
      if (chat !== "individual") return
      projectsApi
        .markIndividualRead(projectId)
        .then(() => setIndividualUnread(false))
        .catch(() => {
          /* best-effort — badge stays until the next poll tick */
        })
    },
    [projectId, router, searchParams],
  )

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

  // The top-bar gear opens the settings modal on its default (Instructions)
  // tab — navigating to a specific tab from there happens inside the modal
  // itself, via its own tab buttons. Replaces the old rail's separate
  // Invite/Memory triggers (`onInvite`/`onOpenMemory`), both folded into
  // this one modal's tabs.
  const onOpenSettings = useCallback(() => setSettingsTab("instructions"), [])
  // The "+" invite affordance opens the SAME settings modal directly on the
  // Invite tab (vs the gear's default Instructions tab).
  const onOpenInvite = useCallback(() => setSettingsTab("invite"), [])
  const onCloseSettings = useCallback(() => setSettingsTab(null), [])
  // The artifacts/task modals below are this ticket's bodies for these
  // top-bar triggers. "Add existing artifact" is now a folded view INSIDE the
  // artifacts modal (ArtifactsModal owns the list ⇆ add swap), so there is no
  // separate add-artifact rail modal to open here.
  const onOpenArtifacts = useCallback((type?: ProjectArtifactType) => setRailModal({ kind: "artifacts", type }), [])
  const onOpenTasks = useCallback(() => setRailModal({ kind: "tasks" }), [])
  // Re-fetches ONLY the project's artifact list — the ArtifactsModal's
  // `onArtifactsChanged` callback (a pick just wrote `project_artifacts` rows
  // server-side). Deliberately narrower than `load()` (which would flash the
  // whole shell back to "loading" and drop the active thread), mirroring
  // `refetchProject`'s own posture just above.
  const refetchArtifacts = useCallback(() => {
    projectsApi
      .artifacts(projectId)
      .then((artifacts) => {
        setState((prev) => (prev.status === "ready" ? { ...prev, artifacts } : prev))
      })
      .catch(() => {
        // Best-effort: the add itself already succeeded; a transient
        // refetch failure just leaves the pre-add artifact list showing
        // until the next real load.
      })
  }, [projectId])
  // Artifact invalidation (#9-count): the GROUP channel `project:{id}` gets
  // a best-effort `artifact.added` broadcast from `db/projects.py::
  // add_artifact`'s ONE write chokepoint — a server-side attach
  // (`execute_task`, a report capture) as well as any client-driven add.
  // `onReconcile` re-derives the same way on every (re)subscribe (AD-P22),
  // so a dropped broadcast during a disconnect window still self-heals on
  // reconnect. Deliberately the GROUP channel, not the per-user one above
  // (`unreadTopic`) — every member's artifacts list should refresh, not
  // just the sender's.
  const handleArtifactsEvent = useCallback(
    (event: string) => {
      if (event === "artifact.added") refetchArtifacts()
    },
    [refetchArtifacts],
  )
  const { degraded: artifactsDegraded } = useRealtimeChannel(`project:${projectId}`, {
    onEvent: handleArtifactsEvent,
    onReconcile: refetchArtifacts,
  })

  // Artifacts count fallback poll — same focus-gated posture as the unread
  // badge poll above (AD-P22): while the group channel above is live, the
  // `artifact.added` broadcast + reconnect reconcile keep the count fresh
  // and this interval does not arm. When that channel degrades (e.g. a
  // group/agent-driven creation whose broadcast is dropped), this re-arms
  // and re-fetches the artifact list on the same cadence, gated on the tab
  // having focus. Best-effort: a dropped tick just leaves the count as-is
  // until the next successful tick or a manual panel-reopen.
  useEffect(() => {
    let cancelled = false
    let intervalId: ReturnType<typeof setInterval> | null = null
    const tick = () => {
      if (!cancelled) refetchArtifacts()
    }
    const start = () => {
      if (!artifactsDegraded) return
      if (intervalId != null) return
      intervalId = setInterval(tick, UNREAD_POLL_MS)
    }
    const stop = () => {
      if (intervalId == null) return
      clearInterval(intervalId)
      intervalId = null
    }

    if (typeof document !== "undefined" && document.hasFocus()) start()
    const onFocus = () => start()
    const onBlur = () => stop()
    window.addEventListener("focus", onFocus)
    window.addEventListener("blur", onBlur)

    return () => {
      cancelled = true
      stop()
      window.removeEventListener("focus", onFocus)
      window.removeEventListener("blur", onBlur)
    }
  }, [artifactsDegraded, refetchArtifacts])

  const onCloseRailModal = useCallback(() => {
    setRailModal(null)
    // Acting on tasks inside the modal changes the open counts; refresh the
    // rail card on close (best-effort — the poll tick catches up otherwise).
    projectsApi
      .ledgerCounts(projectId)
      .then(setLedgerCounts)
      .catch(() => {
        /* best-effort — the next poll tick re-derives the counts */
      })
    refetchLedgerRows()
  }, [projectId, refetchLedgerRows])

  // Open a chat artifact in the SAME global side-panel main uses (mounted at the
  // app root via AppShell) — exactly main's panel behaviour (tabs, streaming,
  // open/close, resize handle) for free. PRD and evidence both open like main;
  // the STANDALONE-browse open (ArtifactsModal, by-id) stays deferred — that
  // modal passes no in-place handler and falls back to its own deep-link viewer.
  const onOpenArtifactInPlace = useCallback((artifact: ArtifactItem) => {
    setRailModal(null)
    if (artifact.type === "prd") {
      void loadPrdById(artifact.open.prd_id).then((r) => {
        if (r.ok) {
          setContent({ prd: r.prd, prdMeta: null, prdGenerating: false, prdPartialHtml: null })
          openContentPanel("prd")
        }
      })
    } else if (artifact.type === "evidence" && artifact.open.insight_index != null) {
      // Byte-for-byte main's chat evidence-open content-set (ChatScreen's
      // openPrdInTab evidence branch): scope the Evidence tab to this insight via
      // `prdMeta` and clear any lingering PRD; the tab self-loads the existing
      // evidence through the SHARED `loadEvidenceByInsight`. No DetailState is
      // built (main builds none here either).
      setContent({
        detail: null,
        prd: null,
        prdMeta: { briefId: artifact.open.brief_id, insightIndex: artifact.open.insight_index },
        prdGenerating: false,
        prdPartialHtml: null,
      })
      openContentPanel("evidence")
    }
  }, [setContent, openContentPanel])

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
          {/* Standard app spinner (reused, not a bespoke skeleton/keyframe —
           *  DRY / Check-25): `Spinner` from `components/auth/icons.tsx`,
           *  rotation via the shared `.auth-btn-spin` class in globals.css. */}
          <div className={styles.spinnerWrap}>
            <Spinner width={28} height={28} />
          </div>
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
      mainStyle={{ maxWidth: "none", padding: 0, display: "flex", flexDirection: "column", minHeight: 0, flex: "1 1 auto", position: "relative" }}
    >
      <ProjectDetailView
        project={state.project}
        artifacts={state.artifacts}
        memory={state.memory}
        insightNote={state.insight}
        activeChat={activeChat}
        onSelectChat={onSelectChat}
        individualUnread={individualUnread}
        ledgerCounts={ledgerCounts}
        ledgerRows={ledgerRows}
        onOpenArtifacts={onOpenArtifacts}
        onOpenArtifactInPlace={onOpenArtifactInPlace}
        openPrdId={openPrdId}
        onOpenTasks={onOpenTasks}
        onOpenSettings={onOpenSettings}
        onOpenInvite={onOpenInvite}
        currentUserId={currentUserId}
        onRemoveMember={onRemoveMember}
        refetchArtifacts={refetchArtifacts}
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
      <ProjectSettingsModal
        open={settingsTab !== null}
        onClose={onCloseSettings}
        projectId={projectId}
        project={state.project}
        memory={state.memory}
        currentUserId={currentUserId}
        onRemoveMember={onRemoveMember}
        onInvited={refetchProject}
        initialTab={settingsTab ?? "instructions"}
      />
      <ArtifactsModal
        projectId={projectId}
        open={railModal?.kind === "artifacts"}
        initialFilter={railModal?.kind === "artifacts" ? railModal.type : undefined}
        onClose={onCloseRailModal}
        // Standalone-browse open is DEFERRED (by-id load, non-PRD store shapes) —
        // no in-place handler, so the modal uses its own deep-link viewer (an
        // honest fallback, not a dead control). Chat-driven PRD open still lands
        // in the shared panel via `onOpenArtifactInPlace`.
        onArtifactsChanged={refetchArtifacts}
      />
      <TaskModal
        open={railModal?.kind === "tasks"}
        projectId={projectId}
        onClose={onCloseRailModal}
        ledgerVersion={ledgerVersion}
      />
    </AppLayout>
  )
}
