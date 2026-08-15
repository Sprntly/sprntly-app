"use client"

/**
 * The chat's artifact listing — "what are my PRDs?" answered as CLICKABLE
 * rows, not prose.
 *
 * OpenArtifactChips' contract, one size up: each row carries the ARTIFACT
 * (its ids, its thread binding), and `onOpen` opens it directly — in its own
 * thread when one survives — exactly as clicking the same row on the
 * Artifacts screen would. Nothing is posted back to the thread, and no second
 * round trip re-interprets a request that was already resolved. A suggestion
 * chip that re-sent "Open PRD #2216" as a message is the failure this shape
 * exists to prevent.
 *
 * Rows are buttons in a `role="group"`, not `role="list"` items, for the
 * reason documented on OpenArtifactChips: `role="listitem"` on a <button>
 * REPLACES its button role and screen readers stop announcing it pressable.
 *
 * A reply-adjacent card leaf, props-driven with no coupling to any one
 * screen — `shared/ChatBubble` renders it via its `artifactList` prop, so
 * any surface that hands ChatBubble a turn's `ChatArtifactItem[]` gets the
 * same clickable listing, not a second implementation.
 */
import type { ChatArtifactItem } from "../../lib/api"
import {
  IconChartBar, IconFileText, IconLayoutDashboard, IconMicroscope,
  IconNotes, IconTicket,
} from "@tabler/icons-react"

/** How each kind reads on its badge — user vocabulary, not storage vocabulary
 *  (`custom_artifact` is "Document" everywhere a person sees it). */
const KIND_LABEL: Record<ChatArtifactItem["type"], string> = {
  prd: "PRD",
  evidence: "Evidence",
  prototype: "Prototype",
  report: "Report",
  ticket_set: "Tickets",
  custom_artifact: "Document",
}

// Component REFERENCES, not rendered elements: module-level JSX evaluates at
// import time, before a classic-runtime test harness has set its React global
// — the icons render inside the component body instead.
const KIND_ICON: Record<ChatArtifactItem["type"], React.ComponentType<{ size?: number }>> = {
  prd: IconFileText,
  evidence: IconMicroscope,
  prototype: IconLayoutDashboard,
  report: IconChartBar,
  ticket_set: IconTicket,
  custom_artifact: IconNotes,
}

function when(createdAt: string | null): string | null {
  if (!createdAt) return null
  const d = new Date(createdAt)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString()
}

export function ArtifactListCards({
  items,
  onOpen,
  disabled = false,
}: {
  /** Already recency-sorted and capped by the backend (routes/chat.py). */
  items: ChatArtifactItem[]
  /** Opens THIS artifact — its own thread when one survives, the panel /
   *  canvas otherwise. Never sends a message. */
  onOpen: (item: ChatArtifactItem) => void
  disabled?: boolean
}) {
  if (!items.length) return null

  return (
    <div
      data-testid="artifact-list-cards"
      role="group"
      aria-label="Your artifacts — click one to open it"
      style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}
    >
      {items.map((a) => {
        const Icon = KIND_ICON[a.type]
        const date = when(a.created_at)
        // The thread affordance is only claimed when a click can honour it —
        // both halves present, same rule the open flow itself applies.
        const thread = a.source.conversation_id != null && a.source.conversation_title
          ? a.source.conversation_title
          : null
        return (
          <button
            key={`${a.type}-${a.id}`}
            type="button"
            disabled={disabled}
            data-testid="artifact-list-card"
            data-artifact-id={a.id}
            data-artifact-type={a.type}
            onClick={() => onOpen(a)}
            style={{
              display: "flex", alignItems: "center", gap: 10, width: "100%",
              textAlign: "left", padding: "9px 12px", borderRadius: 10,
              border: "1px solid var(--line)", background: "var(--surface)",
              cursor: disabled ? "default" : "pointer",
            }}
          >
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 5, flexShrink: 0,
              fontSize: 10.5, fontWeight: 600, padding: "3px 8px", borderRadius: 999,
              background: "var(--surface-2)", color: "var(--ink-2)",
              textTransform: "uppercase", letterSpacing: "0.04em",
            }}>
              <Icon size={13} />
              {KIND_LABEL[a.type]}
            </span>
            <span style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}>
              <span style={{
                fontSize: 13, fontWeight: 500, color: "var(--ink)",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {a.title || "(untitled)"}
              </span>
              {/* Every row keeps its context line — a missing half renders
                  what exists rather than dropping the line (house rule). */}
              <span style={{
                fontSize: 11, color: "var(--ink-4)",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {[date, thread ? `from “${thread}”` : null].filter(Boolean).join(" · ") || "—"}
              </span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
