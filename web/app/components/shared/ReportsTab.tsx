"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useContent } from "../../context/ContentContext"
import { useNavigation } from "../../context/NavigationContext"
import { HtmlReportView } from "./HtmlReportView"
import { ReportShareMenu } from "./ReportShareMenu"
import { EmptyPane } from "./EmptyPane"
import { IconArrowLeft } from "@tabler/icons-react"
import { reportKindLabel } from "../../lib/reportKind"
import { formatRelativeDate } from "../../lib/sources-helpers"
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
}: {
  reports: ReportSummary[]
  loading: boolean
  /** The list fetch failed. The tab says so rather than reading as an empty
   *  thread — "no reports" and "couldn't load them" are different facts. */
  error?: boolean
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
  const focusId = content.reportFocusId
  // A focus set for a DIFFERENT thread is ignored: the panel is global, so a
  // leftover id could otherwise open one thread's report inside another's.
  // A standalone report (no conversation) has no list to check against.
  const focusBelongs =
    focusId != null &&
    (conversationId == null ||
      content.threadReportsStatus !== "ready" ||
      reports.some((r) => r.id === focusId))
  // A thread with a SINGLE report IS that report: it opens straight into the
  // document, with no list behind it and so no way back to one (see the crumb
  // below). A one-item list is a click that tells the reader nothing.
  const onlyReport = reports.length === 1 ? reports[0].id : null
  const selectedId = picked ?? (focusBelongs ? focusId : null) ?? onlyReport

  // A thread switch is a different set of reports — drop the open document so it
  // can never bleed across threads. Guarded on an ACTUAL change rather than just
  // running on mount: the tab is mounted by the very hand-off that focuses a
  // report, so a mount-time reset would clear the selection it was opened for.
  const prevConversationRef = useRef(conversationId)
  useEffect(() => {
    if (prevConversationRef.current === conversationId) return
    prevConversationRef.current = conversationId
    setPicked(null)
    setDoc(null)
    setDocError(false)
  }, [conversationId])

  // Fetch the selected document. The listing carries no `html` (N reports would
  // be N full documents), so this is where the body comes from.
  useEffect(() => {
    if (selectedId == null) { setDoc(null); return }
    let cancelled = false
    setDoc(null)
    setDocError(false)
    setDocLoading(true)
    reportsApi.get(selectedId)
      .then((r) => { if (!cancelled) setDoc(r) })
      .catch(() => { if (!cancelled) setDocError(true) })
      .finally(() => { if (!cancelled) setDocLoading(false) })
    return () => { cancelled = true }
  }, [selectedId])

  // Back to the list. Clears BOTH the local pick and the external pointer —
  // leaving the pointer set would re-open this report on the very next render.
  // Only reachable when a list exists (>1 report), so clearing the pointer can
  // never strand a standalone report's tab.
  const handleBack = useCallback(() => {
    setPicked(null)
    setContent({ reportFocusId: null })
  }, [setContent])

  // ── Detail: one report, in place ──────────────────────────────────────────
  if (selectedId != null) {
    const summary = reports.find((r) => r.id === selectedId) ?? null
    const title = doc?.title || summary?.title || "Report"
    return (
      <div className="tkv2-list-wrap reports-panel" data-testid="reports-detail">
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
          {/* Download + share only exist once there is a document to act on. */}
          {doc && (
            <ReportShareMenu
              report={doc}
              onShareChange={(next) => setDoc((cur) => (cur ? { ...cur, ...next } : cur))}
              onToast={showToast}
            />
          )}
        </div>

        <div style={{ margin: "10px 0 14px" }}>
          <div style={{
            fontSize: 10, fontWeight: 700, textTransform: "uppercase",
            letterSpacing: "0.06em", color: "var(--ink-3, #8C8A84)", marginBottom: 3,
          }}>
            {reportKindLabel(doc?.skill ?? summary?.skill)} report
          </div>
          <div data-testid="reports-detail-title" style={{ fontSize: 17, fontWeight: 700 }}>
            {docLoading && !doc ? "Loading…" : title}
          </div>
        </div>

        {docLoading && !doc && <ReportSkeleton />}
        {docError && (
          <div className="tkt-push-status tkt-push-status--err" data-testid="reports-detail-error">
            Couldn&apos;t load this report — go back and open it again to retry.
          </div>
        )}
        {doc && <HtmlReportView html={doc.html} title={title} fitPanel />}
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
