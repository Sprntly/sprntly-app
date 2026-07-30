"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { HtmlReportView } from "./HtmlReportView"
import { useCpanelPhase } from "./useCpanelPhase"
import { IconClose, IconCopy, IconCheck } from "./app-icons"
// The panel-chrome icons come from Tabler, matching ContentPanel's Share menu so
// the two menus are visually identical.
import { IconShare, IconFileTypePdf } from "@tabler/icons-react"
import { reportKindLabel } from "../../lib/reportKind"
import { slugifyTitle } from "../../lib/prdExport"
import { saveBlob } from "../../lib/saveBlob"
import { reportsApi, type ReportDoc } from "../../lib/api"

/**
 * The report's Share menu: download the PDF, and turn a public link on or off.
 *
 * PDF is the only download format (see app/report_pdf.py) — no DOCX/markdown
 * item, because flattening these layout-led documents loses what makes them
 * readable.
 *
 * Sharing is opt-in and starts OFF. The link is only revealed once it exists, so
 * the menu can never show a URL that isn't live.
 */
function ReportShareMenu({
  report,
  onShareChange,
  onToast,
}: {
  report: ReportDoc
  /** Bubble the new state up so the panel's copy of the doc stays truthful. */
  onShareChange: (next: { share_mode: ReportDoc["share_mode"]; share_token: string | null }) => void
  onToast: (title: string, body: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<"pdf" | "share" | null>(null)
  const [copied, setCopied] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDocClick)
    return () => document.removeEventListener("mousedown", onDocClick)
  }, [open])

  const shareUrl =
    report.share_token && typeof window !== "undefined"
      ? `${window.location.origin}/r/${report.share_token}`
      : null

  const handlePdf = useCallback(async () => {
    setBusy("pdf")
    try {
      const { blob, filename } = await reportsApi.downloadPdf(report.id)
      saveBlob(blob, filename || `${slugifyTitle(report.title)}.pdf`)
      setOpen(false)
    } catch (err) {
      // 503 = renderer unavailable. Say so rather than saving a broken file.
      // The reason goes to the console: a failed download is otherwise
      // indistinguishable from a failed render, and the two are fixed in
      // different places.
      console.error("report PDF download failed", err)
      onToast("PDF export failed", "Could not generate the PDF. Please try again.")
    } finally {
      setBusy(null)
    }
  }, [report.id, report.title, onToast])

  const handleToggleShare = useCallback(async () => {
    const turningOn = report.share_mode === "private"
    setBusy("share")
    try {
      const next = await reportsApi.share(report.id, {
        share_mode: turningOn ? "public" : "private",
      })
      onShareChange({
        share_mode: next.share_mode as ReportDoc["share_mode"],
        share_token: next.share_token,
      })
      if (turningOn && next.share_token) {
        await navigator.clipboard?.writeText(
          `${window.location.origin}/r/${next.share_token}`,
        ).catch(() => {})
        onToast("Link created", "Anyone with the link can now view this report.")
      } else {
        onToast("Sharing off", "The link no longer opens this report.")
      }
    } catch {
      onToast("Couldn't update sharing", "Please try again.")
    } finally {
      setBusy(null)
    }
  }, [report.id, report.share_mode, onShareChange, onToast])

  const handleCopy = useCallback(async () => {
    if (!shareUrl) return
    try {
      await navigator.clipboard.writeText(shareUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      onToast("Couldn't copy", "Copy the link from your browser instead.")
    }
  }, [shareUrl, onToast])

  const shared = report.share_mode !== "private"

  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button
        type="button"
        className="cpanel-action-btn"
        data-testid="report-share-button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}
      >
        <IconShare size={12} />{shared ? "Shared" : "Share"}
      </button>
      {open && (
        <div className="share-menu share-menu--down open" role="menu">
          <div
            className="share-menu-item"
            role="menuitem"
            data-testid="report-download-pdf"
            onClick={() => { if (!busy) void handlePdf() }}
          >
            <div className="share-menu-item-icon"><IconFileTypePdf size={14} /></div>
            <div>
              <div style={{ fontWeight: 600 }}>
                {busy === "pdf" ? "Preparing…" : "Download PDF"}
              </div>
              <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
                Export as .pdf
              </div>
            </div>
          </div>

          <div
            className="share-menu-item"
            role="menuitem"
            data-testid="report-toggle-share"
            onClick={() => { if (!busy) void handleToggleShare() }}
          >
            <div className="share-menu-item-icon"><IconShare size={14} /></div>
            <div>
              <div style={{ fontWeight: 600 }}>
                {busy === "share"
                  ? "Updating…"
                  : shared ? "Turn off link sharing" : "Create a share link"}
              </div>
              <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
                {shared
                  ? "Stops the link from opening this report"
                  : "Anyone with the link can view it"}
              </div>
            </div>
          </div>

          {/* Only shown once a link actually exists, so the menu can never
              display a URL that doesn't resolve. */}
          {shareUrl && (
            <div
              className="share-menu-item"
              role="menuitem"
              data-testid="report-copy-link"
              onClick={() => void handleCopy()}
            >
              <div className="share-menu-item-icon">
                {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{copied ? "Copied" : "Copy link"}</div>
                <div style={{
                  fontSize: 11, color: "var(--muted)", fontWeight: 400,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  maxWidth: 240,
                }}>
                  {shareUrl}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * The report viewer drawer — a captured HTML report opened from the Artifacts
 * surface.
 *
 * Deliberately NOT a tab inside ContentPanel: that drawer is the PRD pipeline
 * (Evidence → PRD → Tickets, with a "PRD · …" header and a PRD-scoped share
 * menu), and a report belongs to none of it. This is the same `cpanel` shell and
 * the same slide (shared `useCpanelPhase`), with a report's own header.
 *
 * Presentational — no fetching. ArtifactsScreen owns the load and passes the
 * document in, mirroring the ArtifactsView split so this can be unit-tested
 * without a network stub.
 */
export function ReportPanel({
  report,
  loading,
  attachment,
  onClose,
  onShareChange,
  onToast,
}: {
  report: ReportDoc | null
  loading: boolean
  /** Titles of what the report is attached to, from the artifacts row (which
   *  already resolved them, so the viewer needs no second fetch). A null title
   *  renders nothing — never a fabricated label. */
  attachment?: { conversationTitle?: string | null; prdTitle?: string | null }
  onClose: () => void
  /** Share state changed in the menu — the owner updates its copy of the doc. */
  onShareChange?: (next: {
    share_mode: ReportDoc["share_mode"]; share_token: string | null
  }) => void
  onToast?: (title: string, body: string) => void
}) {
  const open = loading || report != null
  const { mounted, phase } = useCpanelPhase(open)
  if (!mounted) return null

  const closing = phase === "out"
  const from = [
    attachment?.conversationTitle ? `from ${attachment.conversationTitle}` : "",
    attachment?.prdTitle ? `on PRD ${attachment.prdTitle}` : "",
  ].filter(Boolean).join(" · ")

  return (
    <>
      <div className="cpanel-overlay" onClick={onClose} />
      <aside
        data-testid="report-panel"
        className={`cpanel${phase === "in" ? " cpanel--in" : ""}${closing ? " cpanel--out" : ""}`}
        // On the way out the panel is a departing visual only — take it out of
        // the a11y tree and the tab order so it can't be reached mid-slide.
        inert={closing || undefined}
        aria-hidden={closing || undefined}
      >
        <div className="cpanel-head">
          <div style={{ minWidth: 0 }}>
            <div style={{
              fontSize: 10, fontWeight: 700, textTransform: "uppercase",
              letterSpacing: "0.06em", color: "var(--ink-3, #8C8A84)", marginBottom: 3,
            }}>
              {reportKindLabel(report?.skill)} report
            </div>
            <div
              data-testid="report-panel-title"
              className="cpanel-main-name"
              style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {report?.title || (loading ? "Loading…" : "Report")}
            </div>
            {/* The ATTACHMENT: the chat room / PRD this report was generated in.
                Omitted entirely when it stands alone. */}
            {from && (
              <div
                data-testid="report-panel-attachment"
                style={{ fontSize: 11.5, color: "var(--ink-3, #8C8A84)", marginTop: 2 }}
              >
                {from}
              </div>
            )}
          </div>
          <div className="cpanel-head-actions">
            {/* Download + share only exist once there is a document to act on. */}
            {report && (
              <ReportShareMenu
                report={report}
                onShareChange={onShareChange ?? (() => {})}
                onToast={onToast ?? (() => {})}
              />
            )}
            <button type="button" className="cpanel-close" onClick={onClose} aria-label="Close">
              <IconClose size={16} />
            </button>
          </div>
        </div>

        <div className="cpanel-body">
          {loading && (
            <div data-testid="report-panel-loading" style={{ padding: "8px 0" }}>
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
          )}
          {!loading && report && (
            <HtmlReportView html={report.html} title={report.title || "Report"} />
          )}
        </div>
      </aside>
    </>
  )
}
