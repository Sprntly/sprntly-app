"use client"

import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useGuestSession } from "../../context/GuestSessionContext"
import { ShareContextStrip } from "./ShareContextStrip"
import { EvidenceSections } from "./EvidenceSections"
import { EvidenceHtmlBrief } from "./EvidenceHtmlBrief"
import { StreamingHtmlPreview, stripLeadingFence } from "./StreamingHtmlPreview"
import { stripHtmlCodeFence } from "../../lib/htmlBrief"
import { HtmlReportView } from "./HtmlReportView"
import { useCpanelPhase } from "./useCpanelPhase"
import { EmptyPane } from "./EmptyPane"

// LAZY, and deliberately so. DocumentTab mounts the rich-text editor, which
// pulls ProseMirror in behind it. A static import here would put that whole
// dependency in the bundle of every screen that renders the panel — including
// the overwhelming majority of chats that never write a document — and would
// force every existing ContentPanel test to resolve TipTap just to render a
// tab bar. Loaded when the tab is actually opened.
const DocumentTab = lazy(() =>
  import("./DocumentTab").then((m) => ({ default: m.DocumentTab })),
)
import { IconClose, IconSparkle } from "./app-icons"
import { runEvidenceGeneration, loadEvidenceByInsight } from "../../lib/runEvidenceGeneration"
import { runPrdGeneration } from "../../lib/runPrdGeneration"
import { useRouter } from "next/navigation"
import {
  ApiError, storiesApi, ticketSetsApi,
  type ClickUpList, type ClickUpTicketState, type GeneratedStory,
  type JiraProject, type TicketLifecycle, type TicketStub,
  type TicketSyncState, type TrackerMeta, type TrackerProvider,
} from "../../lib/api"
import { runTicketSetGeneration } from "../../lib/runTicketSetGeneration"
import { PrdPanelContent } from "./PrdPanelContent"
import { GeneratingBanner, GeneratingPane } from "./GenerationState"
import { EVIDENCE_GEN, STANDALONE_TICKET_GEN, TICKET_GEN } from "./generationPhases"
import { ReportsTab } from "./ReportsTab"
import { GeneratePrototypeCTA } from "../design-agent/GeneratePrototypeCTA"
import { TicketDetail } from "./TicketDetail"
import { DestinationPicker } from "./DestinationPicker"
import { JiraPushModal, type JiraPushChoice } from "./JiraPushModal"
import { ticketSyncTrackers } from "../../lib/connectorsCatalog"
import {
  IconMicroscope, IconFileText, IconTicket, IconShare, IconFileTypePdf,
  IconRefresh, IconChevronDown, IconPlugConnected, IconChartBar, IconLink,
} from "@tabler/icons-react"
import { downloadPrdPdf, slugifyTitle } from "../../lib/prdExport"
import { buildCombinedHtml } from "../../lib/combinedExport"
import { documentsApi } from "../../lib/api"
import { saveBlob } from "../../lib/saveBlob"
import type {
  PrdState, PrdContent, PrdDesignBlock, AppContentState, TicketSetFailureKind,
} from "../../types/content"
import { prdInScopeFor } from "../../lib/panelPrdScope"

// Tab order mirrors the pipeline: Evidence → PRD → Tickets (each tab's bottom
// bar launches the NEXT artifact). Evidence is hidden for non-brief PRDs (see
// isEvidenceTabHidden), so uploads show PRD → Tickets.
//
// Reports sits AFTER the pipeline because it isn't part of it: a report hangs off
// the CHAT THREAD, not off the PRD, and a thread may hold several. It's hidden
// until the thread actually has one (see reportsTabHidden below), so the pipeline
// tabs are unchanged for every chat that never asked for a report.
const TABS = [
  { icon: <IconMicroscope size={11.5} />, id: "evidence", label: "Evidence" },
  { icon: <IconFileText size={11.5}/> , id: "prd", label: "PRD" },
  { icon: <IconTicket size={11.5}/> , id: "tickets", label: "Tickets" },
  { icon: <IconChartBar size={11.5}/> , id: "reports", label: "Reports" },
  // A team document written from this chat ("draft a leadership update"). Same
  // posture as Reports and for the same reason — it hangs off the THREAD, not
  // off the PRD — so it sits last and is hidden until one exists.
  { icon: <IconFileText size={11.5}/> , id: "document", label: "Document" },
] as const

// The key is versioned because the bounds below moved: widths stored under the
// old key were dragged against a 60vw default and a 650px floor, so replaying
// one now would mean a panel that never opens at its intended 35%.
const CPANEL_WIDTH_KEY = "sprntly-cpanel-width-v2"
const CPANEL_DEFAULT_VW = 0.35 // first open: 35% panel / 65% thread
const CPANEL_MAX_VW   = 0.6    // max: never more than 60% of the viewport
// Floor, not a comfortable width — it's what 35% comes to on a ~1200px window,
// so the default never starts below the point the first drag would clamp to.
const CPANEL_WIDTH_MIN = 420

function clampCpanelWidth(px: number): number {
  const max = Math.round(window.innerWidth * CPANEL_MAX_VW)
  // On a window narrow enough that the floor exceeds the cap, the cap wins.
  const min = Math.min(CPANEL_WIDTH_MIN, max)
  return Math.min(max, Math.max(min, Math.round(px)))
}

// Header Share dropdown — Download PDF of the combined Evidence + PRD (falls
// back to a single-PRD export when there's no evidence). Enabled only when a
// PRD is loaded. The heavy generators are lazy-imported inside the handler.
function ShareMenu({
  prd,
  evidence,
  onToast,
  disabledReason,
}: {
  prd: PrdState | null
  evidence: PrdContent | null
  onToast: (title: string, sub: string) => void
  /** Set (e.g. by a guest session) to force-disable the menu regardless of
   *  `prd`, with a reason surfaced via title/aria-label. */
  disabledReason?: string
}) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const enabled = !!prd && !disabledReason
  // Built from the pre-existing canonical token attached to the PRD record —
  // NO network call on open. public_id (never the raw sequential prd_id) is
  // what this link must carry — see the prds.public_id migration's own
  // comment for why. Fallback to prd_id only covers a PrdState with no
  // public_id at all (not currently reachable — every load path sets it),
  // matching the same defensive-fallback shape used elsewhere. Nullish when
  // the token hasn't landed yet (e.g. the exact stream-completion instant
  // before a refetch) — the control renders disabled rather than minting.
  const shareUrl =
    prd?.shareToken && typeof window !== "undefined"
      ? `${window.location.origin}/?prd=${encodeURIComponent(prd.public_id ?? String(prd.prd_id))}&share=${prd.shareToken}`
      : null
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
      // demand), else the PRD brief alone. Rendered SERVER-side, the same way a
      // report downloads: one identical file per browser, carrying the Sprntly
      // watermark and footer. (This used to open the browser's print dialog,
      // which produced a different file per browser and could not be marked.)
      const ev = prd.html ? await resolveEvidence() : null
      const html = ev?.html && prd.html ? buildCombinedHtml(ev, prd) : prd.html
      if (html) {
        const slug = slugifyTitle(prd.title)
        const name = ev?.html && prd.html ? `${slug}-evidence-prd` : slug
        const res = await documentsApi.downloadPdf(html, name)
        saveBlob(res.blob, res.filename || `${name}.pdf`)
      } else {
        // A markdown-only PRD has no HTML brief to render; the section builder
        // draws one client-side (already watermarked and footered).
        await downloadPrdPdf(prd)
      }
    } catch {
      onToast("PDF export failed", "Could not generate the PDF. Please try again.")
    }
  }

  const handleCopyLink = async () => {
    if (!shareUrl) return
    try {
      await navigator.clipboard.writeText(shareUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Copy failures are non-fatal — the link stays visible to copy manually
      // (mirrors the design-agent ShareMenu's identical catch).
    }
  }

  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button
        type="button"
        className="cpanel-action-btn"
        disabled={!enabled}
        title={disabledReason}
        aria-label={disabledReason ? `Share (${disabledReason})` : "Share"}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); if (enabled) setOpen((o) => !o) }}
      >
        <IconShare size={12} />Share
      </button>
      {open && enabled && (
        <div className="share-menu share-menu--down open" role="menu">
          <div className="share-menu-item" role="menuitem" style={{ cursor: "default" }}>
            <div className="share-menu-item-icon"><IconLink size={14} /></div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontWeight: 600 }}>Share link</div>
              {shareUrl ? (
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2 }}>
                  <code
                    title={shareUrl}
                    style={{
                      fontSize: 11, color: "var(--muted)", overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1,
                      maxWidth: 300, minWidth: 0,
                    }}
                  >
                    {shareUrl}
                  </code>
                  <button type="button" className="btn" onClick={handleCopyLink}>
                    {copied ? "Copied!" : "Copy"}
                  </button>
                </div>
              ) : (
                <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
                  <button type="button" className="btn" disabled>Preparing link…</button>
                </div>
              )}
            </div>
          </div>
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
 * `(brief_id, insight_index)`). Ideation and uploaded PRDs have none — an
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

/**
 * Resolve the PRD for the insight currently in the panel, streaming the draft in
 * as it's written. Shared by the Evidence footer's "Generate PRD" button and the
 * PRD tab's resolve-on-click, so there is ONE implementation of "get me this
 * insight's PRD".
 *
 * `POST /v1/prd/generate` is find-or-create: an insight that already has a
 * ready (or in-flight) PRD returns that row, so this is a plain load in that
 * case and a click can never fork a duplicate document. Only a genuinely
 * PRD-less insight is generated.
 *
 * The insight comes from the open finding (`detail.meta`) or, failing that, the
 * PRD pointer already in the panel — with neither there is nothing to resolve
 * and `meta` is null, which callers use to disable/skip.
 */
function useResolvePrd() {
  const { openContentPanel, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const meta = content.detail?.meta ?? content.prdMeta ?? null
  const [resolving, setResolving] = useState(false)
  // Guards a double-click on one button; `content.prdGenerating` is what keeps
  // the two callers from starting the same run twice.
  const busyRef = useRef(false)

  const resolve = useCallback(async () => {
    if (!meta || busyRef.current) return
    busyRef.current = true
    setResolving(true)
    // Reveal the PRD tab right away — it live-renders the draft as it streams.
    setContent({ prd: null, prdMeta: meta, prdGenerating: true, prdPartialHtml: null })
    openContentPanel("prd")
    try {
      const result = await runPrdGeneration(meta, (html) => setContent({ prdPartialHtml: html }))
      if (result.ok) {
        setContent({ prd: result.prd, prdMeta: meta, prdGenerating: false, prdPartialHtml: null })
      } else {
        setContent({ prdGenerating: false, prdPartialHtml: null })
        showToast("PRD generation failed", result.message)
      }
    } catch (e) {
      setContent({ prdGenerating: false, prdPartialHtml: null })
      showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
    } finally {
      busyRef.current = false
      setResolving(false)
    }
  }, [meta, setContent, openContentPanel, showToast])

  return { meta, resolving, resolve }
}

export function ContentPanel() {
  const { contentPanelTab, openContentPanel, closeContentPanel, showToast } = useNavigation()
  const guestSession = useGuestSession()
  const { content } = useContent()

  const { mounted, phase } = useCpanelPhase(contentPanelTab != null)

  // The tab to RENDER. During the exit animation `contentPanelTab` is already
  // null, and reading it directly would blank the panel's body for the 200ms
  // it spends sliding out — the last open tab keeps rendering instead. The ref
  // is written from an effect (never during render), so by the time a close
  // renders it already holds the tab the previous commit was showing.
  const lastTabRef = useRef<(typeof TABS)[number]["id"]>("prd")
  useEffect(() => {
    if (contentPanelTab) lastTabRef.current = contentPanelTab
  }, [contentPanelTab])
  const shownTab = contentPanelTab ?? lastTabRef.current

  // This thread's captured reports — fetched once per thread by
  // useThreadReportsSync (AppShell), never here. Defaulted because the panel is
  // rendered against partial content in plenty of places (tests, and any surface
  // that sets only the slices it cares about); a missing slice means "no reports
  // in scope", never a crash in the shared panel.
  //
  // Scoped to the conversation the fetch was FOR: the panel is global and the
  // list lives in shared content, so on the commit where the chat switches threads
  // the rows still describe the PREVIOUS one (the fetcher is AppShell's effect,
  // and React runs it AFTER the chat screen's). A list that doesn't belong reads
  // as still LOADING, never as this thread's answer and never as an empty thread —
  // "no reports in this chat" said about another chat's reports is the wrong claim
  // in both directions.
  const reportsBelong = (content.threadReportsConversationId ?? null) === (content.conversationId ?? null)
  const reports = reportsBelong ? (content.threadReports ?? []) : []
  const reportsLoading = !reportsBelong || content.threadReportsStatus === "loading"
  const reportsError = reportsBelong && content.threadReportsStatus === "error"

  // THE RULE: a tab exists only when this thread actually has that artifact.
  //
  // Evidence → PRD → Tickets are one pipeline, entered by having a PRD or the
  // insight to resolve one from. A chat whose only artifact is a report was
  // showing all three regardless, so the panel advertised three documents that
  // did not exist and could not be made from there.
  //
  // "In scope" is deliberately wider than "loaded": the PRD tab resolves an
  // insight's PRD on click, and the Evidence tab loads a finding's brief, so a
  // pointer to one (prdMeta / detail.meta) is as good as the document itself.
  const pipelineInScope = !!(
    content.prd ||
    content.prdGenerating ||
    content.prdMeta ||
    content.detail?.meta ||
    content.evidence ||
    content.evidenceGenerating
  )
  const evidenceHidden = !pipelineInScope || isEvidenceTabHidden(content)

  // Same rule for reports — with one addition: "no reports" has to be KNOWN, not
  // merely unproven. An empty list from a FAILED fetch used to hide the tab, so
  // switching to another tab made the report the user was reading disappear from
  // the panel entirely. So the tab also survives an error.
  const reportsHidden =
    reports.length === 0 &&
    !reportsError &&
    // A standalone report (opened from Artifacts with no chat behind it) has no
    // thread list at all — the open document IS the reason the tab belongs. Keyed
    // on the explicit flag, not on "a focus id exists": a focus id that merely
    // outlived its thread used to keep a Reports tab on chats that have none.
    !content.reportFocusStandalone

  // A standalone ticket set is on screen — INCLUDING the window before the row
  // exists. `runTicketSetGeneration` publishes `ticketSetGenerating` on its very
  // first patch and only learns the set id when POST /v1/stories/generate
  // answers a second or two later; keying purely on `content.ticketSet` left
  // that window with no visible Tickets tab at all (every tab hidden → the
  // panel fell back to a PRD body with no PRD), which is precisely the frame the
  // runner writes that patch to fill.
  const standaloneSet = !!content.ticketSet || !!content.ticketSetGenerating

  const hidden: Record<(typeof TABS)[number]["id"], boolean> = {
    evidence: evidenceHidden,
    prd: !pipelineInScope,
    // A standalone ticket set is a Tickets tab with no pipeline behind it: the
    // tickets came out of a chat, not a PRD. ONLY the tickets key relaxes —
    // `prd` stays hidden, because there is no PRD to open and a tab that
    // resolves one on click (handleTabClick) would generate a document nobody
    // asked for.
    tickets: !pipelineInScope && !standaloneSet,
    reports: reportsHidden,
    // Hidden until this thread has written one. A tab that is always present
    // but usually empty teaches people to ignore it.
    document: content.documentId == null,
  }
  // The tab currently being shown is never pulled out from under the reader —
  // whatever is in the body must stay reachable in the bar above it. Evidence is
  // the exception: it going hidden means this PRD has no research brief AT ALL
  // (not a timing artifact), and the redirect off it is deliberate — see the
  // effect below.
  const visibleTabs = TABS.filter(
    (t) => !hidden[t.id] || (t.id === shownTab && t.id !== "evidence"),
  )

  // Clicking the PRD tab with no PRD in scope IS the request for one — parking on
  // "No PRD draft loaded" makes the user hunt for a button to do the obvious next
  // thing. So the click switches tabs AND resolves the insight's PRD: shows the
  // existing document if there is one (find-or-create, see useResolvePrd), writes
  // it if there isn't. Only on an actual click — a programmatic tab switch must
  // never kick off a generation nobody asked for.
  const { resolve: resolvePrd } = useResolvePrd()
  const handleTabClick = useCallback((id: (typeof TABS)[number]["id"]) => {
    openContentPanel(id)
    if (id === "prd" && !content.prd && !content.prdGenerating) void resolvePrd()
  }, [openContentPanel, content.prd, content.prdGenerating, resolvePrd])

  // If the panel is parked on a tab that just became hidden (a backlog/upload PRD
  // loaded → no Evidence; the panel sliding out off an empty Reports tab), fall
  // back to the first tab that IS visible rather than a stranded body. Not
  // hardcoded to "prd" any more: on a report-only thread the PRD tab is exactly
  // the one that doesn't exist.
  const activeTab = visibleTabs.some((t) => t.id === shownTab)
    ? shownTab
    : (visibleTabs[0]?.id ?? "prd")

  // The PRD a control on THIS render may act on — never `content.prd` directly.
  // See lib/panelPrdScope.ts for why a raw-slot read is insufficient.
  const actionablePrd = prdInScopeFor(content, activeTab)

  // Persist that fallback into navigation state so re-opens land on a real tab.
  useEffect(() => {
    if (evidenceHidden && contentPanelTab === "evidence" && activeTab !== "evidence") {
      openContentPanel(activeTab)
    }
  }, [evidenceHidden, contentPanelTab, activeTab, openContentPanel])

  // Tracks the live pixel width; null = use the CSS default (35vw).
  const widthRef = useRef<number | null>(null)
  // Teardown for the drag session in flight, or null when none is. Doubles as
  // the "already ended" guard — several events can terminate one gesture.
  const endDragRef = useRef<(() => void) | null>(null)

  // On open: restore saved width, apply it, and keep it clamped on window resize.
  // On close: remove the CSS var so it resets to default.
  //
  // Keyed on `mounted`, not on the tab: clearing --cpanel-width the moment the
  // tab went null would snap a user-widened panel back to the 35vw default in
  // the first frame of the exit slide. Now the var lives exactly as long as the
  // panel element does. (It also stops the effect re-running on every tab
  // switch, which pointlessly removed and re-applied the same width.)
  useEffect(() => {
    if (!mounted) return
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
  }, [mounted])

  // Pointer-down on the left-edge handle starts a drag session.
  //
  // Pointer events with capture, not mouse events. The panel body hosts iframes
  // (the PRD and report frames), and an iframe's document swallows the parent's
  // mouse events: the moment the widening panel's edge slid under the cursor,
  // mousemove stopped arriving and the panel froze mid-drag — and because the
  // mouseup was swallowed too, the session never ended, so the panel started
  // tracking the cursor again after the button had already been released.
  // Capturing the pointer pins every event of the gesture to the handle no
  // matter what it travels over, and guarantees exactly one terminating event.
  //
  // Writes are coalesced onto a single animation frame. One width change
  // re-lays out the panel AND the thread column's padding — the expensive half
  // — while a pointermove burst can fire several times per frame, so doing that
  // work per-event rather than per-frame is what made the drag feel heavy.
  const handleResizeStart = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || window.innerWidth <= 768) return
    e.preventDefault()
    const handle = e.currentTarget
    const root = document.documentElement
    const startX = e.clientX
    const { pointerId } = e
    // Seed from what's actually on screen rather than from an assumed default,
    // so the first drag off a never-resized panel doesn't jump.
    const startW = widthRef.current ?? Math.round(
      handle.parentElement?.getBoundingClientRect().width
        || window.innerWidth * CPANEL_DEFAULT_VW,
    )

    let latestX = startX
    let frame = 0
    const flush = () => {
      frame = 0
      // Dragging LEFT widens the panel (panel anchored to right edge).
      const next = clampCpanelWidth(startW + (startX - latestX))
      widthRef.current = next
      root.style.setProperty("--cpanel-width", `${next}px`)
    }

    root.classList.add("cpanel-resizing")
    // Not supported everywhere (and a no-op in jsdom) — the window listeners
    // below still see captured events, since capture retargets but still
    // bubbles, so the drag degrades to the old behaviour rather than breaking.
    try { handle.setPointerCapture(pointerId) } catch { /* fall through */ }

    const onMove = (ev: PointerEvent) => {
      if (ev.pointerId !== pointerId) return
      latestX = ev.clientX
      if (!frame) frame = window.requestAnimationFrame(flush)
    }
    const end = () => {
      if (endDragRef.current !== end) return
      endDragRef.current = null
      // Land the last move rather than dropping it a frame short of the cursor.
      if (frame) { window.cancelAnimationFrame(frame); flush() }
      if (widthRef.current != null) {
        window.localStorage.setItem(CPANEL_WIDTH_KEY, String(widthRef.current))
      }
      root.classList.remove("cpanel-resizing")
      try { handle.releasePointerCapture(pointerId) } catch { /* already gone */ }
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onUp)
      // A cancelled pointer (OS gesture, alt-tab) or capture lost to a
      // disappearing handle both end the gesture with no pointerup at all.
      window.removeEventListener("pointercancel", onUp)
      handle.removeEventListener("lostpointercapture", end)
    }
    const onUp = (ev: PointerEvent) => {
      if (ev.pointerId !== pointerId) return
      end()
    }

    endDragRef.current = end
    window.addEventListener("pointermove", onMove)
    window.addEventListener("pointerup", onUp)
    window.addEventListener("pointercancel", onUp)
    handle.addEventListener("lostpointercapture", end)
  }, [])

  // A gesture can outlive the panel — close it, or navigate away, mid-drag and
  // the window listeners would stay attached with the session still live, which
  // is the same "resizes with no button held" failure by another route. Real
  // browsers fire lostpointercapture when the handle leaves the DOM; this is
  // what makes it true without depending on that.
  useEffect(() => () => endDragRef.current?.(), [])

  if (!mounted) return null

  const closing = phase === "out"

  return (
    <>
      <div className="cpanel-overlay" onClick={closeContentPanel} />
      <aside
        className={`cpanel${phase === "in" ? " cpanel--in" : ""}${closing ? " cpanel--out" : ""}`}
        // On the way out the panel is a departing visual only — take it out of
        // the a11y tree and the tab order so it can't be reached mid-slide.
        inert={closing || undefined}
        aria-hidden={closing || undefined}
      >
        {/* Draggable left edge — grab to resize */}
        <div
          className="cpanel-resize-handle"
          onPointerDown={handleResizeStart}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize panel"
        />
        {guestSession && (
          <div style={{ padding: "10px 20px 0", display: "flex", alignItems: "center", gap: 10 }}>
            <span className="cmdp-kbd" data-testid="cpanel-readonly-badge">READ-ONLY</span>
            <div style={{ flex: 1 }}>
              <ShareContextStrip
                kind="drawer"
                title={content.prd?.title ?? "Shared document"}
                sharerName={guestSession.sharerName ?? guestSession.owningCompanyName}
              />
            </div>
          </div>
        )}
        <div className="cpanel-head">
          <div>
            <div className="cpanel-tabs">
              {visibleTabs.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={`cpanel-tab${activeTab === t.id ? " cpanel-tab--active" : ""}`}
                  onClick={() => handleTabClick(t.id)}
                >
                  {t.icon} {t.label}
                </button>
              ))}
            </div>
          </div>
            {/* The header names what the panel is SHOWING. On Reports that's the
                thread's reports — not the PRD, which the tab isn't about (and
                which a report-only thread may not even have). */}
            <span className="cpanel-main-name">
              {activeTab === "reports"
                ? "Reports"
                // A standalone set has no PRD to name, and naming one anyway
                // ("PRD") would label the panel with a document that does not
                // exist. The line is always rendered — a set still being
                // written just falls back to what it is.
                : activeTab === "tickets" && standaloneSet
                  ? `Tickets · ${content.ticketSet?.title || "from this conversation"}`
                  : actionablePrd?.title ? `PRD · ${actionablePrd.title}` : "PRD"}
            </span>
          <div className="cpanel-head-actions">
            {/* The header Share menu exports the Evidence + PRD pair, so it has no
                meaning on Reports — a report carries its OWN share/PDF actions,
                on the open document (ReportsTab). Force-disabled in guest mode —
                a guest has no edit/export entitlement (AC15). */}
            {activeTab !== "reports" && (
              <ShareMenu
                prd={actionablePrd}
                evidence={content.evidence}
                onToast={showToast}
                disabledReason={guestSession ? "Sign in to a full workspace to share" : undefined}
              />
            )}
            <button type="button" className="cpanel-close" onClick={closeContentPanel} aria-label="Close">
              <IconClose size={16} />
            </button>
          </div>
        </div>

        <div className="cpanel-body">
          {activeTab === "evidence" && <EvidenceTab />}
          {activeTab === "prd" && <PrdPanelContent evidenceTabAvailable={!evidenceHidden} />}
          {activeTab === "tickets" && <TicketsTab />}
          {activeTab === "reports" && (
            <ReportsTab reports={reports} loading={reportsLoading} error={reportsError} />
          )}
          {activeTab === "document" && content.documentId != null && (
            <Suspense fallback={<div style={{ fontSize: 13, opacity: 0.6 }}>Loading document…</div>}>
              <DocumentTab documentId={content.documentId} />
            </Suspense>
          )}
        </div>

        {/* Fixed pipeline bar — each tab's bottom launches the NEXT artifact.
            The PRD tab keeps its OWN footer (autosave + version history + the
            tickets button), so the shared bar is only for Evidence and Tickets.
            Hidden entirely in guest mode: both bars' "next step" actions are
            mutation/generation triggers (Generate PRD, Generate Prototype) a
            read-only guest is never entitled to fire. */}
        {activeTab === "evidence" && !guestSession && <EvidenceBottomBar />}
        {/* The Tickets bar launches the PROTOTYPE, which is generated from a
            PRD. A standalone ticket set has none and never will, so the bar is
            withheld entirely rather than rendered with a permanently disabled
            button — a control that can never be used is worse than no control.
            Deliberately keyed on the SET, not on "no PRD in scope": a tickets
            tab whose PRD was cleared by evidenceOpenScopePatch keeps its
            disabled CTA (that pipeline can still get a PRD; this one cannot). */}
        {activeTab === "tickets" && !guestSession && !standaloneSet && <TicketsBottomBar />}
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
  const { openContentPanel } = useNavigation()
  const { content } = useContent()
  const prd = content.prd
  const { meta, resolving, resolve } = useResolvePrd()

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
          disabled={resolving || !meta}
          onClick={resolve}
        >
          {resolving ? "Generating PRD…" : "Generate PRD"}
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
  // TicketsBottomBar only ever renders under activeTab === "tickets" (see
  // ContentPanel's conditional render below), so the literal tab is correct,
  // not an assumption.
  const scopedPrd = prdInScopeFor(content, "tickets")
  const prdId = scopedPrd?.prd_id ?? null
  return (
    <div className="cpanel-bottom-bar">
      <GeneratePrototypeCTA
        prdId={prdId}
        figmaFileKey={scopedPrd?.figma_file_key ?? null}
        prdTitle={scopedPrd?.title}
        // The PRD's own :::design platform_hint (already parsed into the
        // sections in scope here) seeds the generate panel's platform
        // default; the toggle still overrides. Optional-chained: a PRD
        // hydrated without parsed sections (e.g. a bare record) simply
        // yields no hint.
        platformHint={
          scopedPrd?.sections?.find(
            (s): s is PrdDesignBlock => s.type === "prd-design",
          )?.platformHint ?? null
        }
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
  const guestSession = useGuestSession()
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
    setContent({ evidencePartialHtml: null })
    evidenceLoadedKey = key
    runEvidenceGeneration(detail.meta, undefined, (html) => {
      if (!cancelled) setContent({ evidencePartialHtml: html })
    })
      .then((result) => {
        if (cancelled) return
        if (!result.ok) { setLocalState({ kind: "error", message: result.message }); setContent({ evidencePartialHtml: null }); return }
        setContent({ evidence: result.evidence, evidencePartialHtml: null })
        setLocalState({ kind: "idle" })
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setContent({ evidencePartialHtml: null })
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
    // Guest evidence is already pre-populated in content.evidence by
    // GuestArtifactViewer — this effect must never re-fetch via
    // loadEvidenceByInsight for a guest session (AC11).
    if (guestSession) return
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
  }, [guestSession, detail?.meta, prdMeta?.briefId, prdMeta?.insightIndex, evidence, setContent])

  // Explicit retry after a FAILED generation. force=true skips the backend's
  // failed-row short-circuit and its dedup, starting a genuinely fresh run —
  // the ONLY path that re-generates after a failure (opens never auto-retry).
  const retryEvidence = useCallback(() => {
    const meta = detail?.meta
    if (!meta) return
    setLocalState({ kind: "loading" })
    setContent({ evidencePartialHtml: null })
    runEvidenceGeneration(meta, { force: true }, (html) => setContent({ evidencePartialHtml: html }))
      .then((result) => {
        if (!result.ok) { setLocalState({ kind: "error", message: result.message }); setContent({ evidencePartialHtml: null }); return }
        setContent({ evidence: result.evidence, evidencePartialHtml: null })
        setLocalState({ kind: "idle" })
      })
      .catch((e: unknown) => {
        setContent({ evidencePartialHtml: null })
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
        ) : isLoading && content.evidencePartialHtml ? (
          // Live streaming preview: partial evidence HTML is already arriving —
          // render it as it grows, with a slim pulsing indicator instead of the
          // full-pane skeleton. The finished doc (poll result) replaces this.
          <div style={{ minHeight: 280 }}>
            <GeneratingBanner
              testId="evidence-streaming"
              title="Writing the evidence brief…"
              sub="Rendering it below as it's written — the finished brief replaces this."
            />
            <StreamingHtmlPreview
              html={stripLeadingFence(stripHtmlCodeFence(content.evidencePartialHtml))}
              title="Evidence brief (generating)"
              testId="evidence-streaming-preview"
            />
          </div>
        ) : isLoading ? (
          <GeneratingPane
            {...EVIDENCE_GEN}
            testId="evidence-generating"
            icon={<IconMicroscope size={19} />}
            title="Generating evidence…"
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
  story: GeneratedStory; index: number; onOpen?: () => void; synced?: ClickUpTicketState; tool?: string
}) {
  const preview = story.user_story || story.body
  const excluded = story.lifecycle === "excluded"
  return (
    <button
      type="button"
      className={`tkv2-card${excluded ? " tkv2-row--excluded" : ""}`}
      onClick={onOpen}
      disabled={!onOpen}
    >
      <span className="tkv2-key">{`T-${index + 1}`}</span>
      <div className="tkv2-card-main">
        <div className="tkv2-card-title tkv2-rtitle">
          {story.title}
          {/* Says WHY the row has no tracker chip — without it an excluded
              ticket looks identical to one that simply failed to sync. */}
          {excluded ? <span className="tkv2-exbadge" title={`Not sent to ${tool || "the PM tool"}`}>Excluded</span> : null}
        </div>
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

// A planned-but-not-yet-written ticket (fan-out plan stub): same card shape as
// StoryRow so the list doesn't reflow when the full ticket replaces it, but
// dimmed and inert — there's no detail to open yet.
function StubRow({ stub, index }: { stub: TicketStub; index: number }) {
  return (
    <div
      className="tkv2-card"
      data-testid="ticket-skeleton"
      aria-busy="true"
      style={{ opacity: 0.55, cursor: "default" }}
    >
      <span className="tkv2-key">{`T-${index + 1}`}</span>
      <div className="tkv2-card-main">
        <div className="tkv2-card-title">{stub.title}</div>
        <div className="tkv2-story">
          {stub.summary || "Writing this ticket…"}
          {stub.prd_section ? <span className="ctx"> Context: {stub.prd_section}</span> : null}
        </div>
      </div>
    </div>
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

/** What the four ways a standalone ticket-set run can end badly SAY.
 *
 *  The raw backend message never reaches this map — `content.ticketSet.error`
 *  is a classified kind, so a stack trace, a provider error or a fetch
 *  exception cannot be printed at the user. Each entry states what happened to
 *  their work, because "nothing was saved" and "the run may still be going"
 *  call for opposite next moves.
 *
 *  The 404 case gets no retry: re-running would create a DIFFERENT set, which
 *  is not what "try again" promises on a set that is gone. It also says
 *  nothing about access — a foreign tenant's set and a deleted one are the same
 *  404 by design, and the copy must not let them be told apart. */
const SET_ERROR_COPY: Record<
  TicketSetFailureKind, { lead: string; body: string; retry: boolean }
> = {
  timeout: {
    lead: "The ticket run took too long and stopped.",
    body: "Nothing was saved. Try again — a shorter, more specific ask usually finishes.",
    retry: true,
  },
  failed: {
    lead: "Couldn’t write the tickets.",
    body: "The run stopped before finishing and nothing was saved. Try again.",
    retry: true,
  },
  network: {
    lead: "Lost connection while the tickets were being written.",
    body: "The run may still be going — reopen this chat in a minute to check.",
    retry: true,
  },
  notfound: {
    lead: "This ticket set isn’t available.",
    body: "It may have been deleted.",
    retry: false,
  },
}

export function TicketsTab() {
  const { showToast } = useNavigation()
  const { content, setContent } = useContent()
  const guestSession = useGuestSession()
  const router = useRouter()
  // ── Which artifact's tickets are these? ──────────────────────────────────
  // A PRD's, or a standalone set generated from a chat with no PRD. The two
  // share this whole surface — list, detail, tracker sync — and differ only in
  // the id the backend routes are keyed on, because the sync ENGINE is one
  // engine (app/stories/scope.py::TicketScope).
  //
  // The PRD comes from prdInScopeFor, not from `content.prd`: with a set on
  // screen that returns null, which is what keeps a leftover PRD from this
  // panel's previous occupant driving the header, the generation effect and
  // the tracker calls.
  const ticketSet = content.ticketSet ?? null
  const setId = ticketSet?.id ?? null
  const ticketSetGenerating = Boolean(content.ticketSetGenerating)
  const setStories = ticketSet?.stories ?? []
  const setStubs = ticketSet?.stubs ?? []
  const prd = prdInScopeFor(content, "tickets")
  const prdId = prd?.prd_id ?? null
  const prdTitle = prd?.title ?? "PRD"
  // Rendered whenever a set is on screen — a set whose naming leg hasn't landed
  // yet still gets a header line rather than a collapsed one.
  const setTitle = ticketSet?.title?.trim() || "Tickets from this conversation"
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
        /** The planned roster (fan-out plan leg, ~20-35s in). Stubs not yet
         *  covered by a landed story render as skeleton rows. */
        pendingStubs?: TicketStub[]
      }
    | { kind: "error"; message: string }
  const [genState, setGenState] = useState<GenState>({ kind: "idle" })
  const stories = genState.kind === "ready" ? genState.stories : []
  const refreshing = genState.kind === "ready" && Boolean(genState.refreshing)
  const refreshError = genState.kind === "ready" ? genState.refreshError ?? null : null
  const streaming = genState.kind === "ready" && Boolean(genState.streaming)
  const streamProgress = genState.kind === "ready" ? genState.progress ?? null : null
  // Planned-but-not-yet-written tickets: the stub roster minus any title a
  // landed story already covers (titles are unique within a plan by contract).
  const landedTitles = new Set(stories.map((s) => s.title.trim().toLowerCase()))
  const skeletonStubs =
    genState.kind === "ready" && genState.streaming
      ? (genState.pendingStubs ?? []).filter(
          (st) => !landedTitles.has(st.title.trim().toLowerCase()),
        )
      : []

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
    // A guest session's ticket set is pre-fetched by GuestArtifactViewer into
    // content.guestTickets — this effect must NEVER call storiesApi.generate/
    // getJob/getForPrd for a guest (AC12): those are authed-gated (403/404)
    // AND storiesApi.generate can kick off real, cost-incurring LLM ticket
    // generation, which must never fire for an unentitled viewer.
    if (guestSession) {
      setGenState({ kind: "ready", stories: content.guestTickets ?? [] })
      return
    }
    // A STANDALONE ticket set is already in content: lib/runTicketSetGeneration
    // owns its kick-off and its polling, and republishes the slice as fan-out
    // batches land. Like the guest branch above, this one must NEVER call
    // storiesApi.generate / getJob / getForPrd — but for a different reason.
    // There, the calls are unauthorized; here, the panel polling too would be a
    // SECOND generation: the insight path does not dedupe two phrasings of one
    // ask, so a panel-side kick would write a second `ticket_sets` row and a
    // second multi-minute LLM bill for tickets the user asked for once. Reading
    // is the whole contract.
    if (ticketSet) {
      setGenState({
        kind: "ready",
        stories: ticketSet.stories,
        // Reuses the fan-out streaming machinery below verbatim — the banner,
        // the batch counter and the skeleton rows are the same states the PRD
        // path already renders, because it is the same generator underneath.
        streaming: ticketSetGenerating,
        progress: ticketSet.progress ?? undefined,
        pendingStubs: ticketSet.stubs,
      })
      return
    }
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
            // stream in what exists: the planned stub roster first (skeleton
            // rows, ~20-35s in), then the partial set as fan-out batches land —
            // instead of holding a blank spinner. While REFRESHING an edited PRD
            // we keep the previous complete set untouched — swapping it for a
            // partial would flicker.
            if (!prevStories?.length && (j.stories?.length || j.stubs?.length)) {
              setGenState({
                kind: "ready",
                stories: j.stories ?? [],
                streaming: true,
                progress: j.progress,
                pendingStubs: j.stubs,
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
  }, [prdId, regenNonce, guestSession, content.guestTickets,
      content.ticketSet, content.ticketSetGenerating])

  // ── Sync state: load per PRD, poll while a sync runs ─────────────────────
  // True while a push/registration flow is mid-flight (destination chosen but
  // the backend may not have registered it yet). A poll landing in that window
  // still describes the PREVIOUS binding (or none) and would clobber the
  // optimistic "Syncing with …" state — the button would bounce back to
  // "Push to Jira" and never flip to "Synced". Ignore those responses; the
  // push flow refreshes itself once registration settles.
  const registeringRef = useRef(false)
  const refreshSync = useCallback(() => {
    // Tracker sync state is authed-gated (same class of bug as the guarded
    // effects above) and meaningless for a guest anyway — a guest can never
    // reach the push/sync UI that would populate it (withheld above).
    if (guestSession) return
    // A standalone set syncs two-way on exactly the same terms as a PRD's
    // tickets — same engine, same binding, same reconciliation — so the only
    // difference is which route carries the scope.
    const read = setId != null
      ? ticketSetsApi.getSyncState(setId)
      : prdId != null
        ? storiesApi.getSyncState(prdId)
        : null
    if (read == null) return
    read
      .then((s) => {
        if (registeringRef.current) return
        setSyncState(s)
      })
      // Transient fetch failure must not downgrade a known-configured state.
      .catch(() => setSyncState((prev) => prev ?? { configured: false }))
  }, [prdId, setId, guestSession])

  useEffect(() => {
    setSyncState(null)
    refreshSync()
  }, [prdId, setId, refreshSync])

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
    // Same authed-gated class as refreshSync above — never fetch for a guest.
    if (guestSession) { setTrackerMeta(null); return }
    const read = setId != null
      ? ticketSetsApi.getTrackerMeta(setId)
      : prdId != null
        ? storiesApi.getTrackerMeta(prdId)
        : null
    if (read == null) { setTrackerMeta(null); return }
    let cancelled = false
    read
      .then((r) => {
        if (cancelled) return
        setTrackerMeta(r.meta && r.provider ? { provider: r.provider, meta: r.meta } : null)
      })
      .catch(() => { /* metadata is an enhancement, never a blocker */ })
    return () => { cancelled = true }
    // last_synced_at: every completed sync also re-pulled the vocabulary
    // server-side — re-read the cache so the UI shows workspace changes.
  }, [prdId, setId, guestSession, syncState?.configured, syncState?.destination_id, syncState?.last_synced_at])

  /** Register / re-sync this artifact's destination. One call for both owners:
   *  `/v1/ticket-sets/{id}/sync` and `/v1/stories/sync/{prd_id}` are the same
   *  backend function (app/stories/sync_control.trigger_scope_sync) behind two
   *  tenant gates. */
  const triggerScopeSync = (dest?: {
    provider: TrackerProvider; destination_id: string; destination_name?: string
  }) => {
    // The re-sync call passes no destination AT ALL rather than an explicit
    // undefined — "re-sync what's bound" is a different request from "bind
    // this", and the wire shape should say so.
    if (setId != null) {
      return dest ? ticketSetsApi.triggerSync(setId, dest) : ticketSetsApi.triggerSync(setId)
    }
    if (prdId != null) {
      return dest ? storiesApi.triggerSync(prdId, dest) : storiesApi.triggerSync(prdId)
    }
    return Promise.reject(new Error("No ticket owner in scope"))
  }

  /** Ad-hoc sync of the already-configured destination (the button click). */
  const syncNow = async () => {
    if ((prdId == null && setId == null) || syncing || !syncState?.configured) return
    // Hold the optimistic "Syncing…" against polls until the backend has
    // actually marked the run (triggerSync returning), then let polling own it.
    registeringRef.current = true
    setSyncState((s) => (s ? { ...s, sync_status: "syncing" } : s))
    try {
      await triggerScopeSync()
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
    if ((prdId == null && setId == null) || pickState.kind !== "picking") return
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
      await triggerScopeSync({
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
    if ((prdId == null && setId == null) || pickState.kind !== "picking-jira") return
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
      await triggerScopeSync({
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

  /** Re-run this set's generation from the request that produced it.
   *
   *  The runner owns the whole arc (kick-off, poll, terminal write) — this only
   *  hands it the task again, which is also why the chat's own retry button
   *  (Part 2) and this one cannot race into two sets. */
  const retryTicketSet = () => {
    const task = ticketSet?.sourceText?.trim()
    if (!task) return
    void runTicketSetGeneration(task, ticketSet?.conversationId ?? null, setContent)
  }

  const retryButton = (
    <button
      type="button"
      className="tkv2-btn tkv2-btn--regen"
      style={{ marginTop: 12 }}
      onClick={retryTicketSet}
      disabled={!ticketSet?.sourceText?.trim()}
      title={ticketSet?.sourceText?.trim()
        ? undefined
        : "Ask again in the chat — the original request isn’t available here."}
    >
      <IconRefresh size={15} /> Try again
    </button>
  )

  // ── Standalone ticket set: working state ─────────────────────────────────
  // Before anything exists to show. Once the plan roster or the first batch
  // lands, the run falls through to the list below with a streaming banner —
  // the same treatment as the PRD path, because it is the same fan-out.
  if (ticketSetGenerating && setStories.length === 0 && setStubs.length === 0) {
    return (
      <div className="tkv2 tkt-list-wrap">
        <GeneratingPane
          {...STANDALONE_TICKET_GEN}
          testId="standalone-tickets-generating"
          icon={<IconTicket size={19} />}
          // Same element either way, so the title swaps in place when the set
          // gets its name instead of remounting the pane and restarting the
          // phase rotation under the reader.
          title={ticketSet?.title?.trim()
            ? <>Writing <em>{ticketSet.title.trim()}</em>…</>
            : "Turning this conversation into tickets…"}
          skeleton="rows"
        />
      </div>
    )
  }

  // Failure. The KIND is what's stored (never the backend's words), and each
  // one says what happened to the work — see SET_ERROR_COPY.
  if (ticketSet && !ticketSetGenerating
      && (ticketSet.error || ticketSet.status === "failed")) {
    const copy = SET_ERROR_COPY[ticketSet.error ?? "failed"]
    return (
      <div className="cpanel-empty" role="alert" data-testid="standalone-tickets-error">
        <IconSparkle size={20} />
        <p><strong>{copy.lead}</strong></p>
        <p>{copy.body}</p>
        {copy.retry ? retryButton : null}
      </div>
    )
  }

  // A settled run that produced nothing. Not a "0 tickets" success — say what
  // to change about the ask, because a thin conversation is the usual cause.
  if (ticketSet && !ticketSetGenerating && setStories.length === 0) {
    return (
      <div className="cpanel-empty" data-testid="standalone-tickets-empty">
        <IconSparkle size={20} />
        <p><strong>No tickets came back from that run</strong></p>
        <p>
          The conversation may not have had enough detail to break into work
          items. Add the specifics — what’s broken, for whom — and ask again.
        </p>
        {retryButton}
      </div>
    )
  }

  if (prdId == null && !ticketSet) {
    return (
      <div className="cpanel-empty">
        <IconSparkle size={20} />
        <p>Ticket creation — generate a PRD first, then tickets are drafted from it.</p>
      </div>
    )
  }

  if (genState.kind === "generating") {
    return (
      <div className="tkv2 tkt-list-wrap">
        <GeneratingPane
          {...TICKET_GEN}
          testId="tickets-generating"
          icon={<IconTicket size={19} />}
          title={<>Breaking <em>{prdTitle}</em> into tickets…</>}
          skeleton="rows"
        />
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
  // cached (backend), so Regenerate re-runs cleanly. NOT hit mid-stream: with
  // no landed story yet the planned roster still renders as skeleton rows.
  if (genState.kind === "ready" && stories.length === 0 && skeletonStubs.length === 0) {
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

  // A ticket's lifecycle changed in the detail: mirror it in this list's own
  // copy so the change shows without a refetch. A deleted ticket LEAVES the
  // array (the server drops it from every later read), which is also why the
  // detail closes itself on delete — the index it was rendering is gone.
  const applyLifecycle = (index: number, lifecycle: TicketLifecycle) => {
    if (genState.kind !== "ready") return
    const next =
      lifecycle === "deleted"
        ? genState.stories.filter((_, i) => i !== index)
        : genState.stories.map((s, i) => (i === index ? { ...s, lifecycle } : s))
    setGenState({ ...genState, stories: next })
    if (lifecycle === "deleted") setSelectedIndex(null)
  }

  // A ticket is open → show the editable detail in place of the list.
  const selectedStory = selectedIndex != null ? stories[selectedIndex] : null
  if (selectedStory && (prdId != null || ticketSet)) {
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
          // The owner is part of the key too — `set-` and `prd-` tickets are
          // different rows behind the same index.
          key={setId != null ? `tk-set-${setId}-${selectedIndex}` : `tk-${prdId}-${selectedIndex}`}
          story={selectedStory}
          index={selectedIndex as number}
          // Exactly one owner: a set's tickets are keyed `set-{id}-{story}`,
          // a namespace disjoint from the PRD's, so passing both would be
          // ambiguous about which row the edits land on.
          {...(setId != null ? { setId } : { prdId: prdId as number })}
          onBack={() => setSelectedIndex(null)}
          onOpenLinked={openLinked}
          tracker={trackerMeta ? {
            provider: trackerMeta.provider,
            meta: trackerMeta.meta,
            synced: selectedStory.id ? syncState?.statuses?.[selectedStory.id] : undefined,
          } : undefined}
          onLifecycleChange={(lifecycle) => applyLifecycle(selectedIndex as number, lifecycle)}
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
        {/* A set is named after itself, not after a PRD it doesn't have. The
            line always renders — an unnamed set falls back rather than
            collapsing the header. */}
        <h2>{ticketSet ? setTitle : <>Tickets from <em>{prdTitle}</em></>}</h2>
        {/* The subline must never read as a finished count while a run is in
            flight — that's the whole reason the old treatment went unnoticed. */}
        <div className="tkv2-sub">
          {ticketSet
            ? streaming
              ? `Writing tickets · ${stories.length} ready so far`
              // The chat a set came from can be deleted without taking the set
              // with it — the set survives on its own and says so, rather than
              // silently claiming a thread that no longer exists.
              : content.ticketSetStandalone
                ? `${stories.length} ticket${stories.length !== 1 ? "s" : ""} · the chat this came from was deleted`
                : `${stories.length} ticket${stories.length !== 1 ? "s" : ""} · from this conversation`
            : refreshing
              ? `Regenerating from the edited PRD · showing the previous ${stories.length} ticket${stories.length !== 1 ? "s" : ""}`
              : streaming
                ? `Writing tickets · ${stories.length} ready so far`
                : `${stories.length} ticket${stories.length !== 1 ? "s" : ""} · generated from the PRD`}
        </div>
        {/* The tracker connect/push/sync button is withheld entirely in guest
            mode — it's exactly the "tracker-push" affordance AC15 requires
            absent, and the connect/sync/push actions behind it are all
            authed-gated mutations a guest is never entitled to trigger. */}
        {stories.length > 0 && !guestSession && (
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

      {/* Regeneration + sync status lines (under the header). The two
          "still working" ones are full banners rather than 12px notes — a run
          in flight has to be readable at a glance from the top of the panel. */}
      {streaming && (
        <GeneratingBanner
          testId="tickets-streaming"
          title="Generating tickets…"
          sub={
            stories.length === 0 && skeletonStubs.length > 0
              ? `Planned ${skeletonStubs.length} ticket${skeletonStubs.length !== 1 ? "s" : ""} — writing them now…`
              : `Showing them as they land${streamProgress ? ` — batch ${streamProgress.done} of ${streamProgress.total}` : ""}.`
          }
          progress={streamProgress}
        />
      )}
      {refreshing && (
        <GeneratingBanner
          tone="warn"
          testId="tickets-refreshing"
          title="Regenerating — the PRD changed"
          sub="Updating these tickets from the edited PRD. The previous set stays below until the new one is ready."
        />
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
          {ticketSet ? (
            <>
              {streaming ? "I’m turning" : "I’ve turned"} this conversation into{" "}
              <b>{stories.length + skeletonStubs.length} implementable ticket{stories.length + skeletonStubs.length !== 1 ? "s" : ""}</b> — scoped and
              prioritized from what was discussed.{" "}
            </>
          ) : (
            <>
              {streaming ? "I’m breaking" : "I’ve broken"} <em>{prdTitle}</em> into{" "}
              {/* While streaming, count the whole planned set (landed +
                  skeletons) so the number doesn't creep up batch by batch. */}
              <b>{stories.length + skeletonStubs.length} implementable ticket{stories.length + skeletonStubs.length !== 1 ? "s" : ""}</b> — scoped and
              prioritized from the PRD.{" "}
            </>
          )}
          {streaming
            ? "The rest are landing now."
            : "Review, then push to your tracker."}
        </div>
      </div>

      {/* The stale set is still useful (and still clickable), but it must not
          look current while its replacement is being written — label it and
          hold it back visually. */}
      {refreshing && (
        <div className="gwip-stale-lbl">
          <span className="gwip-stale-dot" aria-hidden /> Previous tickets — being replaced
        </div>
      )}

      <div className={`tkt-list${refreshing ? " tkt-list--stale" : ""}`}>
        {stories.map((s, i) => (
          <StoryRow
            key={i} story={s} index={i}
            // TicketDetail (opened via onOpen) is an editable surface this
            // ticket hasn't audited for guest-safety — withheld entirely
            // rather than assumed safe (AC15: no edit control present).
            onOpen={guestSession ? undefined : () => setSelectedIndex(i)}
            synced={s.id ? syncState?.statuses?.[s.id] : undefined}
            tool={currentTool}
          />
        ))}
        {/* Planned-but-not-yet-written tickets: the plan leg finishes ~20-35s
            in, so the user sees the full roster as skeletons long before the
            first enriched batch lands (on single-wave runs that's the very
            end of the run). */}
        {skeletonStubs.map((st, i) => (
          <StubRow key={`stub-${st.title}`} stub={st} index={stories.length + i} />
        ))}
      </div>

      {/* The two-way sentence is stated VERBATIM for both owners because it is
          equally true for both: a standalone set binds a destination, auto-syncs
          on the same interval and reconciles the same last-writer-wins way (one
          engine — app/stories/sync.py takes a TicketScope, not a prd_id). */}
      <div className="tkv2-foot">
        {ticketSet
          ? "Tickets are generated from this conversation."
          : "Tickets are generated from the PRD."}
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
