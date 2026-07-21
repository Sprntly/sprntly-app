"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { AppLayout } from "./AppLayout"
import { useNavigation } from "../../../context/NavigationContext"
import { useCompany } from "../../../context/CompanyContext"
import { runPrdGenerationFromIdeation } from "../../../lib/runPrdGeneration"
import { prototypePath } from "../../../lib/routes"
import { ideationApi, type IdeationItem, type IdeationTag, type IdeationDetail, type CompletedItem } from "../../../lib/api"

// ── Types ─────────────────────────────────────────────────────────────────────

type IdeaType = "New initiative" | "UI" | "Infra" | "Bug" | "Research"
type IdeaSource = "brief" | "ideation" | "person"
type IdeationTab = "proposed" | "completed"

interface IdeationIdea {
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
  /** The analysis score behind the item (0 for user-added ideas). Drives the
   *  "Re-sequence" action, which re-orders by real impact score. */
  score?: number
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

// ── API → idea mapping ────────────────────────────────────────────────────────
// Ideas come from the weekly analysis: ranks ≥ 4 (the top 3 go into the brief),
// with the weekly prioritization pass shortlisting the 25-30 worth showing —
// the backend returns only that visible set. It returns an empty list when no
// brief exists for the company, so an empty page means "no analysis yet".

const TAG_TO_TYPE: Record<IdeationTag, IdeaType> = {
  something_broken: "Bug",          // FIX
  something_new:    "New initiative", // BUILD
  something_better: "UI",           // OPTIMIZE
}

// Reverse of TAG_TO_TYPE for persisting a user-added idea's type. Only the three
// types that map cleanly to an IdeationTag are stored; Infra/Research have no tag
// (null), so they reload as the default "New initiative" — a known, acceptable
// fidelity loss for manual items (the ideation taxonomy has three tags).
const TYPE_TO_TAG: Partial<Record<IdeaType, IdeationTag>> = {
  Bug: "something_broken",
  "New initiative": "something_new",
  UI: "something_better",
}

function ideationItemToIdea(item: IdeationItem): IdeationIdea {
  return {
    id: item.id,
    rank: item.rank,
    title: item.title,
    sub: item.reasoning ?? "",
    // Every listed item is the analysis remainder — sourced from ideation,
    // not a person or the brief top-3.
    source: "ideation",
    type: item.tag ? TAG_TO_TYPE[item.tag] : "New initiative",
    impact: "—",
    impactClass: "neutral",
    score: item.score ?? 0,
  }
}

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

function SourceCell({ idea }: { idea: IdeationIdea }) {
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
  if (idea.source === "ideation") {
    return (
      <div className="bl-source">
        {/* Orange grid icon for Ideation */}
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
          <rect x="3"  y="3"  width="7" height="7" rx="1.5" fill="#e07d23" />
          <rect x="14" y="3"  width="7" height="7" rx="1.5" fill="#e07d23" opacity="0.7" />
          <rect x="3"  y="14" width="7" height="7" rx="1.5" fill="#e07d23" opacity="0.7" />
          <rect x="14" y="14" width="7" height="7" rx="1.5" fill="#e07d23" opacity="0.45" />
        </svg>
        <span className="bl-source-name">Ideation</span>
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
  idea, onTypeChange, onSelect, dragHandlers, isDragging, isDragOver, isSelected,
}: {
  idea: IdeationIdea
  onTypeChange: (id: string, t: IdeaType) => void
  onSelect: (idea: IdeationIdea) => void
  dragHandlers: {
    onDragStart: (e: React.DragEvent, id: string) => void
    onDragOver:  (e: React.DragEvent, id: string) => void
    onDragEnd:   () => void
    onDrop:      (e: React.DragEvent, id: string) => void
  }
  isDragging: boolean
  isDragOver: boolean
  isSelected: boolean
}) {
  const cls = ["bl-row", isDragging ? "bl-row--dragging" : "", isDragOver ? "bl-row--over" : "", isSelected ? "bl-row--selected" : ""].filter(Boolean).join(" ")
  const impactCls = idea.impactClass === "positive" ? "bl-impact--pos" : idea.impactClass === "negative" ? "bl-impact--neg" : ""

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
        {idea.impact}
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

// ── Proposed tab — table only (add card rendered outside scroll in IdeationScreen) ──

type LoadState = "loading" | "ready" | "error"

function ProposedContent({
  addHandlerRef,
  resequenceHandlerRef,
  reloadKey,
  onSelectIdea,
  selectedIdeaId,
  onCountChange,
}: {
  addHandlerRef: React.MutableRefObject<((title: string, type: IdeaType) => void) | null>
  resequenceHandlerRef: React.MutableRefObject<(() => void) | null>
  reloadKey: number
  onSelectIdea: (idea: IdeationIdea) => void
  selectedIdeaId: string | null
  onCountChange?: (count: number) => void
}) {
  const { showToast }               = useNavigation()
  const { activeCompany }           = useCompany()
  const [ideas, setIdeas]           = useState<IdeationIdea[]>([])
  const [load, setLoad]             = useState<LoadState>("loading")
  const [prioritizedAt, setPrioritizedAt] = useState<string | null>(null)
  const dragId                      = useRef<string | null>(null)
  const [dragOverId, setDragOverId] = useState<string | null>(null)

  // Fetch the visible ideas (the weekly shortlist + user-pinned rows). The
  // route is session-scoped to the company; `activeCompany` and `reloadKey`
  // (bumped by "Sync ideas") are re-fetch triggers.
  useEffect(() => {
    let cancelled = false
    setLoad("loading")
    ideationApi
      .list()
      .then((res) => {
        if (cancelled) return
        const mapped = res.items
          .slice()
          .sort((a, b) => a.rank - b.rank)
          .map(ideationItemToIdea)
        setIdeas(mapped)
        // The shortlist refreshes when the weekly brief generates; the newest
        // updated_at is when this list was last prioritized.
        const newest = res.items
          .map((i) => i.updated_at)
          .filter((d): d is string => Boolean(d))
          .sort()
          .pop()
        setPrioritizedAt(newest ?? null)
        setLoad("ready")
      })
      .catch(() => {
        if (cancelled) return
        setIdeas([])
        setLoad("error")
      })
    return () => { cancelled = true }
  }, [activeCompany, reloadKey])

  // Keep the parent's count badge in sync with the loaded list.
  useEffect(() => { onCountChange?.(ideas.length) }, [ideas.length, onCountChange])

  // Persist a new rank order to the backend (best-effort — the optimistic UI
  // order already applied; a failed save just warns so a refresh won't surprise
  // the user with the old order).
  const persistOrder = useCallback((ordered: IdeationIdea[]) => {
    ideationApi.reorder(ordered.map((i) => i.id)).catch(() => {
      showToast("Couldn't save order", "Your new order may not persist on refresh.")
    })
  }, [showToast])

  const handleTypeChange = (id: string, type: IdeaType) =>
    setIdeas((prev) => prev.map((i) => i.id === id ? { ...i, type } : i))

  const handleDragStart = (_e: React.DragEvent, id: string) => { dragId.current = id }
  const handleDragOver  = (e: React.DragEvent, id: string)  => { e.preventDefault(); setDragOverId(id) }
  const handleDragEnd   = ()                                 => { dragId.current = null; setDragOverId(null) }
  const handleDrop      = (_e: React.DragEvent, targetId: string) => {
    const fromId = dragId.current
    if (!fromId || fromId === targetId) { handleDragEnd(); return }
    const from = ideas.findIndex((i) => i.id === fromId)
    const to   = ideas.findIndex((i) => i.id === targetId)
    if (from !== -1 && to !== -1) {
      const next = [...ideas]
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      const ranked = next.map((item, idx) => ({ ...item, rank: idx + 1 }))
      setIdeas(ranked)
      persistOrder(ranked)  // drag-to-rerank saves server-side
    }
    handleDragEnd()
  }

  // Expose add handler to parent via ref — reassigned every render so the parent
  // always calls the latest closure. Persists the idea to the backend, then
  // appends the returned row (with its real id) so it can be dragged/generated.
  addHandlerRef.current = (title: string, type: IdeaType) => {
    ideationApi
      .create(title, TYPE_TO_TAG[type] ?? null)
      .then((item) => {
        setIdeas((prev) => [...prev, { ...ideationItemToIdea(item), type }])
        showToast("Idea added", `"${title}" saved to ideation.`)
      })
      .catch(() => showToast("Couldn't add idea", "Please try again."))
  }

  // Re-sequence: re-order by real analysis impact score (desc) and persist.
  // User-added ideas (score 0) sink to the bottom.
  resequenceHandlerRef.current = () => {
    if (!ideas.length) return
    const ranked = ideas
      .slice()
      .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
      .map((item, idx) => ({ ...item, rank: idx + 1 }))
    setIdeas(ranked)
    persistOrder(ranked)
    showToast("Re-sequenced", "Ideas re-ordered by impact and saved.")
  }

  if (load === "loading") {
    return (
      <div className="bl-empty" role="status" aria-live="polite" style={{ padding: "48px 24px", textAlign: "center", color: "var(--ink-3)" }}>
        Loading your ideas…
      </div>
    )
  }

  // Empty state — no weekly brief has been generated yet, so the analysis has
  // produced no ideas (the backend returns an empty list with no brief).
  if (load === "ready" && ideas.length === 0) {
    return (
      <div className="bl-empty" role="status" style={{ padding: "56px 24px", textAlign: "center", maxWidth: 480, margin: "0 auto", color: "var(--ink-2)" }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--ink)", margin: "0 0 8px" }}>
          No ideas yet
        </h2>
        <p style={{ fontSize: 13, lineHeight: 1.55, margin: 0 }}>
          Ideation is built from the weekly analysis — the top 3 insights go
          into your brief, and the strongest of the rest are shortlisted here.
          Once a brief has been generated for your company, your prioritized
          ideas will show up automatically.
        </p>
      </div>
    )
  }

  if (load === "error") {
    return (
      <div className="bl-empty" role="alert" style={{ padding: "48px 24px", textAlign: "center", color: "var(--ink-3)" }}>
        Couldn&apos;t load your ideas. Please try again.
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
            shortlisted from your data — re-prioritized each week when your
            brief generates, so only the strongest ideas show. Drag rows to
            re-rank or change a type inline.
          </span>
        </div>
        <div className="bl-info-right">
          <span className="bl-group-label">
            Prioritized weekly{prioritizedAt ? ` · updated ${formatSurfacedDate(prioritizedAt)}` : ""}
          </span>
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
              onSelect={onSelectIdea}
              dragHandlers={{ onDragStart: handleDragStart, onDragOver: handleDragOver, onDragEnd: handleDragEnd, onDrop: handleDrop }}
              isDragging={dragId.current === idea.id}
              isSelected={selectedIdeaId === idea.id}
              isDragOver={dragOverId === idea.id}
            />
          ))}

        </div>
      </div>
    </>
  )
}

// ── Completed tab ─────────────────────────────────────────────────────────────

// How a completed finding's `action` renders as a "Status" badge. prd_created
// and done are the only actions the backend returns for the Completed tab.
const ACTION_STYLE: Record<CompletedItem["action"], { label: string; style: { color: string; bg: string; border: string } }> = {
  prd_created: { label: "PRD created", style: { color: "#5b50b8", bg: "#f0eefb", border: "#c5c0ee" } },
  done:        { label: "Done",        style: { color: "#179463", bg: "#eaf7f1", border: "#9bdcc1" } },
}

function formatSurfacedDate(iso: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return "—"
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
}

function CompletedContent({ onCountChange }: { onCountChange?: (count: number) => void }) {
  const { activeCompany }     = useCompany()
  const [items, setItems]     = useState<CompletedItem[]>([])
  const [load, setLoad]       = useState<LoadState>("loading")

  // Completed = brief findings whose action is prd_created or done. The route is
  // session-scoped to the company; `activeCompany` is only a re-fetch trigger.
  useEffect(() => {
    let cancelled = false
    setLoad("loading")
    ideationApi
      .completed()
      .then((res) => {
        if (cancelled) return
        setItems(res.items)
        setLoad("ready")
      })
      .catch(() => {
        if (cancelled) return
        setItems([])
        setLoad("error")
      })
    return () => { cancelled = true }
  }, [activeCompany])

  useEffect(() => { onCountChange?.(items.length) }, [items.length, onCountChange])

  if (load === "loading") {
    return (
      <div className="bl-empty" role="status" aria-live="polite" style={{ padding: "48px 24px", textAlign: "center", color: "var(--ink-3)" }}>
        Loading completed initiatives…
      </div>
    )
  }

  if (load === "error") {
    return (
      <div className="bl-empty" role="alert" style={{ padding: "48px 24px", textAlign: "center", color: "var(--ink-3)" }}>
        Couldn&apos;t load completed initiatives. Please try again.
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="bl-empty" role="status" style={{ padding: "56px 24px", textAlign: "center", maxWidth: 480, margin: "0 auto", color: "var(--ink-2)" }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--ink)", margin: "0 0 8px" }}>
          Nothing completed yet
        </h2>
        <p style={{ fontSize: 13, lineHeight: 1.55, margin: 0 }}>
          When you create a PRD for a brief finding or mark one done, it moves
          here so you can see what your team acted on across briefs.
        </p>
      </div>
    )
  }

  return (
    <>
      <div className="bl-info-bar">
        <div className="bl-info-left">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ flexShrink: 0, marginTop: 1 }}>
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><polyline points="22 4 12 14.01 9 11.01" />
          </svg>
          <span>
            <strong>{items.length} {items.length === 1 ? "initiative" : "initiatives"}</strong>{" "}
            acted on from Sprntly briefs — a PRD was created or the work was marked done. Most recent first.
          </span>
        </div>
      </div>
      <div className="bl-table-wrap bl-table-wrap--completed">
        <div className="bl-thead bl-thead--completed">
          <div className="bl-th bl-th--initiative">Initiative</div>
          <div className="bl-th bl-th--ctype">Status</div>
          <div className="bl-th bl-th--shipped">Surfaced</div>
        </div>
        <div className="bl-tbody">
          {items.map((item) => {
            const a = ACTION_STYLE[item.action]
            return (
              <div key={item.theme_id} className="bl-completed-row">
                <div className="bl-cell bl-cell--initiative">
                  <div className="bl-project-title">{item.title}</div>
                </div>
                <div className="bl-cell bl-cell--ctype">
                  <span className="bl-type-badge" style={{ color: a.style.color, background: a.style.bg, borderColor: a.style.border, cursor: "default" }}>
                    {a.label}
                  </span>
                </div>
                <div className="bl-cell bl-cell--shipped">{formatSurfacedDate(item.last_surfaced_at)}</div>
              </div>
            )
          })}
        </div>
      </div>
    </>
  )
}

// ── Idea detail modal ─────────────────────────────────────────────────────────

// How we FRAME the problem, per triage tag. Ideation's job is not to restate
// the title — it's to hand a PM the lens and the question that turns a one-line
// idea into a scoped problem. Keyed by tag (the backend's taxonomy) rather than
// the UI's idea-type, since Infra/Research have no tag and fall through.
const TAG_FRAMING: Record<IdeationTag, { lens: string; question: string }> = {
  something_broken: {
    lens: "Something is broken",
    question:
      "What exactly fails, for whom, and how often? A fix is worth scoping when the failure is repeatable and the workaround costs more than the fix.",
  },
  something_new: {
    lens: "Something is missing",
    question:
      "What are people trying to do that they can't do today? Scope this when the job-to-be-done is clear and nothing in the product serves it.",
  },
  something_better: {
    lens: "Something is harder than it should be",
    question:
      "Where does the current path cost people time or attention? Scope this when the friction is measurable and recurring.",
  },
}

const DEFAULT_FRAMING = {
  lens: "Worth a closer look",
  question:
    "What problem would this solve, for whom, and how would we know it worked?",
}

/** Human label for a KG signal's source type ("zendesk" → "Zendesk"). */
function sourceLabel(s: string | null): string {
  if (!s) return "Unknown source"
  return s.charAt(0).toUpperCase() + s.slice(1).replace(/[_-]/g, " ")
}

function IdeaDetailModal({
  idea,
  onClose,
  onGenerateBrief,
  onGeneratePrototype,
  busy,
}: {
  idea: IdeationIdea
  onClose: () => void
  onGenerateBrief: (idea: IdeationIdea, detail: IdeationDetail | null) => void
  onGeneratePrototype: (idea: IdeationIdea) => void
  busy: null | "prd" | "prototype"
}) {
  const [detail, setDetail] = useState<IdeationDetail | null>(null)
  const [load, setLoad] = useState<LoadState>("loading")

  // Pull the idea's evidence trail. The list route doesn't carry it (the table
  // doesn't need it), so the popup fetches per-idea on open.
  useEffect(() => {
    let cancelled = false
    setLoad("loading")
    setDetail(null)
    ideationApi
      .detail(idea.id)
      .then((d) => {
        if (cancelled) return
        setDetail(d)
        setLoad("ready")
      })
      .catch(() => {
        if (cancelled) return
        setLoad("error")
      })
    return () => { cancelled = true }
  }, [idea.id])

  // Escape closes, matching every other overlay in the app.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const framing = detail?.tag ? TAG_FRAMING[detail.tag] : DEFAULT_FRAMING
  const painPoint = detail?.reasoning || idea.sub
  const evidence = detail?.evidence ?? []

  return (
    <div className="bl-modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="bl-modal"
        role="dialog"
        aria-modal="true"
        aria-label={idea.title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="bl-detail-top">
          <span className="bl-detail-kicker">{idea.type}</span>
          <span className="bl-detail-rank">#{idea.rank}</span>
          <button type="button" className="bl-detail-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="bl-detail-body">
          <h2 className="bl-detail-title">{idea.title}</h2>

          {/* Why it's here: Ideation is defined by NOT being prioritized. */}
          <p className="bl-modal-why">
            Not prioritized in the weekly brief — it ranked #{idea.rank} behind
            this week&apos;s top 3.
          </p>

          {/* TL;DR of the pain point */}
          <div className="bl-detail-label">The pain point</div>
          <p className="bl-detail-sub">
            {painPoint || "No rationale was recorded for this idea."}
          </p>

          {/* Problem framing */}
          <div className="bl-detail-label">Framing the problem</div>
          <div className="bl-modal-framing">
            <strong>{framing.lens}.</strong> {framing.question}
          </div>

          {/* Evidence — what we actually heard */}
          <div className="bl-detail-label">
            What we&apos;re hearing
            {detail && detail.evidence_count > 0 && (
              <span className="bl-modal-evidence-count">
                {detail.evidence_count} signal{detail.evidence_count === 1 ? "" : "s"}
                {detail.sources.length > 0 && ` across ${detail.sources.length} source${detail.sources.length === 1 ? "" : "s"}`}
              </span>
            )}
          </div>

          {load === "loading" && (
            <p className="bl-modal-muted" role="status" aria-live="polite">Loading the evidence behind this…</p>
          )}
          {load === "error" && (
            <p className="bl-modal-muted" role="alert">Couldn&apos;t load the evidence for this idea.</p>
          )}
          {load === "ready" && evidence.length === 0 && (
            <p className="bl-modal-muted">
              {detail?.is_manual
                ? "You added this idea by hand, so there's no source evidence behind it yet."
                : "No supporting signals are attached to this theme yet."}
            </p>
          )}
          {evidence.map((e) => (
            <blockquote key={e.signal_id} className="bl-modal-quote">
              <p>{e.content}</p>
              <cite>{sourceLabel(e.source_type)}</cite>
            </blockquote>
          ))}

          {/* CTA — into the existing chat → PRD → tickets → prototype pipeline */}
          <div className="bl-detail-label">Next steps</div>
          <div className="bl-detail-actions">
            <button
              type="button"
              className="bl-detail-btn bl-detail-btn--primary"
              disabled={busy !== null}
              onClick={() => onGenerateBrief(idea, detail)}
            >
              <SparkleIcon size={13} />
              Generate a brief
            </button>
            <button
              type="button"
              className="bl-detail-btn bl-detail-btn--ghost"
              disabled={busy !== null}
              onClick={() => onGeneratePrototype(idea)}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
              {busy === "prototype" ? "Preparing prototype…" : "Generate prototype"}
            </button>
          </div>
          <p className="bl-modal-hint">
            Opens a chat thread and drafts a PRD you can turn into tickets and a
            prototype.
          </p>
        </div>
      </div>
    </div>
  )
}

// ── Sync loading overlay ──────────────────────────────────────────────────────

function SyncingOverlay() {
  return (
    <div className="bl-syncing-overlay" role="status" aria-live="polite">
      <span className="bl-syncing-spinner" aria-hidden />
      Syncing your ideas…
    </div>
  )
}

// ── Main screen ───────────────────────────────────────────────────────────────

export function IdeationScreen() {
  const { showToast, openPrdTab }           = useNavigation()
  const router                              = useRouter()
  const [tab, setTab]                       = useState<IdeationTab>("proposed")
  const [proposedCount, setProposedCount]   = useState<number | null>(null)
  const [completedCount, setCompletedCount] = useState<number | null>(null)
  const [showAddIdea, setShowAddIdea]       = useState(false)
  const [isSyncing, setIsSyncing]           = useState(false)
  const [reloadKey, setReloadKey]           = useState(0)
  const [busy, setBusy]                     = useState<null | "prd" | "prototype">(null)
  const [chatValue, setChatValue]           = useState("")
  const [selectedIdea, setSelectedIdea]     = useState<IdeationIdea | null>(null)
  const textareaRef                         = useRef<HTMLTextAreaElement>(null)
  // Bridges to ProposedContent's handlers without lifting its ideas state.
  const addHandlerRef = useRef<((title: string, type: IdeaType) => void) | null>(null)
  const resequenceHandlerRef = useRef<(() => void) | null>(null)

  const handleSelectIdea = useCallback((idea: IdeationIdea) => {
    setSelectedIdea(idea)
  }, [])

  // Real sync: re-pull the ideas from the backend (bumps ProposedContent's
  // reloadKey). The brief overlay just gives the refetch a visible beat.
  const handleSync = () => {
    if (isSyncing) return
    setIsSyncing(true)
    setReloadKey((k) => k + 1)
    setTimeout(() => {
      setIsSyncing(false)
      showToast("Synced", "Your ideas are up to date.")
    }, 800)
  }

  // Generate a brief from an Ideation idea: open it as a NEW CHAT TAB on the
  // chat surface, with the Evidence / PRD / Tickets panel sliding over it.
  // openPrdTab routes to `/` and ChatScreen drives runPrdGenerationFromIdeation
  // in that tab — the same funnel the brief and command-palette paths use, so
  // the PRD flows on to tickets and prototype unchanged. An ideation PRD isn't
  // at a brief insight_index, so the tab carries no meta — it renders from the
  // PRD payload alone.
  //
  // `seedQuery` puts the user's ask in the thread as a real turn and `insightBody`
  // renders the problem framing as the opening card, so the chat the user lands
  // in is grounded in the idea rather than empty next to a spinning panel.
  const handleGenerateBrief = useCallback((idea: IdeationIdea, detail: IdeationDetail | null) => {
    const painPoint = detail?.reasoning || idea.sub
    openPrdTab({
      title: `PRD · ${idea.title}`,
      insightBody: painPoint || undefined,
      seedQuery: `Generate a brief for "${idea.title}" — an ideation idea that didn't make this week's top 3.`,
      source: { kind: "generateIdeation", ideationItemId: idea.id },
    })
    setSelectedIdea(null)
  }, [openPrdTab])

  // Generate a prototype from an idea: a prototype builds from a PRD, so
  // ensure the theme's PRD exists first (dedup returns it instantly if already
  // generated), then hand off to the prototype route with ?generate=1.
  const handleGeneratePrototype = useCallback(async (idea: IdeationIdea) => {
    setBusy("prototype")
    showToast("Preparing prototype…", "Building the PRD your prototype is based on.")
    try {
      const result = await runPrdGenerationFromIdeation(idea.id)
      if (!result.ok) {
        showToast("Prototype blocked", result.message)
        return
      }
      router.push(prototypePath(result.prd.prd_id, { generate: true }))
    } catch (err) {
      showToast("Prototype failed", err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }, [router, showToast])

  const handleChat = (e: React.FormEvent) => {
    e.preventDefault()
    if (!chatValue.trim()) return
    // Free-text re-prioritization (natural-language re-ranking) isn't wired yet;
    // the concrete "Re-sequence" chip below performs a real, persisted re-order.
    showToast("Not yet available", "Use “Re-sequence” to re-order by impact — free-text re-prioritization is coming soon.")
    setChatValue("")
    if (textareaRef.current) textareaRef.current.style.height = "auto"
  }

  return (
    <AppLayout mainClassName="main--ideation">
      <div className="bl-shell">

        {/* ── Single combined top bar ── */}
        <div className="bl-topbar">
          <div className="bl-topbar-left">
            <h1 className="bl-title">Ideation</h1>
            <span className="bl-count-badge">
              {tab === "proposed"
                ? `${proposedCount ?? 0} ideas`
                : `${completedCount ?? 0} shipped`}
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
              <SyncIcon /> Sync ideas
            </button>
            <button type="button" className="bl-btn-add" onClick={() => { setShowAddIdea(true); setTab("proposed") }}>
              + Add idea
            </button>
          </div>
        </div>

        {/* ── Scrollable content ── */}
        <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
          <div className="bl-body" style={{ flex: 1, overflow: "auto" }}>
            {tab === "proposed"
              ? <ProposedContent addHandlerRef={addHandlerRef} resequenceHandlerRef={resequenceHandlerRef} reloadKey={reloadKey} onSelectIdea={handleSelectIdea} selectedIdeaId={selectedIdea?.id ?? null} onCountChange={setProposedCount} />
              : <CompletedContent onCountChange={setCompletedCount} />}
          </div>

          {/* ── Detail popup: problem framing + evidence + brief CTA ── */}
          {selectedIdea && (
            <IdeaDetailModal
              idea={selectedIdea}
              onClose={() => setSelectedIdea(null)}
              onGenerateBrief={handleGenerateBrief}
              onGeneratePrototype={handleGeneratePrototype}
              busy={busy}
            />
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
                    onClick={() => resequenceHandlerRef.current?.()}>
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
