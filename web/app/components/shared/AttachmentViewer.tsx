"use client"

/**
 * Shared attachment overlay — extracted VERBATIM from `ChatScreen` so every chat
 * surface renders one identical viewer. Main mounts it at its own root exactly
 * as before (an import swap, no behaviour change); the project chat mounts the
 * SAME component on its host, driven by its own viewer state. No logic changed
 * in the move.
 *
 * Full-screen overlay that renders an attachment. When the ORIGINAL file was
 * stored (`key`), it fetches a fresh signed URL and renders the real document —
 * PDF/image inline, everything else offered as a download — falling back to the
 * extracted text. Opened by clicking a file card on a user turn.
 */

import { useEffect, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { attachmentsApi } from "../../lib/api"

export function AttachmentViewer({
  attachment,
  onClose,
}: {
  attachment: { name: string; content: string; key?: string | null; mime?: string | null }
  onClose: () => void
}) {
  const [urls, setUrls] = useState<{ view_url: string; download_url: string; mime: string } | null>(null)
  const [status, setStatus] = useState<"idle" | "loading" | "error">(attachment.key ? "loading" : "idle")

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [onClose])

  // Sign-on-open: the stored URL expires, so mint a fresh one each time the
  // viewer opens. Best-effort — a failure falls back to the extracted text.
  useEffect(() => {
    if (!attachment.key) return
    let cancelled = false
    setStatus("loading")
    attachmentsApi.sign(attachment.key, attachment.name)
      .then((u) => { if (!cancelled) { setUrls(u); setStatus("idle") } })
      .catch(() => { if (!cancelled) setStatus("error") })
    return () => { cancelled = true }
  }, [attachment.key, attachment.name])

  const mime = urls?.mime || attachment.mime || ""
  const isPdf = /pdf/i.test(mime) || /\.pdf$/i.test(attachment.name)
  const isImage = /^image\//i.test(mime) || /\.(png|jpe?g|gif|webp)$/i.test(attachment.name)
  const hasText = !!attachment.content.trim()

  return (
    <div className="bc-file-viewer-backdrop" role="dialog" aria-modal="true" aria-label={attachment.name} onClick={onClose}>
      <div className="bc-file-viewer" onClick={(e) => e.stopPropagation()}>
        <div className="bc-file-viewer-head">
          <span className="bc-file-viewer-title" title={attachment.name}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
            {attachment.name}
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            {urls?.download_url ? (
              <a
                className="bc-file-viewer-download"
                href={urls.download_url}
                download={attachment.name}
                target="_blank"
                rel="noopener noreferrer"
                title={`Download ${attachment.name}`}
                aria-label={`Download ${attachment.name}`}
                style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", padding: 6, color: "inherit", opacity: 0.75 }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
              </a>
            ) : null}
            <button type="button" className="bc-file-viewer-close" aria-label="Close" onClick={onClose}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </span>
        </div>
        <div className="bc-file-viewer-body">
          {attachment.key && status === "loading" ? (
            <p className="bc-file-viewer-empty">Loading document…</p>
          ) : urls && isPdf ? (
            <iframe
              src={urls.view_url}
              title={attachment.name}
              data-testid="attachment-pdf-frame"
              style={{ width: "100%", height: "100%", minHeight: "70vh", border: "none" }}
            />
          ) : urls && isImage ? (
            <img
              src={urls.view_url}
              alt={attachment.name}
              data-testid="attachment-image"
              style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", display: "block", margin: "0 auto" }}
            />
          ) : hasText ? (
            <>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{attachment.content}</ReactMarkdown>
              {urls && !isPdf && !isImage ? (
                <p className="bc-file-viewer-empty">This file type can’t be previewed inline — use the download button above to open the original.</p>
              ) : null}
            </>
          ) : urls ? (
            <p className="bc-file-viewer-empty">This file type can’t be previewed inline — use the download button above to open the original.</p>
          ) : (
            <p className="bc-file-viewer-empty">No preview available for this file.</p>
          )}
        </div>
      </div>
    </div>
  )
}
