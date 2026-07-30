"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { IconCopy, IconCheck } from "./app-icons"
// The panel-chrome icons come from Tabler, matching ContentPanel's Share menu so
// the two menus are visually identical.
import { IconShare, IconFileTypePdf } from "@tabler/icons-react"
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
 *
 * Lives beside the report viewer rather than inside it: a report is read in ONE
 * place now (the content panel's Reports tab), but the actions on a document are
 * a separate concern from the document, and this file is what the panel imports.
 */
export function ReportShareMenu({
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
