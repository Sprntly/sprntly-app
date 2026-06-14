"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { AppLayout } from "./AppLayout"
import { useNavigation } from "../../../context/NavigationContext"
import { useCompany } from "../../../context/CompanyContext"
import { backlogApi, type BacklogItem, type BacklogTag } from "../../../lib/api"

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

// ── API → idea mapping ────────────────────────────────────────────────────────
// Backlog items come from the weekly analysis: ranks ≥ 4 (the top 3 go into the
// brief). The backend returns an empty list when no brief exists for the
// company, so an empty backlog here means "no analysis has run yet".

const TAG_TO_TYPE: Record<BacklogTag, IdeaType> = {
  something_broken: "Bug",          // FIX
  something_new:    "New initiative", // BUILD
  something_better: "UI",           // OPTIMIZE
}

function backlogItemToIdea(item: BacklogItem): BacklogIdea {
  return {
    id: item.id,
    rank: item.rank,
    title: item.title,
    sub: item.reasoning ?? "",
    // Every backlog item is the analysis remainder — sourced from the backlog,
    // not a person or the brief top-3.
    source: "backlog",
    type: item.tag ? TAG_TO_TYPE[item.tag] : "New initiative",
    impact: "—",
    impactClass: "neutral",
  }
}

// ── Prioritization frameworks ─────────────────────────────────────────────────
// Each framework scores ideas on different dimensions. The user picks a framework
// from the "Prioritize by" dropdown and the ideas re-sort by that score.

type PrioritizationFramework = "Impact (ranked)" | "RICE" | "ICE" | "MoSCoW" | "Value vs Effort" | "WSJF"

const PRIORITIZE_OPTIONS: { value: PrioritizationFramework; label: string; description: string }[] = [
  { value: "Impact (ranked)",  label: "Impact (ranked)",  description: "Default Sprntly scoring — VoC volume × severity × strategic fit" },
  { value: "RICE",             label: "RICE",             description: "Reach × Impact × Confidence ÷ Effort" },
  { value: "ICE",              label: "ICE",              description: "Impact × Confidence × Ease" },
  { value: "Value vs Effort",  label: "Value vs Effort",  description: "Business value ÷ implementation effort" },
  { value: "WSJF",             label: "WSJF",             description: "Weighted Shortest Job First — SAFe framework" },
  { value: "MoSCoW",           label: "MoSCoW",           description: "Must / Should / Could / Won't classification" },
]

// Simulated scores per idea per framework (in production these come from the LLM scoring pipeline)
type FrameworkScores = Record<PrioritizationFramework, number>

function generateScores(idea: BacklogIdea, index: number): FrameworkScores {
  // Deterministic pseudo-scores based on idea properties
  const hash = idea.title.length + index * 7
  const isBrief = idea.source === "brief"
  const isBug = idea.type === "Bug"
  const baseImpact = 12 - index // Higher rank = higher impact

  return {
    "Impact (ranked)": baseImpact,
    "RICE":            Math.round((isBrief ? 8 : 5) * (baseImpact / 3) * 0.8 / Math.max(1, (hash % 5) + 1) * 10) / 10,
    "ICE":             Math.round(((baseImpact / 2) * (isBrief ? 0.9 : 0.7) * (isBug ? 9 : 6 + (hash % 4))) * 10) / 10,
    "Value vs Effort": Math.round((baseImpact * (isBrief ? 1.2 : 0.8)) / ((hash % 4) + 2) * 10) / 10,
    "WSJF":            Math.round(((isBug ? 10 : 6) + baseImpact * 0.5) / ((hash % 3) + 1) * 10) / 10,
    "MoSCoW":          isBug ? 4 : isBrief ? (index < 3 ? 4 : 3) : (index < 6 ? 3 : index < 9 ? 2 : 1),
  }
}

const MOSCOW_LABELS: Record<number, string> = { 4: "Must", 3: "Should", 2: "Could", 1: "Won't" }

const GROUP_OPTIONS = PRIORITIZE_OPTIONS.map((o) => o.value)

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
  idea, onTypeChange, onSelect, dragHandlers, isDragging, isDragOver, isSelected, framework,
}: {
  idea: BacklogIdea
  onTypeChange: (id: string, t: IdeaType) => void
  onSelect: (idea: BacklogIdea) => void
  dragHandlers: {
    onDragStart: (e: React.DragEvent, id: string) => void
    onDragOver:  (e: React.DragEvent, id: string) => void
    onDragEnd:   () => void
    onDrop:      (e: React.DragEvent, id: string) => void
  }
  isDragging: boolean
  isDragOver: boolean
  isSelected: boolean
  framework: PrioritizationFramework
}) {
  const cls = ["bl-row", isDragging ? "bl-row--dragging" : "", isDragOver ? "bl-row--over" : "", isSelected ? "bl-row--selected" : ""].filter(Boolean).join(" ")
  const impactCls = idea.impactClass === "positive" ? "bl-impact--pos" : idea.impactClass === "negative" ? "bl-impact--neg" : ""
  const origIdx = INITIAL_IDEAS.findIndex((init) => init.id === idea.id)
  const scores = generateScores(idea, origIdx >= 0 ? origIdx : idea.rank - 1)
  const fwScore = scores[framework]
  const showScore = framework !== "Impact (ranked)"

  return (
    <div
      className={cls}
      draggable
      onClick={() => onSelect(idea)}
      style={{ cursor: "pointer" }}
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
      <div className={`bl-cell bl-cell--impact ${impactCls}`}>
        {showScore ? (
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{
              fontSize: 11, fontWeight: 600, padding: "2px 7px", borderRadius: 5,
              background: "var(--accent-muted, #DBF1E7)", color: "var(--accent, #179463)",
            }}>
              {framework === "MoSCoW" ? MOSCOW_LABELS[fwScore] ?? fwScore : fwScore}
            </span>
            <span style={{ fontSize: 11, color: "var(--ink-4)" }}>{idea.impact}</span>
          </span>
        ) : (
          idea.impact
        )}
      </div>
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

type LoadState = "loading" | "ready" | "error"

function ProposedContent({
  addHandlerRef,
  onSelectIdea,
  selectedIdeaId,
  onCountChange,
}: {
  addHandlerRef: React.MutableRefObject<((title: string, type: IdeaType) => void) | null>
  onSelectIdea: (idea: BacklogIdea) => void
  selectedIdeaId: string | null
  onCountChange?: (count: number) => void
}) {
  const { showToast }               = useNavigation()
  const { activeCompany }           = useCompany()
  const [ideas, setIdeas]           = useState<BacklogIdea[]>([])
  const [load, setLoad]             = useState<LoadState>("loading")
  const [group, setGroup]           = useState(GROUP_OPTIONS[0])
  const dragId                      = useRef<string | null>(null)
  const [dragOverId, setDragOverId] = useState<string | null>(null)

  // Fetch the backlog (ranks ≥ 4 of the latest analysis). The route is
  // session-scoped to the company; `activeCompany` is only a re-fetch trigger.
  useEffect(() => {
    let cancelled = false
    setLoad("loading")
    backlogApi
      .list()
      .then((res) => {
        if (cancelled) return
        const mapped = res.items
          .slice()
          .sort((a, b) => a.rank - b.rank)
          .map(backlogItemToIdea)
        setIdeas(mapped)
        setLoad("ready")
      })
      .catch(() => {
        if (cancelled) return
        setIdeas([])
        setLoad("error")
      })
    return () => { cancelled = true }
  }, [activeCompany])

  // Keep the parent's count badge in sync with the loaded list.
  useEffect(() => { onCountChange?.(ideas.length) }, [ideas.length, onCountChange])

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

  if (load === "loading") {
    return (
      <div className="bl-empty" role="status" aria-live="polite" style={{ padding: "48px 24px", textAlign: "center", color: "var(--ink-3)" }}>
        Loading your backlog…
      </div>
    )
  }

  // Empty state — no weekly brief has been generated yet, so the analysis has
  // produced no backlog items (the backend returns an empty list with no brief).
  if (load === "ready" && ideas.length === 0) {
    return (
      <div className="bl-empty" role="status" style={{ padding: "56px 24px", textAlign: "center", maxWidth: 480, margin: "0 auto", color: "var(--ink-2)" }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--ink)", margin: "0 0 8px" }}>
          No backlog yet
        </h2>
        <p style={{ fontSize: 13, lineHeight: 1.55, margin: 0 }}>
          Your backlog is built from the weekly analysis — the top 3 insights go
          into your brief, and the rest land here. Once a brief has been
          generated for your company, the remaining prioritized ideas will show
          up automatically.
        </p>
      </div>
    )
  }

  if (load === "error") {
    return (
      <div className="bl-empty" role="alert" style={{ padding: "48px 24px", textAlign: "center", color: "var(--ink-3)" }}>
        Couldn&apos;t load the backlog. Please try again.
      </div>
    )
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
            surfaced from your data that aren&apos;t being worked on yet — sequenced by {PRIORITIZE_OPTIONS.find((o) => o.value === group)?.label ?? "impact"}. Drag rows to re-rank, change a type inline, or ask Sprntly below to re-prioritize.
          </span>
        </div>
        <div className="bl-info-right">
          <span className="bl-group-label">Prioritize by</span>
          <select className="bl-group-select" value={group} onChange={(e) => {
            const fw = e.target.value as PrioritizationFramework
            setGroup(fw)
            // Re-sort ideas by the selected framework
            setIdeas((prev) => {
              const scored = prev.map((idea, i) => ({
                ...idea,
                _score: generateScores(idea, i),
              }))
              scored.sort((a, b) => (b._score[fw] ?? 0) - (a._score[fw] ?? 0))
              return scored.map((item, idx) => ({ ...item, rank: idx + 1 }))
            })
          }}>
            {PRIORITIZE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value} title={o.description}>{o.label}</option>
            ))}
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
          <div className="bl-th bl-th--impact">{group === "Impact (ranked)" ? "Impact" : group}</div>
        </div>
        <div className="bl-tbody">
          {ideas.map((idea) => (
            <IdeaRow
              key={idea.id}
              idea={idea}
              onTypeChange={handleTypeChange}
              onSelect={onSelectIdea}
              dragHandlers={{ onDragStart: handleDragStart, onDragOver: handleDragOver, onDragEnd: handleDragEnd, onDrop: handleDrop }}
              isDragging={dragId.current === idea.id}
              isSelected={selectedIdeaId === idea.id}
              framework={group as PrioritizationFramework}
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
  const { showToast, openContentPanel }     = useNavigation()
  const [tab, setTab]                       = useState<BacklogTab>("proposed")
  const [proposedCount, setProposedCount]   = useState<number | null>(null)
  const [showAddIdea, setShowAddIdea]       = useState(false)
  const [isSyncing, setIsSyncing]           = useState(false)
  const [chatValue, setChatValue]           = useState("")
  const [selectedIdea, setSelectedIdea]     = useState<BacklogIdea | null>(null)
  const textareaRef                         = useRef<HTMLTextAreaElement>(null)
  // Bridge to ProposedContent's add-idea handler without lifting ideas state
  const addHandlerRef = useRef<((title: string, type: IdeaType) => void) | null>(null)

  const handleSelectIdea = useCallback((idea: BacklogIdea) => {
    setSelectedIdea(idea)
  }, [])

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
              {tab === "proposed"
                ? `${proposedCount ?? 0} ideas`
                : `${COMPLETED.length} shipped`}
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

        {/* ── Scrollable content + right panel ── */}
        <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
          <div className="bl-body" style={{ flex: 1, overflow: "auto" }}>
            {tab === "proposed"
              ? <ProposedContent addHandlerRef={addHandlerRef} onSelectIdea={handleSelectIdea} selectedIdeaId={selectedIdea?.id ?? null} onCountChange={setProposedCount} />
              : <CompletedContent />}
          </div>

          {/* ── Right panel: idea detail + PRD generation ── */}
          {selectedIdea && (
            <aside style={{
              width: 420, flexShrink: 0, borderLeft: "1px solid var(--line, #E8E6E0)",
              background: "var(--surface, #fff)", display: "flex", flexDirection: "column",
              overflow: "hidden",
            }}>
              {/* Header */}
              <div style={{
                padding: "14px 18px", borderBottom: "1px solid var(--line, #E8E6E0)",
                display: "flex", alignItems: "center", justifyContent: "space-between",
              }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
                  #{selectedIdea.rank} · {selectedIdea.type}
                </span>
                <button type="button" onClick={() => setSelectedIdea(null)}
                  style={{ background: "none", border: "none", cursor: "pointer", fontSize: 18, color: "var(--ink-3)", padding: 0, lineHeight: 1 }}>×</button>
              </div>

              {/* Body */}
              <div style={{ flex: 1, overflowY: "auto", padding: "16px 18px" }}>
                <h2 style={{ fontSize: 17, fontWeight: 600, color: "var(--ink)", margin: "0 0 6px", lineHeight: 1.35 }}>
                  {selectedIdea.title}
                </h2>
                <p style={{ fontSize: 13, color: "var(--ink-3)", margin: "0 0 16px" }}>
                  {selectedIdea.sub}
                </p>

                {/* Impact */}
                <div style={{
                  padding: "10px 14px", borderRadius: 8, background: "var(--accent-muted, #DBF1E7)",
                  marginBottom: 16, fontSize: 13,
                }}>
                  <strong style={{ color: "var(--accent, #179463)" }}>Impact:</strong>{" "}
                  <span style={{ color: "var(--ink)" }}>{selectedIdea.impact}</span>
                </div>

                {/* Chat thread */}
                <div style={{
                  fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em",
                  color: "var(--ink-3)", marginBottom: 10,
                }}>
                  Chat thread
                </div>
                <div style={{
                  padding: "12px 14px", borderRadius: 8, border: "1px solid var(--line, #E8E6E0)",
                  background: "var(--surface-2, #F4F1EA)", marginBottom: 16, fontSize: 13, color: "var(--ink-2)",
                }}>
                  <div style={{ marginBottom: 8, fontWeight: 500, color: "var(--ink)" }}>You</div>
                  <div>Tell me more about &ldquo;{selectedIdea.title}&rdquo; — what&apos;s the problem, who&apos;s affected, and what would a solution look like?</div>
                </div>
                <div style={{
                  padding: "12px 14px", borderRadius: 8, border: "1px solid var(--accent, #179463)",
                  background: "#fff", marginBottom: 16, fontSize: 13, color: "var(--ink)",
                }}>
                  <div style={{ marginBottom: 8, fontWeight: 500, color: "var(--accent)" }}>Sprntly</div>
                  <div style={{ lineHeight: 1.55 }}>
                    <strong>{selectedIdea.title}</strong> — {selectedIdea.sub}. This idea has an estimated impact of <strong>{selectedIdea.impact}</strong>.
                    Based on the data, I recommend generating a PRD to scope this properly before moving to implementation.
                  </div>
                </div>

                {/* Actions */}
                <div style={{
                  fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em",
                  color: "var(--ink-3)", marginBottom: 10,
                }}>
                  Next steps
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <button type="button" onClick={() => {
                    // Navigate to chat with a PRD generation prompt
                    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
                      dbId: 0, title: selectedIdea.title,
                      turns: [
                        { role: "user", content: `Generate a PRD for: ${selectedIdea.title}. Context: ${selectedIdea.sub}. Expected impact: ${selectedIdea.impact}` },
                      ],
                    }))
                    window.location.href = "/"
                  }} style={{
                    fontSize: 13, padding: "10px 16px", borderRadius: 8,
                    background: "var(--accent, #179463)", color: "#fff", border: "none",
                    cursor: "pointer", fontWeight: 600, display: "flex", alignItems: "center", gap: 6,
                  }}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                    Generate PRD
                  </button>
                  <button type="button" onClick={() => {
                    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
                      dbId: 0, title: selectedIdea.title,
                      turns: [
                        { role: "user", content: `Deep dive into "${selectedIdea.title}": ${selectedIdea.sub}. What evidence do we have? What are the risks? Who should own this?` },
                      ],
                    }))
                    window.location.href = "/"
                  }} style={{
                    fontSize: 13, padding: "10px 16px", borderRadius: 8,
                    background: "var(--surface-2, #F4F1EA)", border: "1px solid var(--line, #E8E6E0)",
                    cursor: "pointer", color: "var(--ink-2)", display: "flex", alignItems: "center", gap: 6,
                  }}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                    Continue in chat
                  </button>
                  <button type="button" onClick={() => {
                    showToast("Prototype", `Starting prototype generation for "${selectedIdea.title}"…`)
                  }} style={{
                    fontSize: 13, padding: "10px 16px", borderRadius: 8,
                    background: "var(--surface-2, #F4F1EA)", border: "1px solid var(--line, #E8E6E0)",
                    cursor: "pointer", color: "var(--ink-2)", display: "flex", alignItems: "center", gap: 6,
                  }}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
                    Generate prototype
                  </button>
                </div>
              </div>
            </aside>
          )}
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
