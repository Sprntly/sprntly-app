"use client"

import { Fragment, useEffect, useMemo, useState } from "react"
import {
  IconArrowLeft, IconChevronDown, IconCheck, IconExternalLink,
  IconPencil, IconPlus, IconX,
} from "@tabler/icons-react"
import { useNavigation } from "../../context/NavigationContext"
import {
  ticketDataApi, teamApi,
  type GeneratedStory, type TicketAssignee, type TeamMemberRecord,
} from "../../lib/api"

const STATUS_OPTIONS = ["Backlog", "To do", "In progress", "Review", "Done"]

// The generator's priority enum — what actually lands in the database. The
// pill shows the STORED value verbatim (see priorityPill callers); these are
// the values the picker writes.
const PRIORITY_OPTIONS = ["urgent", "high", "normal", "low"]

/** "2026-07-08 18:23:45.123+00" (backend str(created_at)) → "Jul 8, 2026 · 6:23 PM".
 *  Falls back to the raw string when unparseable so nothing renders blank. */
export function formatWhen(raw: string): string {
  if (!raw) return ""
  // Safari/Firefox won't parse the "YYYY-MM-DD HH:MM:SS+00" shape — make it ISO.
  let d = new Date(raw)
  if (isNaN(d.getTime())) d = new Date(raw.replace(" ", "T"))
  if (isNaN(d.getTime())) return raw
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
  return `${date} · ${time}`
}

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

/** Priority → pill label + variant class. The label is the STORED value
 *  verbatim (uppercased for the pill) — never remapped to a different word —
 *  so what the user sees is exactly what's in the database. Only the pill
 *  COLOR buckets into the three variants (urgent/high/normal). */
export function priorityPill(value: string | null | undefined): { label: string; variant: string } {
  const raw = (value || "").trim()
  const v = raw.toLowerCase()
  let variant = "normal"
  if (v.startsWith("p0") || v.includes("urgent") || v.includes("critical")) variant = "urgent"
  else if (v.startsWith("p1") || v.includes("high")) variant = "high"
  return { label: raw ? raw.toUpperCase() : "—", variant }
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

// ── Description ⇄ editable text ──
//
// The edit override is ONE plain-text column (ticket_edits.description), but a
// structured ticket displays five labeled sections. Edit-what-you-see: the
// editor opens with the sections serialized as labeled text, and the display
// parses that text back into the same styled sections — so an edited ticket
// keeps its What / Why now / … layout instead of collapsing to a blob.

const DESC_SECTION_LABELS = ["What", "Why now", "User story", "The ticket must cover", "Out of scope"]

/** Serialize a structured story's sections into the editable text form. */
export function storyToEditableText(s: GeneratedStory): string {
  const parts: string[] = []
  if (s.what) parts.push(`What\n${s.what}`)
  if (s.why_now) parts.push(`Why now\n${s.why_now}`)
  if (s.user_story) parts.push(`User story\n${s.user_story}`)
  if (s.scope && s.scope.length) parts.push(`The ticket must cover\n${s.scope.map((x) => `- ${x}`).join("\n")}`)
  if (s.out_of_scope) parts.push(`Out of scope\n${s.out_of_scope}`)
  return parts.length ? parts.join("\n\n") : (s.body || "")
}

type DescBlock = { label: string | null; text: string; items?: string[] }

/** Parse edited description text back into labeled display blocks. A line that
 *  is exactly a known section label starts a section; a block of "- " bullets
 *  renders as a list; anything else is a plain paragraph. Freeform text (no
 *  labels) comes back as one unlabeled block. */
export function parseDescBlocks(text: string): DescBlock[] {
  const blocks: DescBlock[] = []
  let cur: DescBlock | null = null
  for (const line of text.split(/\r?\n/)) {
    const label = DESC_SECTION_LABELS.find(
      (l) => line.trim().toLowerCase() === l.toLowerCase(),
    )
    if (label) {
      if (cur && cur.text.trim()) blocks.push(cur)
      cur = { label, text: "" }
    } else {
      if (!cur) cur = { label: null, text: "" }
      cur.text += (cur.text ? "\n" : "") + line
    }
  }
  if (cur && cur.text.trim()) blocks.push(cur)
  return blocks.map((b) => {
    const lines = b.text.split(/\n/).map((x) => x.trim()).filter(Boolean)
    const bullets = lines.length > 0 && lines.every((x) => /^[-•*]\s+/.test(x))
    return bullets
      ? { ...b, text: b.text.trim(), items: lines.map((x) => x.replace(/^[-•*]\s+/, "")) }
      : { ...b, text: b.text.trim() }
  })
}

/** In-panel ticket detail — the `ticket` skill's canonical detail (Jira
 *  anatomy): full-width five-section description over a two-column zone (main
 *  story column + Details rail). Structured fields drive it; legacy/thin
 *  tickets fall back to the plain description + a generated-AC flag. */
export function TicketDetail({ story, index, prdId, onBack, onOpenLinked }: {
  story: GeneratedStory; index: number; prdId: number; onBack: () => void
  /** Open a sibling ticket by its title (linked issues are title references). */
  onOpenLinked?: (title: string) => void
}) {
  const { showToast } = useNavigation()
  const key = useMemo(() => ticketKeyFor(prdId, story), [prdId, story])

  const [title, setTitle] = useState(story.title)
  const [status, setStatus] = useState("Backlog")
  const [assignee, setAssignee] = useState<TicketAssignee | null>(null)
  const [description, setDescription] = useState(story.body)
  const [criteria, setCriteria] = useState<string[]>(story.acceptance_criteria)
  const [priority, setPriority] = useState<string>(story.priority || "")
  const [subtasks, setSubtasks] = useState<string[]>(story.subtasks || [])
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [comments, setComments] = useState<Comment[]>([])
  const [summary, setSummary] = useState<string | null>(null)
  // A concrete acceptance-criteria change the thread proposed (from the summary
  // endpoint) — drives the Accept & propagate action.
  const [proposedCriterion, setProposedCriterion] = useState<string | null>(null)

  const [members, setMembers] = useState<TeamMemberRecord[] | null>(null)
  const [openMenu, setOpenMenu] = useState<null | "status" | "reassign" | "priority">(null)
  const [commentText, setCommentText] = useState("")

  // Hold the body until saved overrides are loaded — rendering the generated
  // ticket first and swapping when the fetch lands reads as "shows the old
  // ticket, then updates".
  const [loaded, setLoaded] = useState(false)

  // Edit modes. The description override is one text column; structured
  // tickets serialize their sections into it for editing and the display
  // parses it back into sections (see storyToEditableText/parseDescBlocks).
  const [hasDescOverride, setHasDescOverride] = useState(false)
  const [editingDesc, setEditingDesc] = useState(false)
  const [descDraft, setDescDraft] = useState("")
  const [acDraft, setAcDraft] = useState<string[] | null>(null) // null = not editing
  const [subsDraft, setSubsDraft] = useState<string[] | null>(null) // null = not editing

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
      if (d.description != null) { setDescription(d.description); setHasDescOverride(true) }
      if (d.acceptance_criteria != null) setCriteria(d.acceptance_criteria)
      if (d.priority != null) setPriority(d.priority)
      if (d.subtasks != null) setSubtasks(d.subtasks)
      setAttachments(d.attachments)
      setComments(d.comments)
      setLoaded(true)
    }).catch(() => setLoaded(true) /* first-open / offline → generated defaults */)
    return () => { cancelled = true }
  }, [key])

  // AI summary of the comment thread — only once there's a real discussion.
  useEffect(() => {
    if (comments.length < 2) { setSummary(null); setProposedCriterion(null); return }
    let cancelled = false
    ticketDataApi.summarizeComments(key)
      .then((r) => {
        if (cancelled) return
        setSummary(r.summary)
        setProposedCriterion(r.proposed_criterion ?? null)
      })
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
  const pickPriority = (v: string) => { setPriority(v); setOpenMenu(null); saveFields({ priority: v }) }

  // ── Description editing ──
  // Seed with exactly what's displayed: the saved override when there is one,
  // else the structured sections serialized to text, else the plain body.
  const startEditDesc = () => {
    setDescDraft(hasDescOverride ? description : structured ? storyToEditableText(story) : description)
    setEditingDesc(true)
  }
  const saveEditDesc = () => {
    const d = descDraft
    setDescription(d); setHasDescOverride(true); setEditingDesc(false)
    saveDescription(d, criteria)
  }

  // ── Acceptance-criteria editing (draft list; null = view mode) ──
  const saveAcDraft = () => {
    if (!acDraft) return
    const next = acDraft.map((c) => c.trim()).filter(Boolean)
    setCriteria(next); setAcDraft(null)
    saveDescription(description, next)
  }

  // ── Child-issue (subtask) editing ──
  const saveSubsDraft = () => {
    if (!subsDraft) return
    const next = subsDraft.map((s) => s.trim()).filter(Boolean)
    setSubtasks(next); setSubsDraft(null)
    saveFields({ subtasks: next })
  }

  /** One draft-list row editor (AC + child issues share the interaction). */
  const editRows = (
    draft: string[],
    setDraft: (v: string[]) => void,
    addLabel: string,
  ) => (
    <>
      {draft.map((v, i) => (
        <div key={i} className="tkv2-editrow">
          <input
            className="input"
            value={v}
            autoFocus={i === draft.length - 1 && v === ""}
            onChange={(e) => setDraft(draft.map((x, j) => (j === i ? e.target.value : x)))}
          />
          <button type="button" className="tkv2-btn2 tkv2-btn2--ghost" aria-label="Remove"
            onClick={() => setDraft(draft.filter((_, j) => j !== i))}>
            <IconX size={13} />
          </button>
        </div>
      ))}
      <button type="button" className="tkv2-btn2 tkv2-btn2--ghost" onClick={() => setDraft([...draft, ""])}>
        <IconPlus size={13} /> {addLabel}
      </button>
    </>
  )

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

  // Accept & propagate: apply the thread's proposed acceptance criterion to this
  // ticket (appended + persisted), and record it as a system note. Propagation
  // BEYOND the ticket (the PRD §5 row + its Part B test with a version bump, and
  // the design agent) is the next step of the change loop.
  const acceptPropagate = () => {
    if (!proposedCriterion) return
    const next = [...criteria, proposedCriterion]
    setCriteria(next)
    saveDescription(description, next)
    ticketDataApi.addComment(key, "Sprntly", `✳ Accepted & propagated to acceptance criteria: ${proposedCriterion}`)
      .then((c) => setComments((xs) => [...xs, c]))
      .catch(() => { /* best-effort */ })
    setProposedCriterion(null)
    showToast("Change propagated", "Added to this ticket's acceptance criteria. PRD + design propagation is next.")
  }
  const rejectPropagate = () => setProposedCriterion(null)

  const pill = priorityPill(priority)
  // Prefer the person's name; fall back to their email before "Unassigned" so
  // a member without a profile name still shows as themselves.
  const assigneeName = assignee?.display_name || assignee?.email || "Unassigned"
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
        </div>
        {loaded ? (
          <input
            className="tkv2-dtitle"
            value={title}
            aria-label="Ticket title"
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => { const t = title.trim(); if (t && t !== story.title) saveFields({ title: t }) }}
          />
        ) : (
          <div className="tkv2-dtitle" style={{ opacity: 0.35 }}>Loading…</div>
        )}
      </div>

      {!loaded ? (
        <div className="tkv2-empty" style={{ margin: "18px 2px" }}>Loading ticket…</div>
      ) : (
        <>

      <div className="tkv2-edithint">
        ✎ Title, status, assignee, priority, description, acceptance criteria,
        child issues, and comments can all be edited here. Edits are saved as
        overrides on top of the generated ticket.
      </div>

      {/* Full-width description */}
      <div className="tkv2-descwide">
        <div className="tkv2-sec">
          <h4>
            Description
            {!editingDesc ? (
              <button type="button" className="tkv2-editbtn" onClick={startEditDesc} aria-label="Edit description">
                <IconPencil size={13} /> Edit
              </button>
            ) : null}
          </h4>
          {editingDesc ? (
            <>
              <textarea
                className="tkv2-dtx"
                style={{ width: "100%", minHeight: 220, resize: "vertical", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 13px" }}
                value={descDraft}
                placeholder="Add a description…"
                autoFocus
                onChange={(e) => setDescDraft(e.target.value)}
              />
              <div className="tkv2-actions2">
                <button type="button" className="tkv2-btn2 tkv2-btn2--primary" onClick={saveEditDesc}>Save</button>
                <button type="button" className="tkv2-btn2 tkv2-btn2--ghost" onClick={() => setEditingDesc(false)}>Cancel</button>
              </div>
            </>
          ) : structured && !hasDescOverride ? (
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
          ) : description ? (
            // Edited description — parse the labeled text back into the same
            // styled sections the generated ticket shows (edit-what-you-see).
            parseDescBlocks(description).map((b, i) => (
              <Fragment key={i}>
                {b.label ? <div className="tkv2-dlbl">{b.label}</div> : null}
                {b.items ? (
                  <ul className="tkv2-dlist">{b.items.map((it, j) => <li key={j}>{it}</li>)}</ul>
                ) : (
                  <p className="tkv2-dtx" style={{ whiteSpace: "pre-wrap" }}>
                    {b.label === "User story" ? highlightGWT(b.text) : b.text}
                  </p>
                )}
              </Fragment>
            ))
          ) : (
            <p className="tkv2-dtx">
              <span className="tkv2-empty">No description yet — click Edit to add one.</span>
            </p>
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
          <div className="tkv2-field" style={{ position: "relative" }}>
            <span className="tkv2-fl">Priority</span>
            <button
              type="button"
              aria-label="Change priority"
              className="tkv2-fv"
              style={{ display: "inline-flex", alignItems: "center", gap: 4, background: "none", border: "none", cursor: "pointer", padding: 0 }}
              onClick={() => setOpenMenu((m) => (m === "priority" ? null : "priority"))}
            >
              <span className={`tkv2-pill tkv2-pill--${pill.variant}`}>{pill.label}</span>
              <IconChevronDown size={12} />
            </button>
            {openMenu === "priority" ? (
              <div className="tkv2-picker" style={{ position: "absolute", left: 0, zIndex: 20 }}>
                {PRIORITY_OPTIONS.map((o) => (
                  <button key={o} type="button" className={`tkv2-pitem${o === priority ? " tkv2-pitem--sel" : ""}`} onClick={() => pickPriority(o)}>
                    {o === priority ? <IconCheck size={12} /> : <span style={{ width: 12 }} />}{o}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
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
            <h4>
              Acceptance criteria — {acCount}
              {acDraft == null ? (
                <button type="button" className="tkv2-editbtn" onClick={() => setAcDraft([...criteria])} aria-label="Edit acceptance criteria">
                  <IconPencil size={13} /> Edit
                </button>
              ) : null}
            </h4>
            {acDraft != null ? (
              <div className="tkv2-ac">
                {editRows(acDraft, setAcDraft, "Add criterion")}
                <div className="tkv2-actions2">
                  <button type="button" className="tkv2-btn2 tkv2-btn2--primary" onClick={saveAcDraft}>Save</button>
                  <button type="button" className="tkv2-btn2 tkv2-btn2--ghost" onClick={() => setAcDraft(null)}>Cancel</button>
                </div>
              </div>
            ) : acCount === 0 ? (
              <div className="tkv2-empty">No acceptance criteria yet — click Edit to add some.</div>
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
                  <span className="tkv2-inherit">Inherited from the PRD&apos;s implementation spec — edits here override the inherited set</span>
                ) : (
                  <span className="tkv2-gen">GENERATED ⚠ not inherited — run prd-author for a Part B to inherit spec-first tests</span>
                )}
              </div>
            )}
          </div>

          {/* Child issues */}
          <div className="tkv2-sec">
            <h4>
              Child issues{subtasks.length ? ` — ${subtasks.length}` : ""}
              {subsDraft == null ? (
                <button type="button" className="tkv2-editbtn" onClick={() => setSubsDraft([...subtasks])} aria-label="Edit child issues">
                  <IconPencil size={13} /> Edit
                </button>
              ) : null}
            </h4>
            {subsDraft != null ? (
              <>
                {editRows(subsDraft, setSubsDraft, "Add child issue")}
                <div className="tkv2-actions2">
                  <button type="button" className="tkv2-btn2 tkv2-btn2--primary" onClick={saveSubsDraft}>Save</button>
                  <button type="button" className="tkv2-btn2 tkv2-btn2--ghost" onClick={() => setSubsDraft(null)}>Cancel</button>
                </div>
              </>
            ) : subtasks.length === 0 ? (
              <div className="tkv2-empty">No child issues yet — click Edit to add some.</div>
            ) : (
              subtasks.map((t, i) => {
                const parallel = /^\s*\[P\]\s*/i.test(t)
                const label = t.replace(/^\s*\[P\]\s*/i, "")
                return (
                  <div key={i} className="tkv2-subt">
                    <span className="tkv2-cb" /> {label}
                    {parallel ? <span className="tkv2-sk">[P] parallel</span> : null}
                  </div>
                )
              })
            )}
          </div>

          {/* Linked issues — title references to sibling tickets in this PRD;
              clicking one opens that ticket's detail. */}
          {(story.blocked_by && story.blocked_by.length) || (story.blocks && story.blocks.length) ? (
            <div className="tkv2-sec">
              <h4>Linked issues</h4>
              {story.blocked_by && story.blocked_by.length ? (
                <>
                  <div className="tkv2-deplbl">is blocked by</div>
                  {story.blocked_by.map((d, i) => (
                    <button key={i} type="button" className="tkv2-dep tkv2-dep--block tkv2-dep--link"
                      title={`Open "${d}"`} onClick={() => onOpenLinked?.(d)}>
                      {d}
                    </button>
                  ))}
                </>
              ) : null}
              {story.blocks && story.blocks.length ? (
                <>
                  <div className="tkv2-deplbl">blocks</div>
                  {story.blocks.map((d, i) => (
                    <button key={i} type="button" className="tkv2-dep tkv2-dep--link"
                      title={`Open "${d}"`} onClick={() => onOpenLinked?.(d)}>
                      {d}
                    </button>
                  ))}
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
                {proposedCriterion ? (
                  <>
                    <div className="tkv2-propose">
                      <b>Proposed acceptance criterion:</b> {proposedCriterion}
                    </div>
                    <div className="tkv2-actions2">
                      <button type="button" className="tkv2-btn2 tkv2-btn2--primary" onClick={acceptPropagate}>Accept &amp; propagate</button>
                      <button type="button" className="tkv2-btn2 tkv2-btn2--ghost" onClick={rejectPropagate}>Reject</button>
                    </div>
                  </>
                ) : null}
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
                      <div className="who2">{c.author}<span className="when">{formatWhen(c.time)}</span></div>
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

        </>
      )}
    </div>
  )
}
