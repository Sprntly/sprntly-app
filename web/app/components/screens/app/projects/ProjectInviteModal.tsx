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
import { projectsApi } from "../../../../lib/api"
import { IconClose } from "../../../shared/app-icons"
import { personAvatarStyle } from "./avatarColor"
import { isEmailNeedle } from "./mentions"
import { useEscapeToClose } from "./useEscapeToClose"
import detailStyles from "./ProjectDetailScreen.module.css"

type CandidateSearchResult = Awaited<ReturnType<typeof projectsApi.candidateSearch>>
type CandidateRow = CandidateSearchResult["candidates"][number]
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

export type ProjectInviteBodyProps = {
  projectId: number | string
  /** Fired after a successful add/invite so the caller re-fetches the roster
   *  (`refetchProject`) and the new member shows in the members list. */
  onInvited: () => void
  /** Optional class for the candidate-list container. When provided (the
   *  Settings › Invite tab passes its Members-tab card class), the list
   *  renders as a full-height card that fills its flex parent and scrolls
   *  internally — matching the Members tab. Omitted by the standalone modal,
   *  which keeps the original fixed-height inline scroll. */
  listClassName?: string
}

/** The candidate-picker body — extracted so the layout redesign's Settings ›
 *  Invite tab can render the SAME search/typeahead/add-by-email surface
 *  (DRY — do not re-implement). Mount-lifetime IS open-lifetime: both callers
 *  (the standalone `ProjectInviteModal` below and the settings tab) only
 *  mount this component while visible, so a fresh instance — and fresh local
 *  state — appears on every open; no separate `open` prop or reset effect
 *  needed here. */
export function ProjectInviteBody({ projectId, onInvited, listClassName }: ProjectInviteBodyProps) {
  const [query, setQuery] = useState("")
  const [candidates, setCandidates] = useState<CandidateRow[]>([])
  // Lower-cased emails with a live (non-expired) pending invite — from
  // `candidateSearch`'s `pending_invites` (`db/team.py::
  // list_pending_invite_emails`, company-wide + already expiry-filtered).
  // Drives the static "Invited" state below, distinct from "Added"
  // (`kind === "member"`).
  const [pendingInvites, setPendingInvites] = useState<Set<string>>(new Set())
  const [candLoading, setCandLoading] = useState(false)
  const [candError, setCandError] = useState(false)
  const [addingNeedle, setAddingNeedle] = useState<string | null>(null)
  const [affordance, setAffordance] = useState<Affordance | null>(null)

  const isPendingEmail = useCallback(
    (email: string | null | undefined) => !!email && pendingInvites.has(email.toLowerCase()),
    [pendingInvites],
  )

  // Candidate fetch — the SAME tenant-scoped read the group chat's mention
  // picker uses (`candidateSearch`), degraded to an in-body error rather
  // than a throw. On mount (empty query) this fetches the workspace
  // non-member list (the primary "add someone" picker) immediately (no
  // debounce); a non-empty query (the user typing) debounces as before.
  useEffect(() => {
    const q = query.trim()
    setCandLoading(true)
    setCandError(false)
    let cancelled = false
    const timer = setTimeout(
      () => {
        projectsApi
          .candidateSearch(projectId, q)
          .then((res) => {
            if (cancelled) return
            setCandidates(res.candidates)
            setPendingInvites(new Set((res.pending_invites ?? []).map((e) => e.toLowerCase())))
            setCandLoading(false)
          })
          .catch(() => {
            if (cancelled) return
            setCandidates([])
            setCandError(true)
            setCandLoading(false)
          })
      },
      q.length === 0 ? 0 : 150,
    )
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [query, projectId])

  // The primary "add someone" list on open (empty query): workspace
  // non-members only — a member already on the project never appears here
  // (that is how "already-in-project members drop off"). Same-company/
  // other-workspace (`kind:"company"`) rows stay reachable via the
  // typeahead below, not this primary list. Also drives the "N not yet in
  // this project" count above the list (layout redesign — same count in
  // both the standalone modal and the Settings › Invite tab, since both
  // render this same body).
  const workspaceCandidates = useMemo(
    () => candidates.filter((c) => c.kind === "workspace"),
    [candidates],
  )

  // Add/invite via the REAL `/tag` path (AD-TNM6 — never throw/block; a
  // refuse degrades to one opaque message). On success the caller refetches
  // the roster so the new member appears in the members list above.
  // `email` (when known — a candidate row's `c.email`, or the by-email row's
  // own validated `q`) is what an INVITE-tier result optimistically adds to
  // `pendingInvites` so the row flips to "Invited" immediately, ahead of the
  // `onInvited()` refetch (which then keeps it there for real from the
  // server-derived set).
  //
  // Query-clear is now PER-BRANCH rather than unconditional in a shared
  // `.finally()`: the t_workspace ("Add") and refuse/error paths still clear
  // it (reset the search for the next action, unchanged from before), but a
  // t_company/t_newuser (invite) success deliberately leaves the query in
  // place — clearing it would immediately hide the very row/badge that just
  // flipped to "Invited", making that state invisible.
  const addByNeedle = useCallback(
    (needle: string, label: string, email?: string | null) => {
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
            setQuery("")
          } else if (res.tier === "t_company" || res.tier === "t_newuser") {
            const invitedEmail = (email ?? (isEmailNeedle(needle) ? needle : null))?.toLowerCase()
            if (invitedEmail) {
              setPendingInvites((prev) => new Set(prev).add(invitedEmail))
            }
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
            setQuery("")
          }
        })
        .catch(() => {
          setAffordance({ tone: "error", text: "Couldn't add that person" })
          setQuery("")
        })
        .finally(() => {
          setAddingNeedle(null)
        })
    },
    [addingNeedle, projectId, onInvited],
  )

  const q = query.trim()
  const emailLike = isEmailNeedle(q)
  const showInviteByEmail = q.length > 0 && (emailLike || (!candLoading && !candError && candidates.length === 0))

  return (
    <>
      <div className={detailStyles.railSectionLabel} data-testid="settings-invite-count">
        Add someone
        <span className={detailStyles.railSectionCount}>{workspaceCandidates.length} not yet in this project</span>
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

      {candLoading ? (
        <p className="modal-sub" style={{ marginTop: 10 }} data-testid="project-invite-loading">
          Searching…
        </p>
      ) : candError ? (
        <p className="modal-sub" style={{ marginTop: 10 }} data-testid="project-invite-search-error">
          Couldn&rsquo;t search right now — keep typing to retry.
        </p>
      ) : (
        // Fixed-height, independently-scrolling list region — same pattern
        // the Settings › Members tab uses (search-first + fixed-height
        // scroll for a long candidate list).
        <div
          className={listClassName}
          style={listClassName ? { marginTop: 10 } : { marginTop: 10, maxHeight: 296, overflowY: "auto" }}
          data-testid="project-invite-results"
        >
          {/* Empty query (on open): the primary "add someone" picker —
              workspace non-members only, a member already on the project
              never appears. Non-empty query: the typeahead over the full
              candidate set (member/workspace/company), unchanged. */}
          {(q.length === 0 ? workspaceCandidates : candidates).map((c) => {
            const label = c.name ?? c.email ?? "Unknown"
            const needle = c.email ?? c.name ?? ""
            const isMember = c.kind === "member"
            const isPending = !isMember && isPendingEmail(c.email)
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
                ) : isPending ? (
                  <span className="modal-sub" data-testid="project-invite-pending">
                    Invited
                  </span>
                ) : (
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => addByNeedle(needle, label, c.email)}
                    disabled={addingNeedle === needle}
                    data-testid="project-invite-add"
                  >
                    {addingNeedle === needle ? "Adding…" : "Add"}
                  </button>
                )}
              </div>
            )
          })}
          {q.length === 0 && workspaceCandidates.length === 0 ? (
            <p className="modal-sub" data-testid="project-invite-empty-workspace">
              Everyone in this workspace is already on the project — invite someone new by email below.
            </p>
          ) : null}
          {showInviteByEmail ? (
            <div className={detailStyles.memberRow} data-testid="project-invite-by-email-row">
              <div className={detailStyles.memberMain}>
                <div className={detailStyles.memberName}>
                  {emailLike ? `Invite ${q} by email` : "No matches — invite by email"}
                </div>
                <div className={detailStyles.memberRole}>They get an invite and land in this project.</div>
              </div>
              {emailLike && isPendingEmail(q) ? (
                <span className="modal-sub" data-testid="project-invite-pending">
                  Invited
                </span>
              ) : (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => addByNeedle(q, q, q)}
                  disabled={!emailLike || addingNeedle === q}
                  title={emailLike ? undefined : "Enter a full email address to invite someone new"}
                  data-testid="project-invite-by-email"
                >
                  {addingNeedle === q ? "Inviting…" : "Invite"}
                </button>
              )}
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
    </>
  )
}

export type ProjectInviteModalProps = {
  projectId: number | string
  open: boolean
  onClose: () => void
  /** Fired after a successful add/invite so the caller re-fetches the roster
   *  (`refetchProject`) and the new member shows in the members list. */
  onInvited: () => void
}

export function ProjectInviteModal({ projectId, open, onClose, onInvited }: ProjectInviteModalProps) {
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

  useEscapeToClose(open, onClose)

  if (!open) return null

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
          <ProjectInviteBody projectId={projectId} onInvited={onInvited} />
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
