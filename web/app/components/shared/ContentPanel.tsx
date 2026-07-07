"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { EvidenceSections } from "./EvidenceSections"
import { EvidenceHtmlBrief } from "./EvidenceHtmlBrief"
import { EmptyPane } from "./EmptyPane"
import { IconClose, IconSparkle } from "./app-icons"
import { runEvidenceGeneration, loadEvidenceByInsight } from "../../lib/runEvidenceGeneration"
import { ApiError, storiesApi, type ClickUpList, type ClickUpTicketState, type GeneratedStory } from "../../lib/api"
import { PrdPanelContent } from "./PrdPanelContent"
import { TicketDetail, priorityPill } from "./TicketDetail"
import { StoryMap, storyMapSizing } from "./StoryMap"
import { DestinationPicker } from "./DestinationPicker"

// Per-PRD push destination ("remember for this PRD"). Persisted client-side so a
// second push for the same PRD goes straight to the remembered list without
// re-opening the picker. Keyed by PRD id; scoped to this browser for now
// (server-side per-workspace persistence is a follow-up).
function rememberedDest(prdId: number | null): string | null {
  if (prdId == null || typeof window === "undefined") return null
  try {
    return window.localStorage.getItem(`sprntly_ticket_dest_${prdId}`)
  } catch {
    return null
  }
}
function saveRememberedDest(prdId: number | null, listId: string): void {
  if (prdId == null || typeof window === "undefined") return
  try {
    window.localStorage.setItem(`sprntly_ticket_dest_${prdId}`, listId)
  } catch {
    /* storage unavailable — the choice just won't persist */
  }
}
import { IconMicroscope, IconFileText, IconTicket, IconDeviceFloppy, IconShare, IconMail, IconFileTypePdf, IconFileTypeDocx } from "@tabler/icons-react"
import { buildPrdMailto, downloadPrdPdf, downloadPrdDocx, printPrdHtml, downloadPrdHtmlDoc } from "../../lib/prdExport"
import type { PrdState } from "../../types/content"

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

// Header Share dropdown — Email (mailto) / Download PDF (jsPDF) / Download DOCX
// (docx). Enabled only when a PRD is loaded. The heavy generators are
// lazy-imported inside the handlers (see lib/prdExport).
function ShareMenu({ prd, onToast }: { prd: PrdState | null; onToast: (title: string, sub: string) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const enabled = !!prd

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDocClick)
    return () => document.removeEventListener("mousedown", onDocClick)
  }, [open])

  const handleEmail = () => {
    if (!prd) return
    setOpen(false)
    const link = typeof window !== "undefined" ? window.location.href : ""
    window.location.href = buildPrdMailto(prd.title, link)
  }
  const handlePdf = async () => {
    if (!prd) return
    setOpen(false)
    try {
      // v3 HTML PRD: print the page itself (its print stylesheet strips the
      // editing chrome) rather than the empty parsed-section PDF.
      if (prd.html) printPrdHtml(prd)
      else await downloadPrdPdf(prd)
    } catch {
      onToast("PDF export failed", "Could not generate the PDF. Please try again.")
    }
  }
  const handleDocx = async () => {
    if (!prd) return
    setOpen(false)
    try {
      // v3 HTML PRD exports as an HTML .doc (Word opens it directly), keeping
      // the visual system; markdown PRDs use the docx builder.
      if (prd.html) await downloadPrdHtmlDoc(prd)
      else await downloadPrdDocx(prd)
    } catch {
      onToast("DOCX export failed", "Could not generate the document. Please try again.")
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
          <div className="share-menu-item" role="menuitem" onClick={handleEmail}>
            <div className="share-menu-item-icon"><IconMail size={14} /></div>
            <div>
              <div style={{ fontWeight: 600 }}>Email</div>
              <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>Draft a mail with a link</div>
            </div>
          </div>
          <div className="share-menu-divider" />
          <div className="share-menu-item" role="menuitem" onClick={handlePdf}>
            <div className="share-menu-item-icon"><IconFileTypePdf size={14} /></div>
            <div>
              <div style={{ fontWeight: 600 }}>Download PDF</div>
              <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>Export as .pdf</div>
            </div>
          </div>
          <div className="share-menu-item" role="menuitem" onClick={handleDocx}>
            <div className="share-menu-item-icon"><IconFileTypeDocx size={14} /></div>
            <div>
              <div style={{ fontWeight: 600 }}>Download DOCX</div>
              <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>Export as .docx</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export function ContentPanel() {
  const { contentPanelTab, openContentPanel, closeContentPanel, showToast } = useNavigation()
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
            <ShareMenu prd={content.prd} onToast={showToast} />
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
          <EmptyPane
            title="Couldn't load full evidence"
            hint={localState.message}
            placeholders={0}
          />
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
function StoryRow({ story, index, onOpen, synced }: {
  story: GeneratedStory; index: number; onOpen: () => void; synced?: ClickUpTicketState
}) {
  const pill = priorityPill(story.priority)
  const preview = story.user_story || story.body
  const acCount = story.acceptance_criteria.length
  const type = story.ticket_type ?? "build"
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
        <div className="tkv2-row">
          {type !== "build" ? <span className={`tkv2-typechip tkv2-typechip--${type}`}>{type}</span> : null}
          <span className={`tkv2-pill tkv2-pill--${pill.variant}`}>{pill.label}</span>
          {acCount > 0 ? <span className="tkv2-acchip">{acCount} AC</span> : null}
          {synced?.status ? (
            <span className="tkv2-synced" title={synced.assignee ? `Assignee: ${synced.assignee}` : undefined}>
              ⟳ ClickUp: {synced.status}
            </span>
          ) : null}
        </div>
      </div>
    </button>
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

  // Which ticket (if any) is open in the in-panel editable detail view.
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  // List vs. Story-map view (the toggle only appears when the sizing gate fires).
  const [view, setView] = useState<"list" | "map">("list")

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
  // "Remember for this PRD" toggle in the destination picker.
  const [rememberDest, setRememberDest] = useState<boolean>(true)
  // Current ClickUp state pulled back per ticket id (bidirectional sync).
  const [syncedStatuses, setSyncedStatuses] = useState<Record<string, ClickUpTicketState>>({})
  const [syncing, setSyncing] = useState(false)

  // Manual regenerate: tickets are cached per PRD and only auto-regenerate when
  // the PRD changes, so give the user an explicit way to force a fresh set. A
  // nonce re-runs the generation effect; the ref tells it to SKIP the cache read
  // and regenerate (vs the normal cache-first path on PRD change).
  const [regenNonce, setRegenNonce] = useState(0)
  const forceRegenRef = useRef(false)
  const regenerate = () => {
    forceRegenRef.current = true
    setRegenNonce((n) => n + 1)
  }

  // Tickets are persisted per PRD (keyed by a content hash of the rendered PRD).
  // On open / PRD change we READ the stored set first: if it's fresh (generated
  // from the PRD's current content) we render it instantly with no LLM call. Only
  // when there's no row, or the PRD has changed since (stale), or a prior run
  // failed, do we (re)generate — fire-and-forget on the backend (a multi-minute
  // call), so we kick it off, get a job id, then POLL until ready/failed. The
  // backend re-persists on completion, so the next open is a cache hit.
  useEffect(() => {
    // A new PRD (or a regenerate) invalidates the open detail.
    setSelectedIndex(null)
    if (prdId == null) {
      setGenState({ kind: "idle" })
      return
    }
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    // A deploy/restart can drop an in-flight (not-yet-persisted) job → the poll
    // 404s. Treat that as "work was lost" and re-kick generation (bounded)
    // rather than surfacing an error.
    let restarts = 0

    const fail = (e: unknown) => {
      if (cancelled) return
      setGenState({
        kind: "error",
        message: e instanceof Error ? e.message : "Couldn't generate tickets",
      })
    }

    const poll = (jobId: number) => {
      storiesApi
        .getJob(jobId)
        .then((j) => {
          if (cancelled) return
          if (j.status === "ready") {
            setGenState({ kind: "ready", stories: j.stories ?? [] })
          } else if (j.status === "failed") {
            setGenState({ kind: "error", message: j.error || "Couldn't generate tickets" })
          } else {
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
      setGenState({ kind: "generating" })
      storiesApi
        .generate(prdId)
        .then((r) => {
          if (!cancelled) poll(r.job_id)
        })
        .catch(fail)
    }

    setPushState({ kind: "idle" })
    setGenState({ kind: "generating" })

    // Manual "Regenerate" forces a fresh set; skip the cache read entirely.
    const force = forceRegenRef.current
    forceRegenRef.current = false
    if (force) {
      start()
      return () => {
        cancelled = true
        if (timer) clearTimeout(timer)
      }
    }

    // Cache-first: serve the persisted set if it's still fresh, else regenerate.
    storiesApi
      .getForPrd(prdId)
      .then((cache) => {
        if (cancelled) return
        if (cache.status === "ready" && cache.fresh) {
          setGenState({ kind: "ready", stories: cache.stories })
        } else {
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

  const handleClickUpPush = async () => {
    if (stories.length === 0) return
    if (pushState.kind === "fetching-lists" || pushState.kind === "pushing") return
    // The push button is always shown at the top; if ClickUp isn't connected,
    // point the user to Settings rather than failing with a raw API error.
    if (!isClickUpConnected) {
      showToast("ClickUp not connected", "Connect ClickUp in Settings to push these tickets.")
      return
    }
    // First click → fetch the lists. If this PRD already has a remembered
    // destination, push straight to it; otherwise open the picker.
    setPushState({ kind: "fetching-lists" })
    try {
      const r = await storiesApi.listClickUpLists()
      if (r.lists.length === 0) {
        setPushState({ kind: "error", message: "No ClickUp lists found. Create a list in ClickUp first." })
        return
      }
      const remembered = rememberedDest(prdId)
      if (remembered && r.lists.some((l) => l.id === remembered)) {
        const list = r.lists.find((l) => l.id === remembered)
        await pushToList(remembered, list?.name ?? remembered)
        return
      }
      setSelectedListId(r.lists[0].id)
      setPushState({ kind: "picking", lists: r.lists })
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Unknown error"
      setPushState({ kind: "error", message: msg })
    }
  }

  // Push the reviewed tickets to a chosen list (the field-mapped sync runs on the
  // backend). Persists the destination when "remember for this PRD" is on.
  const pushToList = async (listId: string, listName: string) => {
    if (rememberDest) saveRememberedDest(prdId, listId)
    setPushState({ kind: "pushing", listName })
    try {
      const result = await storiesApi.pushToClickUp(listId, stories)
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
  }

  // Bidirectional read: pull the current ClickUp state for tickets already
  // synced to this PRD's remembered list, and surface it on the cards.
  const handleSyncFromClickUp = async () => {
    if (syncing) return
    if (!isClickUpConnected) {
      showToast("ClickUp not connected", "Connect ClickUp in Settings first.")
      return
    }
    const listId = rememberedDest(prdId)
    if (!listId) {
      showToast("Nothing to sync yet", "Push these tickets to ClickUp first, then sync brings their status back.")
      return
    }
    const ticketIds = stories.map((s) => s.id).filter((x): x is string => Boolean(x))
    if (ticketIds.length === 0) return
    setSyncing(true)
    try {
      const { statuses } = await storiesApi.pullClickUpStatus(listId, ticketIds)
      setSyncedStatuses(statuses)
      const n = Object.keys(statuses).length
      showToast(n ? "Synced from ClickUp" : "No synced tickets found",
        n ? `Updated status for ${n} ticket${n !== 1 ? "s" : ""}.` : "None of these tickets are in ClickUp yet.")
    } catch (e) {
      showToast("Couldn't sync from ClickUp", e instanceof Error ? e.message.slice(0, 120) : "Try again.")
    } finally {
      setSyncing(false)
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

  // A ready-but-empty result means generation didn't return any tickets (a
  // transient/truncated run — a real PRD always yields some). Don't show the
  // "0 tickets" success chrome; offer a retry instead. The empty set was not
  // cached (backend), so Regenerate re-runs cleanly.
  if (genState.kind === "ready" && stories.length === 0) {
    return (
      <div className="cpanel-empty" data-testid="tickets-empty">
        <IconSparkle size={20} />
        <p>No tickets came back from that run. This is usually transient — try again.</p>
        <button type="button" className="tkv2-btn tkv2-btn--push" style={{ marginTop: 12 }} onClick={regenerate}>
          ⟳ Regenerate
        </button>
      </div>
    )
  }

  const pushLabel =
    pushState.kind === "fetching-lists" ? "Loading…"
      : pushState.kind === "pushing" ? "Pushing…"
      : pushState.kind === "error" ? "Retry"
      : pushState.kind === "done" ? "Push again"
      : "Push to ClickUp"

  // A ticket is open → show the editable detail in place of the list.
  const selectedStory = selectedIndex != null ? stories[selectedIndex] : null
  if (selectedStory && prdId != null) {
    return (
      <div className="tkt-list-wrap">
        <TicketDetail
          story={selectedStory}
          index={selectedIndex as number}
          prdId={prdId}
          onBack={() => setSelectedIndex(null)}
        />
      </div>
    )
  }

  // Split build tickets from decision/spike tickets so the latter render as a
  // separate group, per the reference — while preserving each ticket's real
  // index into `stories` (that's what opens the right detail).
  const withIdx = stories.map((s, i) => ({ s, i }))
  const builds = withIdx.filter((x) => (x.s.ticket_type ?? "build") === "build")
  const decisions = withIdx.filter((x) => x.s.ticket_type === "decision" || x.s.ticket_type === "spike")

  // Story-map sizing gate (computed from the placed tickets). When it fires, a
  // Tickets ⟷ Story map toggle appears and the intro states the call; otherwise
  // the set is flat and only the list shows.
  const sizing = storyMapSizing(stories)
  const showMap = sizing.build
  const activeView = showMap ? view : "list"

  return (
    <div className="tkv2 tkt-list-wrap">
      {/* Header block — serif title, subline, then a Regenerate + Push actions
          row. Push is explicit (only on click); when the tracker isn't
          connected the handler points the user to Settings. */}
      <div className="tkv2-topbar">
        <h2>Tickets from <em>{prdTitle}</em></h2>
        <div className="tkv2-sub">
          {stories.length} ticket{stories.length !== 1 ? "s" : ""} · generated from the PRD
        </div>
        {stories.length > 0 && (
          <div className="tkv2-hactions">
            <button type="button" className="tkv2-btn tkv2-btn--regen" onClick={regenerate} title="Regenerate tickets from the current PRD">
              ⟳ Regenerate
            </button>
            {isClickUpConnected && (
              <button type="button" className="tkv2-btn tkv2-btn--regen" onClick={handleSyncFromClickUp} disabled={syncing} title="Pull current status back from ClickUp">
                {syncing ? "Syncing…" : "⟳ Sync from ClickUp"}
              </button>
            )}
            <div style={{ position: "relative", display: "inline-flex" }}>
              <button
                type="button"
                className="tkv2-btn tkv2-btn--push"
                onClick={handleClickUpPush}
                disabled={pushState.kind === "fetching-lists" || pushState.kind === "pushing"}
              >
                ✓ {pushLabel}
              </button>
              {pushState.kind === "picking" && (
                <DestinationPicker
                  tool="ClickUp"
                  lists={pushState.lists}
                  selectedId={selectedListId}
                  onSelect={setSelectedListId}
                  remember={rememberDest}
                  onToggleRemember={setRememberDest}
                  count={stories.length}
                  onPush={() => {
                    const list = (pushState as { kind: "picking"; lists: ClickUpList[] }).lists.find((l) => l.id === selectedListId)
                    if (list) void pushToList(list.id, list.name)
                  }}
                  onCancel={() => setPushState({ kind: "idle" })}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* Push status line (under the header). */}
      {pushState.kind === "pushing" && (
        <div className="tkt-push-status">Pushing to “{pushState.listName}”…</div>
      )}
      {pushState.kind === "error" && (
        <div className="tkt-push-status tkt-push-status--err">{pushState.message}</div>
      )}
      {pushState.kind === "done" && (
        <div className="tkt-push-status tkt-push-status--ok">
          {pushState.created} ticket{pushState.created !== 1 ? "s" : ""} created in ClickUp
          {pushState.errors > 0 ? ` · ${pushState.errors} failed` : ""}
        </div>
      )}

      <div className="tkv2-intro">
        <span className="tkv2-spark">✳</span>
        <div>
          I&apos;ve broken <em>{prdTitle}</em> into{" "}
          <b>{stories.length} implementable ticket{stories.length !== 1 ? "s" : ""}</b> — scoped and
          prioritized from the PRD. <b>Story map:</b> {sizing.reason}. Review, then push to your tracker.
        </div>
      </div>

      {/* Tickets ⟷ Story map toggle — only when the feature is sized large. */}
      {showMap && (
        <div className="tkv2-vtabs">
          <button type="button" className={`tkv2-vtab${activeView === "list" ? " tkv2-vtab--active" : ""}`} onClick={() => setView("list")}>Tickets</button>
          <button type="button" className={`tkv2-vtab${activeView === "map" ? " tkv2-vtab--active" : ""}`} onClick={() => setView("map")}>Story map</button>
        </div>
      )}

      {activeView === "map" ? (
        <StoryMap stories={stories} onOpen={(i) => setSelectedIndex(i)} />
      ) : (
        <div className="tkt-list">
          {builds.map(({ s, i }) => (
            <StoryRow key={i} story={s} index={i} onOpen={() => setSelectedIndex(i)} synced={s.id ? syncedStatuses[s.id] : undefined} />
          ))}
          {decisions.length > 0 && (
            <>
              <div className="tkv2-grouplbl">Decisions &amp; spikes</div>
              {decisions.map(({ s, i }) => (
                <StoryRow key={i} story={s} index={i} onOpen={() => setSelectedIndex(i)} synced={s.id ? syncedStatuses[s.id] : undefined} />
              ))}
            </>
          )}
        </div>
      )}

      <div className="tkv2-foot">
        Tickets are generated from the PRD.{!isClickUpConnected && " Connect ClickUp in Settings to push them."}
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
