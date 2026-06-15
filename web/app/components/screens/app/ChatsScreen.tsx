"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { useCompany } from "../../../context/CompanyContext"
import {
  conversationsApi,
  artifactsApi,
  briefApi,
  prdApi,
  evidenceApi,
  type ConversationRecord,
  type ArtifactItem,
} from "../../../lib/api"
import type { ConversationRow } from "../../../types/content"
import { markdownToPrdState } from "../../../lib/prd-adapter"
import { markdownToEvidenceState } from "../../../lib/evidence-adapter"
import { prototypePath } from "../../../lib/routes"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

// ── Agent type config ──

type AgentType = "pm" | "oncall" | "ds" | "design" | "ask"

const AGENT_CONFIG: Record<AgentType, { label: string; bg: string; color: string; iconBg: string; iconColor: string }> = {
  pm:     { label: "PM AGENT",      bg: "#DBF1E7", color: "#0E6E49", iconBg: "#DBF1E7", iconColor: "#179463" },
  oncall: { label: "ON-CALL AGENT", bg: "#FEE2E2", color: "#DC2626", iconBg: "#FEF2F2", iconColor: "#DC2626" },
  ds:     { label: "DS AGENT",      bg: "#DBEAFE", color: "#1E40AF", iconBg: "#EFF6FF", iconColor: "#2563EB" },
  design: { label: "DESIGN AGENT",  bg: "#DBF1E7", color: "#0E6E49", iconBg: "#F0FDF4", iconColor: "#179463" },
  ask:    { label: "ASK",           bg: "#F3F4F6", color: "#6B7280", iconBg: "#F3F4F6", iconColor: "#6B7280" },
}

function detectAgent(title: string): AgentType {
  const l = title.toLowerCase()
  if (l.includes("on-call") || l.includes("oncall") || l.includes("sev-")) return "oncall"
  if (l.includes("prototype") || l.includes("design") || l.includes("wizard")) return "design"
  if (l.includes("cohort") || l.includes("breakdown") || l.includes("ds agent") || l.includes("analytics")) return "ds"
  if (l.includes("brief") || l.includes("prd") || l.includes("okr") || l.includes("expansion") || l.includes("scoping") || l.includes("handoff") || l.includes("onboarding")) return "pm"
  return "ask"
}

// ── Agent icons (SVG matching the design) ──

function AgentIcon({ agent }: { agent: AgentType }) {
  const cfg = AGENT_CONFIG[agent]
  const iconStyle: React.CSSProperties = {
    width: 38, height: 38, borderRadius: "50%", display: "flex",
    alignItems: "center", justifyContent: "center",
    background: cfg.iconBg, flexShrink: 0,
  }

  if (agent === "pm" || agent === "ask") {
    return (
      <div style={iconStyle}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={cfg.iconColor} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" />
        </svg>
      </div>
    )
  }
  if (agent === "oncall") {
    return (
      <div style={iconStyle}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={cfg.iconColor} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      </div>
    )
  }
  if (agent === "ds") {
    return (
      <div style={iconStyle}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={cfg.iconColor} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" />
          <line x1="6" y1="20" x2="6" y2="14" />
        </svg>
      </div>
    )
  }
  // design
  return (
    <div style={iconStyle}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={cfg.iconColor} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
      </svg>
    </div>
  )
}

// ── Helpers ──

function dateGroup(timeStr: string): "Pinned" | "Today" | "Yesterday" | "This week" | "Earlier" {
  const now = new Date()
  const date = new Date(timeStr)
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterdayStart = new Date(todayStart.getTime() - 86400000)
  const weekStart = new Date(todayStart.getTime() - todayStart.getDay() * 86400000)

  if (date >= todayStart) return "Today"
  if (date >= yesterdayStart) return "Yesterday"
  if (date >= weekStart) return "This week"
  return "Earlier"
}

function formatTime(timeStr: string): string {
  const date = new Date(timeStr)
  const now = new Date()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (date >= todayStart) {
    return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
  }
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
  return `${days[date.getDay()]} ${date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`
}

const GROUP_ORDER = ["Pinned", "Today", "Yesterday", "This week", "Earlier"] as const

// ── Weekly-brief pin ──
//
// The current weekly brief is surfaced as a synthetic, always-pinned entry at
// the very top of the chats list (above per-conversation pins). It is NOT a
// conversation row — it links to the brief surface (`goTo("brief")`). It stays
// at the top for the entire week: `/v1/brief/current` always returns this
// week's brief, and when a new brief lands the entry simply reflects the new
// one (same top spot). We additionally require the brief to belong to the
// current calendar week so a stale brief is never shown as "this week's".

/** The minimal current-brief shape needed to render the pinned entry. */
export type BriefEntry = {
  /** Brief id (for keys / debugging); not otherwise used by the row. */
  id: number
  /** Human week label, e.g. "Week of May 20". */
  weekLabel: string
  /** Brief headline shown as the row's description. */
  headline: string
  /** ISO timestamp the brief was generated. */
  generatedAt: string
}

/** True when `generatedAt` falls within the current (Sun-start) calendar week.
 *  Used to decide whether the current brief is genuinely "this week's brief"
 *  and should hold the pinned top slot for the entire week. */
export function isCurrentWeekBrief(generatedAt: string, now: Date = new Date()): boolean {
  const date = new Date(generatedAt)
  if (Number.isNaN(date.getTime())) return false
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const weekStart = new Date(todayStart.getTime() - todayStart.getDay() * 86400000)
  const weekEnd = new Date(weekStart.getTime() + 7 * 86400000)
  return date >= weekStart && date < weekEnd
}

// ── Artifacts tab ──

type TabId = "chats" | "artifacts"
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

/** The meta/source line for a row, per the locked design. */
function artifactSourceLine(a: ArtifactItem): string {
  const rel = a.created_at ? relativeTime(a.created_at) : ""
  if (a.type === "prototype") {
    const parts = [`from PRD ${a.source.prd_title}`]
    if (a.status) parts.push(a.status)
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

/** Presentational artifacts list. Pure (no hooks/fetching) so it can be unit
 *  tested with renderToStaticMarkup + a jsdom click test, mirroring the
 *  `SlackChannelPickerView` / `LabCodeChatView` pattern in this repo. */
export function ArtifactsView({
  items,
  filter,
  loading,
  onFilterChange,
  onOpen,
}: {
  items: ArtifactItem[]
  filter: ArtifactFilter
  loading: boolean
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
          hint="Generate a PRD, prototype, or evidence from a brief finding."
          placeholders={2}
        />
      )}

      {/* List */}
      {!loading && filtered.map((a) => {
        const rowWrapper = {
          "data-artifact-type": a.type,
          onClick: () => onOpen(a),
          role: "button" as const,
          tabIndex: 0,
          onKeyDown: (e: React.KeyboardEvent) => { if (e.key === "Enter") onOpen(a) },
          onMouseEnter: (e: React.MouseEvent) => { (e.currentTarget as HTMLDivElement).style.background = "var(--surface-2, #F4F1EA)" },
          onMouseLeave: (e: React.MouseEvent) => { (e.currentTarget as HTMLDivElement).style.background = "transparent" },
        }

        // Prototype rows WITH a real preview render as an image card (thumbnail
        // on top, title + sub-line below). Every other case — PRD, evidence, and
        // prototypes without a preview — keeps the icon+text row unchanged.
        if (a.type === "prototype" && a.preview_image_url) {
          return (
            <div
              key={`${a.type}-${a.id}`}
              {...rowWrapper}
              style={{
                display: "flex", flexDirection: "column", gap: 10,
                padding: "14px 10px", borderRadius: 10, cursor: "pointer",
                transition: "background 0.12s",
              }}
            >
              <div style={{
                width: "100%", height: 150, overflow: "hidden", borderRadius: 8,
                border: "1px solid var(--line, #E8E6E0)", background: "var(--surface-2, #F4F1EA)",
              }}>
                <img className="fc-preview-img" src={a.preview_image_url} alt={a.title} />
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{
                  fontSize: 14, fontWeight: 600, color: "var(--ink, #1A1A17)",
                  marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {a.title}
                </div>
                <div style={{ fontSize: 11.5, color: "var(--ink-3, #8C8A84)" }}>
                  {`Prototype · ${relativeTime(a.created_at)}`}
                </div>
              </div>
            </div>
          )
        }

        return (
          <div
            key={`${a.type}-${a.id}`}
            {...rowWrapper}
            style={{
              display: "flex", alignItems: "flex-start", gap: 14,
              padding: "14px 10px", borderRadius: 10, cursor: "pointer",
              transition: "background 0.12s",
            }}
          >
            <ArtifactTypeIcon type={a.type} />
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

// ── Chats list (presentational) ──

/** The synthetic, always-pinned brief row. Pure so it renders in both
 *  renderToStaticMarkup and jsdom tests. Identified by `data-brief-pin`. */
function BriefPinRow({ entry, onOpen }: { entry: BriefEntry; onOpen: () => void }) {
  const cfg = AGENT_CONFIG.pm
  return (
    <div
      data-brief-pin="true"
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter") onOpen() }}
      style={{
        display: "flex", alignItems: "flex-start", gap: 14,
        padding: "14px 10px", borderRadius: 10, cursor: "pointer",
        transition: "background 0.12s",
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "var(--surface-2, #F4F1EA)" }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "transparent" }}
    >
      <AgentIcon agent="pm" />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 14, fontWeight: 600, color: "var(--ink, #1A1A17)",
          marginBottom: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          This week's brief
        </div>
        <div style={{
          fontSize: 12.5, color: "var(--ink-2, #5A5853)", lineHeight: 1.45,
          marginBottom: 6, overflow: "hidden", textOverflow: "ellipsis",
          display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
        }}>
          {entry.headline}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{
            fontSize: 10, fontWeight: 700, textTransform: "uppercase",
            letterSpacing: "0.04em", padding: "2px 8px", borderRadius: 4,
            background: cfg.bg, color: cfg.color,
          }}>
            {cfg.label}
          </span>
          <span style={{ fontSize: 11.5, color: "var(--ink-3, #8C8A84)" }}>
            {entry.weekLabel}
          </span>
        </div>
      </div>
      {/* Pinned indicator (filled, always-on — this row can't be unpinned). */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4, flexShrink: 0, paddingTop: 2 }}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="var(--accent, #179463)" stroke="none" aria-hidden>
          <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z" />
        </svg>
      </div>
    </div>
  )
}

/** Presentational chats list — pure (no hooks/fetching), extracted from
 *  ChatsScreen so it can be unit-tested with renderToStaticMarkup + jsdom
 *  clicks, mirroring `ArtifactsView`. Owns grouping (Pinned first, then by
 *  date) and renders the always-pinned `briefEntry` at the very top of Pinned. */
export function ChatsListView({
  rows,
  briefEntry,
  onRowClick,
  onPin,
  onDelete,
  onOpenBrief,
}: {
  rows: ConversationRow[]
  /** The current weekly brief, pinned to the top; null when there's none. */
  briefEntry: BriefEntry | null
  onRowClick: (row: ConversationRow) => void
  onPin: (row: ConversationRow) => void
  onDelete: (row: ConversationRow) => void
  onOpenBrief: () => void
}) {
  const grouped = useMemo(() => {
    const map = new Map<string, ConversationRow[]>()
    for (const g of GROUP_ORDER) map.set(g, [])
    for (const row of rows) {
      if ((row as any)._pinned) {
        map.get("Pinned")!.push(row)
      } else {
        map.get(dateGroup(row.time))!.push(row)
      }
    }
    return map
  }, [rows])

  return (
    <>
      {GROUP_ORDER.map((group) => {
        const dataRows = grouped.get(group) ?? []
        // The brief pin lives at the head of the Pinned group. The group is
        // rendered whenever it has rows OR a brief pin to show.
        const showBriefPin = group === "Pinned" && !!briefEntry
        if (dataRows.length === 0 && !showBriefPin) return null
        return (
          <div key={group}>
            {/* Group header with line */}
            <div style={{
              display: "flex", alignItems: "center", gap: 12,
              padding: "18px 0 8px", margin: "0 0 2px",
            }}>
              <span style={{
                fontSize: 11, fontWeight: 600, textTransform: "uppercase",
                letterSpacing: "0.06em", color: "var(--ink-3, #8C8A84)",
                whiteSpace: "nowrap",
              }}>
                {group}
              </span>
              <div style={{ flex: 1, height: 1, background: "var(--line, #E8E6E0)" }} />
            </div>

            {/* Always-pinned weekly brief, at the very top of Pinned. */}
            {showBriefPin && briefEntry && (
              <BriefPinRow entry={briefEntry} onOpen={onOpenBrief} />
            )}

            {dataRows.map((row) => {
              const agent = detectAgent(row.title)
              const cfg = AGENT_CONFIG[agent]
              const isPinned = group === "Pinned"
              const extraMeta = getExtraMeta(row, agent)

              return (
                <div
                  key={row.id}
                  onClick={() => onRowClick(row)}
                  style={{
                    display: "flex", alignItems: "flex-start", gap: 14,
                    padding: "14px 10px", borderRadius: 10, cursor: "pointer",
                    transition: "background 0.12s",
                  }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "var(--surface-2, #F4F1EA)" }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "transparent" }}
                >
                  <AgentIcon agent={agent} />

                  <div style={{ flex: 1, minWidth: 0 }}>
                    {/* Title */}
                    <div style={{
                      fontSize: 14, fontWeight: 600, color: "var(--ink, #1A1A17)",
                      marginBottom: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}>
                      {row.title}
                    </div>

                    {/* Description */}
                    <div style={{
                      fontSize: 12.5, color: "var(--ink-2, #5A5853)", lineHeight: 1.45,
                      marginBottom: 6, overflow: "hidden", textOverflow: "ellipsis",
                      display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                    }}>
                      {row.savedTurn?.query || row.title}
                    </div>

                    {/* Agent pill + extra meta */}
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <span style={{
                        fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                        letterSpacing: "0.04em", padding: "2px 8px", borderRadius: 4,
                        background: cfg.bg, color: cfg.color,
                      }}>
                        {cfg.label}
                      </span>
                      {extraMeta.map((m, i) => (
                        <span key={i} style={{ fontSize: 11.5, color: "var(--ink-3, #8C8A84)" }}>
                          {i > 0 && <span style={{ margin: "0 2px" }}>·</span>}
                          {m}
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Time + actions */}
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4, flexShrink: 0, paddingTop: 2 }}>
                    <span style={{ fontSize: 11, color: "var(--ink-4, #B0AEA6)", whiteSpace: "nowrap" }}>
                      {formatTime(row.time)}
                    </span>
                    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                      {/* Pin toggle */}
                      <button
                        type="button"
                        title={isPinned ? "Unpin" : "Pin"}
                        onClick={(e) => { e.stopPropagation(); onPin(row) }}
                        style={{ background: "none", border: "none", cursor: "pointer", padding: 2, lineHeight: 1 }}
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill={isPinned ? "var(--accent, #179463)" : "none"} stroke={isPinned ? "none" : "var(--ink-4, #B0AEA6)"} strokeWidth="2">
                          <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z" />
                        </svg>
                      </button>
                      {/* Delete */}
                      {(row as any)._dbId && (
                        <button
                          type="button"
                          title="Delete"
                          onClick={(e) => { e.stopPropagation(); onDelete(row) }}
                          style={{ background: "none", border: "none", cursor: "pointer", padding: 2, lineHeight: 1, color: "var(--ink-4, #B0AEA6)", fontSize: 14 }}
                        >
                          ×
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )
      })}
    </>
  )
}

// ── Screen ──

export function ChatsScreen() {
  const { goTo, setPendingOndemandDraft, openContentPanel } = useNavigation()
  const { content, setContent } = useContent()
  const { activeCompany } = useCompany()
  const router = useRouter()
  const [search, setSearch] = useState("")
  const [dbChats, setDbChats] = useState<ConversationRecord[]>([])
  const [loaded, setLoaded] = useState(false)

  // ── Current weekly brief (drives the always-pinned top entry) ──
  const [briefEntry, setBriefEntry] = useState<BriefEntry | null>(null)

  // ── Tab + artifacts state ──
  const [tab, setTab] = useState<TabId>("chats")
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [artifactsLoading, setArtifactsLoading] = useState(false)
  const [artifactFilter, setArtifactFilter] = useState<ArtifactFilter>("all")

  // Refetch artifacts whenever the Artifacts tab is opened or the company
  // changes (the required refetch-on-open baseline — no real-time wiring).
  useEffect(() => {
    if (tab !== "artifacts" || !activeCompany) return
    let cancelled = false
    setArtifactsLoading(true)
    artifactsApi.list(activeCompany)
      .then((rows) => { if (!cancelled) setArtifacts(rows) })
      .catch(() => { if (!cancelled) setArtifacts([]) })
      .finally(() => { if (!cancelled) setArtifactsLoading(false) })
    return () => { cancelled = true }
  }, [tab, activeCompany])

  // Row click → OPEN the existing viewer, reusing the brief's exact mechanisms:
  //  - prd      → load by id, setContent({prd, prdMeta}) + openContentPanel("prd")
  //  - evidence → load by id, setContent({evidence}) + openContentPanel("evidence")
  //  - prototype→ router.push(/prototype?prd=<prd_id>) (the in-tab canvas surface)
  const openArtifact = useCallback(async (a: ArtifactItem) => {
    try {
      if (a.type === "prd") {
        const rec = await prdApi.get(a.open.prd_id)
        setContent({
          prd: { ...markdownToPrdState(rec.payload_md), prd_id: rec.id, figma_file_key: undefined },
          prdMeta: { briefId: a.open.brief_id, insightIndex: a.open.insight_index ?? 0 },
        })
        openContentPanel("prd")
        return
      }
      if (a.type === "evidence") {
        const rec = await evidenceApi.get(a.open.evidence_id)
        // Set evidence content directly (no detail.meta), so the EvidenceTab
        // renders the loaded doc without re-generating.
        setContent({ evidence: markdownToEvidenceState(rec.payload_md), evidenceGenerating: false })
        openContentPanel("evidence")
        return
      }
      // prototype — open the in-tab canvas for its parent PRD.
      router.push(prototypePath(a.open.prd_id))
    } catch {
      /* Best-effort open; a failed load leaves the list in place. */
    }
  }, [setContent, openContentPanel, router])

  // Load from Supabase on mount
  useEffect(() => {
    let cancelled = false
    conversationsApi.list().then((res) => {
      if (!cancelled) { setDbChats(res.conversations); setLoaded(true) }
    }).catch(() => { if (!cancelled) setLoaded(true) })
    return () => { cancelled = true }
  }, [])

  // Fetch the current weekly brief so we can pin it to the top of the list.
  // `/v1/brief/current` always returns this week's brief, so the pinned entry
  // automatically stays put for the whole week and swaps to the new brief when
  // one is generated. We only surface it when the brief belongs to the current
  // calendar week — a 404 (no brief yet) or a stale brief leaves it unpinned,
  // so we never render a broken/empty pinned row. The brief page owns its own
  // generating/empty states, so we don't duplicate them here.
  useEffect(() => {
    if (!activeCompany) return
    let cancelled = false
    briefApi.current(activeCompany)
      .then((brief) => {
        if (cancelled) return
        if (!isCurrentWeekBrief(brief.generated_at)) { setBriefEntry(null); return }
        setBriefEntry({
          id: brief.id,
          weekLabel: brief.week_label || "This week",
          headline: brief.summary_headline || "Your weekly brief is ready.",
          generatedAt: brief.generated_at,
        })
      })
      .catch(() => {
        // 404 = no brief yet this week; any other error → just omit the entry.
        if (!cancelled) setBriefEntry(null)
      })
    return () => { cancelled = true }
  }, [activeCompany])

  // Map DB records to ConversationRow shape
  const dbRows: ConversationRow[] = useMemo(() =>
    dbChats.map((c) => ({
      id: String(c.id),
      title: c.title,
      time: c.created_at,
      savedTurn: { id: String(c.id), query: c.query || c.preview },
      _pinned: c.pinned,
      _agentType: c.agent_type,
      _dbId: c.id,
    } as ConversationRow & { _pinned?: boolean; _agentType?: string; _dbId?: number })),
  [dbChats])

  // Merge DB chats + in-memory chats (dedup by title), fallback to mock
  const allChats = useMemo(() => {
    const inMemory = (content.conversations ?? []).map((c) => ({
      ...c,
      time: c.time.includes("T") ? c.time : new Date().toISOString(),
    }))
    // If DB has data, merge with in-memory (DB is source of truth for persisted ones)
    if (dbRows.length > 0) {
      const dbTitles = new Set(dbRows.map((r) => r.title))
      // Add any in-memory conversations not yet in DB
      const extra = inMemory.filter((c) => !dbTitles.has(c.title))
      return [...dbRows, ...extra]
    }
    return inMemory
  }, [dbRows, content.conversations])

  const handleDelete = useCallback((row: ConversationRow) => {
    const dbId = (row as any)._dbId
    if (dbId) {
      conversationsApi.remove(dbId).catch(() => {})
      setDbChats((prev) => prev.filter((c) => c.id !== dbId))
    }
  }, [])

  const handlePin = useCallback((row: ConversationRow) => {
    const dbId = (row as any)._dbId
    const current = (row as any)._pinned ?? false
    if (dbId) {
      conversationsApi.update(dbId, { pinned: !current }).catch(() => {})
      setDbChats((prev) => prev.map((c) => c.id === dbId ? { ...c, pinned: !current } : c))
    }
  }, [])

  const filtered = useMemo(() => {
    if (!search.trim()) return allChats
    const q = search.toLowerCase()
    return allChats.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        (c.savedTurn?.query ?? "").toLowerCase().includes(q),
    )
  }, [allChats, search])

  // Opening the pinned weekly-brief entry → the brief surface (`/brief`).
  const openBrief = useCallback(() => { goTo("brief") }, [goTo])

  const handleRowClick = async (row: ConversationRow) => {
    const dbId = (row as any)._dbId as number | undefined

    if (dbId) {
      try {
        const res = await conversationsApi.listTurns(dbId)
        if (res.turns && res.turns.length > 0) {
          localStorage.setItem("sprntly_resume_conv", JSON.stringify({
            dbId,
            title: row.title,
            turns: res.turns,
          }))
          goTo("chat")
          return
        }
      } catch { /* fallback below */ }
    }

    // Fallback: build a thread from the saved turn
    if (row.savedTurn?.query) {
      const fakeTurns = [{ role: "user", content: row.savedTurn.query }]
      if (row.savedTurn.reply) {
        const replyText = typeof row.savedTurn.reply === "string"
          ? row.savedTurn.reply
          : (row.savedTurn.reply as any)?.answer ?? ""
        if (replyText) fakeTurns.push({ role: "assistant", content: replyText })
      }
      localStorage.setItem("sprntly_resume_conv", JSON.stringify({
        dbId: dbId ?? 0,
        title: row.title,
        turns: fakeTurns,
      }))
      goTo("chat")
      return
    }

    goTo("chat")
  }

  return (
    <AppLayout>
      <div style={{ maxWidth: 780, margin: "0 auto", padding: "0 4px" }}>
        {/* Top bar */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
          {/* Chats | Artifacts tab switcher */}
          <div style={{
            display: "flex", gap: 2, padding: 2, borderRadius: 8,
            background: "var(--surface-2, #F0EDE7)",
          }}>
            {([["chats", "Chats"], ["artifacts", "Artifacts"]] as const).map(([id, label]) => {
              const active = tab === id
              return (
                <button
                  key={id}
                  type="button"
                  data-tab={id}
                  onClick={() => setTab(id)}
                  style={{
                    fontSize: 12.5, fontWeight: 600, padding: "5px 14px", borderRadius: 6,
                    border: "none", cursor: "pointer",
                    background: active ? "var(--surface, #fff)" : "transparent",
                    color: active ? "var(--ink, #1A1A17)" : "var(--ink-3, #8C8A84)",
                    boxShadow: active ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
                  }}
                >
                  {label}
                </button>
              )
            })}
          </div>

          {/* Search (Chats tab only) */}
          {tab === "chats" && (
          <>
          {/* Search */}
          <div style={{ flex: 1, position: "relative" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#8C8A84" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}>
              <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              type="text"
              placeholder="Search chats, briefs, threads..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{
                fontSize: 13, padding: "7px 12px 7px 32px", borderRadius: 8, width: "100%",
                border: "1px solid var(--line, #E8E6E0)", outline: "none",
                background: "var(--surface, #fff)", color: "var(--ink, #1A1A17)",
              }}
            />
            <span style={{
              position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
              fontSize: 10, color: "var(--ink-4, #B0AEA6)", border: "1px solid var(--line, #E8E6E0)",
              borderRadius: 4, padding: "1px 5px", fontFamily: "var(--font-mono, monospace)",
            }}>
              ⌘K
            </span>
          </div>
          </>
          )}

          {/* Spacer keeps New chat right-aligned when the search is hidden */}
          {tab === "artifacts" && <div style={{ flex: 1 }} />}

          {/* New chat */}
          <button
            type="button"
            onClick={() => goTo("chat")}
            style={{
              fontSize: 13, padding: "7px 16px", borderRadius: 8,
              background: "var(--accent, #179463)", color: "#fff", border: "none",
              fontWeight: 600, cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
              whiteSpace: "nowrap",
            }}
          >
            + New chat
          </button>
        </div>

        {/* Artifacts tab */}
        {tab === "artifacts" && (
          <ArtifactsView
            items={artifacts}
            filter={artifactFilter}
            loading={artifactsLoading}
            onFilterChange={setArtifactFilter}
            onOpen={openArtifact}
          />
        )}

        {/* Chats tab (default) */}
        {tab === "chats" && (
        <>
        {/* Loading state */}
        {!loaded && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "20px 0" }}>
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 14, padding: "14px 10px", borderRadius: 10 }}>
                <div style={{ width: 38, height: 38, borderRadius: "50%", background: "var(--surface-2, #F0EDE7)", animation: "chats-pulse 1.4s ease-in-out infinite", animationDelay: `${i * 0.1}s` }} />
                <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
                  <div style={{ height: 13, borderRadius: 6, background: "var(--surface-2, #F0EDE7)", width: `${50 + i * 8}%`, animation: "chats-pulse 1.4s ease-in-out infinite", animationDelay: `${i * 0.1}s` }} />
                  <div style={{ height: 10, borderRadius: 4, background: "var(--surface-2, #F0EDE7)", width: `${70 + i * 5}%`, animation: "chats-pulse 1.4s ease-in-out infinite", animationDelay: `${i * 0.15}s` }} />
                  <div style={{ display: "flex", gap: 6, marginTop: 2 }}>
                    <div style={{ height: 16, borderRadius: 8, background: "var(--surface-2, #F0EDE7)", width: 60, animation: "chats-pulse 1.4s ease-in-out infinite", animationDelay: `${i * 0.2}s` }} />
                    <div style={{ height: 16, borderRadius: 8, background: "var(--surface-2, #F0EDE7)", width: 40, animation: "chats-pulse 1.4s ease-in-out infinite", animationDelay: `${i * 0.2}s` }} />
                  </div>
                </div>
                <div style={{ width: 55, height: 10, borderRadius: 4, background: "var(--surface-2, #F0EDE7)", animation: "chats-pulse 1.4s ease-in-out infinite", animationDelay: `${i * 0.1}s` }} />
              </div>
            ))}
            <style>{`@keyframes chats-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }`}</style>
          </div>
        )}

        {/* Empty state */}
        {loaded && allChats.length === 0 && (
          <EmptyPane
            title="No conversations yet"
            hint="Start a new chat from the home screen."
            placeholders={2}
          />
        )}

        {/* Search empty */}
        {allChats.length > 0 && filtered.length === 0 && (
          <div style={{ textAlign: "center", padding: "40px 0", color: "var(--ink-3)", fontSize: 13 }}>
            No conversations matching "{search}"
          </div>
        )}

        {/* Grouped list — Pinned (incl. the weekly brief) first, then by date */}
        {loaded && (
          <ChatsListView
            rows={filtered}
            briefEntry={briefEntry}
            onRowClick={handleRowClick}
            onPin={handlePin}
            onDelete={handleDelete}
            onOpenBrief={openBrief}
          />
        )}
        </>
        )}
      </div>
    </AppLayout>
  )
}

/** Extra metadata items shown after the agent pill */
function getExtraMeta(row: ConversationRow, agent: AgentType): string[] {
  const meta: string[] = []
  const l = row.title.toLowerCase()
  if (l.includes("brief")) { meta.push("3 insights"); meta.push("15 sources") }
  else if (agent === "oncall") { meta.push("SEV-2"); meta.push("active") }
  else if (agent === "ds") { meta.push("4 segments") }
  else if (l.includes("prototype") || l.includes("wizard")) { meta.push("3 versions"); meta.push("5 comments") }
  else if (l.includes("prd")) { meta.push("PRD draft") }
  else if (l.includes("expansion") || l.includes("cerner")) { meta.push("PRD draft") }
  else if (l.includes("resolved") || l.includes("root-cause")) { meta.push("resolved") }
  else if (l.includes("okr")) { meta.push("doc draft") }
  else if (l.includes("onboarding")) { meta.push("complete") }
  return meta
}
