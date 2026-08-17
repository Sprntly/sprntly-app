"use client"

// ── ProjectSettingsModal — the top-bar gear's 4-tab modal (layout redesign) ──
//
// David asked (2026-08-14, feedback S7) to demote the standing right rail:
// chat is first-class, artifact-viewing second-class, and everything else
// (instructions/memory/members/invite) third-class, behind this modal.
// A thin presentational shell + tab-state — every tab reuses an ALREADY
// existing surface rather than re-implementing it (DRY):
//   - Memory  → `MemorySummaryBody`, extracted from `MemoryModal.tsx`.
//   - Invite  → `ProjectInviteBody`, extracted from `ProjectInviteModal.tsx`.
//   - Members → the SAME `ProjectDetailScreen.module.css` member-row classes
//     the old rail (and `ProjectPanelSection`) already use — no second
//     member-row palette.
// Instructions persists (GET on open, PUT on Save) and folds into both
// project surfaces' agent context server-side — this tab is a thin
// presentational wrapper over `projectsApi.instructions`/`setInstructions`.
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { IconClose } from "../../../shared/app-icons"
import { personAvatarStyle } from "./avatarColor"
import { useEscapeToClose } from "./useEscapeToClose"
import { MemorySummaryBody } from "./MemoryModal"
import { ProjectInviteBody } from "./ProjectInviteModal"
import { projectsApi, type ProjectDetail, type ProjectMember, type ProjectMemorySummary } from "../../../../lib/api"
import detailStyles from "./ProjectDetailScreen.module.css"
import styles from "./ProjectSettingsModal.module.css"

export type SettingsTab = "instructions" | "memory" | "members" | "invite"

// Not exported from `lib/api.ts` — same local alias `ProjectDetailScreen.tsx`
// already declares (structurally identical, so it interops with that file's
// `onRemoveMember` prop without a shared export).
type HumanMember = Extract<ProjectMember, { kind: "human" }>

const TABS: { id: SettingsTab; label: string }[] = [
  { id: "instructions", label: "Instructions" },
  { id: "memory", label: "Memory" },
  { id: "members", label: "Members" },
  { id: "invite", label: "Invite" },
]

// Matches the server's cap (`SetInstructionsRequest.instructions` `max_length`
// + `project_group_context._INSTRUCTIONS_CHARS`) so the client-side count/
// disable never disagrees with what the PUT would actually accept.
const INSTRUCTIONS_MAX = 2000

/** Same initials algorithm `ProjectDetailScreen.tsx`/`ProjectInviteModal.tsx`
 *  already duplicate locally — not a shared export in this codebase. */
function initials(name: string | null | undefined): string {
  if (!name) return "?"
  return name.split(" ").filter(Boolean).map((w) => w[0]).join("").toUpperCase().slice(0, 2)
}

function RemoveIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
      <line x1="6" y1="6" x2="18" y2="18" />
      <line x1="18" y1="6" x2="6" y2="18" />
    </svg>
  )
}

function InfoIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 16v-4M12 8h.01" strokeLinecap="round" />
    </svg>
  )
}

export type ProjectSettingsModalProps = {
  open: boolean
  onClose: () => void
  projectId: number | string
  project: ProjectDetail
  memory: ProjectMemorySummary
  /** The signed-in caller's user id (`null` when unresolved) — used ONLY to
   *  withhold the Remove control on the caller's OWN member row, same rule
   *  the old rail used. */
  currentUserId: string | null
  /** Requests removing a human member — the CONTAINER owns the confirm/busy/
   *  error state and the actual `removeMember` call (same split as every
   *  other settings-triggered action). Never invoked for the agent member,
   *  the project creator, or the caller themselves. */
  onRemoveMember: (member: HumanMember) => void
  /** Fired after a successful add/invite so the caller re-fetches the roster
   *  (`refetchProject`). */
  onInvited: () => void
  /** Which tab to land on when the modal opens — the container passes
   *  `settingsTab ?? "instructions"`. */
  initialTab?: SettingsTab
}

export function ProjectSettingsModal({
  open,
  onClose,
  projectId,
  project,
  memory,
  currentUserId,
  onRemoveMember,
  onInvited,
  initialTab,
}: ProjectSettingsModalProps) {
  const [tab, setTab] = useState<SettingsTab>(initialTab ?? "instructions")
  const [membersQuery, setMembersQuery] = useState("")
  // Instructions tab — `instructions` is the live textarea value;
  // `savedInstructions` is the last value loaded/saved from the server,
  // used purely for change-detection (Save disabled when they match).
  const [instructions, setInstructions] = useState("")
  const [savedInstructions, setSavedInstructions] = useState("")
  const [instructionsLoading, setInstructionsLoading] = useState(false)
  const [instructionsSaving, setInstructionsSaving] = useState(false)
  const [instructionsError, setInstructionsError] = useState<string | null>(null)

  const dialogRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<Element | null>(null)

  // Land on the requested tab and reset transient state each time the modal
  // (re)opens.
  useEffect(() => {
    if (!open) return
    setTab(initialTab ?? "instructions")
    setMembersQuery("")
  }, [open, initialTab])

  // Load the saved instructions on open (or when the project id changes
  // while open) — best-effort: a failed GET degrades to an in-tab error
  // line, never a crash.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setInstructionsError(null)
    setInstructionsLoading(true)
    projectsApi
      .instructions(projectId)
      .then((res) => {
        if (cancelled) return
        const value = res.instructions ?? ""
        setInstructions(value)
        setSavedInstructions(value)
      })
      .catch(() => {
        if (cancelled) return
        setInstructionsError("Couldn't load saved instructions.")
      })
      .finally(() => {
        if (!cancelled) setInstructionsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, projectId])

  const instructionsOverCap = instructions.length > INSTRUCTIONS_MAX
  const instructionsUnchanged = instructions === savedInstructions
  const instructionsSaveDisabled =
    instructionsUnchanged || instructionsOverCap || instructionsLoading || instructionsSaving

  const saveInstructions = useCallback(() => {
    setInstructionsError(null)
    setInstructionsSaving(true)
    projectsApi
      .setInstructions(projectId, instructions)
      .then((res) => {
        const value = res.instructions ?? ""
        setInstructions(value)
        setSavedInstructions(value)
      })
      .catch(() => {
        setInstructionsError("Couldn't save instructions — try again.")
      })
      .finally(() => {
        setInstructionsSaving(false)
      })
  }, [projectId, instructions])

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

  useEscapeToClose(open, onClose)

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key !== "Tab") return
    const focusables = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(
        "button:not([disabled]), [href], input:not([disabled]), select, textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
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

  const humans = useMemo(
    () => project.members.filter((m): m is HumanMember => m.kind === "human"),
    [project.members],
  )
  const filteredHumans = useMemo(() => {
    const q = membersQuery.trim().toLowerCase()
    if (!q) return humans
    return humans.filter(
      (m) => (m.name ?? "").toLowerCase().includes(q) || (m.job_role ?? "").toLowerCase().includes(q),
    )
  }, [humans, membersQuery])

  if (!open) return null

  return (
    <div className="modal-overlay open" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={dialogRef}
        className={`modal modal-md ${styles.panel}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-settings-modal-title"
        data-testid="project-settings-modal"
        onKeyDown={onKeyDown}
      >
        <div className="modal-head">
          <div className="modal-head-text">
            <h2 className="modal-title" id="project-settings-modal-title" data-testid="project-settings-modal-title">
              Project Settings
            </h2>
            <p className="modal-sub">Instructions, memory, members, and invites for this project.</p>
          </div>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
            data-testid="project-settings-modal-close"
          >
            <IconClose size={16} title="Close" />
          </button>
        </div>

        <div className={styles.tabs} role="tablist" aria-label="Project settings tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`${styles.tab} ${tab === t.id ? styles.tabActive : ""}`}
              onClick={() => setTab(t.id)}
              data-testid={`settings-tab-${t.id}`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="modal-body" data-testid="project-settings-modal-body">
          {tab === "instructions" ? (
            <div role="tabpanel" aria-label="Instructions" data-testid="settings-panel-instructions">
              <label className={styles.fieldLabel} htmlFor="settings-instructions-input">
                Instructions for the Sprntly agent
              </label>
              <p className={styles.fieldHelp}>
                These guide how Sprntly responds in this project — it feeds project memory.
              </p>
              <textarea
                id="settings-instructions-input"
                className={styles.textarea}
                value={instructions}
                // Not hard-clamped at INSTRUCTIONS_MAX — the field lets the
                // user type/paste past the cap so the count + Save-disabled
                // state can actually mirror it (AC12); the server enforces
                // the real cap on PUT regardless.
                onChange={(e) => setInstructions(e.target.value)}
                placeholder="e.g. Priced quotes must return in under 60s — treat that as the north-star constraint."
                data-testid="settings-instructions-input"
              />
              <div className={styles.saveBar}>
                <span className={styles.charCount} data-testid="settings-instructions-count">
                  {instructions.length} / {INSTRUCTIONS_MAX} characters
                </span>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={instructionsSaveDisabled}
                  onClick={saveInstructions}
                  data-testid="settings-instructions-save"
                >
                  {instructionsSaving ? "Saving…" : "Save"}
                </button>
              </div>
              {instructionsError ? (
                <div className={styles.instructionsError} data-testid="settings-instructions-error">
                  {instructionsError}
                </div>
              ) : null}
              <div className={styles.feedsNote}>
                <InfoIcon />
                <div>
                  These instructions feed <b>project memory</b> and are visible to every member. They persist
                  across all group and private chats in this project.
                </div>
              </div>
            </div>
          ) : null}

          {tab === "memory" ? (
            <div role="tabpanel" aria-label="Memory" data-testid="settings-panel-memory">
              <MemorySummaryBody summary={memory} />
            </div>
          ) : null}

          {tab === "members" ? (
            <div role="tabpanel" aria-label="Members" className={styles.tabFill} data-testid="settings-panel-members">
              <div className={detailStyles.railSectionLabel}>
                Members
                <span className={detailStyles.railSectionCount}>{humans.length}</span>
              </div>
              <input
                type="text"
                className={styles.searchInput}
                placeholder="Filter members by name or role…"
                aria-label="Filter members"
                value={membersQuery}
                onChange={(e) => setMembersQuery(e.target.value)}
                data-testid="settings-members-search"
              />
              <div className={styles.scrollRegion} data-testid="settings-members-scroll">
                {filteredHumans.map((m) => {
                  // Removable-row rule — UNCHANGED from the old rail: never
                  // the project creator, never the caller themselves. The
                  // agent member never reaches this loop (rendered below,
                  // pinned, always shown regardless of the filter).
                  const removable = m.user_id !== project.created_by && m.user_id !== currentUserId
                  return (
                    <div className={detailStyles.memberRow} key={m.user_id} data-testid="member-row-human">
                      <span className={detailStyles.memberAv} aria-hidden="true" style={personAvatarStyle(m.user_id, m.name)}>
                        {initials(m.name)}
                      </span>
                      <div className={detailStyles.memberMain}>
                        <div className={detailStyles.memberName}>{m.name ?? "Unnamed member"}</div>
                        <div className={detailStyles.memberRole}>{m.job_role || "Member"}</div>
                      </div>
                      {removable ? (
                        <button
                          type="button"
                          className={detailStyles.memberRemoveBtn}
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
              </div>
            </div>
          ) : null}

          {tab === "invite" ? (
            <div role="tabpanel" aria-label="Invite" className={styles.tabFill} data-testid="settings-panel-invite">
              <ProjectInviteBody projectId={projectId} onInvited={onInvited} listClassName={styles.scrollRegion} />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
