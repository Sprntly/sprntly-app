"use client"

import { IconCheck } from "@tabler/icons-react"
import type { ClickUpList } from "../../lib/api"

/** The push destination picker — a compact popover listing the tracker's
 *  lists/projects (with their path) and a "Push N tickets" action. Matches the
 *  locked reference (backend/skills/user-stories/examples/sprntly-ticket-views.html):
 *  the destination is chosen here, registered server-side per PRD, and the
 *  field-mapped sync then runs (and keeps running) on the backend. The
 *  remember toggle is legacy-optional — destinations now always persist.
 *  Styled with the shared `.tkv2-picker` classes. */
export function DestinationPicker({
  tool, lists, selectedId, onSelect, remember, onToggleRemember, count, onPush, onCancel,
}: {
  tool: string
  lists: ClickUpList[]
  selectedId: string
  onSelect: (id: string) => void
  remember?: boolean
  onToggleRemember?: (v: boolean) => void
  count: number
  onPush: () => void
  onCancel: () => void
}) {
  return (
    <>
      {/* Click-away backdrop so the popover closes like the reference. */}
      <div
        onClick={onCancel}
        style={{ position: "fixed", inset: 0, zIndex: 30 }}
        aria-hidden
      />
      {/* Anchored LEFT so the 300px body grows INTO the panel (the trigger
          button sits at the panel's left edge — right-anchoring pushed the
          popover off-panel and clipped it). Width clamps to the viewport. */}
      <div className="tkv2-picker" style={{ position: "absolute", top: "100%", left: 0, zIndex: 31, minWidth: 300, maxWidth: "min(340px, calc(100vw - 32px))" }} role="dialog" aria-label={`Push to ${tool}`}>
        <div className="ph2">Push to {tool} — select a project</div>
        <div style={{ maxHeight: 240, overflowY: "auto" }}>
          {lists.map((l) => {
            const path = [l.space, l.folder].filter(Boolean).join(" / ")
            const sel = l.id === selectedId
            return (
              <button
                key={l.id}
                type="button"
                className={`tkv2-pitem${sel ? " tkv2-pitem--sel" : ""}`}
                onClick={() => onSelect(l.id)}
              >
                <span aria-hidden style={{ width: 12, display: "inline-flex" }}>{sel ? "●" : "○"}</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{l.name}</span>
                {path ? <span className="tkv2-ppath">{path}</span> : null}
              </button>
            )
          })}
        </div>
        <div className="tkv2-pfoot">
          {onToggleRemember ? (
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => onToggleRemember(e.target.checked)}
                style={{ accentColor: "var(--green)" }}
              />
              Remember for this PRD
            </label>
          ) : (
            <span style={{ fontSize: 12, color: "var(--soft)" }}>
              Stays in sync automatically after the first push
            </span>
          )}
          <button
            type="button"
            className="tkv2-btn2 tkv2-btn2--primary"
            style={{ display: "inline-flex", alignItems: "center", gap: 5 }}
            onClick={onPush}
            disabled={!selectedId}
          >
            <IconCheck size={12} /> Push {count} ticket{count !== 1 ? "s" : ""}
          </button>
        </div>
      </div>
    </>
  )
}
