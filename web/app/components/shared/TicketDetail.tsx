"use client"

import { Fragment, useEffect, useMemo, useState } from "react"
import { IconArrowLeft, IconChevronDown, IconCheck, IconExternalLink } from "@tabler/icons-react"
import { useNavigation } from "../../context/NavigationContext"
import {
  ticketDataApi, teamApi,
  type GeneratedStory, type TicketAssignee, type TeamMemberRecord,
} from "../../lib/api"

const STATUS_OPTIONS = ["Backlog", "To do", "In progress", "Review", "Done"]

// Generated tickets carry a free-form priority (urgent|high|normal|low — the
// skill's enum). Map any form (and an already-persisted "Pn — …" label) onto a
// canonical label.
export function normalizePriority(value: string | null | undefined): string {
  const v = (value || "").trim().toLowerCase()
  if (!v) return "P2 — Medium"
  if (v.startsWith("p0") || v.includes("urgent") || v.includes("critical")) return "P0 — Critical"
  if (v.startsWith("p1") || v.includes("high")) return "P1 — High"
  if (v.startsWith("p3") || v.includes("low")) return "P3 — Low"
  return "P2 — Medium"
}

/** Priority → the reference pill label + variant class. Urgent/High/Normal only
 *  (Low renders as Normal per the reference's three-pill vocabulary). */
export function priorityPill(value: string | null | undefined): { label: string; variant: string } {
  const v = (value || "").trim().toLowerCase()
  if (v.startsWith("p0") || v.includes("urgent") || v.includes("critical")) return { label: "URGENT", variant: "urgent" }
  if (v.startsWith("p1") || v.includes("high")) return { label: "HIGH", variant: "high" }
  return { label: "NORMAL", variant: "normal" }
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
 *  `id` stamped at generation so edits survive list reordering. */
export function ticketKeyFor(prdId: number, story: GeneratedStory): string {
  if (story.id) return `prd-${prdId}-${story.id}`
  const slug = (story.title || "ticket")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60)
  return `prd-${prdId}-${slug || "ticket"}`
}

type Attachment = { id: number; label: string; sub: string }
type Comment = { id: number; author: string; body: string; time: string }

/** Highlight the story/criteria keywords the reference renders in green. */
function highlightGWT(text: string) {
  return text.split(/\b(Given|When|Then|As an?|I want|so that)\b/g).map((part, i) =>
    /^(Given|When|Then|As an?|I want|so that)$/.test(part)
      ? <span key={i} className="k">{part}</span>
      : <Fragment key={i}>{part}</Fragment>,
  )
}

/** Split a leading [failure]/[edge] tag off an inherited acceptance criterion. */
function splitAcTag(text: string): { tag: "failure" | "edge" | null; rest: string } {
  const m = text.match(/^\s*\[(failure|edge)\]\s*/i)
  if (!m) return { tag: null, rest: text }
  return { tag: m[1].toLowerCase() as "failure" | "edge", rest: text.slice(m[0].length) }
}

/** In-panel ticket detail — the `ticket` skill's canonical detail (Jira
 *  anatomy): full-width five-section description over a two-column zone (main
 *  story column + Details rail). Structured fields drive it; legacy/thin
 *  tickets fall back to the plain description + a generated-AC flag. */
export function TicketDetail({ story, index, prdId, onBack }: {
  story: GeneratedStory; index: number; prdId: number; onBack: () => void
}) {
  const { showToast } = useNavigation()
  const key = useMemo(() => ticketKeyFor(prdId, story), [prdId, story])

  const [title, setTitle] = useState(story.title)
  const [status, setStatus] = useState("Backlog")
  const [assignee, setAssignee] = useState<TicketAssignee | null>(null)
  const [description, setDescription] = useState(story.body)
  const [criteria, setCriteria] = useState<string[]>(story.acceptance_criteria)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [comments, setComments] = useState<Comment[]>([])
  const [summary, setSummary] = useState<string | null>(null)

  const [members, setMembers] = useState<TeamMemberRecord[] | null>(null)
  const [openMenu, setOpenMenu] = useState<null | "status" | "reassign">(null)
  const [commentText, setCommentText] = useState("")

  // Does this ticket carry the structured five-section contract, or is it a
  // legacy/thin story (body only)? Drives description rendering.
  const structured = Boolean(
    story.what || story.why_now || story.user_story || (story.scope && story.scope.length) || story.out_of_scope,
  )

  // Load saved overrides, merged over the generated story.
  useEffect(() => {
    let cancelled = false
    ticketDataApi.getData(key).then((d) => {
      if (cancelled) return
      if (d.title != null) setTitle(d.title)
      if (d.status != null) setStatus(d.status)
      if (d.assignee != null) setAssignee(d.assignee)
      if (d.description != null) setDescription(d.description)
      if (d.acceptance_criteria != null) setCriteria(d.acceptance_criteria)
      setAttachments(d.attachments)
      setComments(d.comments)
    }).catch(() => { /* first-open / offline → keep generated defaults */ })
    return () => { cancelled = true }
  }, [key])

  // AI summary of the comment thread — only once there's a real discussion.
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

  const pickStatus = (v: string) => { setStatus(v); setOpenMenu(null); saveFields({ status: v }) }

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

  const addComment = () => {
    const body = commentText.trim()
    if (!body) return
    ticketDataApi.addComment(key, "You", body).then((c) => {
      setComments((xs) => [...xs, c]); setCommentText("")
    }).catch(() => showToast("Couldn't post comment", "Try again."))
  }

  // The change loop's first step: record an accepted proposal as a system note.
  // Full cross-artifact propagation (ticket AC + PRD row/test version bump +
  // design agent) lands with the sync phase.
  const acceptPropagate = () => {
    ticketDataApi.addComment(key, "Sprntly", `✳ Accepted proposed change: ${summary ?? ""}`)
      .then((c) => setComments((xs) => [...xs, c]))
      .catch(() => { /* best-effort */ })
    showToast("Change recorded", "Cross-artifact propagation ships with the sync phase.")
  }

  const pill = priorityPill(story.priority)
  const assigneeName = assignee?.display_name || "Unassigned"
  const av = avatarColor(assigneeName)
  const acCount = criteria.length
  const routeAgentReady = (story.route || "").toLowerCase().includes("agent")

  return (
    <div className="tkv2 tkv2-detail">
      {/* Header strip */}
      <div className="tkv2-dtop">
        <div className="tkv2-crumb">
          <button type="button" className="tkv2-back" onClick={onBack}>
            <IconArrowLeft size={13} /> All tickets
          </button>
          &nbsp; /&nbsp; <span className="tkv2-key" style={{ padding: "3px 9px" }}>{`T-${index + 1}`}</span>
          {story.ticket_type && story.ticket_type !== "build" ? (
            <span className={`tkv2-typechip tkv2-typechip--${story.ticket_type}`} style={{ marginLeft: 8 }}>
              {story.ticket_type}
            </span>
          ) : null}
        </div>
        <input
          className="tkv2-dtitle"
          value={title}
          aria-label="Ticket title"
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => { const t = title.trim(); if (t && t !== story.title) saveFields({ title: t }) }}
        />
      </div>

      <div className="tkv2-edithint">
        ✎ Title, fields and comments are editable in place; edits sync on push.
        Acceptance criteria are inherited from the spec — propose changes in comments instead of editing.
      </div>

      {/* Full-width description */}
      <div className="tkv2-descwide">
        <div className="tkv2-sec">
          <h4>Description</h4>
          {story.ticket_type === "decision" ? (
            <DecisionBlock story={story} />
          ) : story.ticket_type === "spike" ? (
            <SpikeBlock story={story} />
          ) : structured ? (
            <>
              {story.what ? (<><div className="tkv2-dlbl">What</div><p className="tkv2-dtx">{story.what}</p></>) : null}
              {story.why_now ? (<><div className="tkv2-dlbl">Why now</div><p className="tkv2-dtx">{story.why_now}</p></>) : null}
              {story.user_story ? (<><div className="tkv2-dlbl">User story</div><p className="tkv2-dtx">{highlightGWT(story.user_story)}</p></>) : null}
              {story.scope && story.scope.length ? (
                <><div className="tkv2-dlbl">The ticket must cover</div>
                  <ul className="tkv2-dlist">{story.scope.map((s, i) => <li key={i}>{s}</li>)}</ul></>
              ) : null}
              {story.out_of_scope ? (<><div className="tkv2-dlbl">Out of scope</div><p className="tkv2-dtx">{story.out_of_scope}</p></>) : null}
              {(story.prd_section || (story.signals && story.signals.length) || (story.data_gaps && story.data_gaps.length)) ? (
                <p className="tkv2-dtx tkv2-ground" style={{ marginTop: 10 }}>
                  Grounding: {story.prd_section ? <a>{story.prd_section}</a> : null}
                  {story.signals && story.signals.length ? <> · {story.signals.join(" · ")}</> : null}
                  {story.data_gaps && story.data_gaps.length
                    ? story.data_gaps.map((g, i) => <span key={i} className="tkv2-need"> [{g}]</span>)
                    : null}
                </p>
              ) : null}
            </>
          ) : (
            <textarea
              className="tkv2-dtx"
              style={{ width: "100%", minHeight: 90, resize: "vertical", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 13px" }}
              value={description}
              placeholder="Add a description…"
              onChange={(e) => setDescription(e.target.value)}
              onBlur={() => saveDescription(description, criteria)}
            />
          )}
        </div>
      </div>

      {/* Two-column zone */}
      {/* Details bar — horizontal, sized for the narrow tickets panel. (Was a
          300px side rail that cramped and clipped beside the tall criteria
          column; a rail only works at the reference's full page width.) */}
      <div className="tkv2-detailbar">
        <div style={{ position: "relative" }}>
          <button type="button" className="tkv2-statusbtn" onClick={() => setOpenMenu((m) => (m === "status" ? null : "status"))}>
            {status} <IconChevronDown size={12} />
          </button>
          {openMenu === "status" ? (
            <div className="tkv2-picker" style={{ position: "absolute", zIndex: 20 }}>
              {STATUS_OPTIONS.map((o) => (
                <button key={o} type="button" className={`tkv2-pitem${o === status ? " tkv2-pitem--sel" : ""}`} onClick={() => pickStatus(o)}>
                  {o === status ? <IconCheck size={12} /> : <span style={{ width: 12 }} />}{o}
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <div className="tkv2-fields">
          <div className="tkv2-field" style={{ position: "relative" }}>
            <span className="tkv2-fl">Assignee</span>
            <button type="button" aria-label="Reassign" onClick={openReassign} className="tkv2-fv" style={{ display: "inline-flex", alignItems: "center", gap: 6, background: "none", border: "none", cursor: "pointer", padding: 0 }}>
              <span className="tkv2-av" style={{ width: 22, height: 22, fontSize: 9, background: av.bg, color: av.color, borderColor: av.color }}>{initials(assigneeName)}</span>
              {assigneeName}
            </button>
            {openMenu === "reassign" ? (
              <div className="tkv2-picker" style={{ position: "absolute", left: 0, zIndex: 20 }}>
                <div className="ph2">Reassign</div>
                {members == null ? <div className="tkv2-pitem">Loading…</div>
                  : members.length === 0 ? <div className="tkv2-pitem">No team members</div>
                  : members.map((m) => {
                    const nm = m.display_name || m.email || "Member"
                    return (
                      <button key={m.user_id} type="button" className={`tkv2-pitem${assignee?.user_id === m.user_id ? " tkv2-pitem--sel" : ""}`} onClick={() => pickAssignee(m)}>
                        {nm}{m.role ? <span className="tkv2-ppath">{m.role}</span> : null}
                      </button>
                    )
                  })}
              </div>
            ) : null}
          </div>
          <div className="tkv2-field"><span className="tkv2-fl">Reporter</span><span className="tkv2-fv tkv2-fv--muted">Sprntly PM Agent</span></div>
          <div className="tkv2-field"><span className="tkv2-fl">Priority</span><span className="tkv2-fv"><span className={`tkv2-pill tkv2-pill--${pill.variant}`}>{pill.label}</span></span></div>
          {story.labels && story.labels.length ? (
            <div className="tkv2-field"><span className="tkv2-fl">Labels</span><span className="tkv2-fv tkv2-fv--muted">{story.labels.join(" · ")}</span></div>
          ) : null}
          {story.prd_section ? (
            <div className="tkv2-field"><span className="tkv2-fl">Provenance</span><span className="tkv2-fv">{story.prd_section}</span></div>
          ) : null}
          {story.story_points != null ? (
            <div className="tkv2-field"><span className="tkv2-fl">Story points</span><span className="tkv2-fv">{story.story_points}</span></div>
          ) : null}
          {story.route ? (
            <div className="tkv2-field"><span className="tkv2-fl">Route</span><span className="tkv2-fv" style={{ color: routeAgentReady ? "var(--green-d)" : undefined }}>{story.route}</span></div>
          ) : null}
          {story.ears_ids && story.ears_ids.length ? (
            <div className="tkv2-field"><span className="tkv2-fl">Traces</span><span className="tkv2-fv tkv2-fv--muted">{story.ears_ids.join(" · ")}</span></div>
          ) : null}
        </div>
      </div>

      {/* Main content — full width */}
      <div className="tkv2-body">
          {/* Acceptance criteria */}
          <div className="tkv2-sec">
            <h4>Acceptance criteria — {acCount}</h4>
            {acCount === 0 ? (
              <div className="tkv2-empty">No acceptance criteria yet.</div>
            ) : (
              <div className="tkv2-ac">
                {criteria.map((c, i) => {
                  const { tag, rest } = splitAcTag(c)
                  return (
                    <div key={i} className="tkv2-acitem">
                      <span className="tkv2-cb" />
                      <span className="tkv2-actxt">
                        {tag === "failure" ? <span className="tkv2-tagf">[failure]</span> : null}
                        {tag === "edge" ? <span className="tkv2-tagn">[edge]</span> : null}
                        {highlightGWT(rest)}
                      </span>
                    </div>
                  )
                })}
                {story.ac_inherited ? (
                  <span className="tkv2-inherit">Inherited from the PRD&apos;s implementation spec · propose changes in comments — don&apos;t edit here</span>
                ) : (
                  <span className="tkv2-gen">GENERATED ⚠ not inherited — run prd-author for a Part B to inherit spec-first tests</span>
                )}
              </div>
            )}
          </div>

          {/* Child issues */}
          {story.subtasks && story.subtasks.length ? (
            <div className="tkv2-sec">
              <h4>Child issues</h4>
              {story.subtasks.map((t, i) => {
                const parallel = /^\s*\[P\]\s*/i.test(t)
                const label = t.replace(/^\s*\[P\]\s*/i, "")
                return (
                  <div key={i} className="tkv2-subt">
                    <span className="tkv2-cb" /> {label}
                    {parallel ? <span className="tkv2-sk">[P] parallel</span> : null}
                  </div>
                )
              })}
            </div>
          ) : null}

          {/* Linked issues */}
          {(story.blocked_by && story.blocked_by.length) || (story.blocks && story.blocks.length) ? (
            <div className="tkv2-sec">
              <h4>Linked issues</h4>
              {story.blocked_by && story.blocked_by.length ? (
                <>
                  <div className="tkv2-deplbl">is blocked by</div>
                  {story.blocked_by.map((d, i) => <span key={i} className="tkv2-dep tkv2-dep--block">{d}</span>)}
                </>
              ) : null}
              {story.blocks && story.blocks.length ? (
                <>
                  <div className="tkv2-deplbl">blocks</div>
                  {story.blocks.map((d, i) => <span key={i} className="tkv2-dep">{d}</span>)}
                </>
              ) : null}
            </div>
          ) : null}

          {/* Attachments */}
          <div className="tkv2-sec">
            <h4>Attachments{attachments.length ? ` — ${attachments.length}` : ""}</h4>
            {attachments.length === 0 ? (
              <div className="tkv2-empty">No attachments yet.</div>
            ) : (
              <div className="tkv2-att">
                {attachments.map((a) => (
                  <a key={a.id} className="tkv2-attchip" href={a.sub || "#"} target="_blank" rel="noopener noreferrer">
                    <IconExternalLink size={13} /> {a.label} <span className="open">↗</span>
                  </a>
                ))}
              </div>
            )}
          </div>

          {/* Activity */}
          <div className="tkv2-sec">
            <h4>Activity</h4>
            <div className="tkv2-acttabs">
              <span className="tkv2-atab tkv2-atab--active">Comments</span>
            </div>
            {summary ? (
              <div className="tkv2-aisum">
                <div className="ah">✳ AI summary</div>
                {summary}
                <div className="tkv2-actions2">
                  <button type="button" className="tkv2-btn2 tkv2-btn2--primary" onClick={acceptPropagate}>Accept &amp; propagate</button>
                  <button type="button" className="tkv2-btn2 tkv2-btn2--ghost">Edit</button>
                  <button type="button" className="tkv2-btn2 tkv2-btn2--ghost">Reject</button>
                </div>
              </div>
            ) : null}
            {comments.length === 0 ? (
              <div className="tkv2-empty">No comments yet.</div>
            ) : (
              comments.map((c) => {
                const cc = avatarColor(c.author)
                return (
                  <div key={c.id} className="tkv2-cmt">
                    <span className="tkv2-av" style={{ background: cc.bg, color: cc.color, borderColor: cc.color }}>{initials(c.author)}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="who2">{c.author}<span className="when">{c.time}</span></div>
                      <p>{c.body}</p>
                    </div>
                  </div>
                )
              })
            )}
            <div className="tkv2-ask">
              <input
                placeholder="Ask about this ticket…"
                value={commentText}
                onChange={(e) => setCommentText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") addComment() }}
              />
              <button type="button" className="tkv2-btn2 tkv2-btn2--primary" onClick={addComment} disabled={!commentText.trim()}>Send</button>
            </div>
          </div>
      </div>
    </div>
  )
}

/** Decision-ticket description ([ESCALATE] → decision / owner / decide-by). */
function DecisionBlock({ story }: { story: GeneratedStory }) {
  return (
    <>
      {story.decision ? (<><div className="tkv2-dlbl">Decision</div><p className="tkv2-dtx">{story.decision}</p></>) : null}
      {story.owner ? (<><div className="tkv2-dlbl">Owner</div><p className="tkv2-dtx">{story.owner}</p></>) : null}
      {story.decide_by ? (<><div className="tkv2-dlbl">Decide by</div><p className="tkv2-dtx">{story.decide_by}</p></>) : null}
      {story.blocks && story.blocks.length ? (
        <><div className="tkv2-dlbl">Blocks</div><ul className="tkv2-dlist">{story.blocks.map((b, i) => <li key={i}>{b}</li>)}</ul></>
      ) : null}
    </>
  )
}

/** Spike-ticket description ([ASSUMPTION → T0] → timebox / exit condition). */
function SpikeBlock({ story }: { story: GeneratedStory }) {
  return (
    <>
      {story.what ? (<><div className="tkv2-dlbl">What to validate</div><p className="tkv2-dtx">{story.what}</p></>) : null}
      {story.timebox ? (<><div className="tkv2-dlbl">Timebox</div><p className="tkv2-dtx">{story.timebox}</p></>) : null}
      {story.exit_condition ? (<><div className="tkv2-dlbl">Exit condition</div><p className="tkv2-dtx">{story.exit_condition}</p></>) : null}
    </>
  )
}
