"use client"

import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import type { ScreenId } from "../../types"

export function Sidebar() {
  const { currentScreen, goTo } = useNavigation()
  const { content } = useContent()

  const NavItem = ({
    screen,
    icon,
    label,
    count,
  }: {
    screen: ScreenId
    icon: React.ReactNode
    label: string
    count?: number | null
  }) => (
    <a
      className={`sb-item ${currentScreen === screen ? "active" : ""}`}
      onClick={() => goTo(screen)}
    >
      <span className="sb-icon">{icon}</span>
      {label}
      {count != null && count > 0 ? (
        <span className="sb-count">{count > 99 ? "99+" : count}</span>
      ) : null}
    </a>
  )

  const initials =
    content.userInitials ??
    (content.userName
      ? content.userName
          .split(/\s+/)
          .map((p) => p[0])
          .join("")
          .slice(0, 2)
          .toUpperCase()
      : "—")
  const displayName = content.userName ?? "Account"
  const displayEmail = content.userEmail ?? "Sign in to sync"

  return (
    <aside className="sidebar">
      <div className="sb-brand">
        <span className="sb-brand-dot"></span>spr<span>ntly</span>
      </div>
      <div className="sb-section-title">Intelligence</div>
      <NavItem
        screen="brief"
        icon="✦"
        label="Weekly brief"
        count={content.sidebarBriefCount ?? undefined}
      />
      <NavItem
        screen="ondemand"
        icon={
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        }
        label="Ask Sprntly"
        count={content.sidebarConvCount ?? undefined}
      />
      <NavItem screen="shipped" icon="✓" label="Shipped" />
      <div className="sb-spacer"></div>
      <div className="sb-section-title">Workspace</div>
      <NavItem screen="connectors" icon="⊞" label="Connectors" />
      <NavItem screen="team" icon="○" label="Team" />
      <NavItem screen="settings" icon="⚙" label="Settings" />
      <div className="sb-footer">
        <div className="sb-user">
          <div className="sb-avatar">{initials}</div>
          <div className="sb-user-info">
            <div className="sb-user-name">{displayName}</div>
            <div className="sb-user-email">{displayEmail}</div>
          </div>
        </div>
      </div>
    </aside>
  )
}
