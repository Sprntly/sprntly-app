"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useCompany } from "../../context/CompanyContext"
import { useWorkspace } from "../../context/WorkspaceContext"
import { ApiError, briefApi, type AskResponse } from "../../lib/api"
import { runAskGeneration, resumeAskGeneration, getPendingAsk } from "../../lib/runAskGeneration"
import { runPrdGeneration, loadPrdById, loadLatestPrd } from "../../lib/runPrdGeneration"
import { runEvidenceGeneration } from "../../lib/runEvidenceGeneration"
import { runMultiAgentGeneration } from "../../lib/runMultiAgentGeneration"
import { usePipelineStatus } from "../../lib/usePipelineStatus"
import { AGENT_NAME } from "../../lib/agent"
import type {
  BriefV2CompactFinding,
  BriefV2HeroFinding,
  BriefV2InlineChart,
  BriefV2State,
} from "../../lib/brief-v2-adapter"
import { AssistantThinkingSkeleton } from "./AssistantThinkingSkeleton"
import { AskReplyBody } from "./AskReplyBody"
import { IconClose, IconSendUp, IconSparkle, IconUndo } from "./app-icons"
import { IconPlug } from "@tabler/icons-react"
import { useBriefPrototypeMap } from "../design-agent/useBriefPrototypeMap"
import { prototypeStateForInsight } from "../design-agent/briefPrototypeMap.helpers"
import { GenerateModal } from "../design-agent/GenerateModal"
import { GenerationLoadingScreen } from "../design-agent/GenerationLoadingScreen"
import type { DesignAgentGenResult } from "../../lib/runDesignAgentGeneration"
import { prototypePath } from "../../lib/routes"
import { useRouter } from "next/navigation"

type Finding = BriefV2HeroFinding | BriefV2CompactFinding

// ── Turn model ─────────────────────────────────────────────────────────────
type AgentAction = "prd" | "evidence" | "tickets" | "prototype" | "multi-agent"
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
  /** When set (to this turn's id), an Ask job is in flight for this agent turn.
   *  Persisted so a remount can re-attach to the fire-and-forget poll. */
  askPending?: string
}

const STORAGE_PREFIX = "sprntly_brief_chat_"
// Dismissed finding cards persist (localStorage) per brief so a grey-out survives
// re-render and reload within the session. The brief V2 payload carries no id, so
// we namespace by company + week-of (the distinct brief identity) and store the
// set of dismissed `detailKey`s under it.
const DISMISS_PREFIX = "sprntly_brief_dismissed_"
const COMPOSER_MAX_PX = 200

function dismissKeyFor(company: string | null | undefined, weekOf: string | null | undefined): string | null {
  if (!company) return null
  return `${DISMISS_PREFIX}${company}::${weekOf ?? "current"}`
}

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

function buildGreeting(v2: BriefV2State | null, firstName: string | null): string {
  const who = firstName ? `, ${firstName}` : ""
  if (!v2 || (!v2.hero && v2.supporting.length === 0)) {
    // Distinguish "we received your data but it isn't connected-evidence-rich
    // enough yet" from a brand-new, no-data account. The backend sets
    // `insufficientEvidence` on the empty brief in the former case so we can
    // reassure the user their upload landed instead of telling them to "add a
    // first source". `_empty_reason` can carry internal jargon, so we only use
    // it when it's clearly a user-facing sentence; otherwise static copy.
    if (v2?.insufficientEvidence) {
      return `We've got your data${who} — but there isn't enough connected evidence yet to build your brief. Connect another source or add richer data, and your brief will fill in.`
    }
    return `Good day${who} — there isn't enough connected yet to generate a weekly brief. Please add more sources and connect them to us, and your brief will appear here.`
  }
  // Lead with a clean one-line intro and let the finding cards below carry the
  // titles — inlining the (Title-Cased) finding titles into this sentence read
  // as an awkward run-on.
  const n = [v2.hero, ...v2.supporting].filter(Boolean).length
  return `Good day${who} — here's this week's brief. I spotted ${n} thing${
    n !== 1 ? "s" : ""
  } worth your attention this week.`
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

// Data-driven fallback chart for insights whose payload carries no chart_hints.
// Rather than draw a hardcoded/placeholder shape, derive a real bar chart from
// the finding's own quantitative fields — the numeric KPI stat tiles (which come
// straight from `insight.metrics`). Only when there is genuinely no numeric
// signal do we return null so the card simply renders without a chart.
function chartFromStatTiles(finding: Finding): BriefV2InlineChart | null {
  const tiles = finding.statTiles || []
  const data = tiles
    .map((t) => ({ label: (t.label || "").trim(), value: firstNumber(t.value) }))
    .filter((d): d is { label: string; value: number } => d.value != null)
    .map((d) => ({ label: d.label || "—", value: d.value }))
  if (data.length === 0) return null
  return { kind: "bar", title: finding.metricHighlight || finding.title || "", data }
}

/** Pure: the primary finding-card CTA. When a PRD already exists for this
 *  insight the button becomes "View PRD" (opens the existing PRD); otherwise
 *  "Generate PRD" (runs the full system), reflecting in-flight as "Generating…".
 *  Extracted so the view-vs-generate decision is unit-testable. */
export function prdCtaState(
  insightState: { hasPrd: boolean; prdId: number | null } | null | undefined,
  generating: boolean,
): { label: string; isView: boolean } {
  if (insightState?.hasPrd && insightState.prdId != null) {
    return { label: "View PRD", isView: true }
  }
  return { label: generating ? "Generating…" : "Generate PRD", isView: false }
}

// ── Finding card — matches reference layout ───────────────────────────────────
function BriefFindingCard({
  finding,
  busy,
  generating,
  dismissed,
  onAsk,
  onGenerateAll,
  onViewPrd,
  onDismiss,
  onRestore,
  onPreview,
  insightState,
}: {
  finding: Finding
  busy: boolean
  generating: boolean
  dismissed: boolean
  onAsk: () => void
  onGenerateAll: () => void
  onViewPrd: () => void
  onDismiss: () => void
  onRestore: () => void
  onPreview: () => void
  insightState?: {
    hasPrd: boolean
    prdId: number | null
    prototypeReady: boolean
    previewImageUrl: string | null
    prdTitle: string | null
  } | null
}) {
  const accent = finding.actionAccent
  const category = finding.category || finding.actionLabel
  const priority = finding.priority || "P0"
  const statTiles = finding.statTiles || []
  // Real chart from the insight payload (chart_hints → BriefV2InlineChart). When
  // the insight ships no chart_hints, derive a data-driven bar chart from the
  // finding's numeric KPI tiles instead of a hardcoded placeholder.
  const chart = finding.chart ?? chartFromStatTiles(finding)

  // ── Dismissed (greyed) state ──────────────────────────────────────────────
  // Greys the card out in place — keeps the finding present (not deleted) and
  // hides the heavy detail/viz, exposing a "click to restore" affordance.
  // Clicking the card body (or the restore button) un-greys it.
  if (dismissed) {
    return (
      <article
        className={`fc fc--${accent} fc--dismissed`}
        role="button"
        tabIndex={0}
        title="Dismissed · click to restore"
        aria-label={`Dismissed finding: ${finding.title}. Click to restore.`}
        onClick={onRestore}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault()
            onRestore()
          }
        }}
      >
        <div className="fc-dismissed-row">
          <span className="fc-dismissed-title">{finding.title}</span>
          <button
            type="button"
            className="fc-iconbtn fc-restorebtn"
            title="Restore finding"
            aria-label="Restore finding"
            onClick={(e) => {
              e.stopPropagation()
              onRestore()
            }}
          >
            <IconUndo size={13} />
          </button>
        </div>
        <span className="fc-dismissed-hint">Dismissed · click to restore</span>
      </article>
    )
  }

  return (
    <article className={`fc fc--${accent}`}>
      {/* Top row: category · priority badge + icons */}
      <div className="fc-top">
        <span className={`fc-pill fc-pill--${accent}`}>
          <span className="fc-dot" aria-hidden />
          {category} · {priority}
        </span>
        <div className="fc-top-right">
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
            {(() => {
              const cta = prdCtaState(insightState, generating)
              return (
                <button
                  type="button"
                  className={`fc-btn-prd fc-btn-prd--${accent}`}
                  onClick={cta.isView ? onViewPrd : onGenerateAll}
                  // View is a cheap read — allowed while another job is busy;
                  // Generate is gated on `busy` as before.
                  disabled={busy && !cta.isView}
                  title={
                    cta.isView
                      ? "Open the existing PRD"
                      : "Generates the full system: PRD + Evidence + Technical Design + QA Test Cases + Risk Analysis + Traceability Matrix"
                  }
                >
                  <IconFileText size={14} />
                  {cta.label}
                </button>
              )
            })()}
            {/* Prototype option only when the fix can be visualized as a UI
                prototype (LLM `prototypeable` flag). Backend/data/pricing/ops
                findings have nothing to render, so we don't offer it. */}
            {finding.prototypeable ? (
              <button type="button" className="fc-btn-secondary" onClick={onPreview}>
                <IconTerminalPrompt size={13} />
                View prototype
              </button>
            ) : null}
          </div>
        </div>

        {/* The right-rail prototype preview thumbnail was removed: the design-agent
            screenshot capture serves the staged bundle as text/plain, so Chromium
            photographs the raw index.html SOURCE instead of the rendered page — the
            thumbnail showed HTML markup, not the prototype. Until that capture is
            fixed (design-agent screenshot.py), there is no reliable image to show,
            so we render nothing here. The full prototype is still reachable via the
            "View prototype" button → /prototype?prd=<id>, which renders the live
            bundle correctly. */}
      </div>
    </article>
  )
}

// ── Suggested-actions state machine ──────────────────────────────────────────
// The chip stack above the composer offers the most useful next step. Generating
// a PRD already lives on each finding card, so the composer's suggestion starts
// at the downstream flow: create tickets from the PRD, then view the PRD.
type SuggestStage = "prd" | "tickets"
type SuggestKind = "create-ticket" | "view-prd"

interface SuggestSpec {
  kind: SuggestKind
  label: string
  icon: "file" | "ticket"
  primary?: boolean
}

const SUGGEST_STAGES: Record<SuggestStage, SuggestSpec[]> = {
  // Default — break the PRD into tickets.
  prd: [
    { kind: "create-ticket", label: "Create ticket", icon: "ticket", primary: true },
  ],
  // After Create ticket.
  tickets: [
    { kind: "view-prd", label: "View PRD", icon: "file", primary: true },
  ],
}

// Stage to advance to after a kind is clicked (null → keep the current stage).
const SUGGEST_NEXT: Record<SuggestKind, SuggestStage | null> = {
  "create-ticket": "tickets",
  "view-prd": null,
}

// The AgentAction a kind dispatches.
const SUGGEST_ACTION: Record<SuggestKind, AgentAction> = {
  "create-ticket": "tickets",
  "view-prd": "prd",
}

// ── Brief generating / WIP indicator ─────────────────────────────────────────
// Shown on the brief surface while the backend is generating this week's brief
// (hydration kind === "generating"). Visually distinct from the empty greeting
// ("no brief yet") and the failed state: a live spinner + reassuring copy that
// is REPLACED by the real brief the moment hydration flips to ready.
function BriefGeneratingState() {
  return (
    <div className="bc-generating" role="status" aria-live="polite">
      <span className="bc-generating-spinner" aria-hidden />
      <div className="bc-generating-copy">
        <p className="bc-generating-title">Generating your Monday brief…</p>
        <p className="bc-generating-sub">
          Analyzing your sources — this usually takes a minute.
        </p>
      </div>
    </div>
  )
}

function SuggestIcon({ name }: { name: SuggestSpec["icon"] }) {
  if (name === "file") return <IconFileText size={14} />
  return <IconTicket size={14} />
}

export function BriefChat() {
  const { aiBarValue, setAIBarValue, openContentPanel, showToast, goTo, contentPanelTab } = useNavigation()
  const router = useRouter()
  const { content, setContent } = useContent()
  const { activeCompany } = useCompany()
  const { workspace } = useWorkspace()
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
  const dismissKeyRef = useRef<string | null>(null)
  const skipDismissPersistRef = useRef(false)

  // Derive briefId from the first available detail meta (briefDetails are keyed by detailKey)
  const briefId = useMemo(() => {
    const details = content.briefDetails
    for (const key of Object.keys(details)) {
      const meta = details[key]?.meta
      if (meta?.briefId != null) return meta.briefId
    }
    return null
  }, [content.briefDetails])

  // One fetch per brief — drives the per-card right-rail state + the
  // Generate/View PRD button. refetch() is called after a card generation so the
  // button flips from "Generate PRD" → "View PRD" in place (no reload).
  const { entriesByInsight, refetch: refetchPrototypeMap } =
    useBriefPrototypeMap(briefId)

  // GenerateModal / LoadingScreen state — mounted once at BriefChat level
  const genLoadingRef = useRef(false)
  const [genLoading, setGenLoading] = useState(false)
  const [genPrdId, setGenPrdId] = useState<number | null>(null)
  const [genFigmaKey, setGenFigmaKey] = useState<string | null>(null)
  const [genGithubRepo, setGenGithubRepo] = useState<string | null>(null)
  const [genProtoId, setGenProtoId] = useState<number | null>(null)
  const [genModalOpen, setGenModalOpen] = useState(false)

  const handleGenStart = useCallback((ctx?: { figmaFileKey?: string | null; githubRepo?: string | null }) => {
    setGenFigmaKey(ctx?.figmaFileKey ?? null)
    setGenGithubRepo(ctx?.githubRepo ?? null)
    setGenProtoId(null)
    genLoadingRef.current = true
    setGenLoading(true)
  }, [])

  const handleGenDone = useCallback((result?: DesignAgentGenResult) => {
    genLoadingRef.current = false
    setGenLoading(false)
    setGenModalOpen(false)
    if (result?.ok && genPrdId != null) {
      // Carry the prd context: /prototype?prd=<id>, NOT a bare goTo("prototype").
      // PrototypeRoute resolves the just-built prototype from `?prd=`; a bare nav
      // drops to the "No PRD selected" empty state (the build looks lost).
      router.push(prototypePath(genPrdId))
    }
  }, [genPrdId, router])

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
    // Dismissed-card state is loaded by its own effect (keyed on the brief), so
    // a dismissal survives reload rather than being cleared on company change.
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
        // Drop transient "thinking" turns — EXCEPT a pending Ask, whose
        // working state must survive a remount so the resume effect can
        // re-attach to the in-flight fire-and-forget answer.
        .filter((t) => t.state !== "thinking" || !!t.askPending)
        .map(({ fresh: _fresh, ...rest }) => rest)
      localStorage.setItem(key, JSON.stringify(persistable))
    } catch {
      /* best effort */
    }
  }, [turns])

  // ── Restore dismissed finding cards for the active brief ───────────────────
  // Keyed by company + week-of so a grey-out persists across re-render/reload.
  const dismissKey = dismissKeyFor(activeCompany, content.briefV2?.weekOf)
  useEffect(() => {
    if (!dismissKey) return
    let restored = new Set<string>()
    try {
      const raw = localStorage.getItem(dismissKey)
      if (raw) {
        const parsed = JSON.parse(raw)
        if (Array.isArray(parsed)) restored = new Set(parsed.filter((k) => typeof k === "string"))
      }
    } catch {
      /* ignore corrupt storage */
    }
    skipDismissPersistRef.current = true
    dismissKeyRef.current = dismissKey
    setDismissed(restored)
  }, [dismissKey])

  // ── Persist dismissed set (skip the write a fresh restore triggers) ────────
  useEffect(() => {
    const key = dismissKeyRef.current
    if (!key) return
    if (skipDismissPersistRef.current) {
      skipDismissPersistRef.current = false
      return
    }
    try {
      localStorage.setItem(key, JSON.stringify([...dismissed]))
    } catch {
      /* best effort */
    }
  }, [dismissed])

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

  // ── Card "Ask" hand-off: cards set aiBarValue → prefill ─────
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
    // PRD generation is a COMMAND, not a conversation: the PRD (in-progress and
    // final) lives in the right-rail panel, never as a bottom chat message. So
    // this opens the rail with a generating spinner and surfaces failures as a
    // toast — NO chat turn — exactly mirroring the finding-card "Generate PRD"
    // path (cardGeneratePrd). (Previously this appended a "PRD draft ready" agent
    // turn, which was redundant with the rail.)
    setContent({ prd: null, prdMeta: null, prdGenerating: true })
    openContentPanel("prd")
    try {
      const brief = await briefApi.current(activeCompany)
      const insights = brief.insights || []
      if (!insights.length) {
        setContent({ prdGenerating: false })
        showToast("No brief yet", "Run the pipeline to refresh this week's brief first.")
        return
      }
      const result = await runPrdGeneration({ briefId: brief.id, insightIndex: 0 })
      if (!result.ok) {
        setContent({ prdGenerating: false })
        showToast("PRD generation failed", result.message.slice(0, 200))
        return
      }
      setContent({ prd: result.prd, prdMeta: { briefId: brief.id, insightIndex: 0 }, prdGenerating: false })
      openContentPanel("prd")
    } catch (e) {
      setContent({ prdGenerating: false })
      showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
    }
  }, [activeCompany, openContentPanel, setContent, showToast])

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
      // Carry the open PRD's id: /prototype?prd=<id>, NOT a bare goTo("prototype")
      // — the route needs `?prd=` to resolve the prototype, else it lands on the
      // "No PRD selected" empty state.
      router.push(prototypePath(content.prd.prd_id))
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
  }, [content.prd, router, scrollToEnd])

  const evidenceFlow = useCallback(() => {
    openContentPanel("evidence")
  }, [openContentPanel])

  const multiAgentFlow = useCallback(async () => {
    const aId = uid()
    setTurns((t) => [
      ...t,
      {
        id: aId,
        role: "agent",
        persona: "pm",
        status: "running multi-agent analysis…",
        state: "thinking",
      },
    ])
    scrollToEnd()
    const fail = (error: string) =>
      setTurns((t) => t.map((x) => (x.id === aId ? { ...x, state: "error", error } : x)))
    try {
      const brief = await briefApi.current(activeCompany)
      const insights = brief.insights || []
      if (!insights.length) {
        fail("No brief insights available yet. Run the pipeline first.")
        return
      }
      const insight = insights[0]
      const result = await runMultiAgentGeneration(brief.id, 0, "aggressive")
      if (!result.ok) {
        fail(result.message)
        return
      }
      const docCount = result.docs.docs.length
      const readyCount = result.docs.docs.filter((d) => d.status === "ready").length
      setTurns((t) =>
        t.map((x) =>
          x.id === aId
            ? {
                ...x,
                state: "done",
                status: "multi-agent analysis complete",
                message:
                  `**Multi-Agent Aggressive Analysis** complete for **${insight.title}**.\n\n` +
                  `Generated **${readyCount}/${docCount + 3}** documents:\n` +
                  `- PRD (human-readable + implementation spec)\n` +
                  `- Evidence report (KG-grounded)\n` +
                  `- User stories with acceptance criteria\n` +
                  result.docs.docs
                    .map((d) => `- ${d.title} — ${d.status === "ready" ? "ready" : d.status}`)
                    .join("\n") +
                  `\n\nAll documents are cross-referenced in the **Traceability Matrix**. ` +
                  `Missing requirements, risks, and assumptions have been identified.`,
                actions: ["prd", "tickets", "prototype"],
              }
            : x,
        ),
      )
    } catch (e) {
      fail(e instanceof Error ? e.message : "Multi-agent generation failed")
    }
  }, [activeCompany, scrollToEnd])

  const plainAsk = useCallback(
    async (q: string) => {
      const aId = uid()
      // Persist a pending-ask marker keyed by this agent turn so a
      // backgrounded/remounted tab re-attaches via the mount resume effect
      // instead of orphaning the in-flight answer. The "thinking…" agent turn
      // is itself persisted (STORAGE_PREFIX), so the working state survives too.
      setTurns((t) => [...t, { id: aId, role: "agent", persona: "ds", status: "thinking…", state: "thinking", askPending: aId }])
      scrollToEnd()
      try {
        // Fire-and-forget + visibility-aware poll (blur/remount-safe).
        const res = await runAskGeneration(q, activeCompany, aId)
        setTurns((t) => t.map((x) => (x.id === aId ? { ...x, state: "done", status: undefined, reply: res, fresh: true, askPending: undefined } : x)))
      } catch (e) {
        const msg = parseAskError(e)
        setTurns((t) => t.map((x) => (x.id === aId ? { ...x, state: "error", error: msg, askPending: undefined } : x)))
        showToast("Ask failed", msg.slice(0, 120))
      }
    },
    [activeCompany, scrollToEnd, showToast],
  )

  // ── Resume an orphaned in-flight ASK on (re)mount ─────────────────────────
  // A plainAsk is fire-and-forget: the persisted "thinking…" agent turn carries
  // an `askPending` marker, and the active ask_id is persisted (jobResume). On
  // remount, re-attach to the visibility-aware poll against the existing status
  // endpoint — NOT re-ask — and clear the working state when it resolves.
  const resumedAskRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!activeCompany) return
    for (const turn of turns) {
      if (turn.role !== "agent" || !turn.askPending) continue
      if (turn.reply !== undefined && turn.reply !== null) continue
      if (resumedAskRef.current.has(turn.id)) continue
      const pending = getPendingAsk(activeCompany, turn.id)
      if (!pending) continue
      const askId = Number(pending.id)
      if (!Number.isFinite(askId)) continue
      resumedAskRef.current.add(turn.id)
      const aId = turn.id
      busyRef.current = true
      setBusy(true)
      void (async () => {
        try {
          const res = await resumeAskGeneration(askId, activeCompany, aId)
          if (!mountedRef.current) return
          setTurns((t) => t.map((x) => (x.id === aId ? { ...x, state: "done", status: undefined, reply: res, fresh: true, askPending: undefined } : x)))
        } catch (e) {
          if (!mountedRef.current) return
          const msg = parseAskError(e)
          setTurns((t) => t.map((x) => (x.id === aId ? { ...x, state: "error", error: msg, askPending: undefined } : x)))
        } finally {
          if (mountedRef.current) {
            busyRef.current = false
            setBusy(false)
            scrollToEnd()
          }
        }
      })()
    }
  }, [activeCompany, turns, scrollToEnd])

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
      // A PRD command opens its work in the right rail (no chat turn), so don't
      // echo it as a chat message either — it's a command, not a conversation.
      if (!isPrdCommand(q)) appendUser(q)
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
        if (a === "multi-agent") return multiAgentFlow()
      })
    },
    [content.prd, evidenceFlow, multiAgentFlow, openContentPanel, prdFlow, prototypeFlow, runGate, ticketsFlow],
  )

  // ── Suggested-actions: hand the implementation brief to a coding agent ─────
  // Active suggestion chips, each advancing the stage as the user acts.
  const suggestions = useMemo(
    () =>
      SUGGEST_STAGES[suggestStage].map((spec) => ({
        ...spec,
        onClick: () => {
          const next = SUGGEST_NEXT[spec.kind]
          if (next) setSuggestStage(next)
          onAction(SUGGEST_ACTION[spec.kind])
        },
      })),
    [suggestStage, onAction],
  )

  // ── Per-card actions (evidence/PRD wiring) ────────
  const cardAsk = useCallback(
    (finding: Finding) => {
      const q = finding.askQuestion
      setDraft((d) => (d.trim() ? `${d}\n\n${q}` : q))
      focusComposer()
    },
    [focusComposer],
  )

  // Lightweight single-PRD generation. The card's visible "Generate PRD" button
  // runs the full multi-agent system (cardGenerateAll); this helper is the PRD
  // scaffold used by the prototype-preview flow's "no PRD yet → make one first"
  // path, where the full 7-agent suite would be overkill.
  const cardGeneratePrd = useCallback(
    async (finding: Finding) => {
      const key = finding.detailKey
      const detail = key ? content.briefDetails?.[key] : null
      const meta = detail?.meta
      if (!meta) {
        showToast("Can't generate PRD", "Open evidence from a finding with a linked brief first.")
        return
      }
      // If a PRD is already loaded for the same insight, just show it
      // instead of re-generating.
      const currentPrdMeta = content.prdMeta
      if (
        content.prd &&
        currentPrdMeta &&
        currentPrdMeta.briefId === meta.briefId &&
        currentPrdMeta.insightIndex === meta.insightIndex
      ) {
        openContentPanel("prd")
        return
      }
      // Share the single-flight gate with the composer / agent-button flows so a
      // card PRD and a composer "generate PRD" can't race on content.prd, and a
      // second card can't start while one is in flight.
      if (busyRef.current) return
      busyRef.current = true
      setBusy(true)
      setCardBusyKey(key ?? null)
      // Open the right rail up front with a generating spinner so the PRD always
      // surfaces on the right while it's being drafted — not just when it's ready.
      setContent({ prd: null, prdMeta: null, prdGenerating: true })
      openContentPanel("prd")
      try {
        const result = await runPrdGeneration(meta)
        if (!result.ok) {
          setContent({ prdGenerating: false })
          showToast("PRD generation failed", result.message.slice(0, 200))
          return
        }
        setContent({ prd: result.prd, prdMeta: meta, prdGenerating: false })
        openContentPanel("prd")
      } catch (e) {
        setContent({ prdGenerating: false })
        showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
      } finally {
        busyRef.current = false
        if (mountedRef.current) {
          setBusy(false)
          setCardBusyKey(null)
        }
      }
    },
    [content.briefDetails, content.prd, content.prdMeta, openContentPanel, setContent, showToast],
  )

  const cardGenerateAll = useCallback(
    async (finding: Finding) => {
      const key = finding.detailKey
      const detail = key ? content.briefDetails?.[key] : null
      const meta = detail?.meta
      if (!meta) {
        showToast("Can't run multi-agent", "Open evidence from a finding with a linked brief first.")
        return
      }
      if (busyRef.current) return
      busyRef.current = true
      setBusy(true)
      setCardBusyKey(key ?? null)
      // Open the PRD rail card up front with a spinner so the work surfaces on
      // the right immediately (the same content panel as Evidence) instead of
      // only landing as a toast when the whole run finishes.
      setContent({ prd: null, prdMeta: meta, prdGenerating: true })
      openContentPanel("prd")
      try {
        const result = await runMultiAgentGeneration(meta.briefId, meta.insightIndex, "aggressive")
        if (!result.ok) {
          setContent({ prdGenerating: false })
          showToast("Multi-agent generation failed", result.message.slice(0, 200))
          return
        }
        const docCount = result.docs.docs.length
        // Refresh the brief→PRD map so the card's button flips "Generate PRD" →
        // "View PRD" in place (the PRD now exists for this insight).
        refetchPrototypeMap()
        // Land the generated PRD in the rail card. The run just created the PRD
        // record for this insight, so it's the company's latest — fetch it (a
        // pure read, no re-generation) and surface it in the open panel.
        const prdResult = await loadLatestPrd(activeCompany)
        if (prdResult.ok) {
          setContent({ prd: prdResult.prd, prdMeta: meta, prdGenerating: false })
          openContentPanel("prd")
        } else {
          setContent({ prdGenerating: false })
        }
        showToast(
          "Multi-agent complete",
          `Generated PRD + Evidence + ${docCount} analysis documents. All cross-referenced.`,
        )
      } catch (e) {
        setContent({ prdGenerating: false })
        showToast("Multi-agent failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
      } finally {
        busyRef.current = false
        if (mountedRef.current) {
          setBusy(false)
          setCardBusyKey(null)
        }
      }
    },
    [content.briefDetails, activeCompany, openContentPanel, setContent, showToast, refetchPrototypeMap],
  )

  // Dismiss greys the card out in place (it stays in the list); restore un-greys
  // it. Toggling the dismissed set drives both — and the persist effect writes it.
  const cardDismiss = useCallback((finding: Finding) => {
    const key = finding.detailKey
    if (!key) return
    setDismissed((s) => {
      if (s.has(key)) return s
      const next = new Set(s)
      next.add(key)
      return next
    })
  }, [])

  // Prototype-preview click — context-aware routing:
  //   case 1: ready prototype → open it
  //   case 2: PRD exists, no prototype → open generate modal
  //   case 3: no PRD → PRD-first flow
  const cardPreview = useCallback(
    (finding: Finding) => {
      const key = finding.detailKey
      const detail = key ? content.briefDetails?.[key] : null
      const meta = detail?.meta
      if (!meta) {
        // case 3: no detail meta — fall back to PRD-first flow
        void runGate(() => prototypeFlow())
        return
      }
      const state = prototypeStateForInsight(entriesByInsight, meta.insightIndex)
      if (state.hasPrd && state.prototypeReady && state.prdId != null) {
        // case 1: prototype ready → open it in the in-tab canvas at
        // /prototype?prd=<id>, matching the PRD-drawer preview card's nav
        // (ApproveModal / DesignAgentLauncher use router.push(prototypePath(prdId))).
        // goTo("prototype") alone navigates the screen WITHOUT the ?prd= param, so
        // PrototypeRoute has no PRD to resolve and the editor never loads.
        router.push(prototypePath(state.prdId))
      } else if (state.hasPrd && !state.prototypeReady && state.prdId != null) {
        // case 2: PRD exists but no prototype → open generate modal
        setGenPrdId(state.prdId)
        setGenModalOpen(true)
      } else {
        // case 3: no PRD → PRD-first flow
        void runGate(() => cardGeneratePrd(finding))
      }
    },
    [content.briefDetails, entriesByInsight, router, prototypeFlow, runGate, cardGeneratePrd],
  )

  // "View PRD" — open the insight's EXISTING PRD at /prd?prd=<id> (mirrors the
  // "View prototype" router.push(prototypePath(prdId)) nav). Safety fallback to
  // the generate flow if the prd id can't be resolved (the button only offers
  // "View PRD" when hasPrd && prdId, so this is belt-and-suspenders).
  const cardViewPrd = useCallback(
    async (finding: Finding) => {
      const key = finding.detailKey
      const meta = key ? content.briefDetails?.[key]?.meta : null
      const state =
        meta != null
          ? prototypeStateForInsight(entriesByInsight, meta.insightIndex)
          : null
      // No PRD yet → generate one (cardGeneratePrd opens it in the rail too).
      if (state?.prdId == null) {
        void runGate(() => cardGeneratePrd(finding))
        return
      }
      // Existing PRD → open it in the right-rail content panel (the SAME card as
      // Evidence), not a separate page. If it's already loaded for this insight,
      // just re-open the panel.
      if (
        content.prd &&
        content.prdMeta &&
        meta &&
        content.prdMeta.briefId === meta.briefId &&
        content.prdMeta.insightIndex === meta.insightIndex
      ) {
        openContentPanel("prd")
        return
      }
      setContent({ prd: null, prdMeta: meta ?? null, prdGenerating: true })
      openContentPanel("prd")
      try {
        const result = await loadPrdById(state.prdId)
        if (!result.ok) {
          setContent({ prdGenerating: false })
          showToast("Couldn't open PRD", result.message.slice(0, 200))
          return
        }
        setContent({ prd: result.prd, prdMeta: meta ?? null, prdGenerating: false })
        openContentPanel("prd")
      } catch (e) {
        setContent({ prdGenerating: false })
        showToast("Couldn't open PRD", (e instanceof Error ? e.message : String(e)).slice(0, 200))
      }
    },
    [
      content.briefDetails,
      content.prd,
      content.prdMeta,
      entriesByInsight,
      openContentPanel,
      runGate,
      cardGeneratePrd,
      setContent,
      showToast,
    ],
  )

  const cardRestore = useCallback((finding: Finding) => {
    const key = finding.detailKey
    if (!key) return
    setDismissed((s) => {
      if (!s.has(key)) return s
      const next = new Set(s)
      next.delete(key)
      return next
    })
  }, [])

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
  // Dismissed cards stay in the list (greyed out via the dismissed prop), so the
  // finding is never removed — only collapsed until restored.

  const userInitials = content.userInitials ?? (content.userName ? content.userName.slice(0, 2).toUpperCase() : "You")
  const userName = content.userName ?? "You"
  const company = v2?.company ?? ""
  const week = weekLabel(v2?.weekOf ?? null)
  const refreshing = (pipeline.runStatus as { status?: string } | null)?.status === "running"
  // The brief is being generated when hydration reports "generating" AND we
  // don't yet have a brief to show. Once findings arrive (ready), the WIP
  // indicator is replaced by the real brief. The failed state never trips this.
  const generatingBrief = content.briefHydration === "generating" && findings.length === 0

  return (
    <section className="briefx" aria-label="Weekly brief">
      <header className="bh">
        <div className="bh-main">
          {/* Title intentionally omitted — the "Monday brief" label lives in the
              tab name above; repeating it here was a redundant duplicate. */}
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
                <span className="bc-agent-name">{AGENT_NAME}</span>
                <span className="bc-agent-badge">
                  <IconSparkle size={10} />
                  PM COWORKER
                </span>
                <span className="bc-agent-status">
                  Monday brief · {generatingBrief ? "generating…" : greetTime}
                </span>
              </div>
              <div className="bc-agent-body">
                {generatingBrief ? (
                  <BriefGeneratingState />
                ) : (
                <>
                <p className="bc-greeting">{greeting}</p>
                {findings.length > 0 ? (
                  <div className="fc-stack">
                    {findings.map((f) => {
                      const key = f.detailKey
                      const meta = key ? content.briefDetails?.[key]?.meta : undefined
                      const insightState = meta != null
                        ? prototypeStateForInsight(entriesByInsight, meta.insightIndex)
                        : undefined
                      return (
                        <BriefFindingCard
                          key={f.detailKey ?? `${f.tagType}-${f.title}`}
                          finding={f}
                          busy={busy}
                          generating={cardBusyKey === f.detailKey}
                          dismissed={!!f.detailKey && dismissed.has(f.detailKey)}
                          onAsk={() => cardAsk(f)}
                          onGenerateAll={() => cardGenerateAll(f)}
                          onViewPrd={() => cardViewPrd(f)}
                          onDismiss={() => cardDismiss(f)}
                          onRestore={() => cardRestore(f)}
                          onPreview={() => cardPreview(f)}
                          insightState={insightState}
                        />
                      )
                    })}
                  </div>
                ) : null}
                {v2?.sourcesLine ? (
                  <div className="fc-sources">
                    <span className="fc-sources-label">Sources this week</span>
                    <span>{v2.sourcesLine}</span>
                  </div>
                ) : null}
                </>
                )}
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
          {/* "Create ticket" only makes sense against an open PRD — gate the chip
              stack on the PRD rail being open so it isn't a hanging button. */}
          {findings.length > 0 && contentPanelTab === "prd" ? (
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
      {genModalOpen && genPrdId != null && (
        <GenerateModal
          open={genModalOpen}
          onClose={() => {
            if (!genLoadingRef.current) setGenModalOpen(false)
          }}
          prdId={genPrdId}
          figmaFileKey={genFigmaKey}
          savedPreference={workspace?.design_source ?? null}
          onGenStart={handleGenStart}
          onKickoff={(id) => setGenProtoId(id)}
          onGenDone={handleGenDone}
        />
      )}
      <GenerationLoadingScreen
        open={genLoading}
        figmaFileKey={genFigmaKey}
        githubRepo={genGithubRepo}
        prototypeId={genProtoId}
      />
    </section>
  )
}

// ── Agent turn (chat replies / command confirmations) ────────────────────────
const ACTION_LABEL: Record<AgentAction, string> = {
  prd: "Generate PRD",
  evidence: "View evidence",
  tickets: "Create tickets",
  prototype: "Generate prototype",
  "multi-agent": "Generate PRD first",
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
  const personaName = turn.persona === "pm" ? AGENT_NAME : "DS Agent"
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
