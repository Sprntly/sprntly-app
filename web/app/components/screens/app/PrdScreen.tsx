"use client"

import {
  type CSSProperties,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { PrdSections } from "../../shared/PrdSections"
import { DesignAgentLauncher } from "../../design-agent/DesignAgentLauncher"
import { useCompany } from "../../../context/CompanyContext"
import { ApiError, designAgentApi, prdApi, type PrototypeRecord } from "../../../lib/api"
import { markdownToPrdState } from "../../../lib/prd-adapter"
import { runDesignAgentGeneration } from "../../../lib/runDesignAgentGeneration"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"
import {
  IconCheck,
  IconCopy,
  IconGrid,
  IconLinkInsert,
  IconListBullet,
  IconMail,
  IconRedo,
  IconUndo,
} from "../../shared/app-icons"
import { PrdPatchBanner } from "../../design-agent/PrdPatchBanner"
import type { PrdState } from "../../../types/content"

// ── Auto-save to localStorage ──────────────────────────────────────────────
const PRD_DRAFT_KEY = (prdId: number) => `sprntly_prd_draft_${prdId}`

function loadDraft(prdId: number): string | null {
  try { return localStorage.getItem(PRD_DRAFT_KEY(prdId)) } catch { return null }
}
function saveDraft(prdId: number, html: string) {
  try { localStorage.setItem(PRD_DRAFT_KEY(prdId), html) } catch { /* ignore */ }
}

type SaveStatus = "saved" | "saving" | "unsaved"

// ── Summary strip (extracted from prd-tldr section) ───────────────────────
function PrdSummaryStrip({ prd }: { prd: PrdState }) {
  const tldr = prd.sections.find((s) => s.type === "prd-tldr")
  if (!tldr || tldr.type !== "prd-tldr") return null
  return (
    <div
      style={{
        display: "flex",
        gap: 0,
        marginBottom: 20,
        borderRadius: 10,
        border: "1px solid var(--line)",
        overflow: "hidden",
        fontSize: 12.5,
      }}
    >
      {[
        { label: "Problem", text: tldr.problem, accent: "var(--danger-soft)", ink: "var(--danger)" },
        { label: "Fix", text: tldr.fix, accent: "var(--accent-muted)", ink: "var(--accent-ink)" },
        { label: "Impact", text: tldr.impact, accent: "var(--surface-2)", ink: "var(--ink-2)" },
      ].map(({ label, text, accent, ink }, i, arr) => (
        <div
          key={label}
          style={{
            flex: 1,
            padding: "10px 14px",
            background: accent,
            borderRight: i < arr.length - 1 ? "1px solid var(--line)" : undefined,
          }}
        >
          <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: ink, marginBottom: 4 }}>
            {label}
          </div>
          <div style={{ color: "var(--ink)", lineHeight: 1.45 }}>{text}</div>
        </div>
      ))}
    </div>
  )
}

/**
 * Hot-file exception (sanctioned): this screen is an append-only hot file. The
 * only change here threads `prdTitle={prd.title}` into <PrdSections> so the
 * relocated generate trigger's preview card + canvas breadcrumb can label the
 * PRD. The editable PRD-body region is deliberately left untouched.
 */
type PrdVersion = { id: number; prd_id: number; version_number: number; title: string; payload_md: string; saved_by: string; saved_at: string }

export function PrdScreen() {
  const { goTo, openModal, shareMenuOpen, setShareMenuOpen, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const { activeCompany } = useCompany()
  const prd = content.prd

  const [prdLoading, setPrdLoading] = useState(false)

  // Auto-load the latest PRD on mount if content.prd is empty (e.g. after refresh)
  useEffect(() => {
    if (prd || !activeCompany) return
    let cancelled = false
    setPrdLoading(true)
    prdApi.latest(activeCompany).then((record) => {
      if (cancelled || !record.payload_md) return
      setContent({
        prd: {
          ...markdownToPrdState(record.payload_md),
          prd_id: record.id,
          figma_file_key: undefined,
        },
      })
    }).catch((e) => {
      if (e instanceof ApiError && e.status === 404) return
    }).finally(() => {
      if (!cancelled) setPrdLoading(false)
    })
    return () => { cancelled = true }
  }, [prd, activeCompany, setContent])

  const bodyRef = useRef<HTMLDivElement>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("saved")
  const [versions, setVersions] = useState<PrdVersion[]>([])
  const [showVersions, setShowVersions] = useState(false)
  const [versionsLoading, setVersionsLoading] = useState(false)

  // Load saved draft into the editable PRD body on mount / prd change
  useEffect(() => {
    if (!prd || !bodyRef.current) return
    const draft = loadDraft(prd.prd_id)
    if (draft) {
      bodyRef.current.innerHTML = draft
    }
  }, [prd?.prd_id])

  // Auto-save: debounce 2s after last keystroke → save to Supabase + localStorage
  const handleInput = useCallback(() => {
    setSaveStatus("unsaved")
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(async () => {
      if (!prd || !bodyRef.current) return
      setSaveStatus("saving")
      const html = bodyRef.current.innerHTML
      // Save locally first (instant)
      saveDraft(prd.prd_id, html)
      // Extract text content for the markdown payload
      const textContent = bodyRef.current.innerText || ""
      try {
        await prdApi.update(prd.prd_id, {
          title: prd.title,
          payload_md: textContent,
        })
        setSaveStatus("saved")
      } catch {
        // Supabase save failed — local draft is still safe
        setSaveStatus("saved")
      }
    }, 2000)
  }, [prd])

  // Toolbar: execCommand helpers
  const exec = (cmd: string, value?: string) => {
    bodyRef.current?.focus()
    document.execCommand(cmd, false, value)
  }

  const handleShare = (type: "email" | "slack" | "link") => {
    setShareMenuOpen(false)
    const messages = {
      email: { title: "Opening email draft", sub: "Your email client will open with the PRD attached." },
      slack: { title: "Posted to Slack", sub: "PRD shared in #product. Your team can react & comment inline." },
      link:  { title: "Link copied", sub: "Anyone at sprntly.ai with the link can view this PRD." },
    }
    showToast(messages[type].title, messages[type].sub)
  }

  return (
    <AppLayout mainClassName="main--reading" inlineChat>
      {prd && <PrdPatchBanner prdId={prd.prd_id} />}
      <div className="prd-header-row">
        <a className="detail-back" onClick={() => goTo("detail")}>
          ← Back to evidence
        </a>
      </div>

      <div className="prd-frame">
        <PrdToolbar hasDoc={!!prd} saveStatus={saveStatus} exec={exec} />
        {prd ? (
          <>
            <PrdSummaryStrip prd={prd} />
            <div
              className="prd-body"
              contentEditable
              spellCheck={false}
              suppressContentEditableWarning
              ref={bodyRef}
              onInput={handleInput}
            >
              <div className="prd-meta">{prd.metaLine}</div>
              <h1 className="prd-title">{prd.title}</h1>
              <PrdSections sections={prd.sections} prdId={prd.prd_id} figmaFileKey={prd.figma_file_key ?? null} prdTitle={prd.title} />
            </div>
          </>
        ) : (
          <div className="prd-body" style={{ minHeight: 280 }}>
            {prdLoading ? (
              <div style={{ display: "flex", alignItems: "center", gap: 12, padding: 32, color: "var(--ink-2)" }}>
                <span className="prd-loader" aria-hidden />
                Loading PRD…
              </div>
            ) : (
              <EmptyPane
                title="No PRD draft loaded"
                hint="Generate a PRD from the Weekly Brief by selecting an insight and clicking Generate PRD."
                placeholders={0}
              />
            )}
          </div>
        )}

        <div className="prd-foot">
          <div className="prd-foot-left">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={!prd}
              onClick={async () => {
                if (!prd || !bodyRef.current) return
                setSaveStatus("saving")
                try {
                  await prdApi.update(prd.prd_id, {
                    title: prd.title,
                    payload_md: bodyRef.current.innerText || "",
                  })
                  setSaveStatus("saved")
                  showToast("Draft saved", "Your PRD has been saved to Supabase.")
                } catch {
                  showToast("Save failed", "Could not save to server. Local draft preserved.")
                  setSaveStatus("saved")
                }
              }}
            >
              Save as draft
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={!prd}
              onClick={async () => {
                if (!prd) return
                setShowVersions(!showVersions)
                if (!showVersions) {
                  setVersionsLoading(true)
                  try {
                    const v = await prdApi.listVersions(prd.prd_id)
                    setVersions(v)
                  } catch {
                    setVersions([])
                  }
                  setVersionsLoading(false)
                }
              }}
              style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
            >
              Version history
              <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor" style={{ transform: showVersions ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}>
                <path d="M5 7L1 3h8z" />
              </svg>
            </button>
          </div>
          <div className="prd-foot-right">
            <div style={{ position: "relative" }}>
              <button
                type="button"
                className="btn"
                disabled={!prd}
                onClick={(e) => { e.stopPropagation(); if (!prd) return; setShareMenuOpen(!shareMenuOpen) }}
              >
                Share
                <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor">
                  <path d="M5 7L1 3h8z" />
                </svg>
              </button>
              {shareMenuOpen && prd && (
                <div className="share-menu open">
                  <ShareMenuItem icon={<IconMail size={14} />} title="Email" desc="Send to teammates or stakeholders" onClick={() => handleShare("email")} />
                  <ShareMenuItem
                    icon={<span style={{ fontWeight: 700, fontSize: 10 }}>Sl</span>}
                    iconStyle={{ background: "#4A154B", color: "#fff" }}
                    title="Slack"
                    desc="Post to a channel"
                    onClick={() => handleShare("slack")}
                  />
                  <div className="share-menu-divider" />
                  <ShareMenuItem icon={<IconCopy size={14} />} title="Copy link" desc="Viewable by your team" onClick={() => handleShare("link")} />
                </div>
              )}
            </div>
            <button
              type="button"
              className="btn btn-accent"
              disabled={!prd}
              onClick={() => prd && openModal("approve")}
            >
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <IconCheck size={16} />
                Approve & next step
              </span>
            </button>
          </div>
        </div>
      </div>

      {/* Version history panel */}
      {showVersions && prd && (
        <div style={{
          marginTop: 12, borderRadius: 10, border: "1px solid var(--line)",
          background: "var(--surface)", overflow: "hidden",
        }}>
          <div style={{
            padding: "10px 16px", background: "var(--surface-2)", borderBottom: "1px solid var(--line)",
            fontSize: 12, fontWeight: 600, color: "var(--ink-2)", display: "flex", alignItems: "center", justifyContent: "space-between",
          }}>
            <span>Version History</span>
            <span style={{ fontSize: 11, fontWeight: 400, color: "var(--ink-4)" }}>
              {versions.length} version{versions.length !== 1 ? "s" : ""}
            </span>
          </div>
          {versionsLoading ? (
            <div style={{ padding: "20px 16px", textAlign: "center", fontSize: 12, color: "var(--ink-4)" }}>
              Loading versions...
            </div>
          ) : versions.length === 0 ? (
            <div style={{ padding: "20px 16px", textAlign: "center", fontSize: 12, color: "var(--ink-4)" }}>
              No versions saved yet. Edits auto-save a version each time.
            </div>
          ) : (
            <div style={{ maxHeight: 260, overflowY: "auto" }}>
              {versions.map((v) => (
                <div
                  key={v.id}
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    padding: "10px 16px", borderBottom: "1px solid var(--line)", fontSize: 12.5,
                  }}
                >
                  <div>
                    <div style={{ fontWeight: 500, color: "var(--ink)" }}>
                      v{v.version_number} — {v.title.slice(0, 50)}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 2 }}>
                      {v.saved_by} · {new Date(v.saved_at).toLocaleString()}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={async () => {
                      try {
                        await prdApi.restoreVersion(prd.prd_id, v.id)
                        showToast("Version restored", `Restored to v${v.version_number}. Reload to see changes.`)
                        window.location.reload()
                      } catch {
                        showToast("Restore failed", "Could not restore this version.")
                      }
                    }}
                    style={{
                      fontSize: 11, padding: "4px 10px", borderRadius: 6,
                      border: "1px solid var(--line)", background: "var(--surface)",
                      cursor: "pointer", color: "var(--accent)", fontWeight: 600,
                    }}
                  >
                    Restore
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {prd && (
        <PrototypeSection
          prdId={prd.prd_id}
          figmaFileKey={prd.figma_file_key ?? null}
        />
      )}
    </AppLayout>
  )
}

// ── Toolbar ────────────────────────────────────────────────────────────────
function PrdToolbar({
  hasDoc,
  saveStatus,
  exec,
}: {
  hasDoc: boolean
  saveStatus: SaveStatus
  exec: (cmd: string, value?: string) => void
}) {
  const statusLabel =
    saveStatus === "saving" ? "Saving…" :
    saveStatus === "unsaved" ? "Unsaved" :
    "Saved · Draft"

  const statusColor =
    saveStatus === "saving"  ? "var(--accent)" :
    saveStatus === "unsaved" ? "var(--ink-3)"  :
    "var(--accent)"

  return (
    <div className="prd-toolbar">
      <div className="prd-tools-l">
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Undo" aria-label="Undo" onClick={() => exec("undo")}>
          <IconUndo size={16} />
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Redo" aria-label="Redo" onClick={() => exec("redo")}>
          <IconRedo size={16} />
        </button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Bold" onClick={() => exec("bold")}>
          <strong>B</strong>
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Italic" onClick={() => exec("italic")}>
          <em>I</em>
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Underline" onClick={() => exec("underline")}>
          <u>U</u>
        </button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Heading 1" onClick={() => exec("formatBlock", "h1")}>
          H1
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Heading 2" onClick={() => exec("formatBlock", "h2")}>
          H2
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Bullet list" aria-label="Bullet list" onClick={() => exec("insertUnorderedList")}>
          <IconListBullet size={16} />
        </button>
        <div className="prd-tool-divider" />
        <button
          type="button"
          className="prd-tool"
          disabled={!hasDoc}
          title="Insert link"
          style={{ display: "inline-flex", alignItems: "center" }}
          onClick={() => {
            const url = prompt("Enter URL")
            if (url) exec("createLink", url)
          }}
        >
          <IconLinkInsert size={15} />
          <span style={{ marginLeft: 5 }}>Link</span>
        </button>
        <button
          type="button"
          className="prd-tool"
          disabled={!hasDoc}
          title="Insert table"
          style={{ display: "inline-flex", alignItems: "center" }}
        >
          <IconGrid size={15} />
          <span style={{ marginLeft: 5 }}>Table</span>
        </button>
      </div>
      <div className="prd-status">
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: hasDoc ? statusColor : "var(--muted)", transition: "background 0.3s" }} />
        {hasDoc ? statusLabel : "No draft"}
      </div>
    </div>
  )
}

// ── Prototype section (loads existing + launcher for new) ─────────────────
function PrototypeSection({
  prdId,
  figmaFileKey,
}: {
  prdId: number
  figmaFileKey?: string | null
}) {
  const [existing, setExisting] = useState<PrototypeRecord | null>(null)
  const [loading, setLoading] = useState(true)
  const [polling, setPolling] = useState(false)

  // On mount, check if a prototype already exists for this PRD
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setExisting(null)
    designAgentApi.getByPrd(prdId).then((proto) => {
      if (cancelled) return
      if (proto && proto.status === "ready") {
        setExisting(proto)
        setLoading(false)
      } else if (proto && proto.status === "generating") {
        setPolling(true)
        setLoading(false)
        // Poll until ready
        runDesignAgentGeneration({ prototypeId: proto.id }).then((result) => {
          if (cancelled) return
          setPolling(false)
          if (result.ok) setExisting(result.prototype)
        })
      } else {
        setLoading(false)
      }
    })
    return () => { cancelled = true }
  }, [prdId])

  if (loading) return null

  return (
    <div style={{ marginTop: 24 }}>
      {/* Show generating spinner while polling */}
      {polling && !existing && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "12px 14px",
            borderRadius: 10,
            border: "1px solid var(--accent-alpha-14)",
            background: "var(--accent-muted)",
          }}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="none"
            aria-hidden
            style={{ flexShrink: 0, animation: "da-spin 0.9s linear infinite" }}
          >
            <style>{`@keyframes da-spin { to { transform: rotate(360deg); } }`}</style>
            <circle cx="8" cy="8" r="6" stroke="var(--accent-alpha-28)" strokeWidth="2" />
            <path d="M8 2a6 6 0 0 1 6 6" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent-ink)" }}>
              Generating prototype…
            </div>
            <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 3 }}>
              This usually takes 1–2 minutes.
            </div>
          </div>
        </div>
      )}

      {/* Always show the launcher for (re)generation */}
      <DesignAgentLauncher prdId={prdId} figmaFileKey={figmaFileKey} />
    </div>
  )
}

// ── Share menu item ────────────────────────────────────────────────────────
function ShareMenuItem({
  icon, iconStyle, title, desc, onClick,
}: {
  icon: ReactNode
  iconStyle?: CSSProperties
  title: string
  desc: string
  onClick: () => void
}) {
  return (
    <div className="share-menu-item" onClick={onClick}>
      <div className="share-menu-item-icon" style={iconStyle}>{icon}</div>
      <div>
        <div style={{ fontWeight: 600 }}>{title}</div>
        <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>{desc}</div>
      </div>
    </div>
  )
}
