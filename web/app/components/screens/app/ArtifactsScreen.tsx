"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { useCompany } from "../../../context/CompanyContext"
import {
  artifactsApi,
  customArtifactsApi,
  prdApi,
  evidenceApi,
  type ArtifactItem,
} from "../../../lib/api"
import { markdownToEvidenceState } from "../../../lib/evidence-adapter"
import { evidenceOpenScopePatch } from "../../../lib/panelPrdScope"
import { loadTicketSet } from "../../../lib/runTicketSetGeneration"
import { prototypePath } from "../../../lib/routes"
import { documentPath } from "../../../(app)/artifacts/doc/DocumentRoute"
import { reportKindLabel } from "../../../lib/reportKind"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

// ── Artifacts ──
//
// The Artifacts surface is a dedicated left-nav section (`/artifacts`). It was
// previously a tab inside the History/Chats screen; it now stands on its own so
// History holds only chats and Artifacts is the browsable library of durable
// outputs (PRDs, prototypes, evidence).

type ArtifactFilter =
  | "all" | "prd" | "prototype" | "evidence" | "report" | "ticket_set"
  | "custom_artifact"

// "Tickets", not "Non-PRD tickets": a PRD's tickets are not in this library at
// all (they belong to the PRD row, which is), so the qualifier would send
// people hunting for a chip that does not exist.
const ARTIFACT_FILTERS: { id: ArtifactFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "report", label: "Reports" },
  { id: "prd", label: "PRDs" },
  { id: "prototype", label: "Prototypes" },
  { id: "evidence", label: "Evidence" },
  { id: "ticket_set", label: "Tickets" },
  // "Documents", not "Others". The label used to be "Others" on the reasoning
  // that these are the artifacts which are not one of the named kinds above,
  // so a user finds a leadership update by elimination. In practice nobody
  // eliminates -- they look for the word for the thing they made, and a filter
  // named after what it is NOT is a filter people report as missing.
  { id: "custom_artifact", label: "Documents" },
]

// The "+ New report" picker is GONE, not hidden. It was already dark behind
// `SHOW_NEW_REPORT_BUTTON = false` — reports are asked for in chat, where the
// answer and the document live together — and the fixed report FORMATS it
// picked between (Voice of Customer / Competitor Analysis / Public Feedback,
// server-listed by the deleted `GET /v1/reports/kinds`) no longer exist. There
// is nothing left for a picker to pick, so nothing user-facing changed here.
// Browsing and opening captured reports is untouched: the "Reports" filter, the
// REPORT badge, and `reportKindLabel` all still work on rows already in the
// library.

// The four hexes below predate the design tokens and are left as they are —
// changing them is a visual-consistency pass of its own, not this feature's.
// The new entry uses the tokens, which is what a new badge should do.
type ArtifactBadge = { label: string; bg: string; color: string }

/** The badge for a type the client does not know about.
 *
 *  THE LIBRARY MUST NEVER WHITE-SCREEN BECAUSE THE SERVER LEARNED A NEW TRICK.
 *  `ARTIFACT_BADGE[a.type].bg` was an unguarded dereference on a `Record` with
 *  a fixed key set, so the first artifact type the backend emitted that this
 *  bundle had not shipped support for was a TypeError during render — and with
 *  no ErrorBoundary near this screen, that takes the WHOLE library down:
 *  PRDs, prototypes, evidence, reports, all of it, for one unknown row.
 *
 *  The type union cannot catch it, because the two sides deploy separately: a
 *  backend that lists a new type is live minutes before the web bundle that
 *  renders it, and a user on a cached bundle can be behind for longer. So the
 *  renderer degrades — an unknown row shows as a plain document and stays
 *  un-openable — instead of failing. */
const UNKNOWN_BADGE: ArtifactBadge = {
  label: "DOC", bg: "var(--surface-2, #F0EDE7)", color: "var(--ink-2, #5A5853)",
}

function badgeFor(type: string): ArtifactBadge {
  return (ARTIFACT_BADGE as Record<string, ArtifactBadge | undefined>)[type] ?? UNKNOWN_BADGE
}

const ARTIFACT_BADGE: Record<ArtifactItem["type"], ArtifactBadge> = {
  prd:        { label: "PRD",       bg: "#DBF1E7", color: "#0E6E49" },
  prototype:  { label: "PROTOTYPE", bg: "#DBEAFE", color: "#1E40AF" },
  evidence:   { label: "EVIDENCE",  bg: "#FEF0E6", color: "#B45309" },
  report:     { label: "REPORT",    bg: "#EDE9FE", color: "#6D28D9" },
  ticket_set: { label: "TICKETS",   bg: "var(--info-soft)", color: "var(--info)" },
  // A generic badge for a non-generic thing: the document's OWN kind
  // ("leadership update") is free text, so it cannot be a badge — badges are a
  // closed set with fixed colours. The kind leads the source line underneath
  // instead, where an unknown string renders as itself without breaking the
  // row's colour vocabulary.
  custom_artifact: { label: "DOC", bg: "var(--surface-2, #F0EDE7)", color: "var(--ink-2, #5A5853)" },
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

/** The meta/source line for a row, per the locked design.
 *
 *  Every branch below is keyed on a known `type`; an unrecognised one falls
 *  through to the final `prd | evidence` branch, which reads `a.source` fields
 *  that may not exist. The guard at the top keeps that from throwing — see
 *  UNKNOWN_BADGE for why an unknown type is a normal thing to receive. */
function artifactSourceLine(a: ArtifactItem): string {
  const rel = a.created_at ? relativeTime(a.created_at) : ""
  if (!(a.type in ARTIFACT_BADGE)) return rel
  if (a.type === "prototype") {
    const parts = [`from PRD ${a.source.prd_title}`]
    parts.push(prototypeStatusLabel(a))
    if (rel) parts.push(rel)
    return parts.join(" · ")
  }
  if (a.type === "report") {
    // A report's provenance is its ATTACHMENT: the chat room and/or PRD it was
    // generated in. Each part appears only when that attachment exists AND its
    // title resolved — a deleted chat/PRD leaves the id but no name, and an
    // unattached report simply reads as its kind. Never a fabricated label.
    const parts = [`${reportKindLabel(a.skill)} report`]
    if (a.source.conversation_title) parts.push(`from ${a.source.conversation_title}`)
    if (a.source.prd_title) parts.push(`on PRD ${a.source.prd_title}`)
    // A live share link is worth seeing without opening the report — this
    // document is reachable by anyone holding the URL.
    if (a.share_mode !== "private") parts.push("Shared")
    if (rel) parts.push(rel)
    return parts.join(" · ")
  }
  if (a.type === "ticket_set") {
    // The COUNT leads, because it is the affordance: "how much work is in
    // here" is the only thing that distinguishes one set from another at a
    // glance. It is a sub-line number rather than a chip of its own — the row
    // already carries one badge and a second would compete with it.
    //
    // A set still being written says so instead of claiming a count it
    // doesn't have yet (the row is also not clickable — see `isBuilding`),
    // the same treatment a building prototype gets.
    const parts = [
      a.status === "generating"
        ? "Writing tickets"
        : `${a.ticket_count} ticket${a.ticket_count === 1 ? "" : "s"}`,
    ]
    // Same rule as the report row above: a deleted chat leaves the id but no
    // title, and the row omits the clause rather than inventing a label.
    if (a.source.conversation_title) parts.push(`from ${a.source.conversation_title}`)
    if (rel) parts.push(rel)
    return parts.join(" · ")
  }
  if (a.type === "custom_artifact") {
    // The KIND leads, because it is the only thing distinguishing one document
    // from another at a glance — "leadership update" vs "postmortem" is the
    // real type here, while the badge just says DOC. Title-cased for display
    // only; the stored value stays the user's own words.
    const parts: string[] = []
    if (a.kind.trim()) parts.push(a.kind.trim())
    if (a.status === "generating") parts.push("Writing")
    // A FAILED document said nothing here, so it sat in the library looking
    // like any other row and opened blank — the state that made a failed
    // generation invisible in the product. The row says so; the document
    // itself says WHY (the panel and the page both read `error_code`).
    if (a.status === "failed") parts.push("Couldn't be written")
    // Same rule as the report and ticket-set rows: a deleted chat leaves the
    // id but no title, and the row omits the clause rather than inventing one.
    if (a.source.conversation_title) parts.push(`from ${a.source.conversation_title}`)
    // "Edited", not a bare timestamp: this row's time is the LAST EDIT, and an
    // unlabelled date next to a document reads as when it was created.
    if (rel) parts.push(`Edited ${rel}`)
    return parts.join(" · ")
  }
  // prd | evidence
  const week = a.source.week_label || "brief"
  const parts = [`from Brief ${week}`]
  if (a.status) parts.push(a.status)
  if (rel) parts.push(rel)
  return parts.join(" · ")
}

/** The bold title line for a row. Only ticket sets can legitimately arrive
 *  without one (`ticket_sets.title` is empty until the naming leg runs, and on
 *  rows that predate it) — an empty string would render a blank line where the
 *  name goes, so they fall back to the SAME words the panel's header uses, not
 *  a per-surface invention. */
function artifactTitle(a: ArtifactItem): string {
  if (a.type === "ticket_set") return a.title.trim() || "Tickets from this conversation"
  // A document is named by being typed in, so it is legitimately untitled for
  // as long as the user leaves it that way — the same state a new Google Doc
  // sits in. The row says so rather than rendering a blank line.
  if (a.type === "custom_artifact") return a.title.trim() || "Untitled document"
  return a.title
}

function ArtifactTypeIcon({ type }: { type: ArtifactItem["type"] }) {
  const cfg = badgeFor(type)
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
  if (type === "report") {
    // A bar-chart glyph: these documents lead with charts and sized themes,
    // which is what distinguishes them from the text-document PRD icon.
    return (
      <div style={wrap}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={cfg.color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <line x1="4" y1="20" x2="20" y2="20" />
          <rect x="6" y="11" width="3.4" height="6" rx="1" />
          <rect x="11.4" y="7" width="3.4" height="10" rx="1" />
          <rect x="16.8" y="13" width="3.4" height="4" rx="1" />
        </svg>
      </div>
    )
  }
  if (type === "ticket_set") {
    // A ticket stub: the perforated stub line is what reads as "tickets" at
    // 16px, where a document glyph would just read as another PRD.
    return (
      <div style={wrap}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={cfg.color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <rect x="3" y="6" width="18" height="12" rx="2" /><path d="M9 6v12" />
        </svg>
      </div>
    )
  }
  if (type === "custom_artifact") {
    // A page with lines: the plainest "written document" glyph there is, which
    // is right for the section whose whole meaning is "not one of the named
    // kinds". Deliberately unlike the PRD page-glyph below (no folded corner)
    // so the two do not read as the same thing at 16px.
    return (
      <div style={wrap}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={cfg.color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <rect x="4" y="3" width="16" height="18" rx="2" />
          <line x1="8" y1="8" x2="16" y2="8" />
          <line x1="8" y1="12" x2="16" y2="12" />
          <line x1="8" y1="16" x2="13" y2="16" />
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
          hint="Upload a PRD, generate a PRD, prototype, or evidence from a brief finding, or ask for a report in chat."
          placeholders={2}
        />
      )}

      {/* List */}
      {!loading && filtered.map((a) => {
        // A generating prototype — or a ticket set still being written — is a
        // placeholder, not yet openable: no nav, no hover affordance, default
        // cursor. Every other row stays clickable.
        const isBuilding =
          (a.type === "prototype" || a.type === "ticket_set" ||
            a.type === "custom_artifact") && a.status === "generating"
        // An unknown type has no open handler in this bundle, so it must not
        // offer a click that would silently do nothing (or throw). This stays
        // even now that `custom_artifact` IS known — the guard is about the
        // NEXT type, not this one.
        const known = a.type in ARTIFACT_BADGE
        const clickable = !isBuilding && known
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
              {artifactTitle(a)}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{
                fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                letterSpacing: "0.04em", padding: "2px 8px", borderRadius: 4,
                background: badgeFor(a.type).bg, color: badgeFor(a.type).color,
              }}>
                {badgeFor(a.type).label}
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

/** `?focus=<type>-<id>` — the artifact a link asked to open, or undefined.
 *
 *  A PROP, not a `useSearchParams()` call inside this component, and that is
 *  deliberate. Reading the URL here made every existing suite that renders
 *  this screen fail with "No useSearchParams export is defined on the
 *  next/navigation mock" — fifteen tests across four files, none of them about
 *  this feature. The route already owns the URL (and already carries the
 *  Suspense boundary static export requires for that hook), so it reads the
 *  param and hands it down; the screen stays a component you can render with
 *  props alone. */
export type ArtifactsScreenProps = { focus?: string | null }

export function ArtifactsScreen({ focus }: ArtifactsScreenProps = {}) {
  const {
    openContentPanel, openPrdTab, openReportTab, openTicketSetTab, openDocumentTab, showToast, contentPanelTab,
  } = useNavigation()
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
  //  - prd      → openPrdTab (kind:"load") — a chat tab + the PRD panel over
  //               it, exactly like the brief's "View PRD" (never a bare panel
  //               floating over the artifacts list)
  //  - report   → the chat thread it was generated in + the panel's Reports tab
  //               on that document (openReportTab). Every artifact opens over
  //               its thread; only a report with no surviving chat falls back to
  //               the standalone drawer.
  //  - evidence → load by id, setContent({evidence}) + openContentPanel("evidence")
  //  - prototype→ router.push(/prototype?prd=<prd_id>) (the in-tab canvas surface)
  //  - ticket_set → the chat it was generated in + the panel's Tickets tab on
  //               the set (openTicketSetTab); a set whose chat is gone falls
  //               back to the same panel with no thread under it
  //
  // For evidence, the panel opens IMMEDIATELY in its loading state
  // (evidenceGenerating drives the rail's spinner) and the record fetch fills
  // it in — the click never sits silent while the network round-trip runs.
  const openArtifact = useCallback(async (a: ArtifactItem) => {
    try {
      // Opening anything that ISN'T a report retires the standalone-report
      // pointer. It is the panel's reason to keep showing a Reports tab, so left
      // set it followed the reader onto the next artifact — a Reports tab over an
      // evidence document, still pointing at the report they had finished with.
      if (a.type !== "report") {
        setContent({ reportFocusId: null, reportFocusStandalone: false })
      }
      // The same rule for the ticket set on screen, and for the same reason.
      // `content.ticketSet` is not a pointer the panel checks — it is what the
      // Tickets tab RENDERS, and it also decides whether that tab appears at
      // all (ContentPanel's hidden gate). Left set, opening a PRD next would
      // show the previous chat's set on that PRD's Tickets tab.
      if (a.type !== "ticket_set") {
        setContent({
          ticketSet: null, ticketSetGenerating: false, ticketSetStandalone: false,
        })
      }
      if (a.type === "prd") {
        // ChatScreen consumes the request: spawns the chat tab, loads the PRD
        // by id (with its own loading state), and slides the panel open.
        openPrdTab({
          title: `PRD · ${a.title}`,
          source: {
            kind: "load",
            prdId: a.open.prd_id,
            meta: { briefId: a.open.brief_id, insightIndex: a.open.insight_index ?? 0 },
          },
        })
        return
      }
      if (a.type === "evidence") {
        setActiveArtifactKey(`${a.type}-${a.id}`)
        // Retires whatever PRD was cached from a previous artifact — this
        // fetch cannot attribute the evidence document to it, so no
        // PRD-acting control (Share, header, prototype CTA) may stay armed
        // on it. See lib/panelPrdScope.ts.
        setContent({ evidence: null, evidenceGenerating: true, ...evidenceOpenScopePatch() })
        openContentPanel("evidence")
        const rec = await evidenceApi.get(a.open.evidence_id)
        // Set evidence content directly (no detail.meta), so the EvidenceTab
        // renders the loaded doc without re-generating.
        setContent({
          evidence: { ...markdownToEvidenceState(rec.payload_md), question: rec.question },
          evidenceId: rec.id,
          evidenceGenerating: false,
        })
        return
      }
      if (a.type === "report") {
        // A report's home is the chat it was generated in, so opening one opens
        // THAT THREAD with the panel's Reports tab on the document — the same
        // posture as a PRD row, and the reason the whole thread's other reports
        // are one click away once you're there.
        //
        // `conversation_title` is what the resumed tab is keyed by, and a null
        // title means the chat row is gone (`on delete set null` hasn't fired /
        // the conversation was deleted) — there is no thread left to open, so
        // those fall through to the standalone drawer below.
        if (a.source.conversation_id != null && a.source.conversation_title) {
          // The ordinary "reopen this chat" hand-off — the same payload
          // ChatsScreen and the command palette write. ChatScreen's checkResume
          // spawns the tab and hydrates its turns in the background.
          localStorage.setItem("sprntly_resume_conv", JSON.stringify({
            dbId: a.source.conversation_id,
            title: a.source.conversation_title,
            fallbackTurns: [],
            prdId: a.source.prd_id ?? null,
          }))
          openReportTab({
            conversationId: a.source.conversation_id,
            reportId: a.open.report_id,
          })
          return
        }
        // An UNATTACHED report (no chat, or the chat was deleted) has no thread
        // to open — so it reads in the SAME panel, on the same Reports tab, just
        // without a thread's list behind it. Same posture as evidence above: the
        // panel opens immediately and the tab fetches the document by id, so the
        // click is never silent while it comes over the wire.
        setActiveArtifactKey(`${a.type}-${a.id}`)
        // `reportFocusStandalone` is what tells the Reports tab to trust this
        // pointer despite there being no conversation to check it against. It used
        // to infer that from `conversationId == null` — but a brand-new chat tab
        // also has a null conversation id, so a stale pointer read as standalone
        // and opened the previous thread's document inside an empty chat.
        setContent({
          conversationId: null,
          reportFocusId: a.open.report_id,
          reportFocusStandalone: true,
        })
        openContentPanel("reports")
        return
      }
      if (a.type === "ticket_set") {
        // Structurally the report branch above, one artifact over: a set's home
        // is the chat that produced it, so opening one opens THAT THREAD with
        // the panel's Tickets tab on the set. Same null-title rule too — a
        // `conversation_id` whose title didn't resolve means the chat is gone,
        // and there is no thread left to open.
        if (a.source.conversation_id != null && a.source.conversation_title) {
          localStorage.setItem("sprntly_resume_conv", JSON.stringify({
            dbId: a.source.conversation_id,
            title: a.source.conversation_title,
            fallbackTurns: [],
            prdId: null,
          }))
          openTicketSetTab({
            conversationId: a.source.conversation_id,
            ticketSetId: a.open.ticket_set_id,
          })
          return
        }
        // A set with no chat behind it reads in the SAME panel, on the same
        // Tickets tab, just without a thread under it. `ticketSetStandalone` is
        // STATED rather than inferred from a null conversation id, for the
        // reason spelled out on `reportFocusStandalone`: a brand-new chat tab
        // has a null id too, and inferring from it is a bug this codebase has
        // already shipped once.
        setActiveArtifactKey(`${a.type}-${a.id}`)
        setContent({ ticketSetStandalone: true })
        openContentPanel("tickets")
        // The loader owns the slice (and the scope patch, and the 404 → a
        // classified kind); the panel only ever reads it.
        void loadTicketSet(a.open.ticket_set_id, setContent)
        return
      }
      if (a.type === "custom_artifact") {
        // A document born in a chat opens OVER that chat, with the panel's
        // Document tab on it — the same posture as the PRD, report and ticket
        // set rows beside it in this list. It used to open its own page on the
        // reasoning that writing wants the full measure of a page; the page is
        // still there and still does, but a row that behaved differently from
        // every other row in the same list was the surprise, not the feature.
        if (a.source.conversation_id != null && a.source.conversation_title) {
          localStorage.setItem("sprntly_resume_conv", JSON.stringify({
            dbId: a.source.conversation_id,
            title: a.source.conversation_title,
            fallbackTurns: [],
            prdId: null,
          }))
          openDocumentTab({
            conversationId: a.source.conversation_id,
            documentId: a.open.custom_artifact_id,
          })
          return
        }
        // No chat behind it — uploaded, or its thread was deleted. There is no
        // thread to open over, so the full page is where it reads.
        router.push(documentPath(a.open.custom_artifact_id))
        return
      }
      // prototype — open the in-tab canvas for its parent PRD.
      router.push(prototypePath(a.open.prd_id))
    } catch {
      // Failed load: drop the loading flag (the rail shows its empty state
      // rather than spinning forever) and say what happened.
      setContent({ evidenceGenerating: false })
      showToast("Couldn't open artifact", "The item failed to load. Try again.")
    }
  }, [setContent, openContentPanel, openPrdTab, openReportTab, openTicketSetTab, openDocumentTab, router, showToast])

  // ── `?focus=<type>-<id>` — open one artifact straight from a link ─────────
  //
  // What a Slack share links to. Reports, ticket sets and documents have no
  // per-artifact route of their own (only PRDs do, via `?prd=` — see
  // useArtifactUrlSync), so a link to one lands here and names the row. The
  // key is exactly the `${type}-${id}` shape `activeArtifactKey` already uses,
  // and the open runs through `openArtifact` — the SAME per-kind logic a click
  // on the row runs, so a shared link can never open a document differently
  // from the way the library does.
  //
  // Waits for the list, because the row is what carries the ids the open needs
  // (`a.open.report_id`, the conversation to resume). A key that matches
  // nothing — a deleted artifact, another tenant's id, a mangled link — simply
  // leaves the library on screen, which is the honest outcome: the reader can
  // see what they DO have rather than an error about what they don't.
  const consumedFocusRef = useRef<string | null>(null)
  useEffect(() => {
    if (!focus) {
      consumedFocusRef.current = null
      return
    }
    // One-shot per distinct value: `openArtifact` mutates panel state and
    // `useSearchParams()` can hand back a fresh object each render, so without
    // the latch this would re-open on every re-render. Re-arms when the param
    // goes away, so a later link to a different artifact still fires.
    if (consumedFocusRef.current === focus) return
    if (artifactsLoading || artifacts.length === 0) return
    consumedFocusRef.current = focus
    const row = artifacts.find((a) => `${a.type}-${a.id}` === focus)
    if (row) void openArtifact(row)
  }, [focus, artifacts, artifactsLoading, openArtifact])

  // A blank document, created and opened straight away.
  //
  // No naming dialog, deliberately: a new document is named by being typed
  // into, exactly as a new Google Doc is. Asking for a title before there is
  // any content puts a decision in front of the user at the moment they have
  // least information — and the generation path does not ask either, because
  // it titles the document from its own <h1>.
  const [creatingDoc, setCreatingDoc] = useState(false)
  const handleNewDocument = useCallback(async () => {
    if (!activeCompany || creatingDoc) return
    setCreatingDoc(true)
    try {
      const doc = await customArtifactsApi.create({})
      router.push(documentPath(doc.id))
    } catch {
      showToast("Couldn't create document", "Please try again.")
    } finally {
      setCreatingDoc(false)
    }
  }, [activeCompany, creatingDoc, router, showToast])

  // Import a PRD from an uploaded file. The backend parses + re-lays-it-out into
  // our format. The endpoint parses the file and kicks off generation, returning
  // a 'generating' prd_id fast — so we open the chat window IMMEDIATELY and let
  // the PRD panel poll to ready in-tab (kind:"resume"), the same surface + feel
  // as the Top Insights brief's "generate PRD" flow. No blocking wait behind the
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
            data-testid="new-document-button"
            onClick={() => void handleNewDocument()}
            disabled={creatingDoc || !activeCompany}
            style={{
              fontSize: 13, fontWeight: 600, padding: "7px 16px", borderRadius: 8,
              whiteSpace: "nowrap",
              cursor: creatingDoc || !activeCompany ? "default" : "pointer",
              border: "1px solid var(--line, #E8E6E0)",
              background: "var(--surface, #fff)", color: "var(--ink, #1A1A17)",
              opacity: creatingDoc || !activeCompany ? 0.6 : 1,
            }}
          >
            {creatingDoc ? "Creating…" : "+ New document"}
          </button>
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
