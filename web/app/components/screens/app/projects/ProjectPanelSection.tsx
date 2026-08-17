"use client"

// ── ProjectPanelSection ─────────────────────────────────────────────────────
// The in-panel "project" view for the main-chat entry flow. When a PRD
// generated in the main chat silently forks a project, the content panel gains
// a project-menu icon; toggling it swaps the panel body from the artifact tabs
// to THIS component. It shows the forked project's members, memory teaser, and
// an invite row — the same three rail sections `ProjectDetailView` renders
// inline on the standalone `/projects/[id]` screen, lifted out verbatim so the
// two surfaces stay visually identical.
//
// It fetches its OWN data (project + memory summary) keyed on `projectId`,
// mirroring `ProjectDetailScreen`'s load but MINIMAL: no artifacts, no ledger,
// no realtime, no delegation. It reuses the already-clean `ProjectInviteModal`
// and `MemoryModal` containers unchanged.
//
// CSS coupling: reuses `ProjectDetailScreen.module.css` (card / member / invite
// classes) so it renders identically to the rail. If those classes are renamed
// on the detail screen, this section follows.

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ApiError,
  projectsApi,
  type ProjectDetail,
  type ProjectMember,
  type ProjectMemorySummary,
} from "../../../../lib/api"
import { MemoryModal } from "./MemoryModal"
import { ProjectInviteModal } from "./ProjectInviteModal"
import { personAvatarStyle } from "./avatarColor"
import styles from "./ProjectDetailScreen.module.css"

type HumanMember = Extract<ProjectMember, { kind: "human" }>

/** Same initials algorithm the other Projects surfaces duplicate locally —
 *  not a shared export in this codebase. */
function initials(name: string | null | undefined): string {
  if (!name) return "?"
  return name.split(" ").filter(Boolean).map((w) => w[0]).join("").toUpperCase().slice(0, 2)
}

/** First sentence of the memory summary, markdown-emphasis stripped (AC7). */
function firstSentence(summaryMd: string | null): string {
  if (!summaryMd) return "Nothing synthesized yet — insights will appear as the team collaborates."
  const stripped = summaryMd.replace(/[#*_`]/g, "").trim()
  const match = stripped.match(/^[^.!?]*[.!?]/)
  return (match ? match[0] : stripped).trim()
}

// ── Small icons (duplicated from ProjectDetailScreen — not shared exports) ──

function ClockIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v4l3 2" />
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

type LoadState =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "not_found" }
  | { status: "error" }
  | { status: "ready"; project: ProjectDetail; memory: ProjectMemorySummary }

export function ProjectPanelSection({ projectId }: { projectId: number }) {
  const [state, setState] = useState<LoadState>({ status: "loading" })
  const [memoryOpen, setMemoryOpen] = useState(false)
  const [inviteOpen, setInviteOpen] = useState(false)

  const load = useCallback(() => {
    setState({ status: "loading" })
    Promise.all([projectsApi.get(projectId), projectsApi.memorySummary(projectId)])
      .then(([project, memory]) => setState({ status: "ready", project, memory }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) setState({ status: "forbidden" })
        else if (err instanceof ApiError && err.status === 404) setState({ status: "not_found" })
        else setState({ status: "error" })
      })
  }, [projectId])

  useEffect(() => {
    load()
  }, [load])

  const humans = useMemo(
    () =>
      state.status === "ready"
        ? state.project.members.filter((m): m is HumanMember => m.kind === "human")
        : [],
    [state],
  )
  const agent = useMemo(
    () =>
      state.status === "ready"
        ? state.project.members.find(
            (m): m is Extract<ProjectMember, { kind: "agent" }> => m.kind === "agent",
          )
        : undefined,
    [state],
  )

  if (state.status === "loading") {
    return (
      <div style={{ padding: 16, fontSize: 13, opacity: 0.6 }} data-testid="project-panel-loading">
        Loading project…
      </div>
    )
  }
  if (state.status !== "ready") {
    const msg =
      state.status === "forbidden"
        ? "You don't have access to this project."
        : state.status === "not_found"
          ? "This project no longer exists."
          : "Couldn't load this project."
    return (
      <div style={{ padding: 16, fontSize: 13, opacity: 0.75 }} data-testid="project-panel-error">
        {msg}
      </div>
    )
  }

  const { project, memory } = state

  return (
    <div
      style={{ display: "flex", flexDirection: "column", overflowY: "auto", minHeight: 0, height: "100%" }}
      data-testid="project-panel-section"
    >
      <div className={styles.railSectionLabel} data-testid="rail-section-label">
        Project Settings
      </div>
      <div className={styles.card} data-testid="memory-card">
        <div className={styles.cardHead}>
          <h4>
            <ClockIcon />
            Memory
          </h4>
          <span className={styles.cardCount}>{memory.entry_count}</span>
        </div>
        <div className={styles.teaser} data-testid="memory-teaser">
          <div className={styles.teaserSrc}>What this project knows · read-only</div>
          {firstSentence(memory.summary_md)}
        </div>
        <div className={styles.cardActions}>
          <button
            type="button"
            className={styles.viewAllBtn}
            onClick={() => setMemoryOpen(true)}
            data-testid="memory-view-all"
          >
            View memory
          </button>
        </div>
      </div>

      <div className={styles.railSectionLabel} data-testid="rail-section-label">
        Members
        <span className={styles.railSectionCount}>{project.members.length}</span>
      </div>
      {humans.map((m) => (
        <div className={styles.memberRow} key={m.user_id} data-testid="member-row-human">
          <span className={styles.memberAv} aria-hidden="true" style={personAvatarStyle(m.user_id, m.name)}>
            {initials(m.name)}
          </span>
          <div className={styles.memberMain}>
            <div className={styles.memberName}>{m.name ?? "Unnamed member"}</div>
            <div className={styles.memberRole}>{m.job_role || "Member"}</div>
          </div>
        </div>
      ))}
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
          <span
            className={styles.workingPill}
            role="status"
            aria-label={`Sprntly — ${agent.status}`}
            data-testid="agent-working-status"
          >
            {agent.status}
          </span>
        </div>
      ) : null}
      <div className={styles.inviteRow}>
        <input className={styles.inviteInput} placeholder="Invite by email…" aria-label="Invite by email" />
        <button
          type="button"
          className={styles.inviteBtn}
          onClick={() => setInviteOpen(true)}
          data-testid="invite-button"
        >
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

      <MemoryModal
        projectId={projectId}
        members={project.members}
        open={memoryOpen}
        onClose={() => setMemoryOpen(false)}
      />
      <ProjectInviteModal
        projectId={projectId}
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        onInvited={load}
      />
    </div>
  )
}
