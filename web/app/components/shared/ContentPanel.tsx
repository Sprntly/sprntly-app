"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { EvidenceSections } from "./EvidenceSections"
import { EmptyPane } from "./EmptyPane"
import { IconClose, IconSparkle } from "./app-icons"
import { runEvidenceGeneration } from "../../lib/runEvidenceGeneration"
import { runPrdGeneration } from "../../lib/runPrdGeneration"
import { PrdPanelContent } from "./PrdPanelContent"
import { IconMicroscope, IconFileText, IconTicket, IconDeviceFloppy, IconShare } from "@tabler/icons-react"

const TABS = [
  { icon: <IconMicroscope size={11.5} />, id: "evidence", label: "Evidence" },
  { icon: <IconFileText size={11.5}/> , id: "prd", label: "PRD" },
  { icon: <IconTicket size={11.5}/> , id: "tickets", label: "Tickets" },
] as const

// Content-panel width is persisted across opens and shared with the main column
// via the --cpanel-width custom property on <html>. The panel never exceeds 60%
// of the viewport; the lower bound keeps the document readable — wide enough for
// the 3-up "Impact at a glance" tiles and prose to breathe rather than be crushed.
const CPANEL_WIDTH_KEY = "sprntly-cpanel-width"
const CPANEL_WIDTH_MIN = 650
const CPANEL_MAX_VW = 0.6

/** Clamp a pixel width to [MIN, 60% of viewport]. */
function clampCpanelWidth(px: number) {
  const max = Math.round(window.innerWidth * CPANEL_MAX_VW)
  return Math.min(max, Math.max(CPANEL_WIDTH_MIN, Math.round(px)))
}

export function ContentPanel() {
  const { contentPanelTab, openContentPanel, closeContentPanel } = useNavigation()

  // Live width in px; null means "use the CSS default (60vw)" until the user
  // has resized at least once.
  const widthRef = useRef<number | null>(null)

  // Apply the saved width on open, re-clamp on viewport resize (so it can never
  // exceed 60%), and clear the override when the panel closes.
  useEffect(() => {
    if (!contentPanelTab) return
    const root = document.documentElement

    const saved = Number(window.localStorage.getItem(CPANEL_WIDTH_KEY))
    widthRef.current = Number.isFinite(saved) && saved > 0 ? saved : null

    const apply = () => {
      // On phones the CSS takes over (full-width sheet) — don't fight it.
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

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    if (window.innerWidth <= 768) return
    e.preventDefault()
    const root = document.documentElement
    const startX = e.clientX
    const startW = widthRef.current ?? Math.round(window.innerWidth * CPANEL_MAX_VW)
    root.classList.add("cpanel-resizing")

    const onMove = (ev: MouseEvent) => {
      // Handle sits on the panel's left edge; dragging left widens the panel.
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
  const { detail, evidence } = content

  const [evidenceState, setEvidenceState] = useState<
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "error"; message: string }
  >({ kind: detail?.meta && !evidence ? "loading" : "idle" })
  const [generatingPrd, setGeneratingPrd] = useState(false)
  const loadedKeyRef = useRef<string | null>(null)

  useEffect(() => {
    if (!detail?.meta) return
    const key = `${detail.meta.briefId}:${detail.meta.insightIndex}`
    if (loadedKeyRef.current === key && evidence) return
    let cancelled = false
    setEvidenceState({ kind: "loading" })
    if (loadedKeyRef.current !== key) setContent({ evidence: null })
    loadedKeyRef.current = key
    runEvidenceGeneration(detail.meta)
      .then((result) => {
        if (cancelled) return
        if (!result.ok) { setEvidenceState({ kind: "error", message: result.message }); return }
        setContent({ evidence: result.evidence })
        setEvidenceState({ kind: "idle" })
      })
      .catch((e: unknown) => {
        if (cancelled) return
        const msg = e instanceof Error ? e.message : String(e)
        setEvidenceState({ kind: "error", message: msg })
      })
    return () => { cancelled = true }
  }, [detail?.meta?.briefId, detail?.meta?.insightIndex, setContent])

  const handleGeneratePrd = async () => {
    if (!detail?.meta) {
      showToast("Can't generate PRD", "Open this evidence from the brief first.")
      return
    }
    setGeneratingPrd(true)
    try {
      const result = await runPrdGeneration(detail.meta)
      if (!result.ok) { showToast("PRD generation failed", result.message.slice(0, 200)); return }
      setContent({ prd: result.prd, prdMeta: detail.meta })
      openContentPanel("prd")
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      showToast("PRD generation failed", msg.slice(0, 200))
    } finally {
      setGeneratingPrd(false)
    }
  }

  if (!detail) {
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
        {/* Tag row: tags + BRIEF INSIGHT + ask button */}
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

        {evidence ? (
          <>
            <h1 className="ev-doc-title">{evidence.title}</h1>
            {evidence.metaLine && <div className="ev-doc-meta">{evidence.metaLine}</div>}
            <div className="ev-doc-sections">
              <EvidenceSections sections={evidence.sections} />
            </div>
          </>
        ) : evidenceState.kind === "loading" ? (
          <EmptyPane
            title="Generating evidence…"
            hint="Pulling the data-science slicing, infographics, qualitative signals, and hypothesis for this finding."
            placeholders={4}
          />
        ) : evidenceState.kind === "error" ? (
          <EmptyPane
            title="Couldn't load full evidence"
            hint={evidenceState.message}
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
  attachments: { label: string; sub: string }[]
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
        <div className="tkt-row-title">{ticket.title}</div>
        <div className="tkt-row-tags">
          <span className="tkt-tag tkt-tag--priority" style={{ color: PRIORITY_COLOR[ticket.priority], borderColor: `${PRIORITY_COLOR[ticket.priority]}33` }}>
            {ticket.priority}
          </span>
          {ticket.points > 0 && <span className="tkt-tag">{ticket.points} pts</span>}
          <span className="tkt-tag">{ticket.techTag}</span>
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

function TicketDetail({ ticket, onBack }: { ticket: Ticket; onBack: () => void }) {
  const { showToast } = useNavigation()
  const [status, setStatus] = useState<TicketStatus>("Backlog")
  const [priority, setPriority] = useState<TicketPriority>(ticket.priority)
  const [sprint, setSprint] = useState("Sprint 25")
  const [comment, setComment] = useState("")

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
        <div className="tkt-person-row">
          <div className="tkt-person-avatar" style={{ background: `${ticket.initialsColor}22`, color: ticket.initialsColor }}>
            {ticket.initials}
          </div>
          <div className="tkt-person-info">
            <div className="tkt-person-name">{ticket.personName}</div>
            <div className="tkt-person-role">
              {ticket.personEmail ? `${ticket.personEmail} · ` : ""}{ticket.personRole}
            </div>
          </div>
          <button type="button" className="tkt-reassign-btn" onClick={() => showToast("Reassign", "Reassignment isn't wired up yet.")}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <polyline points="17 1 21 5 17 9" /><path d="M3 11V9a4 4 0 0 1 4-4h14" />
              <polyline points="7 23 3 19 7 15" /><path d="M21 13v2a4 4 0 0 1-4 4H3" />
            </svg>
            Reassign
          </button>
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
        <p className="tkt-detail-desc">{ticket.description}</p>
        <div className="tkt-detail-criteria-label">Acceptance criteria:</div>
        <ul className="tkt-detail-criteria">
          {ticket.acceptanceCriteria.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      </div>

      {/* Attachments */}
      <div className="tkt-detail-section">
        <div className="tkt-detail-section-label">ATTACHMENTS · {ticket.attachments.length}</div>
        <div className="tkt-attachments">
          {ticket.attachments.map((a, i) => (
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
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--ink-4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ flexShrink: 0 }}>
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><polyline points="15 3 21 3 21 9" /><line x1="10" y1="14" x2="21" y2="3" />
              </svg>
            </div>
          ))}
        </div>
        <button type="button" className="tkt-attach-btn" onClick={() => showToast("Attach", "File attachments aren't wired up yet.")}>
          + Attach a file or paste a link
        </button>
      </div>

      {/* Comments */}
      <div className="tkt-detail-section">
        <div className="tkt-detail-section-label">COMMENTS</div>
        <div className="tkt-comments-empty">No comments yet — be the first to add context.</div>
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
                  showToast("Comment added", "Comments aren't persisted yet — coming soon.")
                  setComment("")
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
  const [selectedTicket, setSelectedTicket] = useState<Ticket | null>(null)

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

  return (
    <div className="tkt-list-wrap">
      <div className="tkt-intro-box">
        <IconSparkle size={14} />
        <p>I've broken PRD v0.3 into <strong>{MOCK_TICKETS.length} implementable tasks</strong> and drafted a ticket for each — scoped, prioritized, and assigned by expertise. Review or edit any before sending to Jira or Claude Code.</p>
      </div>

      <div className="tkt-list-header">
        <span className="tkt-list-title">Tickets from <em>PRD v0.3</em></span>
        <span className="tkt-list-meta">{MOCK_TICKETS.length} tasks · click any to open the ticket · linked to PRD</span>
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
        <span>Click a chunk to edit it as a full ticket. Or say <em>"send all to Jira"</em> in chat.</span>
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
