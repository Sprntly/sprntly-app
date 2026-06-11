"use client"

import { useRef, useState } from "react"
import { AppLayout } from "./AppLayout"
import { useNavigation } from "../../../context/NavigationContext"

// ── Types ─────────────────────────────────────────────────────────────────────

type IdeaType = "New initiative" | "UI" | "Infra" | "Bug" | "Research"
type IdeaSource = "brief" | "backlog" | "person"
type BacklogTab = "proposed" | "completed"

interface BacklogIdea {
  id: string
  rank: number
  title: string
  sub: string
  source: IdeaSource
  sourceName?: string
  sourceInitials?: string
  sourceColor?: string
  type: IdeaType
  impact: string
  impactClass: "positive" | "negative" | "neutral"
}

interface CompletedInitiative {
  id: string
  title: string
  sub: string
  type: IdeaType
  shipped: string
  impactDelivered: string
  impactDir: "up" | "down" | "neutral"
}

// ── Static data ───────────────────────────────────────────────────────────────

const IDEA_TYPES: IdeaType[] = ["New initiative", "UI", "Infra", "Bug", "Research"]

const TYPE_STYLE: Record<IdeaType, { color: string; bg: string; border: string }> = {
  "New initiative": { color: "#179463", bg: "#eaf7f1", border: "#9bdcc1" },
  UI:               { color: "#5b50b8", bg: "#f0eefb", border: "#c5c0ee" },
  Infra:            { color: "#b06a10", bg: "#fef3e2", border: "#f0c07a" },
  Bug:              { color: "#c13838", bg: "#fdeaea", border: "#f5b3b3" },
  Research:         { color: "#4a7a9b", bg: "#e8f4fc", border: "#a8d1ed" },
}

const INITIAL_IDEAS: BacklogIdea[] = [
  { id: "b1",  rank: 1,  title: "First-Handoff Wizard to lift Day-30 activation",  sub: "4 deployments · $480K ARR at risk",        source: "brief",   type: "New initiative", impact: "+11pt Day-30",  impactClass: "positive" },
  { id: "b2",  rank: 2,  title: "Co-authoring nudge to amplify the viral loop",     sub: "11 deployments · self-spreading pattern",  source: "brief",   type: "UI",             impact: "+$220K exp.",   impactClass: "positive" },
  { id: "b3",  rank: 3,  title: "Cerner security packet to unblock expansion",      sub: "$580K · 21 days to close",                 source: "brief",   type: "New initiative", impact: "$680K ARR",     impactClass: "positive" },
  { id: "b4",  rank: 4,  title: "Shift-handoff template presets by unit type",      sub: "Cuts setup time for new units",            source: "backlog", type: "UI",             impact: "-2d ramp",      impactClass: "neutral"  },
  { id: "b5",  rank: 5,  title: "EHR session-depth insights for ops leads",         sub: "Surfaces under-utilization early",         source: "person",  sourceName: "Marcus Owens", sourceInitials: "MO", sourceColor: "#179463", type: "New initiative", impact: "+6% WAU",  impactClass: "positive" },
  { id: "b6",  rank: 6,  title: "Cross-location context view for float nurses",     sub: "Helps multi-site clinicians",              source: "brief",   type: "UI",             impact: "+3pt D30",      impactClass: "positive" },
  { id: "b7",  rank: 7,  title: "Veradigm FHIR connector (phase 1 read)",           sub: "Opens 9-account pipeline",                 source: "backlog", type: "Infra",          impact: "9 accts",       impactClass: "neutral"  },
  { id: "b8",  rank: 8,  title: "Fix handoff-sync p95 latency regression",          sub: "Affects save reliability at 3 sites",      source: "brief",   type: "Bug",            impact: "-40% errors",   impactClass: "positive" },
  { id: "b9",  rank: 9,  title: "Bulk care-plan import for new deployments",        sub: "Top ask from 6 enterprise accounts",       source: "person",  sourceName: "Priya Sharma", sourceInitials: "PS", sourceColor: "#c13838", type: "New initiative", impact: "+4 accts", impactClass: "positive" },
  { id: "b10", rank: 10, title: "Mobile handoff summary for night shift",           sub: "38 complaints · night-shift nurses",       source: "brief",   type: "UI",             impact: "+5pt D30",      impactClass: "positive" },
  { id: "b11", rank: 11, title: "Role-based dashboards for charge nurses",          sub: "Charge nurses can't see unit-level data",  source: "backlog", type: "New initiative", impact: "+7% WAU",       impactClass: "positive" },
  { id: "b12", rank: 12, title: "Handoff reminder push notifications",              sub: "Reduces missed shift handoffs",            source: "brief",   type: "UI",             impact: "-22% misses",   impactClass: "positive" },
]

const COMPLETED: CompletedInitiative[] = [
  { id: "c1", title: "Epic FHIR read integration",              sub: "Opened the Epic-account pipeline",          type: "Infra",          shipped: "Apr 14, 2026", impactDelivered: "+14 accounts",      impactDir: "up"   },
  { id: "c2", title: "Care-plan co-authoring (v1)",             sub: "Drove the self-spreading engagement loop",  type: "New initiative", shipped: "Mar 28, 2026", impactDelivered: "+12% WoW",          impactDir: "up"   },
  { id: "c3", title: "Onboarding redesign for new deployments", sub: "Cut time-to-first-handoff dramatically",   type: "UI",             shipped: "Feb 19, 2026", impactDelivered: "-1.5d ramp",        impactDir: "down" },
  { id: "c4", title: "Slack weekly-brief delivery",             sub: "Lifted brief open + action rate",          type: "New initiative", shipped: "Jan 31, 2026", impactDelivered: "+38% opens",        impactDir: "up"   },
  { id: "c5", title: "Auto-flag stale care plans",              sub: "Reduced missed plan updates",              type: "Bug",            shipped: "Jan 12, 2026", impactDelivered: "-18% misses",       impactDir: "down" },
  { id: "c6", title: "SSO via Okta + SCIM provisioning",        sub: "Cleared security gate for enterprise",     type: "Infra",          shipped: "Dec 8, 2025",  impactDelivered: "3 deals unblocked", impactDir: "up"   },
]

const GROUP_OPTIONS = ["Impact (ranked)", "Type", "Source", "Date added"]

// ── Icon helpers ──────────────────────────────────────────────────────────────

function SyncIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  )
}

function SparkleIcon({ size = 12, color = "currentColor" }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill={color} aria-hidden>
      <path d="M8 0 L9.5 6.5 L16 8 L9.5 9.5 L8 16 L6.5 9.5 L0 8 L6.5 6.5 Z" />
    </svg>
  )
}

function DragDots() {
  return (
    <svg width="10" height="14" viewBox="0 0 10 14" fill="var(--ink-4)" aria-hidden>
      <circle cx="3" cy="2.5" r="1.1" /><circle cx="7" cy="2.5" r="1.1" />
      <circle cx="3" cy="7"   r="1.1" /><circle cx="7" cy="7"   r="1.1" />
      <circle cx="3" cy="11.5" r="1.1" /><circle cx="7" cy="11.5" r="1.1" />
    </svg>
  )
}

// ── Type badge with inline dropdown ──────────────────────────────────────────

function TypeBadge({ type, onChange }: { type: IdeaType; onChange: (t: IdeaType) => void }) {
  const [open, setOpen] = useState(false)
  const s = TYPE_STYLE[type]
  return (
    <div className="bl-type-wrap">
      <button
        type="button"
        className="bl-type-badge"
        style={{ color: s.color, background: s.bg, borderColor: s.border }}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {type}
        <svg width="8" height="8" viewBox="0 0 10 10" fill="currentColor" aria-hidden>
          <path d="M5 7L1 3h8z" />
        </svg>
      </button>
      {open && (
        <>
          <div className="bl-type-backdrop" onClick={() => setOpen(false)} />
          <div className="bl-type-menu" role="listbox">
            {IDEA_TYPES.map((t) => {
              const ts = TYPE_STYLE[t]
              return (
                <button
                  key={t}
                  type="button"
                  role="option"
                  aria-selected={t === type}
                  className={`bl-type-option${t === type ? " bl-type-option--active" : ""}`}
                  onClick={() => { onChange(t); setOpen(false) }}
                >
                  <span className="bl-type-dot" style={{ background: ts.color }} />
                  {t}
                </button>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

// ── Source cell — updated icons matching reference ────────────────────────────

function SourceCell({ idea }: { idea: BacklogIdea }) {
  if (idea.source === "person") {
    return (
      <div className="bl-source">
        <span className="bl-source-avatar" style={{ background: `${idea.sourceColor}22`, color: idea.sourceColor }}>
          {idea.sourceInitials}
        </span>
        <span className="bl-source-name">{idea.sourceName}</span>
      </div>
    )
  }
  if (idea.source === "backlog") {
    return (
      <div className="bl-source">
        {/* Orange grid icon for Product backlog */}
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
          <rect x="3"  y="3"  width="7" height="7" rx="1.5" fill="#e07d23" />
          <rect x="14" y="3"  width="7" height="7" rx="1.5" fill="#e07d23" opacity="0.7" />
          <rect x="3"  y="14" width="7" height="7" rx="1.5" fill="#e07d23" opacity="0.7" />
          <rect x="14" y="14" width="7" height="7" rx="1.5" fill="#e07d23" opacity="0.45" />
        </svg>
        <span className="bl-source-name">Product backlog</span>
      </div>
    )
  }
  // brief
  return (
    <div className="bl-source">
      <SparkleIcon size={14} color="var(--accent)" />
      <span className="bl-source-name">Sprntly brief</span>
    </div>
  )
}

// ── Idea row ──────────────────────────────────────────────────────────────────

function IdeaRow({
  idea, onTypeChange, dragHandlers, isDragging, isDragOver,
}: {
  idea: BacklogIdea
  onTypeChange: (id: string, t: IdeaType) => void
  dragHandlers: {
    onDragStart: (e: React.DragEvent, id: string) => void
    onDragOver:  (e: React.DragEvent, id: string) => void
    onDragEnd:   () => void
    onDrop:      (e: React.DragEvent, id: string) => void
  }
  isDragging: boolean
  isDragOver: boolean
}) {
  const cls = ["bl-row", isDragging ? "bl-row--dragging" : "", isDragOver ? "bl-row--over" : ""].filter(Boolean).join(" ")
  const impactCls = idea.impactClass === "positive" ? "bl-impact--pos" : idea.impactClass === "negative" ? "bl-impact--neg" : ""

  return (
    <div
      className={cls}
      draggable
      onDragStart={(e) => dragHandlers.onDragStart(e, idea.id)}
      onDragOver={(e)  => dragHandlers.onDragOver(e, idea.id)}
      onDragEnd={dragHandlers.onDragEnd}
      onDrop={(e)      => dragHandlers.onDrop(e, idea.id)}
    >
      <div className="bl-cell bl-cell--drag"><DragDots /></div>
      <div className="bl-cell bl-cell--rank">{idea.rank}</div>
      <div className="bl-cell bl-cell--project">
        <div className="bl-project-title">{idea.title}</div>
        <div className="bl-project-sub">{idea.sub}</div>
      </div>
      <div className="bl-cell bl-cell--source"><SourceCell idea={idea} /></div>
      <div className="bl-cell bl-cell--type">
        <TypeBadge type={idea.type} onChange={(t) => onTypeChange(idea.id, t)} />
      </div>
      <div className={`bl-cell bl-cell--impact ${impactCls}`}>{idea.impact}</div>
    </div>
  )
}

// ── Inline "Add idea" card (replaces modal) ───────────────────────────────────

function AddIdeaCard({
  onClose,
  onAdd,
}: {
  onClose: () => void
  onAdd: (title: string, type: IdeaType) => void
}) {
  const [value, setValue]   = useState("")
  const [type, setType]     = useState<IdeaType>("New initiative")
  const textareaRef         = useRef<HTMLTextAreaElement>(null)

  const submit = () => {
    if (!value.trim()) return
    onAdd(value.trim(), type)
    onClose()
  }

  return (
    <div className="bl-add-card">
      <div className="bl-add-card-head">
        <span className="bl-add-card-label">
          <SparkleIcon size={11} color="var(--accent)" />
          Create new idea
        </span>
        <button type="button" className="bl-add-card-close" onClick={onClose} aria-label="Close">×</button>
      </div>

      <textarea
        ref={textareaRef}
        className="bl-add-card-input"
        placeholder='Title, then a line on the problem — e.g. "Make incomplete handoffs visible · coverage gaps go unnoticed until activation drops."'
        value={value}
        rows={3}
        autoFocus
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit() }
          if (e.key === "Escape") { e.preventDefault(); onClose() }
        }}
      />

      <div className="bl-add-card-footer">
        <div className="bl-add-card-left">
          <button type="button" className="bl-add-card-action">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            </svg>
            Voice
          </button>
          <div className="bl-add-card-types">
            {IDEA_TYPES.map((t) => {
              const s = TYPE_STYLE[t]
              return (
                <button
                  key={t}
                  type="button"
                  className={`bl-add-card-type${type === t ? " active" : ""}`}
                  style={type === t ? { color: s.color, background: s.bg, borderColor: s.border } : {}}
                  onClick={() => setType(t)}
                >
                  {t}
                </button>
              )
            })}
          </div>
        </div>
        <button type="button" className="bl-add-card-send" disabled={!value.trim()} onClick={submit} aria-label="Add idea">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>
    </div>
  )
}

// ── Proposed tab — table only (add card rendered outside scroll in BacklogScreen) ──

function ProposedContent({
  addHandlerRef,
}: {
  addHandlerRef: React.MutableRefObject<((title: string, type: IdeaType) => void) | null>
}) {
  const { showToast }               = useNavigation()
  const [ideas, setIdeas]           = useState<BacklogIdea[]>(INITIAL_IDEAS)
  const [group, setGroup]           = useState(GROUP_OPTIONS[0])
  const dragId                      = useRef<string | null>(null)
  const [dragOverId, setDragOverId] = useState<string | null>(null)

  const handleTypeChange = (id: string, type: IdeaType) =>
    setIdeas((prev) => prev.map((i) => i.id === id ? { ...i, type } : i))

  const handleDragStart = (_e: React.DragEvent, id: string) => { dragId.current = id }
  const handleDragOver  = (e: React.DragEvent, id: string)  => { e.preventDefault(); setDragOverId(id) }
  const handleDragEnd   = ()                                 => { dragId.current = null; setDragOverId(null) }
  const handleDrop      = (_e: React.DragEvent, targetId: string) => {
    const fromId = dragId.current
    if (!fromId || fromId === targetId) { handleDragEnd(); return }
    setIdeas((prev) => {
      const from = prev.findIndex((i) => i.id === fromId)
      const to   = prev.findIndex((i) => i.id === targetId)
      if (from === -1 || to === -1) return prev
      const next = [...prev]
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      return next.map((item, idx) => ({ ...item, rank: idx + 1 }))
    })
    handleDragEnd()
  }

  // Expose add handler to parent via ref — updated every render so parent always has the latest
  addHandlerRef.current = (title: string, type: IdeaType) => {
    setIdeas((prev) => [...prev, {
      id: `b${Date.now()}`, rank: prev.length + 1,
      title, sub: "", source: "backlog", type, impact: "—", impactClass: "neutral",
    }])
    showToast("Idea added", `"${title}" added to the backlog.`)
  }

  return (
    <>
      <div className="bl-info-bar">
        <div className="bl-info-left">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ flexShrink: 0, marginTop: 1 }}>
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 8 12 12 14 14" />
          </svg>
          <span>
            <strong>{ideas.length} ideas</strong>{" "}
            surfaced from your data that aren&apos;t being worked on yet — sequenced by impact. Drag rows to re-rank, change a type inline, or ask Sprntly below to re-prioritize.
          </span>
        </div>
        <div className="bl-info-right">
          <span className="bl-group-label">Group by</span>
          <select className="bl-group-select" value={group} onChange={(e) => setGroup(e.target.value)}>
            {GROUP_OPTIONS.map((o) => <option key={o}>{o}</option>)}
          </select>
        </div>
      </div>

      <div className="bl-table-wrap">
        <div className="bl-thead">
          <div className="bl-th bl-th--drag" />
          <div className="bl-th bl-th--rank">#</div>
          <div className="bl-th bl-th--project">Project</div>
          <div className="bl-th bl-th--source">Source</div>
          <div className="bl-th bl-th--type">Type</div>
          <div className="bl-th bl-th--impact">Impact</div>
        </div>
        <div className="bl-tbody">
          {ideas.map((idea) => (
            <IdeaRow
              key={idea.id}
              idea={idea}
              onTypeChange={handleTypeChange}
              dragHandlers={{ onDragStart: handleDragStart, onDragOver: handleDragOver, onDragEnd: handleDragEnd, onDrop: handleDrop }}
              isDragging={dragId.current === idea.id}
              isDragOver={dragOverId === idea.id}
            />
          ))}

        </div>
      </div>
    </>
  )
}

// ── Completed tab ─────────────────────────────────────────────────────────────

function ImpactArrow({ dir }: { dir: "up" | "down" | "neutral" }) {
  if (dir === "up") return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#179463" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" /><polyline points="17 6 23 6 23 12" />
    </svg>
  )
  if (dir === "down") return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#c13838" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="23 18 13.5 8.5 8.5 13.5 1 6" /><polyline points="17 18 23 18 23 12" />
    </svg>
  )
  return null
}

function CompletedContent() {
  return (
    <>
      <div className="bl-info-bar">
        <div className="bl-info-left">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ flexShrink: 0, marginTop: 1 }}>
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><polyline points="22 4 12 14.01 9 11.01" />
          </svg>
          <span>
            <strong>{COMPLETED.length} initiatives</strong>{" "}
            shipped from Sprntly briefs — with the measured impact each one delivered. Most recent first.
          </span>
        </div>
      </div>
      <div className="bl-table-wrap bl-table-wrap--completed">
        <div className="bl-thead bl-thead--completed">
          <div className="bl-th bl-th--initiative">Initiative</div>
          <div className="bl-th bl-th--ctype">Type</div>
          <div className="bl-th bl-th--shipped">Shipped</div>
          <div className="bl-th bl-th--delivered">Impact Delivered</div>
        </div>
        <div className="bl-tbody">
          {COMPLETED.map((item) => {
            const s = TYPE_STYLE[item.type]
            return (
              <div key={item.id} className="bl-completed-row">
                <div className="bl-cell bl-cell--initiative">
                  <div className="bl-project-title">{item.title}</div>
                  <div className="bl-project-sub">{item.sub}</div>
                </div>
                <div className="bl-cell bl-cell--ctype">
                  <span className="bl-type-badge" style={{ color: s.color, background: s.bg, borderColor: s.border, cursor: "default" }}>
                    {item.type}
                  </span>
                </div>
                <div className="bl-cell bl-cell--shipped">{item.shipped}</div>
                <div className={`bl-cell bl-cell--delivered${item.impactDir === "up" ? " bl-impact--pos" : item.impactDir === "down" ? " bl-impact--neg" : ""}`}>
                  <ImpactArrow dir={item.impactDir} />
                  {item.impactDelivered}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </>
  )
}

// ── Sync loading overlay ──────────────────────────────────────────────────────

function SyncingOverlay() {
  return (
    <div className="bl-syncing-overlay" role="status" aria-live="polite">
      <span className="bl-syncing-spinner" aria-hidden />
      Syncing with your backlog…
    </div>
  )
}

// ── Main screen ───────────────────────────────────────────────────────────────

export function BacklogScreen() {
  const { showToast }                       = useNavigation()
  const [tab, setTab]                       = useState<BacklogTab>("proposed")
  const [showAddIdea, setShowAddIdea]       = useState(false)
  const [isSyncing, setIsSyncing]           = useState(false)
  const [chatValue, setChatValue]           = useState("")
  const textareaRef                         = useRef<HTMLTextAreaElement>(null)
  // Bridge to ProposedContent's add-idea handler without lifting ideas state
  const addHandlerRef = useRef<((title: string, type: IdeaType) => void) | null>(null)

  const handleSync = () => {
    if (isSyncing) return
    setIsSyncing(true)
    setTimeout(() => {
      setIsSyncing(false)
      showToast("Synced", "Your backlog is up to date.")
    }, 2400)
  }

  const handleChat = (e: React.FormEvent) => {
    e.preventDefault()
    if (!chatValue.trim()) return
    showToast("Re-prioritizing…", `Running: "${chatValue}"`)
    setChatValue("")
    if (textareaRef.current) textareaRef.current.style.height = "auto"
  }

  return (
    <AppLayout mainClassName="main--backlog">
      <div className="bl-shell">

        {/* ── Single combined top bar ── */}
        <div className="bl-topbar">
          <div className="bl-topbar-left">
            <h1 className="bl-title">Briefs</h1>
            <span className="bl-count-badge">
              {tab === "proposed" ? `${INITIAL_IDEAS.length} ideas` : `${COMPLETED.length} shipped`}
            </span>
            <div className="bl-tabs">
              <button
                type="button"
                className={`bl-tab${tab === "proposed" ? " bl-tab--active" : ""}`}
                onClick={() => setTab("proposed")}
              >
                Proposed
              </button>
              <button
                type="button"
                className={`bl-tab${tab === "completed" ? " bl-tab--active" : ""}`}
                onClick={() => setTab("completed")}
              >
                Completed initiatives
              </button>
            </div>
          </div>
          <div className="bl-topbar-right">
            <button
              type="button"
              className={`bl-btn-sync${isSyncing ? " bl-btn-sync--loading" : ""}`}
              onClick={handleSync}
              disabled={isSyncing}
            >
              <SyncIcon /> Sync with backlog
            </button>
            <button type="button" className="bl-btn-add" onClick={() => { setShowAddIdea(true); setTab("proposed") }}>
              + Add idea
            </button>
          </div>
        </div>

        {/* ── Scrollable content ── */}
        <div className="bl-body">
          {tab === "proposed"
            ? <ProposedContent addHandlerRef={addHandlerRef} />
            : <CompletedContent />}
        </div>

        {/* ── Add idea card: outside scroll, replaces chat bar ── */}
        {tab === "proposed" && showAddIdea && (
          <div className="bl-chat-bar bl-chat-bar--add">
            <AddIdeaCard
              onClose={() => setShowAddIdea(false)}
              onAdd={(title, type) => {
                addHandlerRef.current?.(title, type)
                setShowAddIdea(false)
              }}
            />
          </div>
        )}

        {/* ── Chat bar: visible when add-idea card is closed ── */}
        {tab === "proposed" && !showAddIdea && (
          <div className="bl-chat-bar">
            <form className="bl-chat-form" onSubmit={handleChat}>
              <textarea
                ref={textareaRef}
                className="bl-chat-input"
                placeholder='Ask Sprntly to re-prioritize — "push revenue items up", "group by complaint frequency", "turn the top idea into a PRD"…'
                value={chatValue}
                rows={1}
                onChange={(e) => {
                  setChatValue(e.target.value)
                  const el = e.target; el.style.height = "auto"
                  el.style.height = `${Math.min(el.scrollHeight, 120)}px`
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleChat(e as unknown as React.FormEvent) }
                }}
              />
              <div className="bl-chat-footer">
                <div className="bl-chat-footer-left">
                  <button type="button" className="bl-chat-action-btn"
                    onClick={() => showToast("Voice", "Voice input coming soon.")}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                      <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8" />
                    </svg>
                    Voice
                  </button>
                  <button type="button" className="bl-chat-action-btn"
                    onClick={() => showToast("Re-sequencing…", "Sprntly is re-ranking ideas by impact.")}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <line x1="17" y1="10" x2="3" y2="10" /><line x1="21" y1="6" x2="3" y2="6" />
                      <line x1="21" y1="14" x2="3" y2="14" /><line x1="17" y1="18" x2="3" y2="18" />
                    </svg>
                    Re-sequence
                  </button>
                </div>
                <button type="submit" className="bl-chat-send" disabled={!chatValue.trim()} aria-label="Send">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
                  </svg>
                </button>
              </div>
            </form>
          </div>
        )}

        {/* Task 3: Syncing overlay pill */}
        {isSyncing && <SyncingOverlay />}

      </div>
    </AppLayout>
  )
}
