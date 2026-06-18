"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { EvidenceSections } from "./EvidenceSections"
import { EmptyPane } from "./EmptyPane"
import { IconClose, IconSparkle } from "./app-icons"
import { runEvidenceGeneration, loadEvidenceByInsight } from "../../lib/runEvidenceGeneration"
import { runPrdGeneration } from "../../lib/runPrdGeneration"
import { storiesApi, type ClickUpList, type GeneratedStory } from "../../lib/api"
import { PrdPanelContent } from "./PrdPanelContent"
import { ArtifactFooterActions } from "./ArtifactFooterActions"
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
  const { content } = useContent()

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
            <span className="cpanel-main-name">{content.prd?.title ? `PRD · ${content.prd.title}` : "PRD"}</span>
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
  const prdEvidenceKeyRef = useRef<string | null>(null)

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
    if (prdEvidenceKeyRef.current === key && evidence) return
    if (prdEvidenceKeyRef.current !== key) setContent({ evidence: null })
    prdEvidenceKeyRef.current = key
    let cancelled = false
    loadEvidenceByInsight(prdMeta.briefId, prdMeta.insightIndex)
      .then((ev) => {
        if (!cancelled && ev) setContent({ evidence: ev })
      })
      .catch(() => {
        /* read-only best effort — leave the panel's empty/generate state */
      })
    return () => {
      cancelled = true
    }
  }, [detail?.meta, prdMeta?.briefId, prdMeta?.insightIndex, evidence, setContent])

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

        {evidence && <ArtifactFooterActions current="evidence" />}
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

// ── Tickets: real PRD→tickets via the user-stories skill, then push to ClickUp ─
// Priority comes back from the generator as P0–P3 or ClickUp's urgent/high/…;
// map either to a colour, defaulting to neutral.
const STORY_PRIORITY_COLOR: Record<string, string> = {
  P0: "#C13838", P1: "#D97706", P2: "#2563EB", P3: "#6B7280",
  URGENT: "#C13838", HIGH: "#D97706", NORMAL: "#2563EB", LOW: "#6B7280",
}
function storyPriorityColor(p: string | null): string {
  return STORY_PRIORITY_COLOR[(p ?? "").toUpperCase()] ?? "#6B7280"
}

// One generated ticket row (read-only — the source of truth is the PRD; edits
// happen in ClickUp after the push).
function StoryRow({ story, index }: { story: GeneratedStory; index: number }) {
  const priority = (story.priority ?? "").toUpperCase()
  const color = storyPriorityColor(story.priority)
  return (
    <div className="tkt-row tkt-row--static">
      <div className="tkt-row-left">
        <div className="tkt-row-id-wrap">
          <span className="tkt-row-id">{`T-${index + 1}`}</span>
        </div>
        <div className="tkt-row-main">
          <div className="tkt-row-title">{story.title}</div>
          {story.body ? <div className="tkt-row-desc">{story.body}</div> : null}
          <div className="tkt-row-tags">
            {priority ? (
              <span
                className="tkt-tag tkt-tag--priority"
                style={{ color, background: `${color}14`, borderColor: `${color}33` }}
              >
                {priority}
              </span>
            ) : null}
            {story.acceptance_criteria.length > 0 ? (
              <span className="tkt-tag">{story.acceptance_criteria.length} AC</span>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}

export function TicketsTab() {
  const { showToast } = useNavigation()
  const { content } = useContent()
  const prd = content.prd
  const prdId = prd?.prd_id ?? null
  const prdTitle = prd?.title ?? "PRD"
  const isClickUpConnected = content.connectedConnectorIds.includes("clickup")

  // ── Generation (PRD → tickets via the user-stories skill) ──────────────
  type GenState =
    | { kind: "idle" }
    | { kind: "generating" }
    | { kind: "ready"; stories: GeneratedStory[] }
    | { kind: "error"; message: string }
  const [genState, setGenState] = useState<GenState>({ kind: "idle" })
  const stories = genState.kind === "ready" ? genState.stories : []

  // ── ClickUp push ───────────────────────────────────────────────────────
  type PushState =
    | { kind: "idle" }
    | { kind: "fetching-lists" }
    | { kind: "picking"; lists: ClickUpList[] }
    | { kind: "pushing"; listName: string }
    | { kind: "done"; created: number; errors: number }
    | { kind: "error"; message: string }
  const [pushState, setPushState] = useState<PushState>({ kind: "idle" })
  const [selectedListId, setSelectedListId] = useState<string>("")

  // Break the current PRD into tickets on open / whenever the PRD changes.
  useEffect(() => {
    if (prdId == null) {
      setGenState({ kind: "idle" })
      return
    }
    let cancelled = false
    setGenState({ kind: "generating" })
    setPushState({ kind: "idle" })
    storiesApi
      .generate(prdId)
      .then((r) => {
        if (!cancelled) setGenState({ kind: "ready", stories: r.stories })
      })
      .catch((e) => {
        if (!cancelled) {
          const msg = e instanceof Error ? e.message : "Couldn't generate tickets"
          setGenState({ kind: "error", message: msg })
        }
      })
    return () => {
      cancelled = true
    }
  }, [prdId])

  const handleClickUpPush = async () => {
    if (stories.length === 0) return
    if (pushState.kind === "fetching-lists" || pushState.kind === "pushing") return
    // List already chosen → push the generated tickets directly.
    if (pushState.kind === "picking" && selectedListId) {
      const list = pushState.lists.find((l) => l.id === selectedListId)
      setPushState({ kind: "pushing", listName: list?.name ?? selectedListId })
      try {
        const result = await storiesApi.pushToClickUp(selectedListId, stories)
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
    // First click → fetch the lists to pick a target.
    setPushState({ kind: "fetching-lists" })
    try {
      const r = await storiesApi.listClickUpLists()
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

  return (
    <div className="tkt-list-wrap">
      <div className="tkt-intro-box">
        <IconSparkle size={14} />
        <p>
          I&apos;ve broken <em>{prdTitle}</em> into{" "}
          <strong>{stories.length} implementable ticket{stories.length !== 1 ? "s" : ""}</strong> — scoped and
          prioritized from the PRD. Review, then push to ClickUp.
        </p>
      </div>

      {/* ── ClickUp sync banner ── */}
      {isClickUpConnected && pushState.kind !== "done" && stories.length > 0 && (
        <div style={{
          margin: "0 0 12px", padding: "10px 14px", borderRadius: 8,
          background: "#f0faf5", border: "1px solid #b2e0ca",
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#179463" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ flexShrink: 0 }}>
            <polyline points="20 6 9 17 4 12" />
          </svg>
          <span style={{ fontSize: 12.5, color: "#0E6E49", flex: 1 }}>
            {pushState.kind === "idle" && "ClickUp connected — push these tickets to your workspace."}
            {pushState.kind === "fetching-lists" && "Fetching ClickUp lists…"}
            {pushState.kind === "pushing" && `Pushing to "${pushState.listName}"…`}
            {pushState.kind === "error" && <span style={{ color: "#C13838" }}>{pushState.message}</span>}
            {pushState.kind === "picking" && (
              <select
                value={selectedListId}
                onChange={(e) => setSelectedListId(e.target.value)}
                style={{ fontSize: 12, padding: "3px 6px", borderRadius: 5, border: "1px solid #b2e0ca", background: "#fff", marginRight: 6 }}
              >
                {(pushState as { kind: "picking"; lists: ClickUpList[] }).lists.map((l) => (
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
              color: "#fff", border: "none",
              cursor: pushState.kind === "fetching-lists" || pushState.kind === "pushing" ? "not-allowed" : "pointer",
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
        <span className="tkt-list-meta">{stories.length} ticket{stories.length !== 1 ? "s" : ""} · generated from the PRD</span>
      </div>

      <div className="tkt-list">
        {stories.map((s, i) => (
          <StoryRow key={i} story={s} index={i} />
        ))}
      </div>

      <div className="tkt-list-foot">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
        <span>Tickets are generated from the PRD.{!isClickUpConnected && " Connect ClickUp in Settings to push them."}</span>
      </div>

      <ArtifactFooterActions current="tickets" />
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
