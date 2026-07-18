"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useCompany } from "../../context/CompanyContext"
import { briefApi, type AskResponse } from "../../lib/api"
import { loadLatestPrd } from "../../lib/runPrdGeneration"
import { runEvidenceGeneration } from "../../lib/runEvidenceGeneration"
import { runMultiAgentGeneration } from "../../lib/runMultiAgentGeneration"
import { usePipelineStatus } from "../../lib/usePipelineStatus"
import { AGENT_NAME } from "../../lib/agent"
import type {
  BriefV2CompactFinding,
  BriefV2HeroFinding,
  BriefV2State,
} from "../../lib/brief-v2-adapter"
import { AssistantThinkingSkeleton } from "./AssistantThinkingSkeleton"
import { AskReplyBody } from "./AskReplyBody"
import { IconClose, IconSendUp, IconSparkle, IconTerminalPrompt, IconUndo } from "./app-icons"
import { useBriefPrototypeMap } from "../design-agent/useBriefPrototypeMap"
import { prototypeStateForInsight } from "../design-agent/briefPrototypeMap.helpers"
import { GenerateModal } from "../design-agent/GenerateModal"
import { GenerationLoadingScreen } from "../design-agent/GenerationLoadingScreen"
import { useGeneratePrototype } from "../design-agent/useGeneratePrototype"
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

// Exported so ChatScreen intercepts tickets phrasings (incl. "convert this PRD
// into tickets" over an attached document) with the SAME rule instead of
// letting the ask agent answer with markdown.
export const isTicketsCommand = (q: string) =>
  /\b(create|generate|make|draft|break|convert|turn|split)\b.*\btickets?\b/i.test(q)
// A "generate a PRD" phrasing is a COMMAND (open the PRD tab), not a question
// for the ask agent. Exported so ChatScreen intercepts it with the SAME rule —
// otherwise the ask agent answers it with a raw prd-author HTML dump.
// import/convert/upload cover the doc-import phrasings ("import this document
// as a PRD"); a query that is a tickets command is never a PRD command, so
// "convert this PRD into tickets" routes to tickets in every dispatcher
// regardless of check order.
export const isPrdCommand = (q: string) =>
  /\b(generate|create|write|draft|make|import|convert|upload)\b.*\bprd\b/i.test(q) && !isTicketsCommand(q)
const isPrototypeCommand = (q: string) =>
  /\b(generate|create|make|build|spin\s*up)\b.*\b(prototype|proto|mock\s*up|mockup)\b/i.test(q)

// The fixed capability sentence — what the agent continuously does for the user.
// Lower-cased so it flows after the "Good day, {name} - " salutation in a single
// greeting paragraph.
const CAPABILITY_LINE =
  "we continuously monitor how your product is being used, what customers are asking for, and competitor launches — and give you a weekly digest of the most important things worth working on."

// Single greeting paragraph: salutation + capability + a state-dependent tail.
// One paragraph (no separate persistent intro) so there's exactly one
// "Good day, {name}" and the message reads as one flowing line.
function buildGreeting(v2: BriefV2State | null, firstName: string | null): string {
  const who = firstName ? `, ${firstName}` : ""
  const lead = `Good day${who} - ${CAPABILITY_LINE}`
  if (!v2 || (!v2.hero && v2.supporting.length === 0)) {
    // Distinguish "we received your data but it isn't connected-evidence-rich
    // enough yet" from a brand-new, no-data account. The backend sets
    // `insufficientEvidence` on the empty brief in the former case so we can
    // reassure the user their upload landed instead of telling them to "add a
    // first source".
    if (v2?.insufficientEvidence) {
      return `${lead} We've got your data, but there isn't enough connected evidence yet to build this week's brief — connect another source or add richer data and it'll fill in.`
    }
    return `${lead} There isn't enough connected yet to generate this week's brief — add and connect more sources and it'll appear here.`
  }
  const n = [v2.hero, ...v2.supporting].filter(Boolean).length
  return `${lead} Here's the top ${n} thing${n !== 1 ? "s" : ""} worth your attention this week.`
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
/** Pure: the primary finding-card CTA. When a PRD already exists for this
 *  insight the button becomes "View PRD" (opens the existing PRD); otherwise
 *  "Generate PRD" (runs the full system), reflecting in-flight as "Generating…".
 *  While the brief-prototype map is still loading we don't yet KNOW whether a PRD
 *  exists, so `loading` yields a neutral, disabled "Loading…" (waiting=true) —
 *  otherwise the button flashes "Generate PRD" then flips to "View PRD" the
 *  instant the map lands. Extracted so the decision is unit-testable. */
export function prdCtaState(
  insightState: { hasPrd: boolean; prdId: number | null } | null | undefined,
  generating: boolean,
  loading = false,
): { label: string; isView: boolean; waiting: boolean } {
  const hasPrd = !!(insightState?.hasPrd && insightState.prdId != null)
  if (loading && !hasPrd) {
    return { label: "Loading…", isView: false, waiting: true }
  }
  if (hasPrd) {
    return { label: "View PRD", isView: true, waiting: false }
  }
  return { label: generating ? "Generating…" : "Generate PRD", isView: false, waiting: false }
}

/** Pure: the prototype CTA label. "View prototype" once a prototype is actually
 *  built and saved for this insight (prototypeReady — DB-backed via the
 *  brief-prototype map), otherwise "Generate prototype". Shared by the brief
 *  finding card and the chat surface so both relabel identically. The click
 *  handler (cardPreview / handleChatPrototype) view-vs-generates on the SAME
 *  state, keeping label and action in lockstep. */
export function prototypeCtaLabel(
  insightState: { hasPrd: boolean; prototypeReady: boolean } | null | undefined,
): string {
  return insightState?.hasPrd && insightState.prototypeReady ? "View prototype" : "Generate prototype"
}

// ── Finding card — matches reference layout ───────────────────────────────────
function BriefFindingCard({
  finding,
  busy,
  generating,
  dismissed,
  showActions,
  onAsk,
  onGenerateAll,
  onViewPrd,
  onDismiss,
  onRestore,
  onPreview,
  insightState,
  mapLoading,
}: {
  finding: Finding
  busy: boolean
  generating: boolean
  dismissed: boolean
  // True while the brief-prototype map is still fetching — the PRD CTA shows a
  // neutral "Loading…" until we know whether this insight already has a PRD.
  mapLoading: boolean
  // Whether to render the Generate/View PRD + prototype action CTAs. False when
  // the brief has no real data behind it (insufficient-evidence / empty case) —
  // those affordances make no sense without findings to act on.
  showActions: boolean
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
  // Weekly-brief skill design: the card accent is the finding type's canonical
  // hex (derived from type, set as a CSS var the pill / left bar / PRD button
  // read), and the category pill shows the type name only (no P0/P1).
  const accentStyle = { ["--card-accent"]: finding.skillAccent } as React.CSSProperties

  // ── Dismissed (greyed) state ──────────────────────────────────────────────
  // Greys the card out in place — keeps the finding present (not deleted) and
  // hides the heavy detail/viz, exposing a "click to restore" affordance.
  // Clicking the card body (or the restore button) un-greys it.
  if (dismissed) {
    return (
      <article
        className={`fc fc--${accent} fc--dismissed`}
        style={accentStyle}
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
    <article className={`fc fc--${accent} fc--skill`} style={accentStyle}>
      {/* Top row: type pill (skill taxonomy — type name only) + icons */}
      <div className="fc-top">
        <span className="fc-pill fc-pill--skill">
          <span className="fc-dot" aria-hidden />
          {finding.skillLabel}
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

          {/* Body — rendered as markdown so LLM-supplied **bold** shows correctly */}
          {finding.body ? (
            <div className="fc-body fc-body--md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{finding.body}</ReactMarkdown>
            </div>
          ) : null}

          {/* "From" source-chip row — the weekly-brief skill's honest provenance
              row (assets/brief-template.html). Replaces the legacy mini-chart +
              KPI stat columns: the skill puts numbers in the title/body, and a
              quiet source row under it (never implies convergence that didn't
              happen). Hidden when no sources are attached. */}
          {finding.fromSources.length > 0 ? (
            <div className="fc-from" role="list" aria-label="Sources">
              <span className="fc-from-lead">From</span>
              {finding.fromSources.map((s, i) => (
                <span key={i} className="fc-from-src" role="listitem">{s}</span>
              ))}
            </div>
          ) : null}

          {/* Action buttons — hidden entirely when the brief has no real data
              behind it (insufficient-evidence / empty case): a Generate PRD /
              prototype affordance makes no sense without findings to act on. */}
          {showActions ? (
          <div className="fc-actions">
            {(() => {
              const cta = prdCtaState(insightState, generating, mapLoading)
              return (
                <button
                  type="button"
                  className="fc-btn-prd fc-btn-prd--skill"
                  onClick={cta.isView ? onViewPrd : onGenerateAll}
                  // View is a cheap read — allowed while another job is busy;
                  // Generate is gated on `busy` as before. While the map is still
                  // loading (waiting) the label is unknown, so the button is inert.
                  disabled={cta.waiting || (busy && !cta.isView)}
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
            {/* View-only prototype affordance. The weekly brief no longer
                offers GENERATE prototype on finding cards — prototypes are
                generated from the PRD flow. A prototype that already exists
                (built earlier, e.g. from the PRD chat) stays reachable here as
                "View prototype": prototypeReady is only true once one is
                actually built and saved. */}
            {insightState?.prototypeReady ? (
              <button type="button" className="fc-btn-secondary" onClick={onPreview}>
                <IconTerminalPrompt size={13} />
                View prototype
              </button>
            ) : null}
          </div>
          ) : null}
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
        <p className="bc-generating-title">Generating your Weekly brief…</p>
        <p className="bc-generating-sub">
          Analyzing your sources — this usually takes a minute.
        </p>
      </div>
    </div>
  )
}

// ── Brief refreshing banner ──────────────────────────────────────────────────
// Shown ABOVE an existing brief while a fresh one is being built over it
// (content.briefRegenerating, e.g. after a connector was added and the
// workspace is regenerating). Unlike BriefGeneratingState it is non-destructive:
// the current brief stays readable underneath. Clears itself when the new brief
// lands and the flag flips back off.
function BriefRefreshingBanner() {
  return (
    <div className="bc-refreshing" role="status" aria-live="polite">
      <span className="bc-refreshing-spinner" aria-hidden />
      <span className="bc-refreshing-copy">
        Refreshing your brief with your latest sources…
      </span>
    </div>
  )
}

export function BriefChat() {
  const { aiBarValue, setAIBarValue, openContentPanel, openPrdTab, showToast, setPendingChatHandoff } = useNavigation()
  const router = useRouter()
  const { content, setContent } = useContent()
  const { activeCompany } = useCompany()
  // Keep the pipeline-status poll mounted (other surfaces rely on its side
  // effects); the brief header no longer reads its result directly.
  usePipelineStatus(activeCompany)

  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  const [cardBusyKey, setCardBusyKey] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
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
  const { entriesByInsight, loading: prototypeMapLoading, refetch: refetchPrototypeMap } =
    useBriefPrototypeMap(briefId)

  // Which prdId/source context to feed the shared generate/view-prototype
  // hook below — BriefChat's own click-routing (cardPreview) decides WHEN to
  // generate; the hook owns the GenerateModal/loading-overlay lifecycle once
  // that decision is made.
  const [genPrdId, setGenPrdId] = useState<number | null>(null)
  const [genFigmaKey] = useState<string | null>(null)

  // skipExistenceCheck: useBriefPrototypeMap's batch fetch is ALREADY this
  // card's existence source of truth (see cardPreview below) — a second,
  // redundant getByPrd here would just re-derive the same answer per card.
  //
  // listenForCrossSurfaceGenerating is intentionally OMITTED (defaults false).
  // Each finding card's View/Generate label comes from the batch map, not from
  // this hook's `cta` — so listening for the app-wide (unscoped, no-prdId)
  // da:generating/da:generating-done signal would only ever be DEAD state
  // here, and worse, a single BriefChat-level hook instance would flip to
  // "generating" the moment ANY card anywhere started a run. Leaving this off
  // is a deliberate, documented choice, not a gap.
  const gen = useGeneratePrototype(genPrdId, {
    figmaFileKey: genFigmaKey,
    skipExistenceCheck: true,
    // The card label comes from the batch map above, which nothing refreshed
    // after a prototype generation finished — the button stayed on "Generate
    // prototype" until a remount. Refetch it whenever a run settles so the
    // card flips to "View prototype" in place.
    onGenerationSettled: refetchPrototypeMap,
  })

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
    // PRD generation is a COMMAND, not a conversation. It opens the PRD as its
    // OWN chat tab (with the Evidence / PRD / Tickets panel over it), never as a
    // bottom chat message. Resolve the brief's top insight, then hand off via
    // openPrdTab — ChatScreen drives the generation and opens the panel.
    try {
      const brief = await briefApi.current(activeCompany)
      const insights = brief.insights || []
      if (!insights.length) {
        showToast("No brief yet", "Run the pipeline to refresh this week's brief first.")
        return
      }
      openPrdTab({
        title: "PRD · Weekly brief",
        source: { kind: "generate", meta: { briefId: brief.id, insightIndex: 0 } },
      })
    } catch (e) {
      showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
    }
  }, [activeCompany, openPrdTab, showToast])

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
      setDraft("")
      if (composerRef.current) composerRef.current.style.height = "auto"
      // A plain question is a CHAT — it never threads inline into the brief.
      // Hand it to the host ChatScreen, which opens a fresh chat tab seeded with
      // the query (one new tab per chat started here). PRD / prototype / tickets
      // are COMMANDS that drive the right rail in place, so they stay on the brief.
      const isCommand = isPrdCommand(q) || isPrototypeCommand(q) || isTicketsCommand(q)
      if (!isCommand) {
        setPendingChatHandoff({ query: q })
        return
      }
      // A PRD command opens its work in the right rail (no chat turn), so don't
      // echo it as a chat message either — it's a command, not a conversation.
      if (!isPrdCommand(q)) appendUser(q)
      void runGate(() => {
        if (isPrdCommand(q)) return prdFlow()
        if (isPrototypeCommand(q)) return prototypeFlow()
        return ticketsFlow()
      })
    },
    [appendUser, prdFlow, prototypeFlow, runGate, showToast, ticketsFlow, setPendingChatHandoff],
  )

  const onAction = useCallback(
    (a: AgentAction) => {
      void runGate(() => {
        if (a === "prd") return content.prd
          ? openPrdTab({ title: `PRD · ${content.prd.title}`, source: { kind: "ready", prd: content.prd, meta: content.prdMeta ?? null } })
          : prdFlow()
        if (a === "evidence") return evidenceFlow()
        if (a === "tickets") return ticketsFlow()
        if (a === "prototype") return prototypeFlow()
        if (a === "multi-agent") return multiAgentFlow()
      })
    },
    [content.prd, content.prdMeta, evidenceFlow, multiAgentFlow, openPrdTab, prdFlow, prototypeFlow, runGate, ticketsFlow],
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
    (finding: Finding) => {
      const key = finding.detailKey
      const detail = key ? content.briefDetails?.[key] : null
      const meta = detail?.meta
      if (!meta) {
        showToast("Can't generate PRD", "Open evidence from a finding with a linked brief first.")
        return
      }
      const title = `PRD · ${finding.title || "Brief finding"}`
      // A PRD already loaded for this insight → open it in a chat tab from the
      // in-memory doc (no re-generate). Otherwise generate into a fresh PRD tab.
      const currentPrdMeta = content.prdMeta
      if (
        content.prd &&
        currentPrdMeta &&
        currentPrdMeta.briefId === meta.briefId &&
        currentPrdMeta.insightIndex === meta.insightIndex
      ) {
        openPrdTab({ title, insightBody: finding.body, source: { kind: "ready", prd: content.prd, meta } })
        return
      }
      openPrdTab({ title, insightBody: finding.body, source: { kind: "generate", meta } })
    },
    [content.briefDetails, content.prd, content.prdMeta, openPrdTab, showToast],
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
        // prototypePrdId: the PRD the prototype is actually attached to (may be
        // an older PRD than the insight's newest after a PRD regeneration).
        router.push(prototypePath(state.prototypePrdId ?? state.prdId))
      } else if (state.hasPrd && !state.prototypeReady && state.prdId != null) {
        // case 2: PRD exists but no prototype → open generate modal
        setGenPrdId(state.prdId)
        gen.openGenerateModal()
      } else {
        // case 3: no PRD → PRD-first flow
        void runGate(() => cardGeneratePrd(finding))
      }
    },
    [content.briefDetails, entriesByInsight, router, prototypeFlow, runGate, cardGeneratePrd, gen.openGenerateModal],
  )

  // "View PRD" — open the insight's EXISTING PRD at /prd?prd=<id> (mirrors the
  // "View prototype" router.push(prototypePath(prdId)) nav). Safety fallback to
  // the generate flow if the prd id can't be resolved (the button only offers
  // "View PRD" when hasPrd && prdId, so this is belt-and-suspenders).
  const cardViewPrd = useCallback(
    (finding: Finding) => {
      const key = finding.detailKey
      const meta = key ? content.briefDetails?.[key]?.meta : null
      const state =
        meta != null
          ? prototypeStateForInsight(entriesByInsight, meta.insightIndex)
          : null
      const title = `PRD · ${finding.title || "Brief finding"}`
      // No PRD yet → generate one (cardGeneratePrd opens it in a PRD chat tab).
      if (state?.prdId == null) {
        cardGeneratePrd(finding)
        return
      }
      // Already loaded for this insight → open the in-memory doc in a chat tab.
      if (
        content.prd &&
        content.prdMeta &&
        meta &&
        content.prdMeta.briefId === meta.briefId &&
        content.prdMeta.insightIndex === meta.insightIndex
      ) {
        openPrdTab({ title, insightBody: finding.body, source: { kind: "ready", prd: content.prd, meta } })
        return
      }
      // Existing PRD by id → load it into a chat tab (ChatScreen drives the fetch).
      openPrdTab({ title, insightBody: finding.body, source: { kind: "load", prdId: state.prdId, meta: meta ?? null } })
    },
    [
      content.briefDetails,
      content.prd,
      content.prdMeta,
      entriesByInsight,
      openPrdTab,
      cardGeneratePrd,
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

  // Whether the brief has real data behind it. When false — the empty /
  // insufficient-evidence / placeholder case — we suppress every Generate-PRD
  // and Generate-Prototype affordance (finding-card CTAs + composer suggestion
  // chips), leaving only the greeting + "add more sources" guidance. A brief is
  // "real" when it has at least one finding AND the backend didn't flag it as
  // insufficient-evidence.
  const hasRealData = findings.length > 0 && !v2?.insufficientEvidence

  const userInitials = content.userInitials ?? (content.userName ? content.userName.slice(0, 2).toUpperCase() : "You")
  const userName = content.userName ?? "You"
  // "Monday brief · 7:01 AM" line in the agent head, from the brief's
  // generated_at. Hidden when there's no brief (or no timestamp on it).
  const briefTimeLabel = useMemo(() => {
    if (!v2?.generatedAt) return null
    const d = new Date(v2.generatedAt)
    if (Number.isNaN(d.getTime())) return null
    return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
  }, [v2?.generatedAt])
  // The brief is being generated when hydration reports "generating" AND we
  // don't yet have a brief to show. Once findings arrive (ready), the WIP
  // indicator is replaced by the real brief. The failed state never trips this.
  const generatingBrief = content.briefHydration === "generating" && findings.length === 0
  // A fresh brief is being built over the one currently on screen (e.g. a
  // connector was just added). Show a non-destructive "refreshing" banner above
  // the existing brief. Skip when generatingBrief already owns the surface (no
  // brief yet — the full generating state covers that case).
  const refreshingBrief = content.briefRegenerating && !generatingBrief

  return (
    <section className="briefx" aria-label="Weekly brief">
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
                  Product Coworker
                </span>
                {briefTimeLabel ? (
                  <span className="bc-agent-status">Monday brief · {briefTimeLabel}</span>
                ) : null}
              </div>
              <div className="bc-agent-body">
                {generatingBrief ? (
                  <BriefGeneratingState />
                ) : (
                <>
                {refreshingBrief ? <BriefRefreshingBanner /> : null}
                {/* Single greeting paragraph: salutation + the agent's ongoing
                    value + the "top N this week" tail (buildGreeting). Replaces
                    the old separate persistent-intro + greeting that double-led
                    with "Good day {name}". */}
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
                          showActions={hasRealData}
                          onAsk={() => cardAsk(f)}
                          onGenerateAll={() => cardGenerateAll(f)}
                          onViewPrd={() => cardViewPrd(f)}
                          onDismiss={() => cardDismiss(f)}
                          onRestore={() => cardRestore(f)}
                          onPreview={() => cardPreview(f)}
                          insightState={insightState}
                          mapLoading={prototypeMapLoading}
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
      {gen.generateModalProps.open && genPrdId != null && (
        <GenerateModal {...gen.generateModalProps} />
      )}
      <GenerationLoadingScreen {...gen.loadingScreenProps} />
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
  const badge = turn.persona === "pm" ? "Product Coworker" : "DS COWORKER"
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
