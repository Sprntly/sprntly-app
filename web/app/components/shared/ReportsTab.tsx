"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { useContent } from "../../context/ContentContext"
import { useNavigation } from "../../context/NavigationContext"
import { HtmlReportView } from "./HtmlReportView"
import { SavedChatMarkdown } from "./SavedChatMarkdown"
import { ReportShareMenu } from "./ReportShareMenu"
import { ReportDocument } from "./ReportDocument"
import { EmptyPane } from "./EmptyPane"
import { GeneratingBanner, GeneratingPane } from "./GenerationState"
import { REPORT_GEN } from "./generationPhases"
import { IconArrowLeft, IconChartBar } from "@tabler/icons-react"
import { reportKindLabel } from "../../lib/reportKind"
import { formatRelativeDate } from "../../lib/sources-helpers"
import { looksLikeHtmlBrief } from "../../lib/htmlBrief"
import { reportsApi, type ReportDoc, type ReportSummary } from "../../lib/api"

/**
 * The content panel's Reports tab: the captured reports belonging to the chat
 * thread on the left.
 *
 * A thread can hold several (each ask that runs a report skill captures one), so
 * this is a list → detail view in the same slide, the shape the Tickets tab
 * already uses: pick a report, read it in place, come back via "All reports" and
 * pick another. Nothing about a report ever navigates away from the thread.
 *
 * The LIST is owned by ContentPanel (it also decides whether this tab is worth
 * showing at all, which needs the count); the DOCUMENT is fetched here, by id, on
 * open — the listing carries no bodies.
 */
export function ReportsTab({
  reports,
  loading,
  error,
  shareSlot,
}: {
  reports: ReportSummary[]
  loading: boolean
  /** The list fetch failed. The tab says so rather than reading as an empty
   *  thread — "no reports" and "couldn't load them" are different facts. */
  error?: boolean
  /** The panel HEADER slot this tab's share/PDF menu renders into, so it sits
   *  where the PRD's does instead of inside the document it acts on. A portal,
   *  because the menu reads the open report and that document is this tab's.
   *  Absent / null (a test harness, the first commit before the ref lands) puts
   *  the menu back inline, which is where it used to live. */
  shareSlot?: HTMLElement | null
}) {
  const { content, setContent } = useContent()
  const { showToast } = useNavigation()
  const [doc, setDoc] = useState<ReportDoc | null>(null)
  const [docLoading, setDocLoading] = useState(false)
  const [docError, setDocError] = useState(false)
  const conversationId = content.conversationId

  // WHICH report is open is a POINTER held outside this component
  // (`content.reportFocusId`), not local state, because every way in sets it from
  // outside: an Artifacts row, the tab strip's reopen button, and a report card
  // in the chat thread. Local state would go stale against those — and, worse,
  // clicking the same card twice (open → back → click again) would be a no-op,
  // because the pointer never changed.
  //
  // `picked` is the one thing this component owns: a row chosen from ITS list.
  // It wins while set, and Back clears both.
  const [picked, setPicked] = useState<number | null>(null)
  // The top-of-panel slot the document's formatting bar portals into: straight
  // after the artifact tabs, ahead of the crumb and the title. State rather
  // than a ref so the portal re-renders once the node exists.
  const [toolbarSlot, setToolbarSlot] = useState<HTMLDivElement | null>(null)
  const focusId = content.reportFocusId
  // A focus set for a DIFFERENT thread is ignored: the panel is global, so a
  // leftover id could otherwise open one thread's report inside another's.
  //
  // The standalone case — a report from Artifacts whose chat is gone — has no
  // list to check against, and is now identified by its own flag rather than by
  // `conversationId == null`. That inference was the bug: a brand-new chat tab
  // ALSO has a null conversation id (a tab has none until its first ask
  // persists), so a focus left over from the thread before it read as
  // "standalone, trust it" and rendered that thread's whole document inside an
  // empty new chat.
  const focusBelongs =
    focusId != null &&
    (content.reportFocusStandalone === true ||
      (conversationId != null &&
        (content.threadReportsStatus !== "ready" ||
          reports.some((r) => r.id === focusId))))
  // A thread with a SINGLE report IS that report: it opens straight into the
  // document, with no list behind it and so no way back to one (see the crumb
  // below). A one-item list is a click that tells the reader nothing.
  const onlyReport = reports.length === 1 ? reports[0].id : null
  const selectedId = picked ?? (focusBelongs ? focusId : null) ?? onlyReport

  // A thread switch is a different set of reports — drop the open document so it
  // can never bleed across threads. Guarded on an ACTUAL change rather than just
  // running on mount: the tab is mounted by the very hand-off that focuses a
  // report, so a mount-time reset would clear the selection it was opened for.
  //
  // `docLoading` is cleared too. It used not to be, and the fetch below used to be
  // keyed on `selectedId` alone — which a thread switch does not necessarily
  // change — so the reset could leave the detail on a "Loading…" title over a
  // blank body, for a request that was never going to be re-issued.
  const prevConversationRef = useRef(conversationId)
  useEffect(() => {
    if (prevConversationRef.current === conversationId) return
    prevConversationRef.current = conversationId
    setPicked(null)
    setDoc(null)
    setDocError(false)
    setDocLoading(false)
  }, [conversationId])

  // Fetch the selected document. The listing carries no `html` (N reports would
  // be N full documents), so this is where the body comes from.
  //
  // Keyed on the THREAD as well as the report, so the reset above is always
  // followed by a real load rather than by whatever the previous thread left
  // behind. `settledKey` records which key the doc/error state actually describes:
  // this effect runs after render, so on the first commit of a new selection
  // `docLoading` is still false, and an empty state derived from that alone would
  // flash before every open.
  const fetchKey = selectedId == null ? null : `${conversationId ?? "standalone"}:${selectedId}`
  const [settledKey, setSettledKey] = useState<string | null>(null)
  useEffect(() => {
    if (selectedId == null || fetchKey == null) { setDoc(null); return }
    let cancelled = false
    setDoc(null)
    setDocError(false)
    setDocLoading(true)
    reportsApi.get(selectedId)
      .then((r) => { if (!cancelled) setDoc(r) })
      .catch(() => { if (!cancelled) setDocError(true) })
      .finally(() => {
        if (cancelled) return
        setDocLoading(false)
        setSettledKey(fetchKey)
      })
    return () => { cancelled = true }
    // `selectedId` is derived from `fetchKey`; listing both would just re-run the
    // same load twice on a thread change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchKey])

  // Back to the list. Clears BOTH the local pick and the external pointer —
  // leaving the pointer set would re-open this report on the very next render.
  // Only reachable when a list exists (>1 report), so clearing the pointer can
  // never strand a standalone report's tab.
  const handleBack = useCallback(() => {
    setPicked(null)
    // Both halves of the pointer go together — a standalone flag outliving the id
    // it qualifies would keep an empty Reports tab on the panel.
    setContent({ reportFocusId: null, reportFocusStandalone: false })
  }, [setContent])

  // ── Generating: the report is being written, right here ──────────────────
  // A report is an artifact, so it generates where artifacts generate. This is
  // the same shape the PRD panel takes (`PrdPanelContent`): the streamed draft
  // the moment there is one, the rotating working state before that.
  //
  // FIRST, ahead of the detail and the list: while a report is being written it
  // is the only thing this tab is about. A thread's older reports are one click
  // away again the moment it lands.
  if (content.reportGenerating) {
    const partial = content.reportPartialMd
    return (
      <div className="tkv2-list-wrap reports-panel" data-testid="reports-generating">
        {partial ? (
          <div style={{ minHeight: 280 }}>
            <GeneratingBanner
              testId="reports-streaming"
              title="Writing the report…"
              sub="Rendering it below as it's written — the finished report replaces this."
            />
            <div data-testid="reports-streaming-preview">
              <SavedChatMarkdown markdown={partial} />
            </div>
          </div>
        ) : (
          <div style={{ minHeight: 280 }}>
            <GeneratingPane
              {...REPORT_GEN}
              testId="reports-generating-pane"
              icon={<IconChartBar size={19} />}
              title="Generating report…"
            />
          </div>
        )}
      </div>
    )
  }

  // ── Detail: one report, in place ──────────────────────────────────────────
  if (selectedId != null) {
    const summary = reports.find((r) => r.id === selectedId) ?? null
    // Still needed for the legacy self-contained document's iframe title (its
    // accessible name); the panel itself no longer prints a heading — see the
    // removed header block below the toolbar.
    const title = doc?.title || summary?.title || "Report"
    // The load for THIS selection has settled and produced neither a document nor
    // an error: the pointer names a report this tab cannot show (deleted, or a
    // pointer that outlived its thread). Say so, instead of the titled, empty
    // document frame that used to sit under a doubled "REPORT REPORT" eyebrow.
    const unavailable = settledKey === fetchKey && !docLoading && !doc && !docError
    return (
      <div className="tkv2-list-wrap reports-panel" data-testid="reports-detail">
        {/* The formatting bar lands HERE — first thing under the
            artifact tabs, above the crumb and the title, which is where a
            control you reach for while typing belongs. It is filled by
            `ReportDocument` through a portal: everything the bar reads (the
            live editor, the save status) is that component's. */}
        <div
          ref={setToolbarSlot}
          data-testid="reports-toolbar-slot"
          style={{ position: "sticky", top: 0, zIndex: 5, background: "var(--surface, #fff)" }}
        />
        <div style={{
          display: "flex", alignItems: "center", gap: 12,
          justifyContent: "space-between", marginTop: 16,
        }}>
          {/* Back only exists when there is a list to go back TO. With one report
              the tab IS the report, and a "All reports" button leading to a
              one-item list would be a step that shows the reader nothing. */}
          {reports.length > 1 ? (
            <div className="tkv2-crumb" style={{ width: "auto", display: "flex", alignItems: "center" }}>
              <button
                type="button"
                className="tkv2-back"
                data-testid="reports-back"
                onClick={handleBack}
              >
                <IconArrowLeft size={13} /> All reports
              </button>
              {/* The count is the reason to go back — it says what's waiting there. */}
              &nbsp;/&nbsp;
              <span className="tkv2-key" style={{ padding: "3px 9px" }}>
                {reports.length} reports
              </span>
            </div>
          ) : (
            <span />
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {/* NO EDIT BUTTON. A report is editable the moment it is open, the
                way the PRD and the team document are: the toolbar is above it
                and typing saves. A mode you have to ask for is a step between
                the reader and a typo they can already see.

                A legacy self-contained document is the exception and needs no
                control to say so — it renders in its own iframe, with no
                toolbar, because it owns its rendering. */}
            {/* Download + share only exist once there is a document to act on,
                and they render in the panel HEADER when this tab was given a
                slot for them -- beside where the PRD's share sits, rather than
                inside the document they act on. Inline is the fallback for a
                host that passed none. */}
            {doc && !shareSlot && (
              <ReportShareMenu
                report={doc}
                onShareChange={(next) => setDoc((cur) => (cur ? { ...cur, ...next } : cur))}
                onToast={showToast}
              />
            )}
            {doc && shareSlot && createPortal(
              <ReportShareMenu
                report={doc}
                onShareChange={(next) => setDoc((cur) => (cur ? { ...cur, ...next } : cur))}
                onToast={showToast}
              />,
              shareSlot,
            )}
          </div>
        </div>

        {/* NO HEADING HERE. The document opens with its own title (an <h1> the
            report was captured with), so a panel-drawn eyebrow + title printed
            it a second time — "VOICE OF CUSTOMER REPORT / Sprntly Customer
            Report" sitting directly above "Sprntly Customer Report". The
            document is the title; the panel frames it. The report's KIND still
            reads on its row in the list, where it distinguishes one report from
            another. */}
        {docLoading && !doc && <ReportSkeleton />}
        {docError && (
          <div className="tkt-push-status tkt-push-status--err" data-testid="reports-detail-error">
            Couldn&apos;t load this report — go back and open it again to retry.
          </div>
        )}
        {unavailable && (
          <div data-testid="reports-detail-empty">
            <EmptyPane
              title="This report isn't available"
              hint={reports.length > 1
                ? "It may have been deleted. Go back to all reports and open another one."
                : "It may have been deleted. Ask for a new report in this chat and it lands in this tab."}
              placeholders={2}
            />
          </div>
        )}
        {doc && (
          // A report is a RICH DOCUMENT — the same shape, the same editor and
          // the same toolbar as a team document, which is what "edit the report
          // in the panel" means. The body arrives as HTML whatever is stored:
          // new reports are captured as HTML, and the rows written before that
          // (the scheduled monthly runs, and everything captured since #1024)
          // are converted on the way out by `app/report_markdown.py`.
          //
          // A LEGACY SELF-CONTAINED DOCUMENT still reads in its sandboxed
          // iframe: it carries its own <head> and <style> and owns its
          // rendering, so it is shown, not edited.
          isFullHtmlDocument(doc.html) ? (
            <HtmlReportView html={doc.html} title={title} fitPanel />
          ) : (
            <ReportDocument
              key={doc.id}
              reportId={doc.id}
              html={doc.html}
              toolbarSlot={toolbarSlot}
              onSaved={(html) => setDoc((cur) => (cur ? { ...cur, html } : cur))}
            />
          )
        )}
      </div>
    )
  }

  // ── List ──────────────────────────────────────────────────────────────────
  return (
    <div className="tkv2-list-wrap reports-panel" data-testid="reports-list">
      {loading && <ReportSkeleton />}

      {!loading && error && (
        <div className="tkt-push-status tkt-push-status--err" data-testid="reports-list-error">
          Couldn&apos;t load this chat&apos;s reports — reopen the tab to retry.
        </div>
      )}

      {!loading && !error && reports.length === 0 && (
        <EmptyPane
          title="No reports in this chat"
          hint="Ask for a report here — a voice-of-customer read, a competitor review — and it lands in this tab."
          placeholders={2}
        />
      )}

      {!loading && reports.length > 0 && (
        <>
          <div className="tkv2-intro">
            <span className="tkv2-spark">✳</span>
            <div>
              This chat has <b>{reports.length} report{reports.length !== 1 ? "s" : ""}</b>.
              Open one to read it here.
            </div>
          </div>

          <div className="tkt-list">
            {reports.map((r) => (
              <ReportRow key={r.id} report={r} onOpen={() => setPicked(r.id)} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

/** One report in the list. Same card shape as a ticket row, so the panel reads
 *  as one surface rather than four different lists. */
function ReportRow({ report, onOpen }: { report: ReportSummary; onOpen: () => void }) {
  const meta = [
    `${reportKindLabel(report.skill)} report`,
    // A live link is worth seeing without opening the document — this report is
    // reachable by anyone holding the URL.
    report.share_mode !== "private" ? "Shared" : "",
    report.created_at ? formatRelativeDate(report.created_at) : "",
  ].filter(Boolean).join(" · ")

  return (
    <button type="button" className="tkv2-card" data-report-id={report.id} onClick={onOpen}>
      <span className="tkv2-key" style={{ display: "flex", alignItems: "center" }}>
        <ReportGlyph />
      </span>
      <div className="tkv2-card-main">
        <div className="tkv2-card-title">{report.title || "Untitled report"}</div>
        <div className="tkv2-story">{meta}</div>
      </div>
    </button>
  )
}

/** The bar-chart glyph the Artifacts report row uses — these documents lead with
 *  charts and sized themes, which is what sets them apart from a PRD. */
function ReportGlyph() {
  return (
    <svg
      width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden
    >
      <line x1="4" y1="20" x2="20" y2="20" />
      <rect x="6" y="11" width="3.4" height="6" rx="1" />
      <rect x="11.4" y="7" width="3.4" height="10" rx="1" />
      <rect x="16.8" y="13" width="3.4" height="4" rx="1" />
    </svg>
  )
}

/** Shared loading placeholder — matches the report drawer's skeleton so a
 *  report loads the same way wherever it's opened. */
function ReportSkeleton() {
  return (
    <div data-testid="reports-loading" style={{ padding: "8px 0" }}>
      {[1, 2, 3].map((i) => (
        <div
          key={i}
          style={{
            height: i === 1 ? 26 : 13,
            width: i === 1 ? "55%" : `${88 - i * 6}%`,
            borderRadius: 6,
            marginBottom: 14,
            background: "var(--surface-2, #F0EDE7)",
            animation: "chats-pulse 1.4s ease-in-out infinite",
            animationDelay: `${i * 0.1}s`,
          }}
        />
      ))}
      <style>{`@keyframes chats-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }`}</style>
    </div>
  )
}

/** Is this body a SELF-CONTAINED HTML document — doctype, head, its own
 *  `<style>` — rather than the HTML fragment every report is now?
 *
 *  Only the legacy rows are: reports written under the pinned templates, before
 *  #1024 removed them. Those own their rendering and read in a sandboxed
 *  iframe, so they are shown and not edited. Everything else — captured HTML,
 *  and the markdown rows the API converts on the way out — is a fragment this
 *  panel renders and edits like any other document.
 *
 *  The same `looksLikeHtmlBrief` sniff chat uses to choose an iframe and
 *  `report_capture` uses to recognise a document answer, so all three agree
 *  about what a given report is. */
function isFullHtmlDocument(html: string): boolean {
  return looksLikeHtmlBrief(html)
}
