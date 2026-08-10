"use client"

// Standalone Share affordance for a displayed evidence document, mounted on
// EvidenceBottomBar. Mirrors the PRD ShareMenu's inline <code>+copy-flip
// markup and 1.5s "Copied!" timer exactly (ContentPanel.tsx's ShareMenu),
// but reads the canonical token off `content.evidenceShareToken` — never
// `content.prd`, which the standalone-evidence surface has none of — and
// NEVER mints: this is a dedicated control, not a widened ShareMenu, because
// ShareMenu is deliberately scoped to the in-view PRD (prdInScopeFor) and
// re-generalising it would re-open the exact coupling that scoping closed.
import { useEffect, useRef, useState } from "react"
import { useContent } from "../../context/ContentContext"
import { IconLink, IconShare } from "@tabler/icons-react"

export function EvidenceShareControl() {
  const { content } = useContent()
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // Built from the pre-existing canonical token ArtifactsScreen threads onto
  // content.evidenceShareToken (from the evidence GET's get-or-create
  // share_token) — NO network call on open. Nullish while the token hasn't
  // landed yet (or on an evidence-open path that doesn't yet thread it) —
  // the control renders disabled rather than minting.
  const shareUrl =
    content.evidenceShareToken && typeof window !== "undefined"
      ? `${window.location.origin}/?share=${content.evidenceShareToken}`
      : null

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDocClick)
    return () => document.removeEventListener("mousedown", onDocClick)
  }, [open])

  const handleCopyLink = async () => {
    if (!shareUrl) return
    try {
      await navigator.clipboard.writeText(shareUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Copy failures are non-fatal — the link stays visible to copy
      // manually (mirrors ShareMenu's identical catch).
    }
  }

  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button
        type="button"
        className="cpanel-action-btn"
        // Distinct accessible name from the header ShareMenu's "Share"
        // trigger (both can be on screen at once on the evidence tab) —
        // queries that key on an accessible name of "Share" must resolve to
        // exactly the header control, never this one. Visible text stays
        // "Share" to mirror ShareMenu's markup; aria-label overrides the
        // accessible-name computation without changing what's rendered.
        aria-label="Copy evidence link"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}
      >
        <IconShare size={12} />Share
      </button>
      {open && (
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
        </div>
      )}
    </div>
  )
}
