"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { conversationsApi, type ConversationRecord } from "../../../lib/api"
import type { ConversationRow } from "../../../types/content"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

// ── Mock data (matches the design mockup exactly) ──

const MOCK_CHATS: ConversationRow[] = [
  {
    id: "pinned-1",
    title: "Monday Brief · Week of May 27",
    time: new Date(Date.now() - 3600000).toISOString(),
    savedTurn: { id: "pinned-1-t", query: "Three signals — Day-30 retention dip in 4 deployments, Cerner deal stalled in security review, care plan co-authoring up 12% WoW." },
  },
  {
    id: "today-1",
    title: "On-call · CarePlan compose latency spike",
    time: new Date(Date.now() - 7200000).toISOString(),
    savedTurn: { id: "today-1-t", query: "SEV-2 active · Sentry + Datadog confluence triggered · 11 Riverside users affected, p99 7.4s." },
  },
  {
    id: "today-2",
    title: "Day-30 cohort breakdown by EHR vendor",
    time: new Date(Date.now() - 10800000).toISOString(),
    savedTurn: { id: "today-2-t", query: "DS Agent ran a cross-segment analysis — Epic deployments show a 4pt gap vs Cerner; deeper dive recommended on Epic-specific FHIR latency." },
  },
  {
    id: "today-3",
    title: "Cerner expansion — security review next steps",
    time: new Date(Date.now() - 14400000).toISOString(),
    savedTurn: { id: "today-3-t", query: "Drafted three talking points for the Cerner InfoSec call · attached SOC 2 packet · suggested legal bring HIPAA BAA addendum." },
  },
  {
    id: "yesterday-1",
    title: "Prototype · First-Handoff Wizard v2",
    time: new Date(Date.now() - 86400000).toISOString(),
    savedTurn: { id: "yesterday-1-t", query: "Design Agent iterated 3 directions for the inline 3-step prompt. Direction B (split-screen) won internal review." },
  },
  {
    id: "yesterday-2",
    title: "Veradigm integration scoping",
    time: new Date(Date.now() - 100000000).toISOString(),
    savedTurn: { id: "yesterday-2-t", query: "PM Agent compared FHIR coverage across Veradigm, Epic, and Cerner. Veradigm scope is roughly 60% smaller. PRD generated." },
  },
  {
    id: "week-1",
    title: "Shift-handoff drop · root-cause investigation",
    time: new Date(Date.now() - 172800000).toISOString(),
    savedTurn: { id: "week-1-t", query: "Resolved · was a deploy-time config regression. Engineer Agent caught it. Full RCA in thread." },
  },
  {
    id: "week-2",
    title: "Q3 OKR mid-quarter review · prep doc",
    time: new Date(Date.now() - 259200000).toISOString(),
    savedTurn: { id: "week-2-t", query: "Drafted the mid-quarter narrative — 2 of 3 OKRs on track, retention OKR at risk (now flagged this week)." },
  },
  {
    id: "earlier-1",
    title: "Onboarding redesign · prototype",
    time: new Date(Date.now() - 604800000).toISOString(),
    savedTurn: { id: "earlier-1-t", query: "Design Agent prototyped a 5-step new-deployment onboarding. Tested with 3 customers. Direction approved." },
  },
]

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

// ── Screen ──

export function ChatsScreen() {
  const { goTo, setPendingOndemandDraft } = useNavigation()
  const { content } = useContent()
  const [search, setSearch] = useState("")
  const [dbChats, setDbChats] = useState<ConversationRecord[]>([])
  const [loaded, setLoaded] = useState(false)

  // Load from Supabase on mount
  useEffect(() => {
    let cancelled = false
    conversationsApi.list().then((res) => {
      if (!cancelled) { setDbChats(res.conversations); setLoaded(true) }
    }).catch(() => { if (!cancelled) setLoaded(true) })
    return () => { cancelled = true }
  }, [])

  // Map DB records to ConversationRow shape
  const dbRows: ConversationRow[] = useMemo(() =>
    dbChats.map((c) => ({
      id: String(c.id),
      title: c.title,
      time: c.created_at,
      savedTurn: { query: c.query || c.preview, reply: c.reply },
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
    if (inMemory.length > 0) return inMemory
    return MOCK_CHATS
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

  // Group: pinned items first, rest by date
  const grouped = useMemo(() => {
    const map = new Map<string, ConversationRow[]>()
    for (const g of GROUP_ORDER) map.set(g, [])
    for (const row of filtered) {
      if ((row as any)._pinned) {
        map.get("Pinned")!.push(row)
      } else {
        map.get(dateGroup(row.time))!.push(row)
      }
    }
    return map
  }, [filtered])

  const handleRowClick = (row: ConversationRow) => {
    const dbId = (row as any)._dbId as number | undefined
    if (dbId) {
      // Load full conversation turns from DB and pass via localStorage
      conversationsApi.listTurns(dbId).then((res) => {
        // Store turns in localStorage so ChatScreen can pick them up
        try {
          localStorage.setItem("sprntly_resume_conv", JSON.stringify({
            dbId,
            title: row.title,
            turns: res.turns,
          }))
        } catch { /* ignore */ }
        goTo("chat")
      }).catch(() => {
        // Fallback: just navigate with the saved turn
        if (row.savedTurn?.query) setPendingOndemandDraft(row.savedTurn.query)
        goTo("chat")
      })
    } else {
      if (row.savedTurn?.query) setPendingOndemandDraft(row.savedTurn.query)
      goTo("chat")
    }
  }

  return (
    <AppLayout>
      <div style={{ maxWidth: 780, margin: "0 auto", padding: "0 4px" }}>
        {/* Top bar */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 16, fontWeight: 600, color: "var(--ink, #1A1A17)" }}>All chats</span>
            <span style={{
              fontSize: 12, fontWeight: 500, color: "var(--ink-3, #8C8A84)",
              background: "var(--surface-2, #F0EDE7)", padding: "2px 8px", borderRadius: 20,
            }}>
              {allChats.length}
            </span>
          </div>

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

        {/* Empty state */}
        {allChats.length === 0 && (
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

        {/* Grouped list */}
        {GROUP_ORDER.map((group) => {
          const rows = grouped.get(group)
          if (!rows || rows.length === 0) return null
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

              {rows.map((row) => {
                const agent = detectAgent(row.title)
                const cfg = AGENT_CONFIG[agent]
                const isPinned = group === "Pinned"
                const extraMeta = getExtraMeta(row, agent)

                return (
                  <div
                    key={row.id}
                    onClick={() => handleRowClick(row)}
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
                          onClick={(e) => { e.stopPropagation(); handlePin(row) }}
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
                            onClick={(e) => { e.stopPropagation(); handleDelete(row) }}
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
