"use client"

// ── ProjectInviteModal — the PROJECT rail's Invite surface ──
//
// Reuse over invention: this is the project-scoped Invite the rail button
// opens INSTEAD of the global mock `shared/InviteModal` (empty email rows +
// toast-only stub). It (a) lists the project's CURRENT members from the
// roster already loaded into `ProjectDetailScreen` (`state.project.members`),
// and (b) adds/invites a person through the SAME real `/tag` path the group
// chat's mention picker uses (`projectsApi.tagCandidate`) — fed by the same
// `projectsApi.candidateSearch` typeahead — NOT a second invite API and NOT
// the toast stub. The modal chrome reuses the SAME global classes every other
// project modal renders with; the member rows reuse `ProjectDetailScreen`'s
// own member-row styles (imported module → identical hashed class names) so
// this introduces no second member-row palette.
//
// Scope note: inviting a BRAND-NEW email is handled by `/tag` too (it
// mints an invite row + best-effort email for a company/new-user needle,
// AD-TNM6), but a first-class "invite a stranger by email with role + custom
// message" flow is the broader invite-flow work — see the flag at the bottom
// of the add section. This surface prioritises: show current members + add an
// existing in-tenant candidate for real.
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { projectsApi, type ProjectMember } from "../../../../lib/api"
import { IconClose } from "../../../shared/app-icons"
import { personAvatarStyle } from "./avatarColor"
import { isEmailNeedle } from "./mentions"
import { useEscapeToClose } from "./useEscapeToClose"
import detailStyles from "./ProjectDetailScreen.module.css"

type CandidateRow = Awaited<ReturnType<typeof projectsApi.candidateSearch>>[number]
type TagResult = Awaited<ReturnType<typeof projectsApi.tagCandidate>>
type Affordance = { tone: "ok" | "error"; text: string }

function initials(name: string | null | undefined): string {
  if (!name) return "?"
  return name.split(" ").filter(Boolean).map((w) => w[0]).join("").toUpperCase().slice(0, 2)
}

/** Lowercase `"failed"` sentinel from `backend/app/team_email.py` (mirrors
 *  `ProjectGroupChat`'s own check). */
function emailFailed(status: string | undefined): boolean {
  return (status ?? "").toLowerCase() === "failed"
}

export type ProjectInviteModalProps = {
  projectId: number | string
  /** The project's current roster (humans + the virtual agent), already
   *  loaded by `ProjectDetailScreen` — no second fetch. */
  members: ProjectMember[]
  open: boolean
  onClose: () => void
  /** Fired after a successful add/invite so the caller re-fetches the roster
   *  (`refetchProject`) and the new member shows in the members list. */
  onInvited: () => void
}

export function ProjectInviteModal({ projectId, members, open, onClose, onInvited }: ProjectInviteModalProps) {
  const [query, setQuery] = useState("")
  const [candidates, setCandidates] = useState<CandidateRow[]>([])
  const [candLoading, setCandLoading] = useState(false)
  const [candError, setCandError] = useState(false)
  const [addingNeedle, setAddingNeedle] = useState<string | null>(null)
  const [affordance, setAffordance] = useState<Affordance | null>(null)

  const dialogRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<Element | null>(null)

  const humans = useMemo(
    () => members.filter((m): m is Extract<ProjectMember, { kind: "human" }> => m.kind === "human"),
    [members],
  )
  const agent = useMemo(
    () => members.find((m): m is Extract<ProjectMember, { kind: "agent" }> => m.kind === "agent"),
    [members],
  )

  // Reset transient state each time the modal (re)opens.
  useEffect(() => {
    if (!open) return
    setQuery("")
    setCandidates([])
    setCandLoading(false)
    setCandError(false)
    setAddingNeedle(null)
    setAffordance(null)
  }, [open])

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

  // Debounced candidate typeahead — the SAME tenant-scoped read the group
  // chat's mention picker uses (`candidateSearch`), degraded to an in-body
  // error rather than a throw.
  useEffect(() => {
    const q = query.trim()
    if (!open || q.length === 0) {
      setCandidates([])
      setCandLoading(false)
      setCandError(false)
      return
    }
    setCandLoading(true)
    setCandError(false)
    let cancelled = false
    const timer = setTimeout(() => {
      projectsApi
        .candidateSearch(projectId, q)
        .then((rows) => {
          if (cancelled) return
          setCandidates(rows)
          setCandLoading(false)
        })
        .catch(() => {
          if (cancelled) return
          setCandidates([])
          setCandError(true)
          setCandLoading(false)
        })
    }, 150)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [query, projectId, open])

  // Add/invite via the REAL `/tag` path (AD-TNM6 — never throw/block; a
  // refuse degrades to one opaque message). On success the caller refetches
  // the roster so the new member appears in the members list above.
  const addByNeedle = useCallback(
    (needle: string, label: string) => {
      if (addingNeedle) return
      setAddingNeedle(needle)
      setAffordance(null)
      projectsApi
        .tagCandidate(projectId, needle)
        .then((raw) => {
          const res = raw as TagResult
          if (res.tier === "t_workspace") {
            setAffordance({ tone: "ok", text: `${label} added to the project` })
            onInvited()
          } else if (res.tier === "t_company" || res.tier === "t_newuser") {
            if (emailFailed(res.email_status)) {
              setAffordance({
                tone: "error",
                text: `Invite created for ${label} — email didn't send; you can re-invite from Team settings`,
              })
            } else {
              setAffordance({ tone: "ok", text: `Invite sent to ${label}` })
            }
            onInvited()
          } else {
            setAffordance({ tone: "ok", text: `${label} is already on the project` })
          }
        })
        .catch(() => {
          setAffordance({ tone: "error", text: "Couldn't add that person" })
        })
        .finally(() => {
          setAddingNeedle(null)
          setQuery("")
        })
    },
    [addingNeedle, projectId, onInvited],
  )

  if (!open) return null

  const q = query.trim()
  const emailLike = isEmailNeedle(q)
  const showInviteByEmail = q.length > 0 && (emailLike || (!candLoading && !candError && candidates.length === 0))

  return (
    <div className="modal-overlay open" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={dialogRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-invite-modal-title"
        data-testid="project-invite-modal"
      >
        <div className="modal-head">
          <div className="modal-head-text">
            <h2 className="modal-title" id="project-invite-modal-title" data-testid="project-invite-modal-title">
              Invite to this project
            </h2>
            <p className="modal-sub">Add a teammate to this project — they land straight in its chats, artifacts, and memory.</p>
          </div>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
            data-testid="project-invite-modal-close"
          >
            <IconClose size={16} title="Close" />
          </button>
        </div>

        <div className="modal-body" data-testid="project-invite-modal-body">
          <div className={detailStyles.railSectionLabel} data-testid="project-invite-members-label">
            On this project
            <span className={detailStyles.railSectionCount}>{members.length}</span>
          </div>
          <div data-testid="project-invite-members">
            {humans.map((m) => (
              <div className={detailStyles.memberRow} key={m.user_id} data-testid="project-invite-member-row">
                <span className={detailStyles.memberAv} aria-hidden="true" style={personAvatarStyle(m.user_id, m.name)}>
                  {initials(m.name)}
                </span>
                <div className={detailStyles.memberMain}>
                  <div className={detailStyles.memberName}>{m.name ?? "Unnamed member"}</div>
                  <div className={detailStyles.memberRole}>{m.email || m.job_role || "Member"}</div>
                </div>
              </div>
            ))}
            {agent ? (
              <div className={`${detailStyles.memberRow} ${detailStyles.memberRowAgent}`} data-testid="project-invite-member-row-agent">
                <span className={detailStyles.agentAv} aria-hidden="true">
                  s
                </span>
                <div className={detailStyles.memberMain}>
                  <div className={detailStyles.memberName}>
                    {agent.name} <span className={detailStyles.agentTag}>Agent</span>
                  </div>
                  <div className={detailStyles.memberRole}>{agent.role_label}</div>
                </div>
              </div>
            ) : null}
          </div>

          <div className={detailStyles.railSectionLabel} style={{ marginTop: 18 }}>
            Add someone
          </div>
          <input
            className="input"
            style={{ width: "100%" }}
            placeholder="Search by name or email…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search people to add"
            data-testid="project-invite-search"
          />

          {q.length === 0 ? (
            <p className="modal-sub" style={{ marginTop: 10 }} data-testid="project-invite-hint">
              Start typing to find a teammate, or enter an email to invite someone new.
            </p>
          ) : candLoading ? (
            <p className="modal-sub" style={{ marginTop: 10 }} data-testid="project-invite-loading">
              Searching…
            </p>
          ) : candError ? (
            <p className="modal-sub" style={{ marginTop: 10 }} data-testid="project-invite-search-error">
              Couldn&rsquo;t search right now — keep typing to retry.
            </p>
          ) : (
            <div style={{ marginTop: 10 }} data-testid="project-invite-results">
              {candidates.map((c) => {
                const label = c.name ?? c.email ?? "Unknown"
                const needle = c.email ?? c.name ?? ""
                const isMember = c.kind === "member"
                return (
                  <div className={detailStyles.memberRow} key={`${c.kind}:${c.user_id}`} data-testid="project-invite-candidate">
                    <span className={detailStyles.memberAv} aria-hidden="true" style={personAvatarStyle(c.user_id, c.name)}>
                      {initials(c.name)}
                    </span>
                    <div className={detailStyles.memberMain}>
                      <div className={detailStyles.memberName}>{label}</div>
                      <div className={detailStyles.memberRole}>{c.email || (isMember ? "Member" : "Not on project")}</div>
                    </div>
                    {isMember ? (
                      <span className="modal-sub" data-testid="project-invite-already">
                        On this project
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-primary"
                        onClick={() => addByNeedle(needle, label)}
                        disabled={addingNeedle === needle}
                        data-testid="project-invite-add"
                      >
                        {addingNeedle === needle ? "Adding…" : "Add"}
                      </button>
                    )}
                  </div>
                )
              })}
              {showInviteByEmail ? (
                <div className={detailStyles.memberRow} data-testid="project-invite-by-email-row">
                  <div className={detailStyles.memberMain}>
                    <div className={detailStyles.memberName}>
                      {emailLike ? `Invite ${q} by email` : "No matches — invite by email"}
                    </div>
                    <div className={detailStyles.memberRole}>They get an invite and land in this project.</div>
                  </div>
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => addByNeedle(q, q)}
                    disabled={!emailLike || addingNeedle === q}
                    title={emailLike ? undefined : "Enter a full email address to invite someone new"}
                    data-testid="project-invite-by-email"
                  >
                    {addingNeedle === q ? "Inviting…" : "Invite"}
                  </button>
                </div>
              ) : null}
            </div>
          )}

          {affordance ? (
            <div
              role="status"
              data-testid="project-invite-affordance"
              style={{
                marginTop: 14,
                padding: "10px 12px",
                borderRadius: 8,
                fontSize: 13,
                background: affordance.tone === "error" ? "var(--danger-soft, #FEE)" : "var(--info-soft)",
                color: affordance.tone === "error" ? "var(--danger, #B4232A)" : "var(--ink-2)",
              }}
            >
              {affordance.text}
            </div>
          ) : null}
        </div>

        <div className="modal-foot">
          <button type="button" className="btn btn-ghost" onClick={onClose} data-testid="project-invite-modal-done">
            Done
          </button>
        </div>
      </div>
    </div>
  )
}
