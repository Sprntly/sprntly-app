"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useCompany } from "../../context/CompanyContext"
import { ApiError, askApi, briefApi, type AskResponse } from "../../lib/api"
import { runPrdGeneration } from "../../lib/runPrdGeneration"
import { runEvidenceGeneration } from "../../lib/runEvidenceGeneration"
import { usePipelineStatus } from "../../lib/usePipelineStatus"
import type {
  BriefV2CompactFinding,
  BriefV2HeroFinding,
  BriefV2InlineChart,
  BriefV2State,
} from "../../lib/brief-v2-adapter"
import { AssistantThinkingSkeleton } from "./AssistantThinkingSkeleton"
import { AskReplyBody } from "./AskReplyBody"
import { IconClose, IconSendUp, IconSparkle } from "./app-icons"
import { IconPlug } from "@tabler/icons-react"

type Finding = BriefV2HeroFinding | BriefV2CompactFinding

// ── Turn model ─────────────────────────────────────────────────────────────
type AgentAction = "prd" | "evidence" | "tickets" | "prototype"
type Persona = "ds" | "pm"

interface ChatTurn {
  id: string
  role: "user" | "agent"
  text?: string
  time?: string
  persona?: Persona
  status?: string
  state?: "thinking" | "done" | "error"
  reply?: AskResponse | null
  message?: string | null
  error?: string | null
  actions?: AgentAction[]
  fresh?: boolean
}

const STORAGE_PREFIX = "sprntly_brief_chat_"
const COMPOSER_MAX_PX = 200

function uid(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `t-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
}
function nowTime(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
}

const isPrdCommand = (q: string) => /\b(generate|create|write|draft|make)\b.*\bprd\b/i.test(q)
const isPrototypeCommand = (q: string) =>
  /\b(generate|create|make|build|spin\s*up)\b.*\b(prototype|proto|mock\s*up|mockup)\b/i.test(q)
const isTicketsCommand = (q: string) =>
  /\b(create|generate|make|draft|break)\b.*\btickets?\b/i.test(q)

// Short headline for the greeting (drop everything after the first em/en dash).
function shortTitle(title: string): string {
  const t = title.split(/\s+[—–-]\s+/)[0].trim()
  return t.length > 64 ? `${t.slice(0, 61)}…` : t
}
function humanList(items: string[]): string {
  if (items.length === 0) return ""
  if (items.length === 1) return items[0]
  if (items.length === 2) return `${items[0]} and ${items[1]}`
  return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`
}
function buildGreeting(v2: BriefV2State | null, firstName: string | null): string {
  const who = firstName ? `, ${firstName}` : ""
  if (!v2 || (!v2.hero && v2.supporting.length === 0)) {
    return `Good day${who} — I don't see a brief for this week yet. Run the market-intelligence pipeline at the top of the page and I'll lay out the findings here, then help you turn any of them into a PRD, tickets, or a prototype.`
  }
  const findings = [v2.hero, ...v2.supporting].filter(Boolean) as Finding[]
  const list = humanList(findings.map((f) => shortTitle(f.title)))
  const n = findings.length
  return `Good day${who} — here's this week's brief. I spotted ${n} thing${
    n !== 1 ? "s" : ""
  } worth your attention: ${list}.`
}

function weekLabel(weekOf: string | null): string {
  if (!weekOf) return ""
  if (/week/i.test(weekOf)) return weekOf
  // Bare ISO date → "Week of Jun 10"
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(weekOf)
  if (m) {
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
    return `Week of ${d.toLocaleDateString([], { month: "short", day: "numeric" })}`
  }
  return weekOf
}

function briefTitle(weekOf: string | null): string {
  if (!weekOf) return "Weekly Brief"
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(weekOf)
  if (m) {
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
    return `${d.toLocaleDateString([], { weekday: "long" })} Brief`
  }
  return "Weekly Brief"
}

function parseAskError(e: unknown): string {
  const detail =
    e instanceof ApiError && e.body && typeof e.body === "object" && "detail" in e.body
      ? (e.body as { detail: unknown }).detail
      : null
  const detailStr =
    typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail
            .map((x) =>
              typeof x === "object" && x && "msg" in x ? String((x as { msg: string }).msg) : String(x),
            )
            .join(" · ")
        : null
  return e instanceof ApiError
    ? detailStr || e.message
    : e instanceof Error
      ? e.message
      : "Something went wrong"
}

// ── Composer / header affordance icons ───────────────────────────────────────
function IconMic({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </svg>
  )
}
function IconPaperclip({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 11.5l-8.6 8.6a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8" />
    </svg>
  )
}
function IconAt({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="12" r="4" />
      <path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-3.9 7.9" />
    </svg>
  )
}
function IconMoreDots({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <circle cx="5" cy="12" r="1.7" />
      <circle cx="12" cy="12" r="1.7" />
      <circle cx="19" cy="12" r="1.7" />
    </svg>
  )
}

// ── Icons used inside the finding card ───────────────────────────────────────
function IconFileText({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  )
}
function IconChevronRight({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="9 18 15 12 9 6" />
    </svg>
  )
}
function IconCode({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </svg>
  )
}
function IconTerminalPrompt({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="4 17 10 11 4 5" />
      <line x1="12" y1="19" x2="20" y2="19" />
    </svg>
  )
}
function IconTicket({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 9a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v1a2 2 0 0 0 0 4v1a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-1a2 2 0 0 0 0-4z" />
      <path d="M13 7v10" />
    </svg>
  )
}
function IconSearch({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  )
}

// ── Mini inline chart + prototype preview (reference card chrome) ─────────────
// First signed number found in a string ("70% handoff threshold" → 70). Used
// to place the dashed reference line on the same domain as the bars.
function firstNumber(text?: string): number | null {
  if (!text) return null
  const m = String(text).replace(/,/g, "").match(/-?\d+(\.\d+)?/)
  return m ? Number(m[0]) : null
}

// Compact 2–3 char axis tick from a bar label ("Riverside General" → "RG").
function shortBarLabel(label: string): string {
  const t = (label || "").trim()
  if (!t) return "—"
  const words = t.split(/[\s_-]+/).filter(Boolean)
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase()
  return t.slice(0, 3).toUpperCase()
}

// Reduce a metric value to its numeric core for the large KPI tile — drops
// appended comparisons / trailing words ("76% (vs 93.5% Enterprise)" → "76%",
// "12 (most recent customer)" → "12") so the stat row stays clean. Values with
// no number fall through unchanged (rendered small by the caller).
function compactMetricValue(raw: string): string {
  const s = (raw || "").trim().split(/[(（[]/)[0].trim()
  const m = s.match(/[-+]?[$€£]?\s?\d[\d,.]*\s?(?:%|[kKmMbB]\b|x|×)?/)
  return m ? m[0].replace(/\s+/g, "").trim() : s
}

// A short prototype-style name for the preview caption (drop trailing clauses).
function previewName(finding: Finding): string {
  const t = finding.title.split(/\s+[—–:]\s+/)[0].trim()
  return t.length > 30 ? `${t.slice(0, 28)}…` : t
}

const num = (v: number | string) => (typeof v === "number" ? v : Number(v) || 0)

// Compact ring / donut — share-of-whole or progress (card 2's "3/4 answered").
// Shows the first slice's share of the total; the centre reads as a "3/4"
// ratio when the values are small whole numbers, else a percentage.
function FindingMiniRing({ chart }: { chart: BriefV2InlineChart }) {
  const data = chart.data.slice(0, 6)
  if (data.length === 0) return null
  const nums = data.map((d) => num(d.value))
  const total = nums.reduce((a, b) => a + b, 0) || 1
  const first = nums[0] ?? 0
  const pct = Math.max(0, Math.min(1, first / total))
  const allWhole = nums.every((n) => Number.isInteger(n)) && total <= 20
  const center = data.length >= 2 && allWhole ? `${first}/${Math.round(total)}` : `${Math.round(pct * 100)}%`
  const label = (data[0]?.label || chart.title || "").trim()
  const r = 21
  const circ = 2 * Math.PI * r
  return (
    <figure className="fc-mc fc-mc--ring" aria-label={chart.title || "Finding ratio chart"}>
      <div className="fc-ring" title={chart.title || `${center} ${label}`}>
        <svg viewBox="0 0 54 54" width="54" height="54" aria-hidden>
          <circle cx="27" cy="27" r={r} fill="none" stroke="var(--surface-3)" strokeWidth="6" />
          <circle
            cx="27"
            cy="27"
            r={r}
            fill="none"
            stroke="var(--fc-ring-accent, #C68A1E)"
            strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray={`${(pct * circ).toFixed(1)} ${circ.toFixed(1)}`}
            transform="rotate(-90 27 27)"
          />
          <text x="27" y="30" textAnchor="middle" className="fc-ring-val">{center}</text>
        </svg>
        {label ? <span className="fc-ring-label">{label.slice(0, 12).toUpperCase()}</span> : null}
      </div>
    </figure>
  )
}

// Vertical mini bar chart with a dashed reference line — echoes the
// "70% threshold" mini-graph in the reference finding card. Bars below the
// line read red, above read green.
function FindingMiniBars({ chart }: { chart: BriefV2InlineChart }) {
  const data = chart.data.slice(0, 6)
  if (data.length === 0) return null
  const nums = data.map((d) => num(d.value))
  const dataMax = Math.max(...nums, 1)
  // Prefer an explicit threshold from the subtitle/title; otherwise fall back to
  // the mean so the reference line always represents a real statistic.
  const thr = firstNumber(chart.subtitle) ?? firstNumber(chart.title)
  const useThreshold = thr != null && thr > 0
  const mean = nums.reduce((a, b) => a + b, 0) / nums.length
  const lineVal = useThreshold ? (thr as number) : mean
  // Headroom above the tallest bar / reference line so the dashed line sits
  // clearly inside the plot — bars read as "below threshold", not pinned to top.
  const domainMax = Math.max(dataMax, lineVal, 1) * 1.18
  const lineLabel = useThreshold
    ? chart.subtitle || chart.title || `${thr}% threshold`
    : chart.subtitle || "avg"
  const cols = { gridTemplateColumns: `repeat(${data.length}, 1fr)` }
  return (
    <figure className="fc-mc" aria-label={chart.title || "Finding metric chart"}>
      <div className="fc-mc-plot">
        <div className="fc-mc-line" style={{ bottom: `${(lineVal / domainMax) * 100}%` }}>
          <span className="fc-mc-line-label">{lineLabel}</span>
        </div>
        <div className="fc-mc-bars" style={cols}>
          {data.map((d, i) => {
            const h = Math.max(10, (nums[i] / domainMax) * 100)
            const under = nums[i] < lineVal
            return (
              <div
                key={i}
                className={`fc-mc-bar ${under ? "fc-mc-bar--under" : "fc-mc-bar--over"}`}
                style={{ height: `${h}%` }}
                title={`${d.label}: ${d.value}`}
              />
            )
          })}
        </div>
      </div>
      <div className="fc-mc-ticks" style={cols}>
        {data.map((d, i) => (
          <span key={i} className="fc-mc-tick">{shortBarLabel(d.label)}</span>
        ))}
      </div>
    </figure>
  )
}

// Inline mini chart — routes share-of-whole / progress kinds to a ring and
// everything else to the threshold bar chart, so every card gets a graph.
function FindingMiniChart({ chart }: { chart: BriefV2InlineChart }) {
  const ringKind = chart.kind === "pie" || chart.kind === "donut" || chart.kind === "gauge"
  return ringKind ? <FindingMiniRing chart={chart} /> : <FindingMiniBars chart={chart} />
}

// Right-rail prototype preview — a browser-style mock + caption, mirroring the
// "First-Handoff Wizard" teaser in the reference. Clicking opens the prototype
// flow (which routes to the PRD-first message when no PRD exists yet).
function FindingPreview({ finding, onOpen }: { finding: Finding; onOpen: () => void }) {
  return (
    <button type="button" className="fc-preview" onClick={onOpen} title="Open prototype preview">
      <span className="fc-preview-mock" aria-hidden>
        <span className="fc-preview-mock-bar">
          <i /><i /><i />
        </span>
        <span className="fc-preview-mock-body">
          <span className="fc-preview-line fc-preview-line--a" />
          <span className="fc-preview-line fc-preview-line--b" />
          <span className="fc-preview-line fc-preview-line--c" />
          <span className="fc-preview-line fc-preview-line--d" />
        </span>
      </span>
      <span className="fc-preview-foot">
        <span className="fc-preview-title">
          <span className="fc-preview-glyph" aria-hidden>{">_"}</span>
          {previewName(finding)}
        </span>
        <span className="fc-preview-sub">Prototype preview · open design</span>
      </span>
    </button>
  )
}

// ── Finding card — matches reference layout ───────────────────────────────────
function BriefFindingCard({
  finding,
  busy,
  generating,
  onAsk,
  onViewEvidence,
  onGeneratePrd,
  onDismiss,
  onPreview,
}: {
  finding: Finding
  busy: boolean
  generating: boolean
  onAsk: () => void
  onViewEvidence: () => void
  onGeneratePrd: () => void
  onDismiss: () => void
  onPreview: () => void
}) {
  const accent = finding.actionAccent
  const pct = Math.round((finding.confidence ?? 0) * 100)
  const category = finding.category || finding.actionLabel
  const priority = finding.priority || "P0"
  const statTiles = finding.statTiles || []
  // Every finding carries an inline chart hint (hero and supporting alike).
  const chart = finding.chart

  return (
    <article className={`fc fc--${accent}`}>
      {/* Top row: category · priority badge + confidence + icons */}
      <div className="fc-top">
        <span className={`fc-pill fc-pill--${accent}`}>
          <span className="fc-dot" aria-hidden />
          {category} · {priority}
        </span>
        <div className="fc-top-right">
          <span className="fc-conf" title="Model confidence">
            <span className="fc-conf-dot" aria-hidden />
            {pct}%
          </span>
          <button type="button" className="fc-iconbtn" title="Ask about this finding" aria-label="Ask about this finding" onClick={onAsk}>
            <IconSparkle size={13} />
          </button>
          <button type="button" className="fc-iconbtn" title="Dismiss" aria-label="Dismiss finding" onClick={onDismiss}>
            <IconClose size={13} />
          </button>
        </div>
      </div>

      {/* Two-column body: main content (chart + KPIs + copy) and a prototype preview */}
      <div className="fc-grid">
        <div className="fc-col-main">
          {/* Title — sans-serif bold */}
          <h3 className="fc-title">{finding.title}</h3>

          {/* Stats row: mini chart (hero only) followed by KPI tiles */}
          {chart || statTiles.length > 0 ? (
            <div className="fc-stats-row">
              {chart ? <FindingMiniChart chart={chart} /> : null}
              <div className="fc-stats-kpi">
                {statTiles.map((tile, i) => {
                  const compact = compactMetricValue(tile.value)
                  const numeric = /\d/.test(compact)
                  return (
                    <div key={i} className={`fc-stat fc-stat--${tile.tone}`}>
                      <span
                        className={`fc-stat-value${numeric ? "" : " fc-stat-value--text"}`}
                        title={tile.value}
                      >
                        {compact}
                      </span>
                      {tile.label ? <span className="fc-stat-label">{tile.label}</span> : null}
                    </div>
                  )
                })}
              </div>
            </div>
          ) : null}

          {/* Body — rendered as markdown so LLM-supplied **bold** shows correctly */}
          {finding.body ? (
            <div className="fc-body fc-body--md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{finding.body}</ReactMarkdown>
            </div>
          ) : null}

          {/* Action buttons */}
          <div className="fc-actions">
            <button
              type="button"
              className={`fc-btn-prd fc-btn-prd--${accent}`}
              onClick={onGeneratePrd}
              disabled={busy}
            >
              <IconFileText size={14} />
              {generating ? "Generating…" : "View PRD"}
            </button>
            <button type="button" className="fc-btn-secondary" onClick={onPreview}>
              <IconTerminalPrompt size={13} />
              View prototype
            </button>
            <button type="button" className="fc-btn-secondary" onClick={onViewEvidence}>
              <IconSearch size={13} />
              View evidence
            </button>
          </div>
        </div>

        {/* Right rail — prototype preview with title */}
        <FindingPreview finding={finding} onOpen={onPreview} />
      </div>
    </article>
  )
}

// ── Suggested-actions state machine ──────────────────────────────────────────
// The chip stack above the composer offers the most useful next steps, and the
// set advances as the user acts: explore → prd → prototype / tickets / coding.
// Generating a PRD already lives on each finding card, so the composer's
// suggestions skip it and start at the downstream flow: create ticket → coding.
type SuggestStage = "prd" | "tickets" | "prototype" | "coding"
type SuggestKind = "create-ticket" | "view-prototype" | "view-prd" | "coding"

interface SuggestSpec {
  kind: SuggestKind
  label: string
  icon: "code" | "terminal" | "file" | "ticket"
  primary?: boolean
}

// Each stage offers three next-step chips; clicking one advances the stage so
// the set updates (mirrors the reference: ready → tickets → coding flow).
const SUGGEST_STAGES: Record<SuggestStage, SuggestSpec[]> = {
  // Default / reference screen 1.
  prd: [
    { kind: "create-ticket", label: "Create ticket", icon: "ticket", primary: true },
    { kind: "view-prototype", label: "View prototype", icon: "terminal" },
    { kind: "coding", label: "Send to coding agent", icon: "code" },
  ],
  // Reference screen 2 — after Create ticket.
  tickets: [
    { kind: "coding", label: "Send to coding agent", icon: "code", primary: true },
    { kind: "view-prototype", label: "View prototype", icon: "terminal" },
    { kind: "view-prd", label: "View PRD", icon: "file" },
  ],
  prototype: [
    { kind: "coding", label: "Send to coding agent", icon: "code", primary: true },
    { kind: "create-ticket", label: "Create ticket", icon: "ticket" },
    { kind: "view-prd", label: "View PRD", icon: "file" },
  ],
  coding: [
    { kind: "view-prototype", label: "View prototype", icon: "terminal", primary: true },
    { kind: "create-ticket", label: "Create ticket", icon: "ticket" },
    { kind: "view-prd", label: "View PRD", icon: "file" },
  ],
}

// Stage to advance to after a kind is clicked (null → keep the current stage).
const SUGGEST_NEXT: Record<SuggestKind, SuggestStage | null> = {
  "create-ticket": "tickets",
  "view-prototype": "prototype",
  "view-prd": null,
  coding: "coding",
}

// The AgentAction a kind dispatches (coding is handled separately).
const SUGGEST_ACTION: Record<Exclude<SuggestKind, "coding">, AgentAction> = {
  "create-ticket": "tickets",
  "view-prototype": "prototype",
  "view-prd": "prd",
}

function SuggestIcon({ name }: { name: SuggestSpec["icon"] }) {
  if (name === "code") return <IconCode size={14} />
  if (name === "terminal") return <IconTerminalPrompt size={14} />
  if (name === "file") return <IconFileText size={14} />
  return <IconTicket size={14} />
}

export function BriefChat() {
  const { aiBarValue, setAIBarValue, openContentPanel, showToast, goTo } = useNavigation()
  const { content, setContent } = useContent()
  const { activeCompany } = useCompany()
  const pipeline = usePipelineStatus(activeCompany)

  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  const [cardBusyKey, setCardBusyKey] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const [suggestStage, setSuggestStage] = useState<SuggestStage>("prd")
  const busyRef = useRef(false)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const mountedRef = useRef(true)
  const loadedKeyRef = useRef<string | null>(null)
  const skipPersistRef = useRef(false)

  const greetTime = useMemo(() => nowTime(), [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // ── Restore saved thread on company change ────────────────────────────────
  useEffect(() => {
    if (!activeCompany) return
    const key = `${STORAGE_PREFIX}${activeCompany}`
    let restored: ChatTurn[] = []
    try {
      const raw = localStorage.getItem(key)
      if (raw) {
        const parsed = JSON.parse(raw)
        if (Array.isArray(parsed)) restored = parsed as ChatTurn[]
      }
    } catch {
      /* ignore corrupt storage */
    }
    skipPersistRef.current = true
    loadedKeyRef.current = key
    setTurns(restored)
    setDismissed(new Set())
  }, [activeCompany])

  // ── Persist terminal turns (skip the write a fresh restore triggers) ──────
  useEffect(() => {
    const key = loadedKeyRef.current
    if (!key) return
    if (skipPersistRef.current) {
      skipPersistRef.current = false
      return
    }
    try {
      const persistable = turns
        .filter((t) => t.state !== "thinking")
        .map(({ fresh: _fresh, ...rest }) => rest)
      localStorage.setItem(key, JSON.stringify(persistable))
    } catch {
      /* best effort */
    }
  }, [turns])

  const scrollToEnd = useCallback(() => {
    requestAnimationFrame(() => endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }))
  }, [])

  const focusComposer = useCallback(() => {
    requestAnimationFrame(() => {
      const ta = composerRef.current
      if (ta) {
        ta.style.height = "auto"
        ta.style.height = `${Math.min(ta.scrollHeight, COMPOSER_MAX_PX)}px`
        ta.focus()
        ta.scrollIntoView({ behavior: "smooth", block: "center" })
      }
    })
  }, [])

  // ── Card "Ask" hand-off: BriefScreen / cards set aiBarValue → prefill ─────
  useEffect(() => {
    if (!aiBarValue) return
    setDraft((d) => (d.trim() ? `${d}\n\n${aiBarValue}` : aiBarValue))
    setAIBarValue("")
    focusComposer()
  }, [aiBarValue, setAIBarValue, focusComposer])

  const appendUser = useCallback((q: string) => {
    setTurns((t) => [...t, { id: uid(), role: "user", text: q, time: nowTime() }])
  }, [])

  // ── Composer agent flows (mirror the old AIBar command logic) ─────────────
  const prdFlow = useCallback(async () => {
    const aId = uid()
    setTurns((t) => [...t, { id: aId, role: "agent", persona: "pm", status: "generating PRD…", state: "thinking" }])
    scrollToEnd()
    const fail = (error: string) => setTurns((t) => t.map((x) => (x.id === aId ? { ...x, state: "error", error } : x)))
    try {
      const brief = await briefApi.current(activeCompany)
      const insights = brief.insights || []
      if (!insights.length) {
        fail("No brief insights available yet. Run the pipeline to refresh this week's brief first.")
        return
      }
      const insight = insights[0]
      const result = await runPrdGeneration({ briefId: brief.id, insightIndex: 0 })
      if (!result.ok) {
        fail(result.message)
        return
      }
      setContent({ prd: result.prd, prdMeta: { briefId: brief.id, insightIndex: 0 } })
      openContentPanel("prd")
      setTurns((t) =>
        t.map((x) =>
          x.id === aId
            ? {
                ...x,
                state: "done",
                status: "PRD draft ready",
                message: `Drafted the PRD from **${insight.title}**. Opened it on the right — six sections, fully editable, auto-saving.`,
                actions: ["prd", "tickets", "prototype"],
              }
            : x,
        ),
      )
    } catch (e) {
      fail(e instanceof Error ? e.message : "PRD generation failed")
    }
  }, [activeCompany, openContentPanel, scrollToEnd, setContent])

  const ticketsFlow = useCallback(() => {
    openContentPanel("tickets")
    const hasPrd = !!content.prd
    setTurns((t) => [
      ...t,
      {
        id: uid(),
        role: "agent",
        persona: "pm",
        status: hasPrd ? "tickets" : "needs a PRD",
        state: "done",
        message: hasPrd
          ? "Opened the **Tickets** tab on the right — I break the current PRD's requirements and acceptance criteria into tickets here."
          : "Tickets are drafted from a PRD. **Generate a PRD first** and I'll break it into tickets for you.",
        actions: hasPrd ? ["prd", "prototype"] : ["prd"],
      },
    ])
    scrollToEnd()
  }, [content.prd, openContentPanel, scrollToEnd])

  const prototypeFlow = useCallback(() => {
    if (content.prd) {
      goTo("prototype")
      return
    }
    setTurns((t) => [
      ...t,
      {
        id: uid(),
        role: "agent",
        persona: "pm",
        status: "needs a PRD",
        state: "done",
        message: "I build prototypes from an approved PRD. **Generate a PRD first**, then I can spin up a working prototype.",
        actions: ["prd"],
      },
    ])
    scrollToEnd()
  }, [content.prd, goTo, scrollToEnd])

  const evidenceFlow = useCallback(() => {
    openContentPanel("evidence")
  }, [openContentPanel])

  const plainAsk = useCallback(
    async (q: string) => {
      const aId = uid()
      setTurns((t) => [...t, { id: aId, role: "agent", persona: "ds", status: "thinking…", state: "thinking" }])
      scrollToEnd()
      try {
        const res = await askApi.ask(q, activeCompany)
        setTurns((t) => t.map((x) => (x.id === aId ? { ...x, state: "done", status: undefined, reply: res, fresh: true } : x)))
      } catch (e) {
        const msg = parseAskError(e)
        setTurns((t) => t.map((x) => (x.id === aId ? { ...x, state: "error", error: msg } : x)))
        showToast("Ask failed", msg.slice(0, 120))
      }
    },
    [activeCompany, scrollToEnd, showToast],
  )

  const runGate = useCallback(
    async (fn: () => void | Promise<void>) => {
      if (busyRef.current) return
      busyRef.current = true
      setBusy(true)
      try {
        await fn()
      } finally {
        if (mountedRef.current) {
          busyRef.current = false
          setBusy(false)
          scrollToEnd()
        }
      }
    },
    [scrollToEnd],
  )

  const submitAsk = useCallback(
    (raw: string) => {
      const q = raw.trim()
      if (q.length < 3) {
        showToast("Question too short", "Use at least 3 characters.")
        return
      }
      if (busyRef.current) return
      appendUser(q)
      setDraft("")
      if (composerRef.current) composerRef.current.style.height = "auto"
      void runGate(() => {
        if (isPrdCommand(q)) return prdFlow()
        if (isPrototypeCommand(q)) return prototypeFlow()
        if (isTicketsCommand(q)) return ticketsFlow()
        return plainAsk(q)
      })
    },
    [appendUser, plainAsk, prdFlow, prototypeFlow, runGate, showToast, ticketsFlow],
  )

  const onAction = useCallback(
    (a: AgentAction) => {
      void runGate(() => {
        if (a === "prd") return content.prd ? openContentPanel("prd") : prdFlow()
        if (a === "evidence") return evidenceFlow()
        if (a === "tickets") return ticketsFlow()
        if (a === "prototype") return prototypeFlow()
      })
    },
    [content.prd, evidenceFlow, openContentPanel, prdFlow, prototypeFlow, runGate, ticketsFlow],
  )

  // ── Suggested-actions: hand the implementation brief to a coding agent ─────
  const sendToCoding = useCallback(() => {
    if (!content.prd) {
      showToast("Generate a PRD first", "I hand the implementation brief to your coding agent once a PRD exists.")
      return
    }
    showToast("Sent to coding agent", "Handed the PRD's implementation brief to your coding agent.")
  }, [content.prd, showToast])

  // Active suggestion chips, each advancing the stage as the reference flow does.
  const suggestions = useMemo(
    () =>
      SUGGEST_STAGES[suggestStage].map((spec) => ({
        ...spec,
        onClick: () => {
          const next = SUGGEST_NEXT[spec.kind]
          if (next) setSuggestStage(next)
          if (spec.kind === "coding") sendToCoding()
          else onAction(SUGGEST_ACTION[spec.kind])
        },
      })),
    [suggestStage, onAction, sendToCoding],
  )

  // ── Per-card actions (replicate BriefScreen's evidence/PRD wiring) ────────
  const cardAsk = useCallback(
    (finding: Finding) => {
      const q = finding.askQuestion
      setDraft((d) => (d.trim() ? `${d}\n\n${q}` : q))
      focusComposer()
    },
    [focusComposer],
  )

  const cardViewEvidence = useCallback(
    (finding: Finding) => {
      const key = finding.detailKey
      const detail = key ? content.briefDetails?.[key] : null
      // Select the finding; the ContentPanel's EvidenceTab effect generates the
      // evidence from detail.meta (so we don't double-fire the generation here).
      if (detail) setContent({ detail, evidence: null })
      openContentPanel("evidence")
    },
    [content.briefDetails, openContentPanel, setContent],
  )

  const cardGeneratePrd = useCallback(
    async (finding: Finding) => {
      const key = finding.detailKey
      const detail = key ? content.briefDetails?.[key] : null
      const meta = detail?.meta
      if (!meta) {
        showToast("Can't generate PRD", "Open evidence from a finding with a linked brief first.")
        return
      }
      // Share the single-flight gate with the composer / agent-button flows so a
      // card PRD and a composer "generate PRD" can't race on content.prd, and a
      // second card can't start while one is in flight.
      if (busyRef.current) return
      busyRef.current = true
      setBusy(true)
      setCardBusyKey(key ?? null)
      try {
        const result = await runPrdGeneration(meta)
        if (!result.ok) {
          showToast("PRD generation failed", result.message.slice(0, 200))
          return
        }
        setContent({ prd: result.prd, prdMeta: meta })
        openContentPanel("prd")
      } catch (e) {
        showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
      } finally {
        busyRef.current = false
        if (mountedRef.current) {
          setBusy(false)
          setCardBusyKey(null)
        }
      }
    },
    [content.briefDetails, openContentPanel, setContent, showToast],
  )

  const cardDismiss = useCallback((finding: Finding) => {
    const key = finding.detailKey
    if (!key) return
    setDismissed((s) => {
      const next = new Set(s)
      next.add(key)
      return next
    })
  }, [])

  // Prototype-preview click → run the prototype flow (gated; routes to the
  // PRD-first message when no PRD exists yet).
  const cardPreview = useCallback(() => {
    void runGate(() => prototypeFlow())
  }, [prototypeFlow, runGate])

  // ── Composer handlers ─────────────────────────────────────────────────────
  const onComposerKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      if (!busy) submitAsk(draft)
    }
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === "m" || e.key === "M")) {
      e.preventDefault()
      showToast("Voice input", "Dictation isn't wired up yet — type your question for now.")
    }
  }
  const onComposerInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(e.target.value)
    const el = e.target
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, COMPOSER_MAX_PX)}px`
  }
  const insertSourceToken = () => {
    setDraft((d) => `${d}${d && !d.endsWith(" ") ? " " : ""}@`)
    requestAnimationFrame(() => composerRef.current?.focus())
  }

  // ── Derived render data ────────────────────────────────────────────────────
  const v2 = content.briefV2
  const firstName = content.userName ? content.userName.split(/\s+/)[0] : null
  const greeting = useMemo(() => buildGreeting(v2, firstName), [v2, firstName])
  const findings: Finding[] = useMemo(() => {
    if (!v2) return []
    return [v2.hero, ...v2.supporting].filter(Boolean) as Finding[]
  }, [v2])
  const visibleFindings = findings.filter((f) => !(f.detailKey && dismissed.has(f.detailKey)))

  const userInitials = content.userInitials ?? (content.userName ? content.userName.slice(0, 2).toUpperCase() : "You")
  const userName = content.userName ?? "You"
  const company = v2?.company ?? ""
  const week = weekLabel(v2?.weekOf ?? null)
  const heading = briefTitle(v2?.weekOf ?? null)
  const refreshing = (pipeline.runStatus as { status?: string } | null)?.status === "running"

  return (
    <section className="briefx" aria-label="Weekly brief">
      <header className="bh">
        <div className="bh-main">
          <h1 className="bh-title">{heading}</h1>
          <span className={`bh-live${refreshing ? " bh-live--refreshing" : ""}`}>
            <span className="bh-live-dot" aria-hidden />
            {refreshing ? "REFRESHING" : "LIVE"}
          </span>
          {week ? <span className="bh-sep">·</span> : null}
          {week ? <span className="bh-week">{week}</span> : null}
          {company ? <span className="bh-sep">·</span> : null}
          {company ? <span className="bh-company">{company}</span> : null}
        </div>
        <div className="bh-actions">
          <button
            type="button"
            className="bh-iconbtn"
            title="Connectors"
            aria-label="Open connectors"
            onClick={() => goTo("connectors")}
          >
            <IconPlug />
          </button>
          <button
            type="button"
            className="bh-iconbtn"
            title="More"
            aria-label="More options"
            onClick={() => showToast("More options", "No menu is wired up here yet.")}
          >
            <IconMoreDots />
          </button>
        </div>
      </header>

        <div className="bc-scroll">
          <div className="bc-thread">
            {/* PM coworker brief message — greeting + stacked finding cards */}
            <div className="bc-turn">
              <div className="bc-agent-head">
                <span className="bc-agent-mark">
                  <IconSparkle size={14} />
                </span>
                <span className="bc-agent-name">PM Agent</span>
                <span className="bc-agent-badge">
                  <IconSparkle size={10} />
                  PM COWORKER
                </span>
                <span className="bc-agent-status">Weekly brief · {greetTime}</span>
              </div>
              <div className="bc-agent-body">
                <p className="bc-greeting">{greeting}</p>
                {visibleFindings.length > 0 ? (
                  <div className="fc-stack">
                    {visibleFindings.map((f) => (
                      <BriefFindingCard
                        key={f.detailKey ?? `${f.tagType}-${f.title}`}
                        finding={f}
                        busy={busy}
                        generating={cardBusyKey === f.detailKey}
                        onAsk={() => cardAsk(f)}
                        onViewEvidence={() => cardViewEvidence(f)}
                        onGeneratePrd={() => cardGeneratePrd(f)}
                        onDismiss={() => cardDismiss(f)}
                        onPreview={cardPreview}
                      />
                    ))}
                  </div>
                ) : null}
                {v2?.sourcesLine ? (
                  <div className="fc-sources">
                    <span className="fc-sources-label">Sources this week</span>
                    <span>{v2.sourcesLine}</span>
                  </div>
                ) : null}
              </div>
            </div>

            {/* Chat back-and-forth */}
            {turns.map((turn) =>
              turn.role === "user" ? (
                <div key={turn.id} className="bc-turn">
                  <div className="bc-user-head">
                    <span className="bc-avatar">{userInitials}</span>
                    <span className="bc-user-name">{userName}</span>
                    {turn.time ? <span className="bc-time">{turn.time}</span> : null}
                  </div>
                  <div className="bc-user-bubble">{turn.text}</div>
                </div>
              ) : (
                <AgentTurn key={turn.id} turn={turn} hasPrd={!!content.prd} onAction={onAction} busy={busy} />
              ),
            )}
            <div ref={endRef} />
          </div>
        </div>

        <div className="bc-dock">
          {findings.length > 0 ? (
            <div className="bc-suggest">
              <div className="bc-suggest-list">
                {suggestions.map((s) => (
                  <button
                    key={s.kind}
                    type="button"
                    className={`bc-suggest-btn${s.primary ? " bc-suggest-btn--primary" : ""}`}
                    onClick={s.onClick}
                    disabled={busy}
                  >
                    <SuggestIcon name={s.icon} />
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          <div className="bc-composer">
            <textarea
              ref={composerRef}
              className="bc-composer-input"
              placeholder={'Ask anything, or try "generate PRD", "create tickets", "generate prototype"…'}
              rows={1}
              value={draft}
              onChange={onComposerInput}
              onKeyDown={onComposerKeyDown}
            />
            <div className="bc-composer-bar">
              <div className="bc-composer-tools">
                <button
                  type="button"
                  className="bc-tool"
                  onClick={() => showToast("Voice input", "Dictation isn't wired up yet — type your question for now.")}
                >
                  <IconMic /> Voice
                </button>
                <button type="button" className="bc-tool" onClick={() => showToast("Attach", "File attachments aren't wired up yet.")}>
                  <IconPaperclip /> Attach
                </button>
                <button type="button" className="bc-tool" onClick={insertSourceToken}>
                  <IconAt /> Source
                </button>
                <span className="bc-tool-kbd">
                  <kbd>⌘</kbd>
                  <kbd>/</kbd>
                </span>
              </div>
              <button
                type="button"
                className="bc-send"
                aria-label="Send"
                disabled={busy || draft.trim().length < 3}
                onClick={() => submitAsk(draft)}
              >
                <IconSendUp size={17} />
              </button>
            </div>
          </div>
          <div className="bc-hints">
            <span>
              <kbd>Enter</kbd> send
            </span>
            <span>·</span>
            <span>
              <kbd>Shift+Enter</kbd> newline
            </span>
            <span>·</span>
            <span>
              <kbd>⌘+Shift+M</kbd> voice
            </span>
          </div>
        </div>
    </section>
  )
}

// ── Agent turn (chat replies / command confirmations) ────────────────────────
const ACTION_LABEL: Record<AgentAction, string> = {
  prd: "Generate PRD",
  evidence: "View evidence",
  tickets: "Create tickets",
  prototype: "Generate prototype",
}

function AgentTurn({
  turn,
  hasPrd,
  onAction,
  busy,
}: {
  turn: ChatTurn
  hasPrd: boolean
  onAction: (a: AgentAction) => void
  busy: boolean
}) {
  const personaName = turn.persona === "pm" ? "PM Agent" : "DS Agent"
  const badge = turn.persona === "pm" ? "PM COWORKER" : "DS COWORKER"
  return (
    <div className="bc-turn">
      <div className="bc-agent-head">
        <span className="bc-agent-mark">
          <IconSparkle size={14} />
        </span>
        <span className="bc-agent-name">{personaName}</span>
        <span className="bc-agent-badge">
          <IconSparkle size={10} />
          {badge}
        </span>
        {turn.status ? <span className="bc-agent-status">{turn.status}</span> : null}
      </div>
      <div className="bc-agent-body">
        {turn.state === "thinking" ? (
          <AssistantThinkingSkeleton compact />
        ) : turn.state === "error" ? (
          <div className="bc-error">{turn.error}</div>
        ) : turn.reply ? (
          <AskReplyBody reply={turn.reply} animateIn={!!turn.fresh} simulateTyping={!!turn.fresh} omitCitations />
        ) : turn.message ? (
          <div className="ai-bar-reply-answer">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.message}</ReactMarkdown>
          </div>
        ) : null}
      </div>
      {turn.actions && turn.actions.length > 0 && turn.state !== "thinking" ? (
        <div className="bc-actions">
          {turn.actions.map((a) => {
            const label = a === "prd" && hasPrd ? "Open PRD" : ACTION_LABEL[a]
            const primary = a === "prd"
            return (
              <button
                key={a}
                type="button"
                className={`bc-action-btn${primary ? " bc-action-btn--primary" : ""}`}
                disabled={busy}
                onClick={() => onAction(a)}
              >
                {label}
              </button>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}
