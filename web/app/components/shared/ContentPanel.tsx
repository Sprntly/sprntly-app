"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { EvidenceSections } from "./EvidenceSections"
import { EvidenceHtmlBrief } from "./EvidenceHtmlBrief"
import { EmptyPane } from "./EmptyPane"
import { IconClose, IconSparkle } from "./app-icons"
import { runEvidenceGeneration, loadEvidenceByInsight } from "../../lib/runEvidenceGeneration"
import { runPrdGeneration } from "../../lib/runPrdGeneration"
import { useRouter } from "next/navigation"
import {
  ApiError, storiesApi,
  type ClickUpList, type ClickUpTicketState, type GeneratedStory,
  type JiraProject, type TicketSyncState, type TrackerMeta,
  type TrackerProvider,
} from "../../lib/api"
import { PrdPanelContent } from "./PrdPanelContent"
import { GeneratePrototypeCTA } from "../design-agent/GeneratePrototypeCTA"
import { TicketDetail } from "./TicketDetail"
import { DestinationPicker } from "./DestinationPicker"
import { JiraPushModal, type JiraPushChoice } from "./JiraPushModal"
import { ticketSyncTrackers } from "../../lib/connectorsCatalog"
import {
  IconMicroscope, IconFileText, IconTicket, IconShare, IconFileTypePdf,
  IconRefresh, IconChevronDown, IconPlugConnected,
} from "@tabler/icons-react"
import { downloadPrdPdf, printPrdHtml } from "../../lib/prdExport"
import { printCombined } from "../../lib/combinedExport"
import type { PrdState, PrdContent, AppContentState } from "../../types/content"

// Tab order mirrors the pipeline: Evidence → PRD → Tickets (each tab's bottom
// bar launches the NEXT artifact). Evidence is hidden for non-brief PRDs (see
// isEvidenceTabHidden), so uploads show PRD → Tickets.
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

// Header Share dropdown — Download PDF of the combined Evidence + PRD (falls
// back to a single-PRD export when there's no evidence). Enabled only when a
// PRD is loaded. The heavy generators are lazy-imported inside the handler.
function ShareMenu({
  prd,
  evidence,
  onToast,
}: {
  prd: PrdState | null
  evidence: PrdContent | null
  onToast: (title: string, sub: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const enabled = !!prd
  // An HTML PRD generated from a brief insight almost always has an Evidence
  // brief, so we offer the combined Evidence + PRD download. The evidence may
  // not be loaded into context yet (it's populated by the Evidence tab), so the
  // export handlers fetch it on demand from the PRD's insight when needed.
  const canFetchEvidence = prd?.briefId != null && prd?.insightIndex != null
  const combined = !!prd?.html && (!!evidence?.html || canFetchEvidence)

  // Resolve the Evidence brief for a combined export: prefer what's already in
  // context, else read-load it from the PRD's insight. Returns null when the
  // insight has no ready HTML evidence (→ caller exports the PRD alone).
  const resolveEvidence = async (): Promise<PrdContent | null> => {
    if (evidence?.html) return evidence
    if (prd?.briefId == null || prd?.insightIndex == null) return null
    try {
      return await loadEvidenceByInsight(prd.briefId, prd.insightIndex)
    } catch {
      return null
    }
  }

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDocClick)
    return () => document.removeEventListener("mousedown", onDocClick)
  }, [open])

  const handlePdf = async () => {
    if (!prd) return
    setOpen(false)
    try {
      // Combined Evidence + PRD when both are HTML briefs (evidence fetched on
      // demand); otherwise the v3 HTML PRD prints itself (its print stylesheet
      // strips the editing chrome), and a markdown PRD uses the section builder.
      const ev = prd.html ? await resolveEvidence() : null
      if (ev?.html && prd.html) printCombined(ev, prd)
      else if (prd.html) printPrdHtml(prd)
      else await downloadPrdPdf(prd)
    } catch {
      onToast("PDF export failed", "Could not generate the PDF. Please try again.")
    }
  }

  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button
        type="button"
        className="cpanel-action-btn"
        disabled={!enabled}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); if (enabled) setOpen((o) => !o) }}
      >
        <IconShare size={12} />Share
      </button>
      {open && enabled && (
        <div className="share-menu share-menu--down open" role="menu">
          <div className="share-menu-item" role="menuitem" onClick={handlePdf}>
            <div className="share-menu-item-icon"><IconFileTypePdf size={14} /></div>
            <div>
              <div style={{ fontWeight: 600 }}>Download PDF</div>
              <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>{combined ? "Evidence + PRD as .pdf" : "Export as .pdf"}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * Whether to hide the right-panel Evidence tab for the current content.
 *
 * Only brief-insight PRDs carry their own research Evidence (keyed at
 * `(brief_id, insight_index)`). Backlog and uploaded PRDs have none — an
 * uploaded PRD may genuinely have no evidence at all — so the Evidence tab is
 * hidden for them. We still show it while evidence is loaded/generating into
 * context (e.g. a brief-finding flow), and a missing `source` (legacy rows) is
 * treated as brief. Only gates once a PRD is actually loaded.
 */
export function isEvidenceTabHidden(
  content: Pick<AppContentState, "prd" | "evidence" | "evidenceGenerating">,
): boolean {
  const prd = content.prd
  return (
    prd != null &&
    prd.source != null &&
    prd.source !== "brief" &&
    !content.evidence &&
    !content.evidenceGenerating
  )
}

export function ContentPanel() {
  const { contentPanelTab, openContentPanel, closeContentPanel, showToast } = useNavigation()
  const { content } = useContent()

  const evidenceHidden = isEvidenceTabHidden(content)
  const visibleTabs = evidenceHidden ? TABS.filter((t) => t.id !== "evidence") : TABS

  // If the panel is parked on Evidence but that tab just became hidden (a
  // backlog/upload PRD loaded), render the PRD tab instead of a stranded body.
  const activeTab = evidenceHidden && contentPanelTab === "evidence" ? "prd" : contentPanelTab

  // Persist that fallback into navigation state so re-opens land on a real tab.
  useEffect(() => {
    if (evidenceHidden && contentPanelTab === "evidence") openContentPanel("prd")
  }, [evidenceHidden, contentPanelTab, openContentPanel])

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
              {visibleTabs.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={`cpanel-tab${activeTab === t.id ? " cpanel-tab--active" : ""}`}
                  onClick={() => openContentPanel(t.id)}
                >
                  {t.icon} {t.label}
                </button>
              ))}
            </div>
          </div>
            <span className="cpanel-main-name">{content.prd?.title ? `PRD · ${content.prd.title}` : "PRD"}</span>
          <div className="cpanel-head-actions">
            <ShareMenu prd={content.prd} evidence={content.evidence} onToast={showToast} />
            <button type="button" className="cpanel-close" onClick={closeContentPanel} aria-label="Close">
              <IconClose size={16} />
            </button>
          </div>
        </div>

        <div className="cpanel-body">
          {activeTab === "evidence" && <EvidenceTab />}
          {activeTab === "prd" && <PrdPanelContent evidenceTabAvailable={!evidenceHidden} />}
          {activeTab === "tickets" && <TicketsTab />}
        </div>

        {/* Fixed pipeline bar — each tab's bottom launches the NEXT artifact.
            The PRD tab keeps its OWN footer (autosave + version history + the
            tickets button), so the shared bar is only for Evidence and Tickets. */}
        {activeTab === "evidence" && <EvidenceBottomBar />}
        {activeTab === "tickets" && <TicketsBottomBar />}
      </aside>
    </>
  )
}

// ── Fixed bottom bar: Evidence tab → Generate / View PRD ──────────────────────
// The Evidence tab's next pipeline step is the PRD. "View PRD" (one is already
// loaded for this context) just switches tabs; otherwise "Generate PRD" runs the
// generation for the current insight, flips to the PRD tab, and lands the doc
// there. Disabled when there's no insight meta to generate from.
function EvidenceBottomBar() {
  const { openContentPanel, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const prd = content.prd
  const meta = content.detail?.meta ?? content.prdMeta ?? null
  const [generating, setGenerating] = useState(false)

  const generate = useCallback(async () => {
    if (!meta || generating) return
    setGenerating(true)
    // Reveal the PRD tab with its generating spinner right away.
    setContent({ prd: null, prdMeta: meta, prdGenerating: true })
    openContentPanel("prd")
    try {
      const result = await runPrdGeneration(meta)
      if (result.ok) {
        setContent({ prd: result.prd, prdMeta: meta, prdGenerating: false })
      } else {
        setContent({ prdGenerating: false })
        showToast("PRD generation failed", result.message)
      }
    } catch (e) {
      setContent({ prdGenerating: false })
      showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
    } finally {
      setGenerating(false)
    }
  }, [meta, generating, setContent, openContentPanel, showToast])

  return (
    <div className="cpanel-bottom-bar">
      {prd ? (
        <button type="button" className="btn btn-primary btn-sm cpanel-next-btn" onClick={() => openContentPanel("prd")}>
          View PRD
        </button>
      ) : (
        <button
          type="button"
          className="btn btn-primary btn-sm cpanel-next-btn"
          data-testid="evidence-footer-prd-cta"
          disabled={generating || !meta}
          onClick={generate}
        >
          {generating ? "Generating PRD…" : "Generate PRD"}
        </button>
      )}
    </div>
  )
}

// ── Fixed bottom bar: Tickets tab → Generate / View Prototype ─────────────────
// The Tickets tab's next pipeline step is the prototype, driven by the canonical
// GeneratePrototypeCTA (the only sanctioned generate/view-prototype trigger). A
// Tickets tab always has a PRD in scope, so the button is never disabled here.
function TicketsBottomBar() {
  const { content } = useContent()
  const prdId = content.prd?.prd_id ?? null
  return (
    <div className="cpanel-bottom-bar">
      <GeneratePrototypeCTA
        prdId={prdId}
        figmaFileKey={content.prd?.figma_file_key ?? null}
        // Safe: the panel shows ONE current PRD at a time, so the unscoped
        // da:generating signal can't mislabel a different PRD's run.
        listenForCrossSurfaceGenerating
        render={({ label, onClick, disabled }) => (
          <button
            type="button"
            className="btn btn-primary btn-sm cpanel-next-btn"
            data-testid="tickets-footer-prototype-cta"
            disabled={disabled || prdId == null}
            onClick={onClick}
          >
            {label}
          </button>
        )}
      />
    </div>
  )
}

// Which `(briefId:insightIndex)` the evidence currently in ContentContext was
// loaded for. MODULE-level (not a ref) so it survives the EvidenceTab
// unmount/remount that every panel tab switch causes — with a per-mount ref the
// tab wiped `content.evidence` and refetched from scratch on every PRD ⇄
// Evidence switch, making each switch wait on the network again.
let evidenceLoadedKey: string | null = null
let prdEvidenceLoadedKey: string | null = null

function EvidenceTab() {
  const { expandAiPanel, setAIBarValue } = useNavigation()
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

  useEffect(() => {
    if (!detail?.meta) return
    const key = `${detail.meta.briefId}:${detail.meta.insightIndex}`
    // Already loaded this exact insight (possibly by a previous mount of this
    // tab) — the evidence in context is current, don't re-fetch.
    if (evidenceLoadedKey === key && evidence) return
    // Switching to a different insight — clear stale evidence.
    if (evidenceLoadedKey !== key) setContent({ evidence: null })
    let cancelled = false
    setLocalState({ kind: "loading" })
    evidenceLoadedKey = key
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

  // PRD-driven population: when a PRD is being viewed/generated for an insight
  // (content.prdMeta) WITHOUT an explicit finding-detail context, READ-load that
  // insight's existing evidence so the Evidence tab is populated instead of
  // empty. Pure read (loadEvidenceByInsight) — never kicks off generation; the
  // detail.meta loader above owns the generate-if-clicked-from-a-finding case.
  const prdMeta = content.prdMeta
  useEffect(() => {
    if (detail?.meta) return
    if (!prdMeta) return
    const key = `${prdMeta.briefId}:${prdMeta.insightIndex}`
    if (prdEvidenceLoadedKey === key && evidence) return
    if (prdEvidenceLoadedKey !== key) setContent({ evidence: null })
    prdEvidenceLoadedKey = key
    let cancelled = false
    // Show the loading skeleton (not "No evidence loaded yet") while the read
    // is in flight — only on first load; a later remount hits the cache above.
    setLocalState({ kind: "loading" })
    loadEvidenceByInsight(prdMeta.briefId, prdMeta.insightIndex)
      .then((ev) => {
        if (cancelled) return
        if (ev) setContent({ evidence: ev })
        setLocalState({ kind: "idle" })
      })
      .catch(() => {
        /* read-only best effort — leave the panel's empty/generate state */
        if (!cancelled) setLocalState({ kind: "idle" })
      })
    return () => {
      cancelled = true
    }
  }, [detail?.meta, prdMeta?.briefId, prdMeta?.insightIndex, evidence, setContent])

  // Explicit retry after a FAILED generation. force=true skips the backend's
  // failed-row short-circuit and its dedup, starting a genuinely fresh run —
  // the ONLY path that re-generates after a failure (opens never auto-retry).
  const retryEvidence = useCallback(() => {
    const meta = detail?.meta
    if (!meta) return
    setLocalState({ kind: "loading" })
    runEvidenceGeneration(meta, { force: true })
      .then((result) => {
        if (!result.ok) { setLocalState({ kind: "error", message: result.message }); return }
        setContent({ evidence: result.evidence })
        setLocalState({ kind: "idle" })
      })
      .catch((e: unknown) => {
        setLocalState({ kind: "error", message: e instanceof Error ? e.message : String(e) })
      })
  }, [detail?.meta, setContent])

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
          evidence.html ? (
            // v3 evidence — the self-contained HTML visual brief. It carries its
            // own title/eyebrow/meta, so we render JUST the brief (sandboxed
            // iframe) and skip the panel's title/meta/section chrome.
            <EvidenceHtmlBrief html={evidence.html} />
          ) : (
            <>
              <h1 className="ev-doc-title">{evidence.title}</h1>
              {evidence.metaLine && <div className="ev-doc-meta">{evidence.metaLine}</div>}
              <div className="ev-doc-sections">
                <EvidenceSections sections={evidence.sections} />
              </div>
            </>
          )
        ) : isLoading ? (
          <EmptyPane
            title="Generating evidence…"
            hint="Pulling the data-science slicing, infographics, qualitative signals, and hypothesis for this finding."
            placeholders={4}
          />
        ) : localState.kind === "error" ? (
          <>
            <EmptyPane
              title="Couldn't load full evidence"
              hint={localState.message}
              placeholders={0}
            />
            {detail?.meta ? (
              <div style={{ display: "flex", justifyContent: "center", marginTop: 12 }}>
                <button
                  type="button"
                  className="tkv2-btn tkv2-btn--regen"
                  onClick={retryEvidence}
                >
                  <IconRefresh size={15} /> Try again
                </button>
              </div>
            ) : null}
          </>
        ) : null}

      </div>
    </div>
  )
}

// ── Tickets: real PRD→tickets via the `ticket` skill, then push to a tracker ──
// One generated ticket card, styled to the locked design reference
// (backend/skills/user-stories/examples/sprntly-ticket-views.html). Click to
// open the editable in-panel detail (TicketDetail) — the generated story is the
// base, edits persist as overrides.
function StoryRow({ story, index, onOpen, synced, tool }: {
  story: GeneratedStory; index: number; onOpen: () => void; synced?: ClickUpTicketState; tool?: string
}) {
  const preview = story.user_story || story.body
  return (
    <button type="button" className="tkv2-card" onClick={onOpen}>
      <span className="tkv2-key">{`T-${index + 1}`}</span>
      <div className="tkv2-card-main">
        <div className="tkv2-card-title">{story.title}</div>
        {preview ? (
          <div className="tkv2-story">
            {preview}
            {story.prd_section ? <span className="ctx"> Context: {story.prd_section}</span> : null}
          </div>
        ) : null}
        {/* The row carries ONLY the ticket's tracker stage (priority + AC
            count live in the detail view). The chip shows the bare stage —
            the tool name sits in the tooltip. */}
        {synced?.status ? (
          <div className="tkv2-row">
            <span
              className="tkv2-synced"
              // Completion is category-driven (tracker metadata), so ANY
              // workspace's "done" status — "Shipped", "Released", … — reads
              // as complete without name matching.
              style={synced.status_category === "done" ? { color: "var(--green-d)" } : undefined}
              title={`${tool || "Tracker"} status${synced.assignee ? ` · Assignee: ${synced.assignee}` : ""}`}
            >
              {synced.status}
            </span>
          </div>
        ) : null}
      </div>
    </button>
  )
}

// ── Ticket trackers ──────────────────────────────────────────────────────────
// The task-management tools tickets can sync with — derived from the
// connector catalog's TYPES (connectors typed "task-management" that the
// backend sync engine implements), so the sync button follows the catalog
// instead of hardcoding providers. Adding a tool = type it in the catalog +
// a backend push/pull pair (app/stories/push.py) + a provider branch in
// `fetchDestinations` below.
const TRACKERS = ticketSyncTrackers() as { id: TrackerProvider; label: string }[]

const trackerLabel = (id: string | undefined | null): string =>
  TRACKERS.find((t) => t.id === id)?.label ?? "tracker"


/** "2026-07-10T12:00:00+00:00" → "just now" / "11 mins ago" / "3 hrs ago" / "Jul 8". */
export function relTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ""
  const secs = Math.max(0, (Date.now() - d.getTime()) / 1000)
  if (secs < 60) return "just now"
  const m = Math.floor(secs / 60)
  if (m < 60) return `${m} min${m !== 1 ? "s" : ""} ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} hr${h !== 1 ? "s" : ""} ago`
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

export function TicketsTab() {
  const { showToast } = useNavigation()
  const { content } = useContent()
  const router = useRouter()
  const prd = content.prd
  const prdId = prd?.prd_id ?? null
  const prdTitle = prd?.title ?? "PRD"
  // Which task-management tools this workspace has connected — drives the sync
  // button's label (one tool), its dropdown (several), or the connectors
  // redirect (none).
  const connectedTrackers = TRACKERS.filter((t) => content.connectedConnectorIds.includes(t.id))

  // ── Generation (PRD → tickets via the user-stories skill) ──────────────
  type GenState =
    | { kind: "idle" }
    | { kind: "generating" } // first-ever generation — nothing older to show
    | {
        kind: "ready"
        stories: GeneratedStory[]
        /** The PRD was edited: these are the PREVIOUS tickets, shown while
         *  the replacement set generates in the background. */
        refreshing?: boolean
        /** A background refresh failed — the old set stays, with this note. */
        refreshError?: string | null
        /** First-generation streaming: these tickets are a PARTIAL set arriving
         *  batch-by-batch (fan-out); more are still landing. */
        streaming?: boolean
        /** Batch progress while `streaming`, e.g. {done: 2, total: 4}. */
        progress?: { done: number; total: number }
      }
    | { kind: "error"; message: string }
  const [genState, setGenState] = useState<GenState>({ kind: "idle" })
  const stories = genState.kind === "ready" ? genState.stories : []
  const refreshing = genState.kind === "ready" && Boolean(genState.refreshing)
  const refreshError = genState.kind === "ready" ? genState.refreshError ?? null : null
  const streaming = genState.kind === "ready" && Boolean(genState.streaming)
  const streamProgress = genState.kind === "ready" ? genState.progress ?? null : null

  // Which ticket (if any) is open in the in-panel editable detail view.
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)

  // ── Tracker sync ─────────────────────────────────────────────────────────
  // One button, one server-side state. The FIRST push picks a destination
  // (registered on the backend); after that the backend auto-syncs on an
  // interval and the button shows "Syncing…" / "Synced Xm ago" and re-syncs
  // ad-hoc on click. `pickState` drives the choose-a-tool/-destination flow.
  type PickState =
    | { kind: "idle" }
    | { kind: "menu" } // choosing WHICH tool (several connected)
    | { kind: "fetching"; provider: TrackerProvider }
    | { kind: "picking"; provider: TrackerProvider; lists: ClickUpList[] }
    // Jira's destination step is a richer modal (project + issue type +
    // per-ticket assignees) rather than the compact list picker.
    | { kind: "picking-jira"; provider: "jira"; projects: JiraProject[] }
  const [pickState, setPickState] = useState<PickState>({ kind: "idle" })
  const [selectedListId, setSelectedListId] = useState<string>("")
  // null = not loaded yet for this PRD.
  const [syncState, setSyncState] = useState<TicketSyncState | null>(null)

  // Retry for a run that came back empty (a transient LLM failure): a nonce
  // re-runs the generation effect and the ref tells it to SKIP the cache read.
  // This is NOT a user-facing "regenerate" — PRD edits trigger regeneration
  // automatically via the stale-cache check below.
  const [regenNonce, setRegenNonce] = useState(0)
  const forceRegenRef = useRef(false)
  const regenerate = () => {
    forceRegenRef.current = true
    setRegenNonce((n) => n + 1)
  }

  // Tickets are persisted per PRD (keyed by a content hash of the rendered PRD).
  // On open / PRD change we READ the stored set first: if it's fresh (generated
  // from the PRD's current content) we render it instantly with no LLM call.
  // When the PRD has been EDITED since (stale), we keep showing the previous
  // set and regenerate in the background (stale-while-revalidate) — the new
  // set replaces it atomically when the job completes; a failure keeps the old
  // set with a quiet note. The full-screen spinner is reserved for the FIRST
  // generation, when there is nothing older to show.
  useEffect(() => {
    // A new PRD invalidates the open detail.
    setSelectedIndex(null)
    if (prdId == null) {
      setGenState({ kind: "idle" })
      return
    }
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    // The previous (stale) set shown while regenerating; null = nothing older.
    let prevStories: GeneratedStory[] | null = null
    // A deploy/restart can drop an in-flight (not-yet-persisted) job → the poll
    // 404s. Treat that as "work was lost" and re-kick generation (bounded)
    // rather than surfacing an error.
    let restarts = 0

    const fail = (e: unknown) => {
      if (cancelled) return
      const message = e instanceof Error ? e.message : "Couldn't generate tickets"
      // With a previous set on screen, a failed refresh must not nuke it.
      if (prevStories?.length) {
        setGenState({ kind: "ready", stories: prevStories, refreshError: message })
      } else {
        setGenState({ kind: "error", message })
      }
    }

    const poll = (jobId: number) => {
      storiesApi
        .getJob(jobId)
        .then((j) => {
          if (cancelled) return
          if (j.status === "ready") {
            // Swap in the fresh set; close any stale detail so an open ticket
            // can't point at the wrong story in the replaced list.
            if (prevStories?.length) setSelectedIndex(null)
            setGenState({ kind: "ready", stories: j.stories ?? [] })
          } else if (j.status === "failed") {
            fail(new Error(j.error || "Couldn't generate tickets"))
          } else {
            // Still generating. On a FIRST generation (nothing older on screen),
            // stream the partial set as fan-out batches land instead of holding a
            // blank spinner. While REFRESHING an edited PRD we keep the previous
            // complete set untouched — swapping it for a partial would flicker.
            if (!prevStories?.length && j.stories?.length) {
              setGenState({
                kind: "ready",
                stories: j.stories,
                streaming: true,
                progress: j.progress,
              })
            }
            timer = setTimeout(() => poll(jobId), 2000)
          }
        })
        .catch((e) => {
          if (!cancelled && e instanceof ApiError && e.status === 404 && restarts < 2) {
            restarts++
            start()
            return
          }
          fail(e)
        })
    }

    const start = () => {
      if (cancelled) return
      if (prevStories?.length) {
        setGenState({ kind: "ready", stories: prevStories, refreshing: true })
      } else {
        setGenState({ kind: "generating" })
      }
      storiesApi
        .generate(prdId)
        .then((r) => {
          if (!cancelled) poll(r.job_id)
        })
        .catch(fail)
    }

    setPickState({ kind: "idle" })

    // Empty-run retry forces a fresh set; skip the cache read entirely.
    const force = forceRegenRef.current
    forceRegenRef.current = false
    if (force) {
      setGenState({ kind: "generating" })
      start()
      return () => {
        cancelled = true
        if (timer) clearTimeout(timer)
      }
    }

    setGenState({ kind: "generating" })

    // Cache-first: serve the persisted set if it's still fresh; a STALE set
    // (the PRD was edited) stays on screen while the replacement generates.
    storiesApi
      .getForPrd(prdId)
      .then((cache) => {
        if (cancelled) return
        if (cache.status === "ready" && cache.fresh) {
          setGenState({ kind: "ready", stories: cache.stories })
        } else {
          if (cache.stories?.length) prevStories = cache.stories
          start()
        }
      })
      .catch((e) => {
        // The cache read failing shouldn't dead-end the tab — fall back to
        // generating (404/none is the common "first time" case anyway).
        if (cancelled) return
        if (e instanceof ApiError && e.status === 404) {
          start()
          return
        }
        fail(e)
      })

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [prdId, regenNonce])

  // ── Sync state: load per PRD, poll while a sync runs ─────────────────────
  // True while a push/registration flow is mid-flight (destination chosen but
  // the backend may not have registered it yet). A poll landing in that window
  // still describes the PREVIOUS binding (or none) and would clobber the
  // optimistic "Syncing with …" state — the button would bounce back to
  // "Push to Jira" and never flip to "Synced". Ignore those responses; the
  // push flow refreshes itself once registration settles.
  const registeringRef = useRef(false)
  const refreshSync = useCallback(() => {
    if (prdId == null) return
    storiesApi.getSyncState(prdId)
      .then((s) => {
        if (registeringRef.current) return
        setSyncState(s)
      })
      // Transient fetch failure must not downgrade a known-configured state.
      .catch(() => setSyncState((prev) => prev ?? { configured: false }))
  }, [prdId])

  useEffect(() => {
    setSyncState(null)
    refreshSync()
  }, [prdId, refreshSync])

  const syncing = syncState?.sync_status === "syncing"

  // While a sync runs, poll until it settles — the button then flips to
  // "Synced with <tool> just now" within a couple of seconds of completion.
  // Keyed on the BOOLEAN, not the state object: a failed poll (or an ignored
  // response above) leaves the state reference unchanged, and an object-keyed
  // effect would never re-arm — wedging the button on "Syncing…" forever.
  useEffect(() => {
    if (!syncing) return
    const t = setInterval(refreshSync, 2000)
    return () => clearInterval(t)
  }, [syncing, refreshSync])

  // Surface the outcome once when a sync settles (success toast / error
  // stays visible under the header).
  const wasSyncing = useRef(false)
  useEffect(() => {
    if (wasSyncing.current && !syncing && syncState) {
      if (syncState.last_error) {
        showToast("Sync finished with problems", syncState.last_error.slice(0, 120))
      } else if (syncState.last_synced_at) {
        showToast(`Synced with ${trackerLabel(syncState.provider)}`, "Tickets and statuses are up to date.")
      }
    }
    wasSyncing.current = syncing
  }, [syncing, syncState, showToast])

  // relTime() is computed at render, so without re-renders the button would
  // freeze on "Synced with Jira just now" — tick each minute to age it.
  const [, setAgeTick] = useState(0)
  useEffect(() => {
    if (!syncState?.last_synced_at || syncing) return
    const t = setInterval(() => setAgeTick((n) => n + 1), 60_000)
    return () => clearInterval(t)
  }, [syncState?.last_synced_at, syncing])

  // ── Tracker metadata: the connected tracker's REAL vocabulary ────────────
  // Loaded per PRD and passed into the ticket detail so tickets render the
  // workspace's own statuses/priorities/fields instead of the canned lists.
  // Works from the moment a tracker is CONNECTED (the backend serves the
  // connect-time-warmed cache even before any push binds a destination).
  // Best-effort: no meta → the detail falls back to defaults.
  const [trackerMeta, setTrackerMeta] =
    useState<{ provider: TrackerProvider; meta: TrackerMeta } | null>(null)
  useEffect(() => {
    if (prdId == null) { setTrackerMeta(null); return }
    let cancelled = false
    storiesApi.getTrackerMeta(prdId)
      .then((r) => {
        if (cancelled) return
        setTrackerMeta(r.meta && r.provider ? { provider: r.provider, meta: r.meta } : null)
      })
      .catch(() => { /* metadata is an enhancement, never a blocker */ })
    return () => { cancelled = true }
    // last_synced_at: every completed sync also re-pulled the vocabulary
    // server-side — re-read the cache so the UI shows workspace changes.
  }, [prdId, syncState?.configured, syncState?.destination_id, syncState?.last_synced_at])

  /** Ad-hoc sync of the already-configured destination (the button click). */
  const syncNow = async () => {
    if (prdId == null || syncing || !syncState?.configured) return
    // Hold the optimistic "Syncing…" against polls until the backend has
    // actually marked the run (triggerSync returning), then let polling own it.
    registeringRef.current = true
    setSyncState((s) => (s ? { ...s, sync_status: "syncing" } : s))
    try {
      await storiesApi.triggerSync(prdId)
      registeringRef.current = false
      refreshSync()
    } catch (e) {
      registeringRef.current = false
      refreshSync()
      showToast("Couldn't sync", e instanceof Error ? e.message.slice(0, 120) : "Try again.")
    }
  }

  /** First push (or tool switch): fetch the tool's destinations, then open
   *  its destination step — the compact list picker for ClickUp, the richer
   *  project/assignees modal for Jira. */
  const startPush = async (provider: TrackerProvider) => {
    if (pickState.kind === "fetching") return
    setPickState({ kind: "fetching", provider })
    try {
      if (provider === "jira") {
        const r = await storiesApi.listJiraProjects()
        if (r.projects.length === 0) {
          setPickState({ kind: "idle" })
          showToast("No Jira projects found", "Create a project in Jira first.")
          return
        }
        setPickState({ kind: "picking-jira", provider: "jira", projects: r.projects })
        return
      }
      // ClickUp lists and Asana projects share the compact list picker (both
      // return {lists:[{id,name}]}); only the destination-fetch call differs.
      const r = provider === "asana"
        ? await storiesApi.listAsanaProjects()
        : await storiesApi.listClickUpLists()
      if (r.lists.length === 0) {
        setPickState({ kind: "idle" })
        showToast(
          provider === "asana" ? "No Asana projects found" : "No ClickUp lists found",
          provider === "asana"
            ? "Create a project in Asana first."
            : "Create a list in ClickUp first.",
        )
        return
      }
      setSelectedListId(r.lists[0].id)
      setPickState({ kind: "picking", provider, lists: r.lists })
    } catch (e) {
      setPickState({ kind: "idle" })
      showToast("Couldn't load destinations", e instanceof Error ? e.message.slice(0, 120) : "Try again.")
    }
  }

  /** Destination chosen → register it server-side and run the first sync.
   *  From here on the backend auto-syncs this PRD on an interval. */
  const confirmDestination = async () => {
    if (prdId == null || pickState.kind !== "picking") return
    const list = pickState.lists.find((l) => l.id === selectedListId)
    if (!list) return
    const provider = pickState.provider
    setPickState({ kind: "idle" })
    registeringRef.current = true
    setSyncState((s) => ({
      ...(s ?? {}), configured: true, provider,
      destination_id: list.id, destination_name: list.name, sync_status: "syncing",
    }))
    try {
      await storiesApi.triggerSync(prdId, {
        provider, destination_id: list.id, destination_name: list.name,
      })
      registeringRef.current = false
      refreshSync()
    } catch (e) {
      registeringRef.current = false
      refreshSync()
      showToast("Couldn't start the sync", e instanceof Error ? e.message.slice(0, 120) : "Try again.")
    }
  }

  // A stable per-ticket key for the Jira assignee map (content id, else index).
  const storyKey = (s: GeneratedStory, i: number) => s.id ?? `idx-${i}`

  /** Jira destination chosen in the modal (project + issue type + per-ticket
   *  assignees): one assignee-carrying push first, THEN register the
   *  destination server-side and run the sync pass. The extra push exists
   *  because assignees are push-time-only (never generated); the sync engine
   *  updates content/status idempotently and never writes assignee, so the
   *  assignments persist. */
  const confirmJiraPush = async (choice: JiraPushChoice) => {
    if (prdId == null || pickState.kind !== "picking-jira") return
    const project = pickState.projects.find((p) => p.key === choice.projectKey)
    const destinationName = project?.name ?? choice.projectKey
    setPickState({ kind: "idle" })
    // The push itself can take a while — hold the optimistic "Syncing with
    // Jira…" state against mid-flight polls until the destination is
    // registered (triggerSync below), so the button flips straight from
    // Syncing → Synced instead of bouncing back to "Push to Jira".
    registeringRef.current = true
    setSyncState((s) => ({
      ...(s ?? {}), configured: true, provider: "jira",
      destination_id: choice.projectKey, destination_name: destinationName,
      sync_status: "syncing",
    }))
    try {
      const withAssignee = stories.map((s, i) => ({
        ...s,
        assignee_account_id: choice.assigneeByKey[storyKey(s, i)] || null,
      }))
      const result = await storiesApi.pushToJira(choice.projectKey, withAssignee, choice.issueType)
      if (result.errors.length > 0) {
        showToast("Jira push partial", `${result.created.length} created, ${result.errors.length} failed.`)
      }
      await storiesApi.triggerSync(prdId, {
        provider: "jira", destination_id: choice.projectKey, destination_name: destinationName,
      })
      registeringRef.current = false
      refreshSync()
    } catch (e) {
      registeringRef.current = false
      refreshSync()
      showToast("Jira push failed", e instanceof Error ? e.message.slice(0, 120) : "Try again.")
    }
  }

  /** No tracker connected → the button takes the user to the connectors page. */
  const goToConnectors = () => router.push("/settings?section=connectors")

  if (prdId == null) {
    return (
      <div className="cpanel-empty">
        <IconSparkle size={20} />
        <p>Ticket creation — generate a PRD first, then tickets are drafted from it.</p>
      </div>
    )
  }

  if (genState.kind === "generating") {
    return (
      <div className="cpanel-empty" data-testid="tickets-generating">
        <span className="prd-loader" aria-hidden />
        <p>Breaking <em>{prdTitle}</em> into tickets…</p>
      </div>
    )
  }

  if (genState.kind === "error") {
    return (
      <div className="cpanel-empty" data-testid="tickets-error">
        <IconSparkle size={20} />
        <p>Couldn&apos;t generate tickets: {genState.message}</p>
      </div>
    )
  }

  // A ready-but-empty result means generation didn't return any tickets (a
  // transient/truncated run — a real PRD always yields some). Don't show the
  // "0 tickets" success chrome; offer a retry instead. The empty set was not
  // cached (backend), so Regenerate re-runs cleanly.
  if (genState.kind === "ready" && stories.length === 0) {
    return (
      <div className="cpanel-empty" data-testid="tickets-empty">
        <IconSparkle size={20} />
        <p>No tickets came back from that run. This is usually transient — try again.</p>
        <button type="button" className="tkv2-btn tkv2-btn--regen" style={{ marginTop: 12 }} onClick={regenerate}>
          <IconRefresh size={15} /> Regenerate
        </button>
      </div>
    )
  }

  // ── The unified tracker button's face ─────────────────────────────────────
  // One button carries the whole lifecycle: connect (nothing connected) →
  // push (connected, never pushed) → syncing/synced (configured; click = sync
  // now). With several tools connected the button opens a tool menu instead.
  const currentTool = trackerLabel(syncState?.provider)
  // A binding to a DISCONNECTED tool (e.g. Jira unplugged after binding) must
  // not keep showing "Sync with Jira" — fall through to the push flow so the
  // user can rebind to a connected tracker (the first push replaces the
  // binding and pulls the new destination's metadata).
  const boundProviderConnected =
    syncState?.configured === true &&
    connectedTrackers.some((t) => t.id === syncState.provider)
  const trackerBtn = (() => {
    if (connectedTrackers.length === 0) {
      return {
        label: <><IconPlugConnected size={15} /> Connect a tracker</>,
        title: "Connect ClickUp or Jira to push and sync these tickets",
        onClick: goToConnectors, disabled: false,
      }
    }
    if (boundProviderConnected) {
      const when = syncState?.last_synced_at ? relTime(syncState.last_synced_at) : null
      return {
        label: syncing
          ? <><span className="tkv2-spin" aria-hidden><IconRefresh size={15} /></span> Syncing with {currentTool}…</>
          : <><IconRefresh size={15} /> {when ? `Synced with ${currentTool} ${when}` : `Sync with ${currentTool} now`}</>,
        title: `Synced with ${currentTool}${syncState?.destination_name ? ` · ${syncState.destination_name}` : ""} — auto-syncs in the background; click to sync now`,
        onClick: syncNow, disabled: syncing || syncState == null,
      }
    }
    if (connectedTrackers.length === 1) {
      const t = connectedTrackers[0]
      return {
        label: <>✓ {pickState.kind === "fetching" ? "Loading…" : `Push to ${t.label}`}</>,
        title: syncState?.configured
          ? `Push these tickets to ${t.label} — replaces the ${currentTool} binding and keeps them in sync automatically`
          : `Push these tickets to ${t.label} — after the first push they stay in sync automatically`,
        onClick: () => void startPush(t.id), disabled: pickState.kind === "fetching" || syncState == null,
      }
    }
    return {
      label: <>✓ Push to tracker <IconChevronDown size={14} /></>,
      title: "Pick which task-management tool to push these tickets to",
      onClick: () => setPickState((p) => (p.kind === "menu" ? { kind: "idle" } : { kind: "menu" })),
      disabled: pickState.kind === "fetching" || syncState == null,
    }
  })()

  // A ticket is open → show the editable detail in place of the list.
  const selectedStory = selectedIndex != null ? stories[selectedIndex] : null
  if (selectedStory && prdId != null) {
    // Linked issues reference sibling tickets BY TITLE (the generator's
    // blocked_by/blocks contract) — resolve the title to its story in this
    // PRD's set and open it in place.
    const openLinked = (title: string) => {
      const want = title.trim().toLowerCase()
      const idx = stories.findIndex((s) => (s.title || "").trim().toLowerCase() === want)
      if (idx >= 0) setSelectedIndex(idx)
    }
    return (
      <div className="tkt-list-wrap">
        <TicketDetail
          // Remount per ticket: a linked-issue jump swaps the story prop on a
          // mounted detail, and its useState seeds would otherwise stay stale.
          key={`tk-${prdId}-${selectedIndex}`}
          story={selectedStory}
          index={selectedIndex as number}
          prdId={prdId}
          onBack={() => setSelectedIndex(null)}
          onOpenLinked={openLinked}
          tracker={trackerMeta ? {
            provider: trackerMeta.provider,
            meta: trackerMeta.meta,
            synced: selectedStory.id ? syncState?.statuses?.[selectedStory.id] : undefined,
          } : undefined}
        />
      </div>
    )
  }

  return (
    <div className="tkv2 tkt-list-wrap">
      {/* Header block — serif title, subline, then the tracker action. ONE
          button covers connect → first push → synced (see trackerBtn above);
          the first push registers the destination and the backend keeps it
          synced automatically from then on. Regeneration has no button — a
          PRD edit triggers it automatically (stale-while-revalidate above). */}
      <div className="tkv2-topbar">
        <h2>Tickets from <em>{prdTitle}</em></h2>
        <div className="tkv2-sub">
          {stories.length} ticket{stories.length !== 1 ? "s" : ""} · generated from the PRD
        </div>
        {stories.length > 0 && (
          <div className="tkv2-hactions">
            <div style={{ position: "relative", display: "inline-flex" }}>
              <button
                type="button"
                className={`tkv2-btn ${syncState?.configured && connectedTrackers.length > 0 ? "tkv2-btn--sync" : "tkv2-btn--push"}`}
                onClick={trackerBtn.onClick}
                // Also locked while a PRD edit is regenerating the set —
                // pushing tickets that are about to be replaced would orphan
                // their tracker mappings.
                disabled={trackerBtn.disabled || refreshing}
                title={refreshing ? "Tickets are updating from the edited PRD…" : trackerBtn.title}
              >
                {trackerBtn.label}
              </button>
              {/* Tool menu — several trackers connected: pick which to sync with.
                  Also reachable from a configured button via its dropdown row. */}
              {pickState.kind === "menu" && (
                <>
                  <div onClick={() => setPickState({ kind: "idle" })} style={{ position: "fixed", inset: 0, zIndex: 30 }} aria-hidden />
                  {/* Left-anchored like the destination picker — the trigger
                      sits at the panel's left, so right-anchoring clips. */}
                  <div className="tkv2-picker" style={{ position: "absolute", top: "100%", left: 0, zIndex: 31, minWidth: 220, maxWidth: "min(340px, calc(100vw - 32px))" }} role="menu">
                    <div className="ph2">Sync these tickets with…</div>
                    {connectedTrackers.map((t) => (
                      <button key={t.id} type="button" className={`tkv2-pitem${syncState?.provider === t.id ? " tkv2-pitem--sel" : ""}`}
                        onClick={() => void startPush(t.id)}>
                        {t.label}
                        {syncState?.provider === t.id ? <span className="tkv2-ppath">current</span> : null}
                      </button>
                    ))}
                  </div>
                </>
              )}
              {pickState.kind === "picking" && (
                <DestinationPicker
                  tool={trackerLabel(pickState.provider)}
                  lists={pickState.lists}
                  selectedId={selectedListId}
                  onSelect={setSelectedListId}
                  count={stories.length}
                  onPush={() => void confirmDestination()}
                  onCancel={() => setPickState({ kind: "idle" })}
                />
              )}
              {/* Jira's destination step: project + issue type + per-ticket
                  assignees, then push + register the sync destination. */}
              {pickState.kind === "picking-jira" && (
                <JiraPushModal
                  items={stories.map((s, i) => ({ key: storyKey(s, i), title: s.title }))}
                  projects={pickState.projects}
                  initialProjectKey={syncState && syncState.provider === "jira" ? syncState.destination_id ?? null : null}
                  loadMembers={async (projectKey) => (await storiesApi.listJiraMembers(projectKey)).members}
                  onPush={(choice) => void confirmJiraPush(choice)}
                  onCancel={() => setPickState({ kind: "idle" })}
                  busy={false}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* Regeneration + sync status lines (under the header). */}
      {streaming && (
        <div className="tkt-push-status" data-testid="tickets-streaming">
          <span className="tkv2-spin" aria-hidden style={{ verticalAlign: "-2px", marginRight: 6 }}><IconRefresh size={13} /></span>
          Generating tickets{streamProgress ? ` — batch ${streamProgress.done} of ${streamProgress.total}` : ""}. Showing them as they land…
        </div>
      )}
      {refreshing && (
        <div className="tkt-push-status">
          <span className="tkv2-spin" aria-hidden style={{ verticalAlign: "-2px", marginRight: 6 }}><IconRefresh size={13} /></span>
          The PRD changed — updating these tickets. Showing the previous set until the new one is ready.
        </div>
      )}
      {!refreshing && refreshError && (
        <div className="tkt-push-status tkt-push-status--err">
          Couldn&apos;t update the tickets from the edited PRD ({refreshError}) — still showing the previous set; reopen the tab to retry.
        </div>
      )}
      {syncing && (
        <div className="tkt-push-status">
          Syncing {stories.length} ticket{stories.length !== 1 ? "s" : ""} with {currentTool}
          {syncState?.destination_name ? ` · “${syncState.destination_name}”` : ""}…
        </div>
      )}
      {!syncing && syncState?.last_error && (
        <div className="tkt-push-status tkt-push-status--err">
          Last sync had problems: {syncState.last_error} — click the sync button to retry.
        </div>
      )}
      {/* Bound tool got disconnected (e.g. Jira unplugged, ClickUp now
          connected) — say why the button flipped back to Push. */}
      {syncState?.configured && !boundProviderConnected && connectedTrackers.length > 0 && (
        <div className="tkt-push-status">
          These tickets were syncing with {currentTool}, which is no longer
          connected — push to {connectedTrackers[0].label} to switch trackers.
        </div>
      )}

      <div className="tkv2-intro">
        <span className="tkv2-spark">✳</span>
        <div>
          I&apos;ve broken <em>{prdTitle}</em> into{" "}
          <b>{stories.length} implementable ticket{stories.length !== 1 ? "s" : ""}</b> — scoped and
          prioritized from the PRD. Review, then push to your tracker.
        </div>
      </div>

      <div className="tkt-list">
        {stories.map((s, i) => (
          <StoryRow
            key={i} story={s} index={i} onOpen={() => setSelectedIndex(i)}
            synced={s.id ? syncState?.statuses?.[s.id] : undefined}
            tool={currentTool}
          />
        ))}
      </div>

      <div className="tkv2-foot">
        Tickets are generated from the PRD.
        {connectedTrackers.length === 0 && " Connect ClickUp or Jira to push them — the button above takes you there."}
        {syncState?.configured && ` Synced with ${currentTool} every few minutes — edits and status changes flow both ways, newest edit wins.`}
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
