"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  IconArrowLeft, IconLink, IconDots, IconUser, IconBook, IconPaperclip,
  IconExternalLink, IconMessageCircle, IconArrowsExchange, IconPlus,
  IconChevronDown, IconCheck, IconX, IconSend, IconSparkles,
} from "@tabler/icons-react"
import { useNavigation } from "../../context/NavigationContext"
import {
  ticketDataApi, teamApi,
  type GeneratedStory, type TicketAssignee, type TeamMemberRecord,
} from "../../lib/api"

// Picker option sets. Priority/status mirror the ClickUp push mapping; sprint is
// a small fixed set (no sprint backend yet — the chosen label is just persisted).
const PRIORITY_OPTIONS = ["P0 — Critical", "P1 — High", "P2 — Medium", "P3 — Low"]
const STATUS_OPTIONS = ["Backlog", "To do", "In progress", "Review", "Done"]
const SPRINT_OPTIONS = ["Sprint 25", "Sprint 26", "Unassigned sprint"]

// Generated stories carry a free-form priority (urgent|high|normal|low — the
// user-stories skill's enum); the picker's canonical labels are P0–P3. Map
// either form (and an already-persisted "Pn — …" label) onto a canonical
// option so the pill shows e.g. "P1 — High" instead of the raw "high" — which,
// matching no option, also left the dropdown with nothing marked active.
export function normalizePriority(value: string | null | undefined): string {
  const v = (value || "").trim().toLowerCase()
  if (!v) return "P2 — Medium"
  if (v.startsWith("p0") || v.includes("urgent") || v.includes("critical")) return "P0 — Critical"
  if (v.startsWith("p1") || v.includes("high")) return "P1 — High"
  if (v.startsWith("p3") || v.includes("low")) return "P3 — Low"
  // p2 / "normal" (the skill's word for medium) / "medium" all land here, as
  // does anything unrecognized — a safe, neutral default.
  return "P2 — Medium"
}

const AVATAR_PALETTE = [
  { bg: "#FDE2E4", color: "#C13838" }, { bg: "#E0F0E9", color: "#179463" },
  { bg: "#E4E9FD", color: "#3B5BDB" }, { bg: "#FBEAD7", color: "#B5740F" },
  { bg: "#EADCF7", color: "#7C3AED" },
]

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return "—"
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

function avatarColor(seed: string) {
  let h = 0
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0
  return AVATAR_PALETTE[Math.abs(h) % AVATAR_PALETTE.length]
}

/** Stable per-ticket key for the overrides store. Prefers the content-derived
 *  `id` stamped at generation (hash of title+body) so edits survive list
 *  reordering and re-attach on an identical regeneration without misattaching
 *  across genuinely-different ones. Falls back to a title slug for any set
 *  cached before `id` existed. */
export function ticketKeyFor(prdId: number, story: GeneratedStory): string {
  if (story.id) return `prd-${prdId}-${story.id}`
  const slug = (story.title || "ticket")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60)
  return `prd-${prdId}-${slug || "ticket"}`
}

type Attachment = { id: number; label: string; sub: string }
type Comment = { id: number; author: string; body: string; time: string }

/** In-panel editable ticket detail. Opens when a generated ticket is clicked in
 *  the Tickets tab. Fields are merged: saved overrides win over the generated
 *  story; every edit persists via ticketDataApi (description/AC, fields,
 *  attachments, comments). */
export function TicketDetail({ story, index, prdId, onBack }: {
  story: GeneratedStory; index: number; prdId: number; onBack: () => void
}) {
  const { showToast } = useNavigation()
  const key = useMemo(() => ticketKeyFor(prdId, story), [prdId, story])

  const [title, setTitle] = useState(story.title)
  const [priority, setPriority] = useState(normalizePriority(story.priority))
  const [status, setStatus] = useState("Backlog")
  const [sprint, setSprint] = useState("Unassigned sprint")
  const [assignee, setAssignee] = useState<TicketAssignee | null>(null)
  const [description, setDescription] = useState(story.body)
  const [criteria, setCriteria] = useState<string[]>(story.acceptance_criteria)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [comments, setComments] = useState<Comment[]>([])
  const [summary, setSummary] = useState<string | null>(null)

  const [members, setMembers] = useState<TeamMemberRecord[] | null>(null)
  const [openMenu, setOpenMenu] = useState<null | "priority" | "status" | "sprint" | "reassign">(null)
  const [adding, setAdding] = useState(false)
  const [newAttach, setNewAttach] = useState({ label: "", sub: "" })
  const [commentText, setCommentText] = useState("")

  // Load saved overrides, merged over the generated story.
  useEffect(() => {
    let cancelled = false
    ticketDataApi.getData(key).then((d) => {
      if (cancelled) return
      if (d.title != null) setTitle(d.title)
      if (d.priority != null) setPriority(normalizePriority(d.priority))
      if (d.status != null) setStatus(d.status)
      if (d.sprint != null) setSprint(d.sprint)
      if (d.assignee != null) setAssignee(d.assignee)
      if (d.description != null) setDescription(d.description)
      if (d.acceptance_criteria != null) setCriteria(d.acceptance_criteria)
      setAttachments(d.attachments)
      setComments(d.comments)
    }).catch(() => { /* first-open / offline → keep generated defaults */ })
    return () => { cancelled = true }
  }, [key])

  // AI summary of the comment thread — only once there's a real discussion
  // (>= 2 comments). Refetched when the comment count changes.
  useEffect(() => {
    if (comments.length < 2) { setSummary(null); return }
    let cancelled = false
    ticketDataApi.summarizeComments(key)
      .then((r) => { if (!cancelled) setSummary(r.summary) })
      .catch(() => { /* best-effort — hide the block on failure */ })
    return () => { cancelled = true }
  }, [key, comments.length])

  const saveFields = (patch: Parameters<typeof ticketDataApi.saveFields>[1]) => {
    ticketDataApi.saveFields(key, patch).catch(() => showToast("Couldn't save", "Your change may not persist."))
  }
  const saveDescription = (desc: string, acs: string[]) => {
    ticketDataApi.saveDescription(key, desc, acs).catch(() => showToast("Couldn't save", "Your change may not persist."))
  }

  const pickPriority = (v: string) => { setPriority(v); setOpenMenu(null); saveFields({ priority: v }) }
  const pickStatus = (v: string) => { setStatus(v); setOpenMenu(null); saveFields({ status: v }) }
  const pickSprint = (v: string) => { setSprint(v); setOpenMenu(null); saveFields({ sprint: v }) }

  const openReassign = () => {
    setOpenMenu((m) => (m === "reassign" ? null : "reassign"))
    if (members == null) {
      teamApi.list().then((r) => setMembers(r.members)).catch(() => setMembers([]))
    }
  }
  const pickAssignee = (m: TeamMemberRecord) => {
    const a: TicketAssignee = {
      user_id: m.user_id, display_name: m.display_name, email: m.email,
      role: m.role, avatar_url: m.avatar_url,
    }
    setAssignee(a); setOpenMenu(null); saveFields({ assignee: a })
  }

  const setCriterion = (i: number, v: string) => setCriteria((cs) => cs.map((c, j) => (j === i ? v : c)))
  const removeCriterion = (i: number) => {
    setCriteria((cs) => { const next = cs.filter((_, j) => j !== i); saveDescription(description, next); return next })
  }
  const addCriterion = () => setCriteria((cs) => [...cs, ""])

  const addAttachment = () => {
    const label = newAttach.label.trim()
    if (!label) return
    ticketDataApi.addAttachment(key, label, newAttach.sub.trim()).then((a) => {
      setAttachments((xs) => [...xs, a]); setNewAttach({ label: "", sub: "" }); setAdding(false)
    }).catch(() => showToast("Couldn't add attachment", "Try again."))
  }
  const removeAttachment = (id: number) => {
    setAttachments((xs) => xs.filter((a) => a.id !== id))
    ticketDataApi.removeAttachment(key, id).catch(() => { /* best-effort */ })
  }

  const addComment = () => {
    const body = commentText.trim()
    if (!body) return
    ticketDataApi.addComment(key, "You", body).then((c) => {
      setComments((xs) => [...xs, c]); setCommentText("")
    }).catch(() => showToast("Couldn't post comment", "Try again."))
  }
  const removeComment = (id: number) => {
    setComments((xs) => xs.filter((c) => c.id !== id))
    ticketDataApi.removeComment(key, id).catch(() => { /* best-effort */ })
  }

  const copyLink = () => {
    const url = `${window.location.origin}${window.location.pathname}?ticket=${encodeURIComponent(key)}`
    navigator.clipboard?.writeText(url).then(
      () => showToast("Link copied", "Ticket link is on your clipboard."),
      () => showToast("Couldn't copy", url),
    )
  }

  const assigneeName = assignee?.display_name || "Unassigned"
  const av = avatarColor(assigneeName)

  return (
    <div className="tkt-detail">
      {/* Nav */}
      <div className="tkt-detail-nav">
        <button type="button" className="tkt-back-btn" onClick={onBack}>
          <IconArrowLeft size={13} /> All chunks
        </button>
        <span className="tkt-detail-id-chip">{`T-${index + 1}`}</span>
        <button type="button" className="tkt-copy-link" onClick={copyLink}>
          <IconLink size={12} /> Copy link
        </button>
        <button type="button" className="tkt-more-btn" aria-label="More"><IconDots size={14} /></button>
      </div>

      {/* Editable title */}
      <input
        className="tkt-detail-title"
        style={{ border: "none", outline: "none", width: "100%", background: "transparent", padding: 0 }}
        value={title}
        aria-label="Ticket title"
        onChange={(e) => setTitle(e.target.value)}
        onBlur={() => { const t = title.trim(); if (t && t !== story.title) saveFields({ title: t }) }}
      />

      {/* Pickers */}
      <div className="tkt-detail-badges">
        <Picker label={priority} kind="priority" open={openMenu === "priority"}
          options={PRIORITY_OPTIONS} active={priority}
          onToggle={() => setOpenMenu((m) => (m === "priority" ? null : "priority"))}
          onPick={pickPriority} className="tkt-badge--priority" />
        <Picker label={status} kind="status" open={openMenu === "status"}
          options={STATUS_OPTIONS} active={status}
          onToggle={() => setOpenMenu((m) => (m === "status" ? null : "status"))}
          onPick={pickStatus} />
        <Picker label={sprint} kind="sprint" open={openMenu === "sprint"}
          options={SPRINT_OPTIONS} active={sprint}
          onToggle={() => setOpenMenu((m) => (m === "sprint" ? null : "sprint"))}
          onPick={pickSprint} />
      </div>

      {/* Person responsible */}
      <div className="tkt-detail-section">
        <div className="tkt-detail-section-label"><IconUser size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />Person responsible</div>
        <div className="tkt-person-row" style={{ position: "relative" }}>
          <span className="tkt-person-avatar" style={{ background: av.bg, color: av.color }}>{initials(assigneeName)}</span>
          <div className="tkt-person-info">
            <div className="tkt-person-name">{assigneeName}</div>
            {assignee ? <div className="tkt-person-role">{[assignee.email, assignee.role].filter(Boolean).join(" · ")}</div> : null}
          </div>
          <button type="button" className="tkt-reassign-btn" onClick={openReassign}>
            <IconArrowsExchange size={11} /> Reassign
          </button>
          {openMenu === "reassign" && (
            <>
              <div className="tkt-reassign-backdrop" onClick={() => setOpenMenu(null)} />
              <div className="tkt-reassign-menu">
                {members == null ? (
                  <div className="tkt-reassign-option-role" style={{ padding: 10 }}>Loading…</div>
                ) : members.length === 0 ? (
                  <div className="tkt-reassign-option-role" style={{ padding: 10 }}>No team members</div>
                ) : members.map((m) => {
                  const nm = m.display_name || m.email || "Member"
                  const c = avatarColor(nm)
                  return (
                    <button key={m.user_id} type="button"
                      className={`tkt-reassign-option${assignee?.user_id === m.user_id ? " tkt-reassign-option--active" : ""}`}
                      onClick={() => pickAssignee(m)}>
                      <span className="tkt-reassign-option-avatar" style={{ background: c.bg, color: c.color }}>{initials(nm)}</span>
                      <span className="tkt-reassign-option-info">
                        <span className="tkt-reassign-option-name">{nm}</span>
                        {m.role ? <span className="tkt-reassign-option-role">{m.role}</span> : null}
                      </span>
                    </button>
                  )
                })}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Description + acceptance criteria */}
      <div className="tkt-detail-section">
        <div className="tkt-detail-section-label"><IconBook size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />Description</div>
        <textarea
          className="tkt-comment-input"
          style={{ minHeight: 90, resize: "vertical", marginBottom: 14, padding: "9px 13px" }}
          value={description}
          placeholder="Add a description…"
          onChange={(e) => setDescription(e.target.value)}
          onBlur={() => saveDescription(description, criteria)}
        />
        <div className="tkt-detail-criteria-label">Acceptance criteria</div>
        <div className="tkt-detail-criteria" style={{ listStyle: "none", paddingLeft: 0 }}>
          {criteria.map((c, i) => (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span aria-hidden style={{ color: "var(--ink-4)" }}>•</span>
              <input className="tkt-comment-input" style={{ padding: "6px 11px", flex: 1 }}
                value={c}
                placeholder="Acceptance criterion"
                onChange={(e) => setCriterion(i, e.target.value)}
                onBlur={() => saveDescription(description, criteria)} />
              <button type="button" className="tkt-more-btn" aria-label="Remove criterion" onClick={() => removeCriterion(i)}><IconX size={13} /></button>
            </div>
          ))}
        </div>
        <button type="button" className="tkt-attach-btn" style={{ marginTop: 10 }} onClick={addCriterion}>
          <IconPlus size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />Add criterion
        </button>
      </div>

      {/* Attachments */}
      <div className="tkt-detail-section">
        <div className="tkt-detail-section-label"><IconPaperclip size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />Attachments · {attachments.length}</div>
        {attachments.length > 0 && (
          <div className="tkt-attachments">
            {attachments.map((a) => (
              <div key={a.id} className="tkt-attachment-row">
                <span className="tkt-attachment-icon"><IconExternalLink size={14} /></span>
                <div className="tkt-attachment-info">
                  <div className="tkt-attachment-label">{a.label}</div>
                  {a.sub ? <div className="tkt-attachment-sub">{a.sub}</div> : null}
                </div>
                <button type="button" className="tkt-more-btn" aria-label="Remove attachment" onClick={() => removeAttachment(a.id)}><IconX size={13} /></button>
              </div>
            ))}
          </div>
        )}
        {adding ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input className="tkt-comment-input" style={{ padding: "7px 11px", flex: "1 1 160px" }}
              autoFocus placeholder="Label or link" value={newAttach.label}
              onChange={(e) => setNewAttach((s) => ({ ...s, label: e.target.value }))}
              onKeyDown={(e) => { if (e.key === "Enter") addAttachment() }} />
            <input className="tkt-comment-input" style={{ padding: "7px 11px", flex: "1 1 160px" }}
              placeholder="Note (optional)" value={newAttach.sub}
              onChange={(e) => setNewAttach((s) => ({ ...s, sub: e.target.value }))}
              onKeyDown={(e) => { if (e.key === "Enter") addAttachment() }} />
            <button type="button" className="tkt-reassign-btn" onClick={addAttachment}><IconCheck size={12} /> Add</button>
          </div>
        ) : (
          <button type="button" className="tkt-attach-btn" onClick={() => setAdding(true)}>
            <IconPlus size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />Attach a file or paste a link
          </button>
        )}
      </div>

      {/* Comments */}
      <div className="tkt-detail-section">
        <div className="tkt-detail-section-label"><IconMessageCircle size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />Comments · {comments.length}</div>
        {summary ? (
          <div className="tkt-comment-summary">
            <div className="tkt-comment-summary-h"><IconSparkles size={12} /> Summary</div>
            {summary}
          </div>
        ) : null}
        {comments.length === 0 ? (
          <div className="tkt-comments-empty">No comments yet.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 12 }}>
            {comments.map((c) => {
              const cc = avatarColor(c.author)
              return (
                <div key={c.id} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                  <span className="tkt-comment-avatar" style={{ background: cc.bg, color: cc.color }}>{initials(c.author)}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <strong style={{ fontSize: 12.5, color: "var(--ink)" }}>{c.author}</strong>
                      <span style={{ fontSize: 11, color: "var(--ink-4)" }}>{c.time}</span>
                      <button type="button" className="tkt-more-btn" style={{ marginLeft: "auto" }} aria-label="Remove comment" onClick={() => removeComment(c.id)}><IconX size={12} /></button>
                    </div>
                    <div style={{ fontSize: 12.5, color: "var(--ink)", lineHeight: 1.5 }}>{c.body}</div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
        <div className="tkt-comment-composer">
          <span className="tkt-comment-avatar" style={{ background: "#E0F0E9", color: "#179463" }}>YOU</span>
          <div className="tkt-comment-input-wrap">
            <textarea className="tkt-comment-input" rows={1} placeholder="Add a comment…"
              value={commentText}
              onChange={(e) => setCommentText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); addComment() } }} />
            <button type="button" className="tkt-comment-send" aria-label="Post comment" disabled={!commentText.trim()} onClick={addComment}>
              <IconSend size={13} />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

/** A badge that opens a dropdown of options — used for priority/status/sprint. */
function Picker({ label, kind, open, options, active, onToggle, onPick, className = "" }: {
  label: string; kind: string; open: boolean; options: string[]; active: string
  onToggle: () => void; onPick: (v: string) => void; className?: string
}) {
  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button type="button" className={`tkt-badge tkt-badge--status ${className}`} aria-label={kind} onClick={onToggle}>
        {label} <IconChevronDown size={12} />
      </button>
      {open && (
        <>
          <div className="tkt-status-backdrop" onClick={onToggle} />
          <div className="tkt-status-menu">
            {options.map((o) => (
              <div key={o} className={`tkt-status-option${o === active ? " tkt-status-option--active" : ""}`} onClick={() => onPick(o)}>
                {o === active ? <IconCheck size={13} /> : <span style={{ width: 13 }} />}{o}
              </div>
            ))}
          </div>
        </>
      )}
    </span>
  )
}
