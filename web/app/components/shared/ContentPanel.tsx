"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { EvidenceSections } from "./EvidenceSections"
import { EmptyPane } from "./EmptyPane"
import { IconClose, IconSparkle } from "./app-icons"
import { runEvidenceGeneration } from "../../lib/runEvidenceGeneration"
import { runPrdGeneration } from "../../lib/runPrdGeneration"
import { ticketPushApi, type ClickUpList } from "../../lib/api"
import { PrdPanelContent } from "./PrdPanelContent"
import { IconMicroscope, IconFileText, IconTicket, IconDeviceFloppy, IconShare } from "@tabler/icons-react"

const TABS = [
  { icon: <IconMicroscope size={11.5} />, id: "evidence", label: "Evidence" },
  { icon: <IconFileText size={11.5}/> , id: "prd", label: "PRD" },
  { icon: <IconTicket size={11.5}/> , id: "tickets", label: "Tickets" },
] as const

const CPANEL_WIDTH_KEY = "sprntly-cpanel-width"
const CPANEL_WIDTH_MIN = 650   // min: content needs room to breathe
const CPANEL_MAX_VW   = 0.6    // max: never more than 60% of the viewport

function clampCpanelWidth(px: number): number {
  const max = Math.round(window.innerWidth * CPANEL_MAX_VW)
  return Math.min(max, Math.max(CPANEL_WIDTH_MIN, Math.round(px)))
}

export function ContentPanel() {
  const { contentPanelTab, openContentPanel, closeContentPanel } = useNavigation()

  // Tracks the live pixel width; null = use the CSS default (60vw).
  const widthRef = useRef<number | null>(null)

  // On open: restore saved width, apply it, and keep it clamped on window resize.
  // On close: remove the CSS var so it resets to default.
  useEffect(() => {
    if (!contentPanelTab) return
    const root = document.documentElement

    const saved = Number(window.localStorage.getItem(CPANEL_WIDTH_KEY))
    widthRef.current = Number.isFinite(saved) && saved >= CPANEL_WIDTH_MIN ? saved : null

    const apply = () => {
      if (window.innerWidth <= 768 || widthRef.current == null) {
        root.style.removeProperty("--cpanel-width")
        return
      }
      const next = clampCpanelWidth(widthRef.current)
      widthRef.current = next
      root.style.setProperty("--cpanel-width", `${next}px`)
    }

    apply()
    window.addEventListener("resize", apply)
    return () => {
      window.removeEventListener("resize", apply)
      root.style.removeProperty("--cpanel-width")
    }
  }, [contentPanelTab])

  // Pointer-down on the left-edge handle starts a drag session.
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    if (window.innerWidth <= 768) return
    e.preventDefault()
    const root = document.documentElement
    const startX = e.clientX
    const startW = widthRef.current ?? Math.round(window.innerWidth * CPANEL_MAX_VW)
    root.classList.add("cpanel-resizing")

    const onMove = (ev: MouseEvent) => {
      // Dragging LEFT widens the panel (panel anchored to right edge).
      const next = clampCpanelWidth(startW + (startX - ev.clientX))
      widthRef.current = next
      root.style.setProperty("--cpanel-width", `${next}px`)
    }
    const onUp = () => {
      if (widthRef.current != null) {
        window.localStorage.setItem(CPANEL_WIDTH_KEY, String(widthRef.current))
      }
      root.classList.remove("cpanel-resizing")
      window.removeEventListener("mousemove", onMove)
      window.removeEventListener("mouseup", onUp)
    }
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
  }, [])

  if (!contentPanelTab) return null

  return (
    <>
      <div className="cpanel-overlay" onClick={closeContentPanel} />
      <aside className="cpanel">
        {/* Draggable left edge — grab to resize */}
        <div
          className="cpanel-resize-handle"
          onMouseDown={handleResizeStart}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize panel"
        />
        <div className="cpanel-head">
          <div>
            <div className="cpanel-tabs">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={`cpanel-tab${contentPanelTab === t.id ? " cpanel-tab--active" : ""}`}
                  onClick={() => openContentPanel(t.id)}
                >
                  {t.icon} {t.label}
                </button>
              ))}
            </div>
          </div>
            <span className="cpanel-main-name">PRD · Handoff Threshold & Champion Enablement</span>
          <div className="cpanel-head-actions">
            <button className="cpanel-action-btn">
              <IconDeviceFloppy size={12} />Save
            </button>
            <button className="cpanel-action-btn">
              <IconShare size={12} />Share
            </button>
            <button type="button" className="cpanel-close" onClick={closeContentPanel} aria-label="Close">
              <IconClose size={16} />
            </button>
          </div>
        </div>

        <div className="cpanel-body">
          {contentPanelTab === "evidence" && <EvidenceTab />}
          {contentPanelTab === "prd" && <PrdPanelContent />}
          {contentPanelTab === "tickets" && <TicketsTab />}
        </div>
      </aside>
    </>
  )
}

function EvidenceTab() {
  const { expandAiPanel, setAIBarValue, showToast, openContentPanel, closeContentPanel } = useNavigation()
  const { content, setContent } = useContent()
  const { detail, evidence, evidenceGenerating } = content

  // Local generation state — used only when coming from the brief/detail flow
  // (detail.meta is present). Chat-flow generation is driven externally by
  // ChatScreen and signalled via content.evidenceGenerating.
  const [localState, setLocalState] = useState<
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "error"; message: string }
  >({ kind: "idle" })
  const [generatingPrd, setGeneratingPrd] = useState(false)
  const loadedKeyRef = useRef<string | null>(null)

  useEffect(() => {
    if (!detail?.meta) return
    const key = `${detail.meta.briefId}:${detail.meta.insightIndex}`
    // Already loaded this exact insight — don't re-fetch.
    if (loadedKeyRef.current === key && evidence) return
    // Switching to a different insight — clear stale evidence.
    if (loadedKeyRef.current !== key) setContent({ evidence: null })
    let cancelled = false
    setLocalState({ kind: "loading" })
    loadedKeyRef.current = key
    runEvidenceGeneration(detail.meta)
      .then((result) => {
        if (cancelled) return
        if (!result.ok) { setLocalState({ kind: "error", message: result.message }); return }
        setContent({ evidence: result.evidence })
        setLocalState({ kind: "idle" })
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setLocalState({ kind: "error", message: e instanceof Error ? e.message : String(e) })
      })
    return () => { cancelled = true }
  }, [detail?.meta?.briefId, detail?.meta?.insightIndex, evidence, setContent])

  const handleGeneratePrd = async () => {
    if (!detail?.meta) {
      showToast("Can't generate PRD", "Open this evidence from the brief first.")
      return
    }
    const currentPrdMeta = content.prdMeta
    if (
      content.prd &&
      currentPrdMeta &&
      currentPrdMeta.briefId === detail.meta.briefId &&
      currentPrdMeta.insightIndex === detail.meta.insightIndex
    ) {
      openContentPanel("prd")
      return
    }
    setGeneratingPrd(true)
    // Switch the rail to the PRD tab immediately and show its generating spinner
    // there, so the in-progress PRD is always on the right.
    setContent({ prd: null, prdMeta: null, prdGenerating: true })
    openContentPanel("prd")
    try {
      const result = await runPrdGeneration(detail.meta)
      if (!result.ok) { setContent({ prdGenerating: false }); showToast("PRD generation failed", result.message.slice(0, 200)); return }
      setContent({ prd: result.prd, prdMeta: detail.meta, prdGenerating: false })
      openContentPanel("prd")
    } catch (e) {
      setContent({ prdGenerating: false })
      showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
    } finally {
      setGeneratingPrd(false)
    }
  }

  // Unified loading flag: either local (brief flow) or external (chat flow)
  const isLoading = localState.kind === "loading" || evidenceGenerating

  // Nothing to show at all
  if (!detail && !evidence && !isLoading && localState.kind !== "error") {
    return (
      <div className="cpanel-empty">
        <IconSparkle size={20} />
        <p>No evidence loaded yet. Open a finding from the brief first.</p>
      </div>
    )
  }

  return (
    <div className="ev-panel">
      {/* Scrollable document body */}
      <div className="ev-doc">
        {/* Tag row — only shown when we have brief detail context */}
        {detail && (
          <div className="ev-doc-tag-row">
            <div className="ev-doc-tags">
              {detail.tags && detail.tags.map((t, i) => (
                <span key={i} className={`ev-tag ${t.className ?? ""}`}>{t.label}</span>
              ))}
              <span className="ev-tag ev-tag--insight">BRIEF INSIGHT</span>
            </div>
            <button
              type="button"
              className="ev-ask-btn"
              title="Ask AI about this finding"
              onClick={() => {
                expandAiPanel()
                setAIBarValue("About this finding — summarize risks and next steps.")
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
            </button>
          </div>
        )}

        {evidence ? (
          <>
            <h1 className="ev-doc-title">{evidence.title}</h1>
            {evidence.metaLine && <div className="ev-doc-meta">{evidence.metaLine}</div>}
            <div className="ev-doc-sections">
              <EvidenceSections sections={evidence.sections} />
            </div>
          </>
        ) : isLoading ? (
          <EmptyPane
            title="Generating evidence…"
            hint="Pulling the data-science slicing, infographics, qualitative signals, and hypothesis for this finding."
            placeholders={4}
          />
        ) : localState.kind === "error" ? (
          <EmptyPane
            title="Couldn't load full evidence"
            hint={localState.message}
            placeholders={0}
          />
        ) : null}
      </div>

      {/* Sticky footer CTA */}
      <div className="ev-panel-cta">
        <button type="button" className="ev-cta-btn" onClick={closeContentPanel}>
          Snooze
        </button>
        <button
          type="button"
          className="ev-cta-btn ev-cta-btn--primary"
          onClick={handleGeneratePrd}
          disabled={generatingPrd}
        >
          {generatingPrd ? "Generating PRD…" : "Generate PRD"}
        </button>
      </div>
    </div>
  )
}

// ── Ticket data ─────────────────────────────────────────────────────────────
type TicketPriority = "P0" | "P1" | "P2"
type TicketStatus = "Backlog" | "To do" | "In progress" | "Review" | "Done"
interface Ticket {
  id: string
  title: string
  category: string
  description: string
  priority: TicketPriority
  points: number
  techTag: string
  initials: string
  initialsColor: string
  personName: string
  personEmail: string
  personRole: string
  acceptanceCriteria: string[]
  prdSection: string
  attachments: { id?: number; label: string; sub: string }[]
}

const MOCK_TICKETS: Ticket[] = [
  {
    id: "MER-481",
    title: "First-Handoff Wizard · 3-step inline prompt",
    category: "Product",
    description: "Inline 3-step wizard that walks a new user through their first handoff in <60 seconds. Skippable. Adds telemetry hooks so we can measure adoption and skip-rate.",
    priority: "P0",
    points: 8,
    techTag: "UI-React",
    initials: "SC",
    initialsColor: "#2A6EC8",
    personName: "Sarah Chen",
    personEmail: "sarah@meridian.health",
    personRole: "Product",
    acceptanceCriteria: [
      "Renders inline on first handoff event, after onboarding",
      "Three steps: select recipient → pick template → review & send",
      "Skippable from any step, skip event logged",
      "Telemetry: started / step_completed / completed / skipped",
    ],
    prdSection: "§ Solution",
    attachments: [
      { label: "PRD v0.3 · § Solution", sub: "Handoff Threshold & Champion Enablement" },
      { label: "Prototype · First-Handoff Wizard v2", sub: "Design Agent · 3 versions · 5 comments" },
      { label: "Evidence · Day-30 retention dip", sub: "91% confidence · 5 sources" },
    ],
  },
  {
    id: "MER-482",
    title: "Telemetry hooks · wizard adoption & skip rate",
    category: "Analytics",
    description: "Instrument the First-Handoff Wizard with Mixpanel events: wizard_started, wizard_step_completed, wizard_completed, wizard_skipped.",
    priority: "P1",
    points: 3,
    techTag: "Mixpanel",
    initials: "JK",
    initialsColor: "#634AB0",
    personName: "Jordan Kim",
    personEmail: "jordan@meridian.health",
    personRole: "Analytics",
    acceptanceCriteria: [
      "wizard_started fires on wizard mount",
      "wizard_step_completed fires with step index on each transition",
      "wizard_completed fires on final submission",
      "wizard_skipped fires with step index when user dismisses",
    ],
    prdSection: "§ Success metrics",
    attachments: [
      { label: "PRD v0.3 · § Success metrics", sub: "wizard_started, wizard_step_completed, wizard_completed, wizard_skipped" },
    ],
  },
  {
    id: "MER-479",
    title: "HandoffSyncService · fix p95 timeout regression",
    category: "Reliability",
    description: "Address HandoffSyncService p95 regression flagged by Engineer Agent. Investigate root cause and apply targeted fix without breaking existing retry logic.",
    priority: "P0",
    points: 5,
    techTag: "Backend",
    initials: "PS",
    initialsColor: "#C13838",
    personName: "Priya Sharma",
    personEmail: "priya@meridian.health",
    personRole: "Engineering",
    acceptanceCriteria: [
      "p95 latency returns to baseline (<800 ms) in staging",
      "No increase in error rate for HandoffSyncService",
      "Existing retry logic unaffected",
      "Load test confirms fix holds under 2× traffic",
    ],
    prdSection: "§ Solution",
    attachments: [
      { label: "PRD v0.3 · § Solution", sub: "Handoff Threshold & Champion Enablement" },
    ],
  },
  {
    id: "MER-480",
    title: "CS champion playbook · v2 with re-assignment",
    category: "CS",
    description: "Assign / re-assign clinical champions at Riverside, Cornerstone, Beacon. CS owns.",
    priority: "P1",
    points: 0,
    techTag: "Playbook",
    initials: "MO",
    initialsColor: "#179463",
    personName: "Marcus O'Brien",
    personEmail: "marcus@meridian.health",
    personRole: "Customer Success",
    acceptanceCriteria: [
      "Champion assigned at each of the 4 affected deployments",
      "Re-assignment flow documented in CS runbook",
      "Champion confirmed via in-app role tag",
    ],
    prdSection: "§ Solution",
    attachments: [
      { label: "PRD v0.3 · § Solution", sub: "Handoff Threshold & Champion Enablement" },
    ],
  },
  {
    id: "MER-483",
    title: "Wizard localization · ES, FR",
    category: "Localization",
    description: "Localize First-Handoff Wizard copy for ES and FR (existing customer base).",
    priority: "P2",
    points: 2,
    techTag: "i18n",
    initials: "—",
    initialsColor: "#AAB3AE",
    personName: "Unassigned",
    personEmail: "",
    personRole: "Localization",
    acceptanceCriteria: [
      "All wizard strings extracted to i18n keys",
      "ES and FR translations provided and QA'd",
      "Locale switches correctly on user preference",
    ],
    prdSection: "§ Acceptance criteria",
    attachments: [
      { label: "PRD v0.3 · § Acceptance criteria", sub: "Localized for ES, FR (existing customer base)" },
    ],
  },
]

type AssigneeOption = { initials: string; color: string; name: string; email: string; role: string }

const DEMO_ASSIGNEES: AssigneeOption[] = [
  { initials: "SC", color: "#2A6EC8", name: "Sarah Chen", email: "sarah@meridian.health", role: "Product" },
  { initials: "JK", color: "#634AB0", name: "Jordan Kim", email: "jordan@meridian.health", role: "Analytics" },
  { initials: "PS", color: "#C13838", name: "Priya Sharma", email: "priya@meridian.health", role: "Engineering" },
  { initials: "MO", color: "#179463", name: "Marcus O'Brien", email: "marcus@meridian.health", role: "Customer Success" },
  { initials: "—", color: "#AAB3AE", name: "Unassigned", email: "", role: "" },
]

const PRIORITY_COLOR: Record<TicketPriority, string> = {
  P0: "#C13838",
  P1: "#C16A0B",
  P2: "#2A6EC8",
}

function TicketRow({ ticket, onClick }: { ticket: Ticket; onClick: () => void }) {
  return (
    <div className="tkt-row" onClick={onClick} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onClick()}>
      <div className="tkt-row-left">
        <div className="tkt-row-id-wrap">
          <span className="tkt-row-id">{ticket.id}</span>
          <span className="tkt-row-cat">{ticket.category}</span>
        </div>
        <div className="tkt-row-main">
          <div className="tkt-row-title">{ticket.title}</div>
          <div className="tkt-row-desc">{ticket.description}</div>
          <div className="tkt-row-tags">
            <span
              className="tkt-tag tkt-tag--priority"
              style={{
                color: PRIORITY_COLOR[ticket.priority],
                background: `${PRIORITY_COLOR[ticket.priority]}14`,
                borderColor: `${PRIORITY_COLOR[ticket.priority]}33`,
              }}
            >
              {ticket.priority}
            </span>
            {ticket.points > 0 && <span className="tkt-tag">{ticket.points} pts</span>}
            <span className="tkt-tag">{ticket.techTag}</span>
          </div>
        </div>
      </div>
      <div className="tkt-row-right">
        <div
          className="tkt-avatar"
          style={{ background: `${ticket.initialsColor}22`, color: ticket.initialsColor }}
          title={ticket.initials}
        >
          {ticket.initials}
        </div>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--ink-4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </div>
    </div>
  )
}

const CHEVRON_DOWN = (
  <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor" aria-hidden style={{ marginLeft: 2, flexShrink: 0 }}>
    <path d="M5 7L1 3h8z" />
  </svg>
)

function PillDropdown<T extends string>({
  value,
  options,
  onChange,
  renderLabel,
  renderOption,
  colorStyle,
}: {
  value: T
  options: T[]
  onChange: (v: T) => void
  renderLabel: (v: T) => string
  renderOption?: (v: T) => string
  colorStyle?: React.CSSProperties
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="tkt-status-wrap">
      <button
        type="button"
        className="tkt-badge tkt-badge--status"
        style={colorStyle}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {renderLabel(value)}
        {CHEVRON_DOWN}
      </button>
      {open && (
        <>
          <div className="tkt-status-backdrop" onClick={() => setOpen(false)} />
          <div className="tkt-status-menu" role="listbox">
            {options.map((opt) => (
              <div
                key={opt}
                role="option"
                aria-selected={opt === value}
                className={`tkt-status-option${opt === value ? " tkt-status-option--active" : ""}`}
                onClick={() => { onChange(opt); setOpen(false) }}
              >
                {opt === value && (
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                )}
                {renderOption ? renderOption(opt) : opt}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

const STATUS_OPTIONS: TicketStatus[] = ["Backlog", "To do", "In progress", "Review", "Done"]
const SPRINT_OPTIONS = ["Sprint 25", "Sprint 26", "Unassigned sprint"]
const PRIORITY_OPTIONS: TicketPriority[] = ["P0", "P1", "P2"]
const PRIORITY_LABEL: Record<TicketPriority, string> = {
  P0: "P0 — Critical",
  P1: "P1 — High",
  P2: "P2 — Medium",
}

// ── localStorage fallback for ticket overrides ──
const TKT_KEY = (id: string) => `sprntly_tkt_${id}`
type TktOverrides = {
  description?: string
  acceptanceCriteria?: string[]
  attachments?: { id?: number; label: string; sub: string }[]
  comments?: { id?: number; author: string; text: string; time: string }[]
}
function loadTktLocal(id: string): TktOverrides {
  try { return JSON.parse(localStorage.getItem(TKT_KEY(id)) ?? "{}") } catch { return {} }
}
function saveTktLocal(id: string, data: TktOverrides) {
  try { localStorage.setItem(TKT_KEY(id), JSON.stringify(data)) } catch { /* ignore */ }
}

function TicketDetail({ ticket, onBack }: { ticket: Ticket; onBack: () => void }) {
  const { showToast } = useNavigation()
  const { content } = useContent()
  const [status, setStatus] = useState<TicketStatus>("Backlog")
  const [priority, setPriority] = useState<TicketPriority>(ticket.priority)
  const [sprint, setSprint] = useState("Sprint 25")
  const [comment, setComment] = useState("")
  const [attachName, setAttachName] = useState("")
  const [attachSub, setAttachSub] = useState("")
  const [showAttachForm, setShowAttachForm] = useState(false)
  const [assignee, setAssignee] = useState<AssigneeOption>({
    initials: ticket.initials,
    color: ticket.initialsColor,
    name: ticket.personName,
    email: ticket.personEmail,
    role: ticket.personRole,
  })
  const [showReassign, setShowReassign] = useState(false)
  const assigneeOptions: AssigneeOption[] = content.teamMembers.length > 0
    ? content.teamMembers.map((m) => ({ initials: m.initials, color: m.color ?? "#4A554F", name: m.name, email: m.email, role: m.role }))
    : DEMO_ASSIGNEES
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const kb = file.size / 1024
    const sizeFmt = kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.round(kb)} KB`
    setAttachName(file.name)
    setAttachSub(`${file.type || "file"} · ${sizeFmt}`)
    setShowAttachForm(true)
    // Reset so the same file can be re-selected if needed
    e.target.value = ""
  }

  // State for description, attachments, comments — loaded from backend on mount
  const [overrides, setOverrides] = useState<TktOverrides>(() => loadTktLocal(ticket.id))
  const desc = overrides.description ?? ticket.description
  const criteria = overrides.acceptanceCriteria ?? ticket.acceptanceCriteria
  const attachments = overrides.attachments ?? ticket.attachments.map((a) => ({ id: a.id, label: a.label, sub: a.sub }))
  const comments = overrides.comments ?? []
  const descTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Load from Supabase on mount
  useEffect(() => {
    let cancelled = false
    import("../../lib/api").then(({ ticketDataApi }) => {
      ticketDataApi.getData(ticket.id).then((data) => {
        if (cancelled) return
        const loaded: TktOverrides = {}
        if (data.description != null) loaded.description = data.description
        if (data.acceptance_criteria != null) loaded.acceptanceCriteria = data.acceptance_criteria
        if (data.attachments.length > 0) loaded.attachments = data.attachments
        if (data.comments.length > 0) loaded.comments = data.comments.map((c) => ({
          id: c.id, author: c.author, text: c.body, time: c.time,
        }))
        if (Object.keys(loaded).length > 0) {
          setOverrides((prev) => ({ ...prev, ...loaded }))
          saveTktLocal(ticket.id, { ...loadTktLocal(ticket.id), ...loaded })
        }
      }).catch(() => { /* use localStorage fallback */ })
    })
    return () => { cancelled = true }
  }, [ticket.id])

  const persist = (patch: Partial<TktOverrides>) => {
    const next = { ...overrides, ...patch }
    setOverrides(next)
    saveTktLocal(ticket.id, next)
  }

  // Debounced description save to backend (2s after last edit)
  const saveDescToBackend = useCallback((newDesc: string, newCriteria: string[]) => {
    if (descTimerRef.current) clearTimeout(descTimerRef.current)
    descTimerRef.current = setTimeout(() => {
      import("../../lib/api").then(({ ticketDataApi }) => {
        ticketDataApi.saveDescription(ticket.id, newDesc, newCriteria).catch(() => {})
      })
    }, 2000)
  }, [ticket.id])

  const persistDesc = (newDesc: string) => {
    persist({ description: newDesc })
    saveDescToBackend(newDesc, criteria)
  }

  const persistCriteria = (newCriteria: string[]) => {
    persist({ acceptanceCriteria: newCriteria })
    saveDescToBackend(desc, newCriteria)
  }

  return (
    <div className="tkt-detail">
      {/* Nav bar */}
      <div className="tkt-detail-nav">
        <button type="button" className="tkt-back-btn" onClick={onBack}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <polyline points="15 18 9 12 15 6" />
          </svg>
          All chunks
        </button>
        <span className="tkt-detail-id-chip">{ticket.id}</span>
        <button
          type="button"
          className="tkt-copy-link"
          onClick={() => showToast("Link copied", `Link to ${ticket.id} copied to clipboard.`)}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
          </svg>
          Copy link
        </button>
        <button type="button" className="tkt-more-btn" aria-label="More options">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
            <circle cx="5" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="19" cy="12" r="1.7" />
          </svg>
        </button>
      </div>

      {/* Title */}
      <h2 className="tkt-detail-title">{ticket.title}</h2>

      {/* Status badges */}
      <div className="tkt-detail-badges">
        <PillDropdown
          value={priority}
          options={PRIORITY_OPTIONS}
          onChange={setPriority}
          renderLabel={(p) => PRIORITY_LABEL[p]}
          renderOption={(p) => PRIORITY_LABEL[p]}
          colorStyle={{ color: PRIORITY_COLOR[priority], background: `${PRIORITY_COLOR[priority]}14`, borderColor: `${PRIORITY_COLOR[priority]}33` }}
        />
        <PillDropdown
          value={status}
          options={STATUS_OPTIONS}
          onChange={setStatus}
          renderLabel={(s) => s}
        />
        <PillDropdown
          value={sprint}
          options={SPRINT_OPTIONS}
          onChange={setSprint}
          renderLabel={(s) => s}
        />
      </div>

      {/* Person responsible */}
      <div className="tkt-detail-section">
        <div className="tkt-detail-section-label">PERSON RESPONSIBLE</div>
        <div className="tkt-person-row" style={{ position: "relative" }}>
          <div className="tkt-person-avatar" style={{ background: `${assignee.color}22`, color: assignee.color }}>
            {assignee.initials}
          </div>
          <div className="tkt-person-info">
            <div className="tkt-person-name">{assignee.name}</div>
            <div className="tkt-person-role">
              {assignee.email ? `${assignee.email} · ` : ""}{assignee.role}
            </div>
          </div>
          <button type="button" className="tkt-reassign-btn" onClick={() => setShowReassign((v) => !v)}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <polyline points="17 1 21 5 17 9" /><path d="M3 11V9a4 4 0 0 1 4-4h14" />
              <polyline points="7 23 3 19 7 15" /><path d="M21 13v2a4 4 0 0 1-4 4H3" />
            </svg>
            Reassign
          </button>
          {showReassign && (
            <>
              <div className="tkt-reassign-backdrop" onClick={() => setShowReassign(false)} />
              <div className="tkt-reassign-menu" role="listbox" aria-label="Select assignee">
                {assigneeOptions.map((opt) => (
                  <button
                    key={opt.email || opt.name}
                    type="button"
                    role="option"
                    aria-selected={opt.email === assignee.email && opt.name === assignee.name}
                    className={`tkt-reassign-option${opt.email === assignee.email && opt.name === assignee.name ? " tkt-reassign-option--active" : ""}`}
                    onClick={() => { setAssignee(opt); setShowReassign(false) }}
                  >
                    <div className="tkt-reassign-option-avatar" style={{ background: `${opt.color}22`, color: opt.color }}>
                      {opt.initials}
                    </div>
                    <div className="tkt-reassign-option-info">
                      <div className="tkt-reassign-option-name">{opt.name}</div>
                      {opt.role ? <div className="tkt-reassign-option-role">{opt.role}</div> : null}
                    </div>
                    {opt.email === assignee.email && opt.name === assignee.name && (
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ flexShrink: 0, marginLeft: "auto" }}>
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    )}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Description */}
      <div className="tkt-detail-section">
        <div className="tkt-detail-section-label">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ marginRight: 5, verticalAlign: "middle" }}>
            <rect x="2" y="3" width="20" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
          </svg>
          DESCRIPTION
        </div>
        <textarea
          className="tkt-detail-desc"
          value={desc}
          onChange={(e) => persistDesc(e.target.value)}
          style={{
            width: "100%", border: "1px solid transparent", borderRadius: 8,
            padding: "10px 12px", fontSize: 14, lineHeight: 1.65, fontFamily: "inherit",
            color: "var(--ink)", background: "transparent", resize: "vertical",
            minHeight: 60, outline: "none", transition: "border-color 0.15s",
          }}
          onFocus={(e) => { e.currentTarget.style.borderColor = "var(--line)" }}
          onBlur={(e) => { e.currentTarget.style.borderColor = "transparent" }}
        />
        <div className="tkt-detail-criteria-label">Acceptance criteria:</div>
        <ul className="tkt-detail-criteria" style={{ listStyle: "disc", paddingLeft: 20 }}>
          {criteria.map((c, i) => (
            <li key={i} style={{ marginBottom: 4 }}>
              <input
                value={c}
                onChange={(e) => {
                  const updated = [...criteria]
                  updated[i] = e.target.value
                  persistCriteria(updated)
                }}
                style={{
                  border: "none", outline: "none", background: "transparent",
                  fontSize: 14, color: "var(--ink)", width: "100%", fontFamily: "inherit",
                }}
              />
            </li>
          ))}
          <li>
            <button
              type="button"
              onClick={() => persistCriteria([...criteria, ""])}
              style={{
                border: "none", background: "none", color: "var(--accent)",
                fontSize: 13, cursor: "pointer", padding: "2px 0", fontWeight: 500,
              }}
            >
              + Add criterion
            </button>
          </li>
        </ul>
      </div>

      {/* Attachments */}
      <div className="tkt-detail-section">
        <div className="tkt-detail-section-label">ATTACHMENTS · {attachments.length}</div>
        <div className="tkt-attachments">
          {attachments.map((a, i) => (
            <div key={i} className="tkt-attachment-row">
              <div className="tkt-attachment-icon">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" />
                </svg>
              </div>
              <div className="tkt-attachment-info">
                <div className="tkt-attachment-label">{a.label}</div>
                <div className="tkt-attachment-sub">{a.sub}</div>
              </div>
              <button
                type="button"
                onClick={() => {
                  const removed = attachments[i]
                  const updated = attachments.filter((_, idx) => idx !== i)
                  persist({ attachments: updated })
                  if (removed.id) {
                    import("../../lib/api").then(({ ticketDataApi }) => {
                      ticketDataApi.removeAttachment(ticket.id, removed.id!).catch(() => {})
                    })
                  }
                }}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-4)", fontSize: 14, padding: 0, lineHeight: 1, flexShrink: 0 }}
                title="Remove"
              >×</button>
            </div>
          ))}
        </div>
        {showAttachForm ? (
          <div style={{
            padding: "10px 12px", borderRadius: 8, border: "1px solid var(--accent, #179463)",
            background: "var(--surface, #fff)", display: "flex", flexDirection: "column", gap: 8,
          }}>
            <input
              autoFocus
              value={attachName}
              onChange={(e) => setAttachName(e.target.value)}
              placeholder="Name (e.g. Design spec v2)"
              style={{
                fontSize: 13, padding: "6px 10px", borderRadius: 6, width: "100%",
                border: "1px solid var(--line, #E8E6E0)", outline: "none", fontFamily: "inherit",
              }}
            />
            <input
              value={attachSub}
              onChange={(e) => setAttachSub(e.target.value)}
              placeholder="Description (e.g. Figma · 12 screens)"
              style={{
                fontSize: 12, padding: "5px 10px", borderRadius: 6, width: "100%",
                border: "1px solid var(--line, #E8E6E0)", outline: "none", fontFamily: "inherit",
                color: "var(--ink-3)",
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && attachName.trim()) {
                  const label = attachName.trim(); const sub = attachSub.trim()
                  persist({ attachments: [...attachments, { label, sub }] })
                  setAttachName(""); setAttachSub(""); setShowAttachForm(false)
                  import("../../lib/api").then(({ ticketDataApi }) => {
                    ticketDataApi.addAttachment(ticket.id, label, sub).catch(() => {})
                  })
                }
              }}
            />
            <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
              <button type="button" onClick={() => { setShowAttachForm(false); setAttachName(""); setAttachSub("") }}
                style={{ fontSize: 12, padding: "4px 12px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--surface)", cursor: "pointer", color: "var(--ink-3)" }}>
                Cancel
              </button>
              <button type="button" disabled={!attachName.trim()}
                onClick={() => {
                  const label = attachName.trim(); const sub = attachSub.trim()
                  persist({ attachments: [...attachments, { label, sub }] })
                  setAttachName(""); setAttachSub(""); setShowAttachForm(false)
                  import("../../lib/api").then(({ ticketDataApi }) => {
                    ticketDataApi.addAttachment(ticket.id, label, sub).catch(() => {})
                  })
                }}
                style={{
                  fontSize: 12, padding: "4px 12px", borderRadius: 6, border: "none",
                  background: attachName.trim() ? "var(--accent, #179463)" : "#ccc", color: "#fff",
                  cursor: attachName.trim() ? "pointer" : "not-allowed", fontWeight: 600,
                }}>
                Add
              </button>
            </div>
          </div>
        ) : (
          <>
            <input
              ref={fileInputRef}
              type="file"
              style={{ display: "none" }}
              onChange={handleFileSelect}
            />
            <button type="button" className="tkt-attach-btn" onClick={() => fileInputRef.current?.click()}>
              + Attach a file or paste a link
            </button>
          </>
        )}
      </div>

      {/* Comments */}
      <div className="tkt-detail-section">
        <div className="tkt-detail-section-label">COMMENTS · {comments.length}</div>
        {comments.length === 0 && (
          <div className="tkt-comments-empty">No comments yet — be the first to add context.</div>
        )}
        {comments.map((c, i) => (
          <div key={i} style={{
            padding: "10px 12px", marginBottom: 8, borderRadius: 8,
            background: "var(--surface-2, #F4F1EA)", border: "1px solid var(--line, #E8E6E0)",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
              <span style={{
                width: 22, height: 22, borderRadius: "50%", fontSize: 9, fontWeight: 600,
                display: "flex", alignItems: "center", justifyContent: "center",
                background: "var(--accent-muted)", color: "var(--accent-ink)",
              }}>{c.author.slice(0, 2).toUpperCase()}</span>
              <strong style={{ fontSize: 12, color: "var(--ink)" }}>{c.author}</strong>
              <span style={{ fontSize: 11, color: "var(--ink-4)" }}>{c.time}</span>
              <button
                type="button"
                onClick={() => {
                  const removed = comments[i]
                  const updated = comments.filter((_, idx) => idx !== i)
                  persist({ comments: updated })
                  if (removed.id) {
                    import("../../lib/api").then(({ ticketDataApi }) => {
                      ticketDataApi.removeComment(ticket.id, removed.id!).catch(() => {})
                    })
                  }
                }}
                style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", color: "var(--ink-4)", fontSize: 13, padding: 0, lineHeight: 1 }}
                title="Delete"
              >×</button>
            </div>
            <div style={{ fontSize: 13, color: "var(--ink)", lineHeight: 1.5 }}>{c.text}</div>
          </div>
        ))}
        <div className="tkt-comment-composer">
          <div className="tkt-comment-avatar" style={{ background: "var(--accent-muted)", color: "var(--accent-ink)" }}>
            You
          </div>
          <div className="tkt-comment-input-wrap">
            <textarea
              className="tkt-comment-input"
              placeholder="Add a comment…"
              rows={1}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && comment.trim()) {
                  e.preventDefault()
                  const body = comment.trim()
                  const c = { author: "You", text: body, time: new Date().toLocaleString() }
                  persist({ comments: [...comments, c] })
                  setComment("")
                  showToast("Comment added", "Saved to this ticket.")
                  import("../../lib/api").then(({ ticketDataApi }) => {
                    ticketDataApi.addComment(ticket.id, "You", body).catch(() => {})
                  })
                }
              }}
              onInput={(e) => {
                const el = e.currentTarget
                el.style.height = "auto"
                el.style.height = `${Math.min(el.scrollHeight, 120)}px`
              }}
            />
            {comment.trim().length > 0 && (
              <button
                type="button"
                className="tkt-comment-send"
                onClick={() => {
                  const body = comment.trim()
                  const c = { author: "You", text: body, time: new Date().toLocaleString() }
                  persist({ comments: [...comments, c] })
                  setComment("")
                  showToast("Comment added", "Saved to this ticket.")
                  import("../../lib/api").then(({ ticketDataApi }) => {
                    ticketDataApi.addComment(ticket.id, "You", body).catch(() => {})
                  })
                }}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="tkt-detail-footer">
        <span>{MOCK_TICKETS.length} tickets generated — linked to PRD v0.3</span>
        <button
          type="button"
          className="tkt-gen-proto-btn"
          onClick={() => showToast("Generate prototype", "Switch to the PRD tab and use Generate prototype.")}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
          </svg>
          Generate prototype
        </button>
      </div>
    </div>
  )
}

function TicketsTab() {
  const { showToast } = useNavigation()
  const { content } = useContent()
  const hasPrd = !!content.prd
  const isClickUpConnected = content.connectedConnectorIds.includes("clickup")
  const [selectedTicket, setSelectedTicket] = useState<Ticket | null>(null)

  // ── ClickUp push state ────────────────────────────────────────────────
  type PushState =
    | { kind: "idle" }
    | { kind: "fetching-lists" }
    | { kind: "picking"; lists: ClickUpList[] }
    | { kind: "pushing"; listName: string }
    | { kind: "done"; created: number; errors: number }
    | { kind: "error"; message: string }
  const [pushState, setPushState] = useState<PushState>({ kind: "idle" })
  const [selectedListId, setSelectedListId] = useState<string>("")

  const handleClickUpPush = async () => {
    if (pushState.kind === "fetching-lists" || pushState.kind === "pushing") return
    // If we already have a list selected, push directly
    if (pushState.kind === "picking" && selectedListId) {
      const list = (pushState as { kind: "picking"; lists: ClickUpList[] }).lists.find(l => l.id === selectedListId)
      setPushState({ kind: "pushing", listName: list?.name ?? selectedListId })
      try {
        const tasks = MOCK_TICKETS.map(t => ({
          task_id: t.id,
          title: t.title,
          description: t.description,
          acceptance_criteria: t.acceptanceCriteria,
          priority: t.priority,
        }))
        const result = await ticketPushApi.pushToClickUp(selectedListId, tasks)
        setPushState({ kind: "done", created: result.created.length, errors: result.errors.length })
        if (result.errors.length > 0) {
          showToast("ClickUp sync partial", `${result.created.length} created, ${result.errors.length} failed.`)
        } else {
          showToast("Synced to ClickUp", `${result.created.length} tickets created successfully.`)
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Unknown error"
        setPushState({ kind: "error", message: msg })
        showToast("ClickUp sync failed", msg.slice(0, 120))
      }
      return
    }
    // Fetch available lists first
    setPushState({ kind: "fetching-lists" })
    try {
      const r = await ticketPushApi.listClickUpLists()
      if (r.lists.length === 0) {
        setPushState({ kind: "error", message: "No ClickUp lists found. Create a list in ClickUp first." })
        return
      }
      setSelectedListId(r.lists[0].id)
      setPushState({ kind: "picking", lists: r.lists })
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Unknown error"
      setPushState({ kind: "error", message: msg })
    }
  }

  if (selectedTicket) {
    return <TicketDetail ticket={selectedTicket} onBack={() => setSelectedTicket(null)} />
  }

  if (!hasPrd) {
    return (
      <div className="cpanel-empty">
        <IconSparkle size={20} />
        <p>Ticket creation — generate a PRD first, then tickets will be drafted from it.</p>
      </div>
    )
  }

  const prdTitle = content.prd?.title ?? "PRD"

  return (
    <div className="tkt-list-wrap">
      <div className="tkt-intro-box">
        <IconSparkle size={14} />
        <p>I've broken <em>{prdTitle}</em> into <strong>{MOCK_TICKETS.length} implementable tasks</strong> — scoped, prioritized, and assigned by expertise. Review or edit any before pushing to ClickUp.</p>
      </div>

      {/* ── ClickUp sync banner ── */}
      {isClickUpConnected && pushState.kind !== "done" && (
        <div style={{
          margin: "0 0 12px",
          padding: "10px 14px",
          borderRadius: 8,
          background: "#f0faf5",
          border: "1px solid #b2e0ca",
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexWrap: "wrap",
        }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#179463" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ flexShrink: 0 }}>
            <polyline points="20 6 9 17 4 12" />
          </svg>
          <span style={{ fontSize: 12.5, color: "#0E6E49", flex: 1 }}>
            {pushState.kind === "idle" && "ClickUp connected — push all tickets to your workspace."}
            {pushState.kind === "fetching-lists" && "Fetching ClickUp lists…"}
            {pushState.kind === "pushing" && `Pushing to "${pushState.listName}"…`}
            {pushState.kind === "error" && <span style={{ color: "#C13838" }}>{pushState.message}</span>}
            {pushState.kind === "picking" && (
              <select
                value={selectedListId}
                onChange={e => setSelectedListId(e.target.value)}
                style={{ fontSize: 12, padding: "3px 6px", borderRadius: 5, border: "1px solid #b2e0ca", background: "#fff", marginRight: 6 }}
              >
                {(pushState as { kind: "picking"; lists: ClickUpList[] }).lists.map(l => (
                  <option key={l.id} value={l.id}>{l.folder ? `${l.folder} / ` : ""}{l.name}</option>
                ))}
              </select>
            )}
          </span>
          <button
            type="button"
            onClick={handleClickUpPush}
            disabled={pushState.kind === "fetching-lists" || pushState.kind === "pushing"}
            style={{
              fontSize: 12, fontWeight: 600, padding: "5px 14px", borderRadius: 6,
              background: pushState.kind === "fetching-lists" || pushState.kind === "pushing" ? "#ccc" : "#179463",
              color: "#fff", border: "none", cursor: pushState.kind === "fetching-lists" || pushState.kind === "pushing" ? "not-allowed" : "pointer",
              flexShrink: 0,
            }}
          >
            {pushState.kind === "fetching-lists" ? "Loading…"
              : pushState.kind === "pushing" ? "Pushing…"
              : pushState.kind === "picking" ? "Push to ClickUp"
              : pushState.kind === "error" ? "Retry"
              : "Sync to ClickUp"}
          </button>
        </div>
      )}

      {/* ClickUp done banner */}
      {pushState.kind === "done" && (
        <div style={{
          margin: "0 0 12px", padding: "10px 14px", borderRadius: 8,
          background: "#f0faf5", border: "1px solid #b2e0ca",
          display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "#0E6E49",
        }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#179463" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <polyline points="20 6 9 17 4 12" />
          </svg>
          {pushState.created} ticket{pushState.created !== 1 ? "s" : ""} created in ClickUp
          {pushState.errors > 0 && <span style={{ color: "#C13838", marginLeft: 4 }}>· {pushState.errors} failed</span>}
        </div>
      )}

      <div className="tkt-list-header">
        <span className="tkt-list-title">Tickets from <em>{prdTitle}</em></span>
        <span className="tkt-list-meta">{MOCK_TICKETS.length} tasks · click any to edit · linked to PRD</span>
      </div>

      <div className="tkt-list">
        {MOCK_TICKETS.map((t) => (
          <TicketRow key={t.id} ticket={t} onClick={() => setSelectedTicket(t)} />
        ))}
      </div>

      <div className="tkt-list-foot">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
        <span>Click a chunk to edit it as a full ticket.{!isClickUpConnected && " Connect ClickUp in Settings to push tickets."}</span>
      </div>
    </div>
  )
}

function AskIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
      <path d="M12 8v4M12 15h0" strokeWidth="2.4" />
    </svg>
  )
}
