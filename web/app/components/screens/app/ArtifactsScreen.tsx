"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { useCompany } from "../../../context/CompanyContext"
import {
  artifactsApi,
  prdApi,
  evidenceApi,
  type ArtifactItem,
} from "../../../lib/api"
import { markdownToPrdState } from "../../../lib/prd-adapter"
import { markdownToEvidenceState } from "../../../lib/evidence-adapter"
import { prototypePath } from "../../../lib/routes"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

// ── Artifacts ──
//
// The Artifacts surface is a dedicated left-nav section (`/artifacts`). It was
// previously a tab inside the History/Chats screen; it now stands on its own so
// History holds only chats and Artifacts is the browsable library of durable
// outputs (PRDs, prototypes, evidence).

type ArtifactFilter = "all" | "prd" | "prototype" | "evidence"

const ARTIFACT_FILTERS: { id: ArtifactFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "prd", label: "PRDs" },
  { id: "prototype", label: "Prototypes" },
  { id: "evidence", label: "Evidence" },
]

const ARTIFACT_BADGE: Record<ArtifactItem["type"], { label: string; bg: string; color: string }> = {
  prd:       { label: "PRD",       bg: "#DBF1E7", color: "#0E6E49" },
  prototype: { label: "PROTOTYPE", bg: "#DBEAFE", color: "#1E40AF" },
  evidence:  { label: "EVIDENCE",  bg: "#FEF0E6", color: "#B45309" },
}

/** Compact relative time, e.g. "just now", "3h ago", "2d ago", "May 3". */
function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ""
  const diffMs = Date.now() - then
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

/** Human-facing lifecycle label for a prototype artifact, derived from
 *  status + is_complete (never the raw status string):
 *    generating          → "Building"
 *    ready & complete     → "Completed"
 *    ready & not complete → "Draft"
 */
function prototypeStatusLabel(
  a: Extract<ArtifactItem, { type: "prototype" }>,
): string {
  if (a.status === "generating") return "Building"
  return a.is_complete ? "Completed" : "Draft"
}

/** The meta/source line for a row, per the locked design. */
function artifactSourceLine(a: ArtifactItem): string {
  const rel = a.created_at ? relativeTime(a.created_at) : ""
  if (a.type === "prototype") {
    const parts = [`from PRD ${a.source.prd_title}`]
    parts.push(prototypeStatusLabel(a))
    if (rel) parts.push(rel)
    return parts.join(" · ")
  }
  // prd | evidence
  const week = a.source.week_label || "brief"
  const parts = [`from Brief ${week}`]
  if (a.status) parts.push(a.status)
  if (rel) parts.push(rel)
  return parts.join(" · ")
}

function ArtifactTypeIcon({ type }: { type: ArtifactItem["type"] }) {
  const cfg = ARTIFACT_BADGE[type]
  const wrap: React.CSSProperties = {
    width: 38, height: 38, borderRadius: "50%", display: "flex",
    alignItems: "center", justifyContent: "center", background: cfg.bg, flexShrink: 0,
  }
  if (type === "prototype") {
    return (
      <div style={wrap}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={cfg.color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
        </svg>
      </div>
    )
  }
  if (type === "evidence") {
    return (
      <div style={wrap}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={cfg.color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
      </div>
    )
  }
  // prd
  return (
    <div style={wrap}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={cfg.color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" />
        <line x1="8" y1="13" x2="16" y2="13" /><line x1="8" y1="17" x2="13" y2="17" />
      </svg>
    </div>
  )
}

/** Inline SVG used as the prototype thumbnail fallback when no preview image is
 *  available (ready row with null preview, e.g. screenshotting unprovisioned).
 *  Matches the `‹›` glyph the round ArtifactTypeIcon shows. */
function PrototypeGlyph() {
  const cfg = ARTIFACT_BADGE.prototype
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={cfg.color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
    </svg>
  )
}

/** Left-aligned thumbnail for a prototype artifact card. Three states:
 *   - generating → shimmer placeholder (chats-pulse), no image
 *   - ready + preview_image_url → the real screenshot
 *   - ready + null preview      → the `‹›` glyph fallback
 *  Sized to match the card row height; uses the same surface tokens as the
 *  round ArtifactTypeIcon so it stays native to the artifacts surface. */
function ArtifactPrototypeThumb({
  proto,
}: {
  proto: Extract<ArtifactItem, { type: "prototype" }>
}) {
  // A present-but-broken preview (e.g. a 404'd screenshot URL) must degrade to
  // the same glyph as the null-preview case — never a browser broken-image icon.
  const [imgFailed, setImgFailed] = useState(false)
  const box: React.CSSProperties = {
    width: 64, height: 48, borderRadius: 8, flexShrink: 0, overflow: "hidden",
    display: "flex", alignItems: "center", justifyContent: "center",
    background: ARTIFACT_BADGE.prototype.bg,
    border: "1px solid var(--line, #E8E6E0)",
  }
  if (proto.status === "generating") {
    return (
      <div data-proto-thumb="building" style={box}>
        <div
          data-proto-shimmer
          style={{
            width: "100%", height: "100%",
            background: "var(--surface-2, #F0EDE7)",
            animation: "chats-pulse 1.4s ease-in-out infinite",
          }}
        />
        <style>{`@keyframes chats-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }`}</style>
      </div>
    )
  }
  if (proto.preview_image_url && !imgFailed) {
    return (
      <div data-proto-thumb="image" style={box}>
        <img
          src={proto.preview_image_url}
          alt=""
          aria-hidden
          onError={() => setImgFailed(true)}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      </div>
    )
  }
  // ready + null preview, OR a preview that failed to load → SVG fallback
  return (
    <div data-proto-thumb="fallback" style={box}>
      <PrototypeGlyph />
    </div>
  )
}

/** Presentational artifacts list. Pure (no hooks/fetching) so it can be unit
 *  tested with renderToStaticMarkup + a jsdom click test, mirroring the
 *  `SlackChannelPickerView` / `LabCodeChatView` pattern in this repo. */
export function ArtifactsView({
  items,
  filter,
  loading,
  activeKey = null,
  onFilterChange,
  onOpen,
}: {
  items: ArtifactItem[]
  filter: ArtifactFilter
  loading: boolean
  /** `${type}-${id}` of the artifact whose panel is currently open — that row
   *  renders in its selected (green) state. Null = nothing selected. */
  activeKey?: string | null
  onFilterChange: (f: ArtifactFilter) => void
  onOpen: (a: ArtifactItem) => void
}) {
  const filtered = filter === "all" ? items : items.filter((a) => a.type === filter)

  return (
    <div>
      {/* Filter chips */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        {ARTIFACT_FILTERS.map((f) => {
          const active = f.id === filter
          return (
            <button
              key={f.id}
              type="button"
              data-filter={f.id}
              onClick={() => onFilterChange(f.id)}
              style={{
                fontSize: 12.5, fontWeight: 600, padding: "5px 13px", borderRadius: 16,
                cursor: "pointer", whiteSpace: "nowrap",
                border: `1px solid ${active ? "var(--accent, #179463)" : "var(--line, #E8E6E0)"}`,
                background: active ? "var(--accent, #179463)" : "var(--surface, #fff)",
                color: active ? "#fff" : "var(--ink-2, #5A5853)",
              }}
            >
              {f.label}
            </button>
          )
        })}
      </div>

      {/* Loading skeleton — matches the chats skeleton style */}
      {loading && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "8px 0" }}>
          {[1, 2, 3, 4].map((i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 14, padding: "14px 10px", borderRadius: 10 }}>
              <div style={{ width: 38, height: 38, borderRadius: "50%", background: "var(--surface-2, #F0EDE7)", animation: "chats-pulse 1.4s ease-in-out infinite", animationDelay: `${i * 0.1}s` }} />
              <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
                <div style={{ height: 13, borderRadius: 6, background: "var(--surface-2, #F0EDE7)", width: `${50 + i * 8}%`, animation: "chats-pulse 1.4s ease-in-out infinite", animationDelay: `${i * 0.1}s` }} />
                <div style={{ height: 10, borderRadius: 4, background: "var(--surface-2, #F0EDE7)", width: `${70 + i * 5}%`, animation: "chats-pulse 1.4s ease-in-out infinite", animationDelay: `${i * 0.15}s` }} />
              </div>
            </div>
          ))}
          <style>{`@keyframes chats-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }`}</style>
        </div>
      )}

      {/* Empty state */}
      {!loading && filtered.length === 0 && (
        <EmptyPane
          title="No artifacts yet"
          hint="Upload a PRD, or generate a PRD, prototype, or evidence from a brief finding."
          placeholders={2}
        />
      )}

      {/* List */}
      {!loading && filtered.map((a) => {
        // A generating prototype is a placeholder, not yet openable: no nav, no
        // hover affordance, default cursor. Every other row stays clickable.
        const isBuilding = a.type === "prototype" && a.status === "generating"
        const clickable = !isBuilding
        // The row whose panel is open renders selected: green tint + ring so
        // it's obvious which item the side panel belongs to.
        const isActive = activeKey === `${a.type}-${a.id}`
        const restBg = isActive ? "var(--accent-alpha-08, rgba(23,148,99,0.08))" : "transparent"
        return (
        <div
          key={`${a.type}-${a.id}`}
          data-artifact-type={a.type}
          data-clickable={clickable ? "true" : "false"}
          data-active={isActive ? "true" : undefined}
          aria-current={isActive ? "true" : undefined}
          onClick={clickable ? () => onOpen(a) : undefined}
          role={clickable ? "button" : undefined}
          aria-disabled={clickable ? undefined : true}
          tabIndex={clickable ? 0 : undefined}
          onKeyDown={clickable ? (e) => { if (e.key === "Enter") onOpen(a) } : undefined}
          style={{
            display: "flex", alignItems: "center", gap: 14,
            padding: "14px 10px", borderRadius: 10,
            cursor: clickable ? "pointer" : "default",
            transition: "background 0.12s, box-shadow 0.12s",
            background: restBg,
            boxShadow: isActive ? "inset 0 0 0 1px var(--accent-alpha-28, rgba(23,148,99,0.28))" : "none",
          }}
          onMouseEnter={clickable ? (e) => { (e.currentTarget as HTMLDivElement).style.background = isActive ? "var(--accent-alpha-10, rgba(23,148,99,0.10))" : "var(--surface-2, #F4F1EA)" } : undefined}
          onMouseLeave={clickable ? (e) => { (e.currentTarget as HTMLDivElement).style.background = restBg } : undefined}
        >
          {a.type === "prototype"
            ? <ArtifactPrototypeThumb proto={a} />
            : <ArtifactTypeIcon type={a.type} />}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontSize: 14, fontWeight: 600, color: "var(--ink, #1A1A17)",
              marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
              {a.title}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{
                fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                letterSpacing: "0.04em", padding: "2px 8px", borderRadius: 4,
                background: ARTIFACT_BADGE[a.type].bg, color: ARTIFACT_BADGE[a.type].color,
              }}>
                {ARTIFACT_BADGE[a.type].label}
              </span>
              <span style={{ fontSize: 11.5, color: "var(--ink-3, #8C8A84)" }}>
                {artifactSourceLine(a)}
              </span>
            </div>
          </div>
        </div>
        )
      })}
    </div>
  )
}

// ── Screen ──

export function ArtifactsScreen() {
  const { openContentPanel, openPrdTab, showToast, contentPanelTab } = useNavigation()
  const { setContent } = useContent()
  const { activeCompany } = useCompany()
  const router = useRouter()

  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [artifactsLoading, setArtifactsLoading] = useState(false)
  const [artifactFilter, setArtifactFilter] = useState<ArtifactFilter>("all")
  // `${type}-${id}` of the row whose panel is open — that row renders selected.
  const [activeArtifactKey, setActiveArtifactKey] = useState<string | null>(null)

  // Closing the side panel deselects the row (the selection exists to tie the
  // open panel to its list item, so it has no meaning once the panel is gone).
  useEffect(() => {
    if (contentPanelTab == null) setActiveArtifactKey(null)
  }, [contentPanelTab])

  // Upload-a-PRD state (the Import flow).
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const refreshArtifacts = useCallback(() => {
    if (!activeCompany) return
    setArtifactsLoading(true)
    artifactsApi.list(activeCompany)
      .then(setArtifacts)
      .catch(() => setArtifacts([]))
      .finally(() => setArtifactsLoading(false))
  }, [activeCompany])

  // Refetch artifacts on mount and whenever the company changes (the required
  // refetch-on-open baseline — no real-time wiring).
  useEffect(() => {
    refreshArtifacts()
  }, [refreshArtifacts])

  // Row click → OPEN the existing viewer, reusing the brief's exact mechanisms:
  //  - prd      → load by id, setContent({prd, prdMeta}) + openContentPanel("prd")
  //  - evidence → load by id, setContent({evidence}) + openContentPanel("evidence")
  //  - prototype→ router.push(/prototype?prd=<prd_id>) (the in-tab canvas surface)
  //
  // The panel opens IMMEDIATELY in its loading state (prdGenerating /
  // evidenceGenerating drive the rail's spinner) and the record fetch fills it
  // in — the click never sits silent while the network round-trip runs.
  // prdGenerating also suppresses PrdPanelContent's own "load latest PRD"
  // fetch, which would otherwise race this one with the wrong record.
  const openArtifact = useCallback(async (a: ArtifactItem) => {
    try {
      if (a.type === "prd" || a.type === "evidence") {
        setActiveArtifactKey(`${a.type}-${a.id}`)
      }
      if (a.type === "prd") {
        setContent({
          prd: null,
          prdGenerating: true,
          prdMeta: { briefId: a.open.brief_id, insightIndex: a.open.insight_index ?? 0 },
        })
        openContentPanel("prd")
        const rec = await prdApi.get(a.open.prd_id)
        setContent({
          prd: { ...markdownToPrdState(rec.payload_md), prd_id: rec.id, figma_file_key: undefined, source: rec.source },
          prdGenerating: false,
        })
        return
      }
      if (a.type === "evidence") {
        setContent({ evidence: null, evidenceGenerating: true })
        openContentPanel("evidence")
        const rec = await evidenceApi.get(a.open.evidence_id)
        // Set evidence content directly (no detail.meta), so the EvidenceTab
        // renders the loaded doc without re-generating.
        setContent({ evidence: markdownToEvidenceState(rec.payload_md), evidenceGenerating: false })
        return
      }
      // prototype — open the in-tab canvas for its parent PRD.
      router.push(prototypePath(a.open.prd_id))
    } catch {
      // Failed load: drop the loading flags (the rail shows its empty state
      // rather than spinning forever) and say what happened.
      setContent({ prdGenerating: false, evidenceGenerating: false })
      showToast("Couldn't open artifact", "The item failed to load. Try again.")
    }
  }, [setContent, openContentPanel, router, showToast])

  // Import a PRD from an uploaded file. The backend parses + re-lays-it-out into
  // our format. The endpoint parses the file and kicks off generation, returning
  // a 'generating' prd_id fast — so we open the chat window IMMEDIATELY and let
  // the PRD panel poll to ready in-tab (kind:"resume"), the same surface + feel
  // as the weekly brief's "generate PRD" flow. No blocking wait behind the
  // button, so a slow generation never looks like a hung upload.
  const handleImport = useCallback(async (file: File) => {
    if (!activeCompany || importing) return
    setImporting(true)
    setImportError(null)
    try {
      const { prd_id, title } = await prdApi.importDoc(file, activeCompany)
      openPrdTab({
        title: `PRD · ${title}`,
        source: { kind: "resume", prdId: prd_id, meta: null },
      })
      refreshArtifacts()
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Import failed. Please try again.")
    } finally {
      setImporting(false)
    }
  }, [activeCompany, importing, openPrdTab, refreshArtifacts])

  return (
    <AppLayout>
      <div style={{ maxWidth: 780, margin: "0 auto", padding: "0 4px" }}>
        {/* Upload a PRD → parsed + converted into our format server-side. */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 12, marginBottom: 16 }}>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.pptx,.docx,.md,.txt"
            data-testid="prd-import-input"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0]
              e.target.value = "" // allow re-selecting the same file
              if (f) void handleImport(f)
            }}
          />
          <button
            type="button"
            data-testid="prd-import-button"
            onClick={() => fileInputRef.current?.click()}
            disabled={importing || !activeCompany}
            style={{
              fontSize: 13, fontWeight: 600, padding: "7px 16px", borderRadius: 8,
              border: "none", whiteSpace: "nowrap",
              cursor: importing || !activeCompany ? "default" : "pointer",
              background: "var(--accent, #179463)", color: "#fff",
              opacity: importing || !activeCompany ? 0.6 : 1,
              display: "flex", alignItems: "center", gap: 6,
            }}
          >
            {importing ? "Importing…" : "+ Upload PRD"}
          </button>
        </div>

        {importError && (
          <div
            data-testid="prd-import-error"
            style={{
              marginBottom: 14, padding: "10px 12px", borderRadius: 8, fontSize: 12.5,
              background: "var(--danger-bg, #FEF2F2)", color: "var(--danger, #DC2626)",
              border: "1px solid var(--danger-line, #FCA5A5)",
            }}
          >
            {importError}
          </div>
        )}

        <ArtifactsView
          items={artifacts}
          filter={artifactFilter}
          loading={artifactsLoading}
          activeKey={activeArtifactKey}
          onFilterChange={setArtifactFilter}
          onOpen={openArtifact}
        />
      </div>
    </AppLayout>
  )
}
