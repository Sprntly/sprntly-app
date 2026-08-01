"use client"

// A collapsed-only, non-interactive stand-in for the real Sidebar — required
// because GuestArtifactViewer never renders AppShell, so the real Sidebar
// (imported only by AppShell) is structurally impossible to reach here (AC21).
// Reuses the existing `.sidebar`/`.sb-rail-*` rail chrome and the `.cmdp-kbd`
// mono-pill convention rather than inventing new CSS.
import { IconLayoutKanban, IconMessageCircle, IconBulb } from "@tabler/icons-react"

const DISABLED_NAV = [
  { label: "Top Insights", icon: <IconBulb size={18} /> },
  { label: "Chat", icon: <IconMessageCircle size={18} /> },
  { label: "Tickets", icon: <IconLayoutKanban size={18} /> },
]

export function GuestRail() {
  return (
    <aside className="sidebar sidebar--collapsed" aria-label="Guest navigation">
      <div className="sb-rail-header">
        <div className="sb-rail-logo">
          <span className="sb-rail-logo-text">
            S<span className="sb-rail-logo-dot">.</span>
          </span>
        </div>
      </div>
      <div className="sb-rail-nav">
        {DISABLED_NAV.map((item) => (
          <button
            key={item.label}
            type="button"
            className="sb-rail-item"
            disabled
            title={`${item.label} — sign in to a full workspace to use this`}
            aria-label={item.label}
          >
            {item.icon}
          </button>
        ))}
      </div>
      <div className="sb-rail-spacer" />
      <span className="cmdp-kbd" data-testid="guest-rail-pill" style={{ margin: "0 auto 12px" }}>
        GUEST
      </span>
    </aside>
  )
}
