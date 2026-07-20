"use client"

import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import {
  IconArrowLeft, IconChevronDown, IconCheck, IconExternalLink,
  IconPlus, IconX,
} from "@tabler/icons-react"
import { useNavigation } from "../../context/NavigationContext"
import {
  ticketDataApi, teamApi,
  type ClickUpTicketState, type GeneratedStory, type TicketAssignee,
  type TeamMemberRecord, type TrackerFieldValue, type TrackerMeta,
  type TrackerProvider, type TrackerTransition,
} from "../../lib/api"
import { TrackerFieldEditor } from "./TrackerFieldEditor"

// The DEFAULT vocabularies — what unbound tickets (no tracker destination)
// render. A PRD bound to a Jira project / ClickUp list renders the
// destination's REAL statuses/priorities from tracker metadata instead
// (see the `tracker` prop).
const STATUS_OPTIONS = ["Backlog", "To do", "In progress", "Review", "Done"]

// The generator's priority enum — what actually lands in the database. The
// pill shows the STORED value verbatim (see priorityPill callers); these are
// the values the picker writes.
const PRIORITY_OPTIONS = ["urgent", "high", "normal", "low"]

/** Tracker context for a bound ticket: the destination's vocabulary (meta)
 *  and this ticket's last-pulled tracker state. Absent = unbound → default
 *  vocabulary + free-text saves, exactly the legacy behavior. */
export type TicketTrackerCtx = {
  provider: TrackerProvider
  meta: TrackerMeta
  synced?: ClickUpTicketState | null
}

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

// ── Description ⇄ contenteditable HTML ──
//
// PRD-style in-place editing: the styled description sections themselves are
// one contenteditable region. They render from an HTML string (so React never
// reconciles the children while the user types) and the edited DOM serializes
// back to the labeled-text override format — the exact inverse of the
// storyToEditableText/parseDescBlocks round trip, so an untouched focus+blur
// produces identical text and saves nothing.

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
}

/** HTML twin of highlightGWT (same keyword set, same .k spans). */
function gwtHtml(text: string): string {
  return escapeHtml(text).replace(
    /\b(Given|When|Then|As an?|I want|so that)\b/g,
    '<span class="k">$1</span>',
  )
}

/** A generated (structured) story's sections as editable HTML. */
function structuredDescHtml(s: GeneratedStory): string {
  const parts: string[] = []
  const label = (l: string) => `<div class="tkv2-dlbl">${l}</div>`
  if (s.what) parts.push(`${label("What")}<p class="tkv2-dtx">${escapeHtml(s.what)}</p>`)
  if (s.why_now) parts.push(`${label("Why now")}<p class="tkv2-dtx">${escapeHtml(s.why_now)}</p>`)
  if (s.user_story) parts.push(`${label("User story")}<p class="tkv2-dtx">${gwtHtml(s.user_story)}</p>`)
  if (s.scope && s.scope.length) {
    parts.push(`${label("The ticket must cover")}<ul class="tkv2-dlist">${
      s.scope.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul>`)
  }
  if (s.out_of_scope) parts.push(`${label("Out of scope")}<p class="tkv2-dtx">${escapeHtml(s.out_of_scope)}</p>`)
  return parts.length ? parts.join("") : (s.body ? `<p class="tkv2-dtx" style="white-space:pre-wrap">${escapeHtml(s.body)}</p>` : "")
}

/** An edited description's parsed blocks as editable HTML (same styled
 *  sections the generated ticket shows — edit-what-you-see). */
function descBlocksHtml(blocks: DescBlock[]): string {
  return blocks.map((b) => {
    const label = b.label ? `<div class="tkv2-dlbl">${escapeHtml(b.label)}</div>` : ""
    const body = b.items
      ? `<ul class="tkv2-dlist">${b.items.map((it) => `<li>${escapeHtml(it)}</li>`).join("")}</ul>`
      : `<p class="tkv2-dtx" style="white-space:pre-wrap">${b.label === "User story" ? gwtHtml(b.text) : escapeHtml(b.text)}</p>`
    return label + body
  }).join("")
}

/** Serialize the edited contenteditable DOM back to the labeled-text form:
 *  .tkv2-dlbl → a section-label line (blank-line separated), <ul> → "- item"
 *  lines, anything else → its text. */
export function serializeDescDom(root: HTMLElement): string {
  const lines: string[] = []
  for (const node of Array.from(root.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      const t = (node.textContent || "").trim()
      if (t) lines.push(t)
      continue
    }
    if (node.nodeType !== Node.ELEMENT_NODE) continue
    const el = node as HTMLElement
    // innerText preserves <br>/<div> line breaks; jsdom lacks it → textContent.
    // NOTE: both regex literals below match U+00A0 (non-breaking space, which
    // contenteditable inserts for consecutive spaces) — not an ASCII space.
    const text = (el.innerText ?? el.textContent ?? "").replace(/ /g, " ")
    if (el.classList.contains("tkv2-dlbl")) {
      if (lines.length) lines.push("")
      lines.push(text.trim())
    } else if (el.tagName === "UL") {
      for (const li of Array.from(el.querySelectorAll("li"))) {
        const t = ((li as HTMLElement).innerText ?? li.textContent ?? "").replace(/ /g, " ").trim()
        if (t) lines.push(`- ${t}`)
      }
    } else if (text.trim()) {
      lines.push(text.replace(/\s+$/, ""))
    }
  }
  return lines.join("\n")
}

/** Click-to-edit list rows (acceptance criteria + child issues share the
 *  interaction). PRD-style editing: click a row's text to edit it in place;
 *  blur or Enter commits and auto-saves, an emptied row (or ✕) removes it,
 *  Escape cancels. No Edit/Save buttons. */
function InlineEditList({ items, commit, addLabel, itemLabel, renderRow }: {
  items: string[]
  /** Persist the new list (state + API) — called only when something changed. */
  commit: (next: string[]) => void
  addLabel: string
  /** aria-label prefix for rows, e.g. "Edit acceptance criterion". */
  itemLabel: string
  renderRow: (text: string, i: number) => ReactNode
}) {
  const [editing, setEditingState] = useState<number | null>(null) // items.length = adding
  const [draft, setDraft] = useState("")
  const escaped = useRef(false) // Escape pressed → the pending blur discards the draft
  // Mirrors `editing` so a stale blur (double-fire, or blur after Escape
  // already closed the editor) can't commit twice.
  const editingRef = useRef<number | null>(null)
  const setEditing = (v: number | null) => { editingRef.current = v; setEditingState(v) }

  const finish = (remove = false) => {
    const idx = editingRef.current
    if (idx == null) return
    const v = draft.trim()
    const next = [...items]
    if (remove || (!v && idx < items.length)) {
      if (idx < items.length) next.splice(idx, 1)
    } else if (idx >= items.length) {
      if (v) next.push(v)
    } else {
      next[idx] = v
    }
    setEditing(null)
    if (next.length !== items.length || next.some((x, i) => x !== items[i])) commit(next)
  }

  const editorRow = (
    <div className="tkv2-editrow">
      <input
        className="input"
        value={draft}
        autoFocus
        aria-label={editing != null && editing < items.length ? itemLabel : addLabel}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          if (escaped.current) { escaped.current = false; setEditing(null) } else finish()
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur()
          if (e.key === "Escape") { escaped.current = true; e.currentTarget.blur() }
        }}
      />
      <button type="button" className="tkv2-btn2 tkv2-btn2--ghost" aria-label="Remove"
        onMouseDown={(e) => e.preventDefault() /* keep focus so blur doesn't double-commit */}
        onClick={() => finish(true)}>
        <IconX size={13} />
      </button>
    </div>
  )

  return (
    <>
      {items.map((t, i) => (editing === i ? (
        <Fragment key={i}>{editorRow}</Fragment>
      ) : (
        <div key={i} className="tkv2-editable" role="button" tabIndex={0}
          title="Click to edit — saves automatically"
          aria-label={`${itemLabel} ${i + 1}`}
          onClick={() => { setDraft(t); setEditing(i) }}
          onKeyDown={(e) => { if (e.key === "Enter") { setDraft(t); setEditing(i) } }}>
          {renderRow(t, i)}
        </div>
      )))}
      {editing != null && editing >= items.length ? editorRow : (
        <button type="button" className="tkv2-btn2 tkv2-btn2--ghost" onClick={() => { setDraft(""); setEditing(items.length) }}>
          <IconPlus size={13} /> {addLabel}
        </button>
      )}
    </>
  )
}

/** In-panel ticket detail — the `ticket` skill's canonical detail (Jira
 *  anatomy): full-width five-section description over a two-column zone (main
 *  story column + Details rail). Structured fields drive it; legacy/thin
 *  tickets fall back to the plain description + a generated-AC flag. */
export function TicketDetail({ story, index, prdId, onBack, onOpenLinked, tracker }: {
  story: GeneratedStory; index: number; prdId: number; onBack: () => void
  /** Open a sibling ticket by its title (linked issues are title references). */
  onOpenLinked?: (title: string) => void
  /** Bound-tracker context — switches status/priority to the destination's
   *  real vocabulary. Omit for unbound tickets (default vocabulary). */
  tracker?: TicketTrackerCtx | null
}) {
  const { showToast } = useNavigation()
  const key = useMemo(() => ticketKeyFor(prdId, story), [prdId, story])

  const [title, setTitle] = useState(story.title)
  const [status, setStatus] = useState("Backlog")
  // Whether `status` came from a saved edit or a user pick (vs the default
  // seed) — gates the tracker-status seed below so it never overwrites a
  // deliberate local value.
  const [statusIsOverride, setStatusIsOverride] = useState(false)
  const [assignee, setAssignee] = useState<TicketAssignee | null>(null)
  const [description, setDescription] = useState(story.body)
  const [criteria, setCriteria] = useState<string[]>(story.acceptance_criteria)
  const [priority, setPriority] = useState<string>(story.priority || "")
  const [subtasks, setSubtasks] = useState<string[]>(story.subtasks || [])
  // Local tracker custom-field overrides (keyed by field id). Display falls
  // back to the last-pulled tracker value when a field has no override.
  const [customFields, setCustomFields] = useState<Record<string, TrackerFieldValue>>({})
  // Tracker issue type (Jira Task/Story/…) — null until an edit or a pulled
  // tracker value provides one.
  const [issueType, setIssueType] = useState<string | null>(null)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [comments, setComments] = useState<Comment[]>([])
  const [summary, setSummary] = useState<string | null>(null)
  // A concrete acceptance-criteria change the thread proposed (from the summary
  // endpoint) — drives the Accept & propagate action.
  const [proposedCriterion, setProposedCriterion] = useState<string | null>(null)

  const [members, setMembers] = useState<TeamMemberRecord[] | null>(null)
  const [openMenu, setOpenMenu] = useState<null | "status" | "reassign" | "priority" | "issuetype">(null)
  const [commentText, setCommentText] = useState("")
  // Posting is in flight — the Send button shows "Sending…" and locks so a
  // slow request can't double-post.
  const [sendingComment, setSendingComment] = useState(false)

  // Hold the body until saved overrides are loaded — rendering the generated
  // ticket first and swapping when the fetch lands reads as "shows the old
  // ticket, then updates".
  const [loaded, setLoaded] = useState(false)

  // Description editing. The override is one text column; structured tickets
  // serialize their sections into it and the display parses it back (see
  // storyToEditableText/parseDescBlocks). PRD-style: the rendered sections
  // themselves are contenteditable — no textarea swap. The DOM serializes
  // back to the labeled-text form on input (debounced) and commits on blur.
  const [hasDescOverride, setHasDescOverride] = useState(false)
  const descRef = useRef<HTMLDivElement>(null)
  const descTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const descPending = useRef<string | null>(null)

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
      if (d.status != null) { setStatus(d.status); setStatusIsOverride(true) }
      if (d.assignee != null) setAssignee(d.assignee)
      if (d.description != null) { setDescription(d.description); setHasDescOverride(true) }
      if (d.acceptance_criteria != null) setCriteria(d.acceptance_criteria)
      if (d.priority != null) setPriority(d.priority)
      if (d.subtasks != null) setSubtasks(d.subtasks)
      if (d.custom_fields != null) setCustomFields(d.custom_fields)
      if (d.issue_type != null) setIssueType(d.issue_type)
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

  const pickStatus = (v: string) => {
    setStatus(v); setStatusIsOverride(true); setOpenMenu(null); saveFields({ status: v })
  }
  const pickPriority = (v: string) => { setPriority(v); setOpenMenu(null); saveFields({ priority: v }) }

  // ── Tracker-native vocabulary (bound tickets) ──
  // Status seed: with no saved local status, a bound ticket shows the
  // tracker's pulled status (their vocabulary) instead of "Backlog".
  useEffect(() => {
    if (loaded && !statusIsOverride && tracker?.synced?.status) {
      setStatus(tracker.synced.status)
    }
  }, [loaded, statusIsOverride, tracker?.synced?.status])

  // Legal status moves, fetched lazily the first time the dropdown opens
  // (Jira: the issue's live workflow transitions; ClickUp: the full list
  // vocabulary in the same shape). Failure → fall back to meta.statuses.
  const [transitions, setTransitions] = useState<TrackerTransition[] | null>(null)
  const [transitionsFailed, setTransitionsFailed] = useState(false)
  const openStatusMenu = () => {
    setOpenMenu((m) => (m === "status" ? null : "status"))
    if (tracker && transitions == null && !transitionsFailed) {
      ticketDataApi.getTransitions(key)
        .then((r) => setTransitions(r.transitions))
        .catch(() => setTransitionsFailed(true))
    }
  }
  // What the status dropdown offers: legal transitions when bound (fallback:
  // the destination's full status list), else the default vocabulary.
  // null = still loading (bound only).
  const statusOptions: { name: string; color: string | null }[] | null = !tracker
    ? STATUS_OPTIONS.map((name) => ({ name, color: null }))
    : transitions != null
      ? transitions.map((t) => ({
          name: t.to_status_name,
          color: tracker.meta.statuses.find(
            (s) => s.name.toLowerCase() === t.to_status_name.toLowerCase(),
          )?.color ?? null,
        }))
      : transitionsFailed
        ? tracker.meta.statuses.map((s) => ({ name: s.name, color: s.color }))
        : null
  const priorityOptions: { name: string; color: string | null }[] = tracker
    ? tracker.meta.priorities.map((p) => ({ name: p.name, color: p.color }))
    : PRIORITY_OPTIONS.map((name) => ({ name, color: null }))

  // Custom-field save: merge locally, send only the changed field (the
  // backend merges too — sibling overrides survive). null clears an override.
  const saveCustomField = (fieldId: string, v: TrackerFieldValue) => {
    setCustomFields((m) => {
      const next = { ...m }
      if (v == null) delete next[fieldId]
      else next[fieldId] = v
      return next
    })
    saveFields({ custom_fields: { [fieldId]: v } })
  }
  const providerLabel =
    tracker?.provider === "jira" ? "Jira"
    : tracker?.provider === "asana" ? "Asana"
    : "ClickUp"

  // Issue type (Jira-bound tickets): the destination's real non-subtask
  // types. Displayed value = local edit ?? pulled tracker type ?? "Task".
  const issueTypeOptions = (tracker?.meta.issue_types ?? []).filter((t) => !t.subtask)
  const shownIssueType = issueType ?? tracker?.synced?.issue_type ?? "Task"
  const pickIssueType = (v: string) => {
    setIssueType(v); setOpenMenu(null); saveFields({ issue_type: v })
  }

  // ── Description editing (contenteditable in place, autosave) ──
  // What the display currently represents as text: the saved override when
  // there is one, else the structured sections serialized, else the plain body.
  const displayedDescText = () =>
    hasDescOverride ? description : structured ? storyToEditableText(story) : description

  // The editable region's HTML. Rendered via dangerouslySetInnerHTML so React
  // leaves the live DOM alone while the user types (the string only changes
  // when a blur commits state, never mid-edit).
  const descHtml = useMemo(() => {
    if (structured && !hasDescOverride) return structuredDescHtml(story)
    return description ? descBlocksHtml(parseDescBlocks(description)) : ""
  }, [structured, hasDescOverride, description, story])

  const onDescInput = () => {
    const root = descRef.current
    if (!root) return
    const text = serializeDescDom(root)
    descPending.current = text
    if (descTimer.current) clearTimeout(descTimer.current)
    descTimer.current = setTimeout(() => {
      descTimer.current = null
      descPending.current = null
      // API-only while typing — a state commit would rebuild the DOM under
      // the cursor. State catches up on blur.
      saveDescription(text, criteria)
    }, 1500)
  }
  const onDescBlur = () => {
    if (descTimer.current) { clearTimeout(descTimer.current); descTimer.current = null }
    descPending.current = null
    const root = descRef.current
    if (!root) return
    const text = serializeDescDom(root)
    if (text === displayedDescText()) return
    setDescription(text); setHasDescOverride(true)
    saveDescription(text, criteria)
  }
  // Flush a not-yet-debounced description edit if the view unmounts before
  // blur fires (e.g. navigating back) so the last keystrokes aren't lost.
  const flushDesc = useRef<() => void>(() => {})
  flushDesc.current = () => {
    if (descTimer.current) { clearTimeout(descTimer.current); descTimer.current = null }
    if (descPending.current == null) return
    ticketDataApi.saveDescription(key, descPending.current, criteria).catch(() => { /* best-effort */ })
    descPending.current = null
  }
  useEffect(() => () => flushDesc.current(), [])

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
    if (!body || sendingComment) return
    setSendingComment(true)
    // No author sent — the backend attributes the comment to the signed-in
    // user (profile name → email) and echoes it back in the response.
    ticketDataApi.addComment(key, body).then((c) => {
      setComments((xs) => [...xs, c]); setCommentText("")
    }).catch(() => showToast("Couldn't post comment", "Try again."))
      .finally(() => setSendingComment(false))
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
    ticketDataApi.addComment(key, `✳ Accepted & propagated to acceptance criteria: ${proposedCriterion}`, "Sprntly")
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
        ✎ Click any text — title, description, acceptance criteria, child
        issues — to edit it in place. Changes save automatically as overrides
        on top of the generated ticket.
      </div>

      {/* Full-width description */}
      <div className="tkv2-descwide">
        <div className="tkv2-sec">
          <h4>Description</h4>
          {/* The styled sections themselves are editable in place (PRD-style):
              click into the text and type — no editor swap, no Save button. */}
          <div
            ref={descRef}
            className="tkv2-editable tkv2-editable--desc tkv2-descedit"
            contentEditable
            suppressContentEditableWarning
            role="textbox"
            aria-multiline="true"
            aria-label="Ticket description"
            title="Click to edit — saves automatically"
            data-placeholder="No description yet — click to add one."
            onInput={onDescInput}
            onBlur={onDescBlur}
            dangerouslySetInnerHTML={{ __html: descHtml }}
          />
          {/* Grounding is generated metadata, not part of the editable text —
              keep it outside the editable region. */}
          {structured && !hasDescOverride &&
          (story.prd_section || (story.signals && story.signals.length) || (story.data_gaps && story.data_gaps.length)) ? (
            <p className="tkv2-dtx tkv2-ground" style={{ marginTop: 10 }}>
              Grounding: {story.prd_section ? <a>{story.prd_section}</a> : null}
              {story.signals && story.signals.length ? <> · {story.signals.join(" · ")}</> : null}
              {story.data_gaps && story.data_gaps.length
                ? story.data_gaps.map((g, i) => <span key={i} className="tkv2-need"> [{g}]</span>)
                : null}
            </p>
          ) : null}
        </div>
      </div>

      {/* Two-column zone */}
      {/* Details bar — horizontal, sized for the narrow tickets panel. (Was a
          300px side rail that cramped and clipped beside the tall criteria
          column; a rail only works at the reference's full page width.) */}
      <div className="tkv2-detailbar">
        <div style={{ position: "relative" }}>
          <button type="button" className="tkv2-statusbtn" onClick={openStatusMenu}>
            {status} <IconChevronDown size={12} />
          </button>
          {openMenu === "status" ? (
            <div className="tkv2-picker" style={{ position: "absolute", zIndex: 20 }}>
              {statusOptions == null ? (
                <div className="tkv2-pitem">Loading…</div>
              ) : statusOptions.length === 0 ? (
                <div className="tkv2-pitem">No moves available</div>
              ) : (
                statusOptions.map((o) => (
                  <button key={o.name} type="button" className={`tkv2-pitem${o.name === status ? " tkv2-pitem--sel" : ""}`} onClick={() => pickStatus(o.name)}>
                    {o.name === status ? <IconCheck size={12} /> : <span style={{ width: 12 }} />}
                    {o.color ? <span aria-hidden style={{ width: 8, height: 8, borderRadius: "50%", background: o.color, display: "inline-block", marginRight: 6 }} /> : null}
                    {o.name}
                  </button>
                ))
              )}
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
          {/* Bound tickets show ONLY the tracker's own properties (status /
              assignee / type / priority + the meta custom fields below) —
              Sprntly's generated metadata rows (Reporter, Labels, Provenance,
              Story points, Route, Traces) are hidden so the panel mirrors the
              customer's Jira/ClickUp. Unbound tickets keep the full layout. */}
          {!tracker ? (
            <div className="tkv2-field"><span className="tkv2-fl">Reporter</span><span className="tkv2-fv tkv2-fv--muted">Sprntly PM Agent</span></div>
          ) : null}
          {/* Issue type — Jira-bound tickets only (the destination's real
              types from metadata). Set on create; changes sync best-effort. */}
          {issueTypeOptions.length > 0 ? (
            <div className="tkv2-field" style={{ position: "relative" }}>
              <span className="tkv2-fl">Type</span>
              <button
                type="button"
                aria-label="Change issue type"
                className="tkv2-fv"
                style={{ display: "inline-flex", alignItems: "center", gap: 4, background: "none", border: "none", cursor: "pointer", padding: 0 }}
                onClick={() => setOpenMenu((m) => (m === "issuetype" ? null : "issuetype"))}
              >
                {shownIssueType}
                <IconChevronDown size={12} />
              </button>
              {openMenu === "issuetype" ? (
                <div className="tkv2-picker" style={{ position: "absolute", left: 0, zIndex: 20 }}>
                  {issueTypeOptions.map((t) => (
                    <button key={t.id} type="button" className={`tkv2-pitem${t.name === shownIssueType ? " tkv2-pitem--sel" : ""}`} onClick={() => pickIssueType(t.name)}>
                      {t.name === shownIssueType ? <IconCheck size={12} /> : <span style={{ width: 12 }} />}{t.name}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
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
                {priorityOptions.map((o) => (
                  <button key={o.name} type="button" className={`tkv2-pitem${o.name === priority ? " tkv2-pitem--sel" : ""}`} onClick={() => pickPriority(o.name)}>
                    {o.name === priority ? <IconCheck size={12} /> : <span style={{ width: 12 }} />}
                    {o.color ? <span aria-hidden style={{ width: 8, height: 8, borderRadius: "50%", background: o.color, display: "inline-block", marginRight: 6 }} /> : null}
                    {o.name}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          {!tracker && story.labels && story.labels.length ? (
            <div className="tkv2-field"><span className="tkv2-fl">Labels</span><span className="tkv2-fv tkv2-fv--muted">{story.labels.join(" · ")}</span></div>
          ) : null}
          {!tracker && story.prd_section ? (
            <div className="tkv2-field"><span className="tkv2-fl">Provenance</span><span className="tkv2-fv">{story.prd_section}</span></div>
          ) : null}
          {!tracker && story.story_points != null ? (
            <div className="tkv2-field"><span className="tkv2-fl">Story points</span><span className="tkv2-fv">{story.story_points}</span></div>
          ) : null}
          {!tracker && story.route ? (
            <div className="tkv2-field"><span className="tkv2-fl">Route</span><span className="tkv2-fv" style={{ color: routeAgentReady ? "var(--green-d)" : undefined }}>{story.route}</span></div>
          ) : null}
          {!tracker && story.ears_ids && story.ears_ids.length ? (
            <div className="tkv2-field"><span className="tkv2-fl">Traces</span><span className="tkv2-fv tkv2-fv--muted">{story.ears_ids.join(" · ")}</span></div>
          ) : null}
          {/* Tracker custom fields — the destination's own properties (from
              tracker metadata), synced both ways, IN the same properties bar
              as the native fields (one seamless panel). EDITABLE fields only:
              exotic read-only types live in the tracker. Bound tickets only.
              display:contents keeps the entries laying out as direct children
              of tkv2-fields. */}
          {tracker && tracker.meta.fields.some((f) => f.editable) ? (
            <div style={{ display: "contents" }} data-testid="tracker-fields">
              {tracker.meta.fields.filter((f) => f.editable).map((f) => (
                <div key={f.id} className="tkv2-field" style={{ position: "relative" }}>
                  <span className="tkv2-fl">{f.name}</span>
                  <TrackerFieldEditor
                    field={f}
                    providerLabel={providerLabel}
                    value={customFields[f.id] ?? tracker.synced?.custom_fields?.[f.id]}
                    onSave={(v) => saveCustomField(f.id, v)}
                  />
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      {/* Main content — full width */}
      <div className="tkv2-body">
          {/* Acceptance criteria */}
          <div className="tkv2-sec">
            <h4>Acceptance criteria — {acCount}</h4>
            <div className="tkv2-ac">
              {acCount === 0 ? (
                <div className="tkv2-empty" style={{ marginBottom: 8 }}>No acceptance criteria yet.</div>
              ) : null}
              <InlineEditList
                items={criteria}
                addLabel="Add criterion"
                itemLabel="Edit acceptance criterion"
                commit={(next) => { setCriteria(next); saveDescription(description, next) }}
                renderRow={(c) => {
                  const { tag, rest } = splitAcTag(c)
                  return (
                    <div className="tkv2-acitem">
                      <span className="tkv2-cb" />
                      <span className="tkv2-actxt">
                        {tag === "failure" ? <span className="tkv2-tagf">[failure]</span> : null}
                        {tag === "edge" ? <span className="tkv2-tagn">[edge]</span> : null}
                        {highlightGWT(rest)}
                      </span>
                    </div>
                  )
                }}
              />
              {acCount > 0 ? (
                story.ac_inherited ? (
                  <span className="tkv2-inherit">Inherited from the PRD&apos;s implementation spec — edits here override the inherited set</span>
                ) : (
                  <span className="tkv2-gen">GENERATED ⚠ not inherited — run prd-author for a Part B to inherit spec-first tests</span>
                )
              ) : null}
            </div>
          </div>

          {/* Child issues */}
          <div className="tkv2-sec">
            <h4>Child issues{subtasks.length ? ` — ${subtasks.length}` : ""}</h4>
            {subtasks.length === 0 ? (
              <div className="tkv2-empty" style={{ marginBottom: 8 }}>No child issues yet.</div>
            ) : null}
            <InlineEditList
              items={subtasks}
              addLabel="Add child issue"
              itemLabel="Edit child issue"
              commit={(next) => { setSubtasks(next); saveFields({ subtasks: next }) }}
              renderRow={(t) => {
                const parallel = /^\s*\[P\]\s*/i.test(t)
                const label = t.replace(/^\s*\[P\]\s*/i, "")
                return (
                  <div className="tkv2-subt">
                    <span className="tkv2-cb" /> {label}
                    {parallel ? <span className="tkv2-sk">[P] parallel</span> : null}
                  </div>
                )
              }}
            />
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
                disabled={sendingComment}
              />
              <button
                type="button"
                className="tkv2-btn2 tkv2-btn2--primary"
                onClick={addComment}
                disabled={!commentText.trim() || sendingComment}
              >
                {sendingComment ? "Sending…" : "Send"}
              </button>
            </div>
          </div>
      </div>

        </>
      )}
    </div>
  )
}
