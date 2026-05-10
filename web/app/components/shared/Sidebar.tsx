"use client"

import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import type { ScreenId } from "../../types"
import {
  IconAsk,
  IconBrief,
  IconEvidence,
  IconHome,
  IconPrd,
  IconSettings,
} from "./sidebar-icons"

export function Sidebar() {
  const { currentScreen, goTo, sidebarCollapsed, toggleSidebar } = useNavigation()
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
      title={label}
      onClick={() => goTo(screen)}
    >
      <span className="sb-icon">{icon}</span>
      <span className="sb-item-label">{label}</span>
      {count != null && count > 0 ? (
        <span className="sb-count">{count > 99 ? "99+" : count}</span>
      ) : null}
    </a>
  )

  const displayName = content.userName ?? "David"
  const initials =
    content.userInitials ??
    displayName
      .split(/\s+/)
      .map((p) => p[0])
      .join("")
      .slice(0, 2)
      .toUpperCase()

  return (
    <aside className={`sidebar${sidebarCollapsed ? " sidebar--collapsed" : ""}`}>
      <div className="sb-top">
        <div className="sb-header">
          <div className="sb-brand">
            <span className="sb-brand-text">
              spr<span>ntly</span>
            </span>
          </div>
          <button
            type="button"
            className="sb-collapse-btn"
            onClick={toggleSidebar}
            aria-expanded={!sidebarCollapsed}
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {sidebarCollapsed ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
                {/* Double chevron right — expand */}
                <g
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  fill="none"
                >
                  <polyline points="7 6 11 12 7 18" />
                  <polyline points="12 6 16 12 12 18" />
                </g>
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
                {/* Double chevron left — collapse */}
                <g
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  fill="none"
                >
                  <polyline points="17 6 13 12 17 18" />
                  <polyline points="12 6 8 12 12 18" />
                </g>
              </svg>
            )}
          </button>
        </div>
      </div>

      <div className="sb-body">
        <div className="sb-section-title">Overview</div>
        <NavItem screen="chat" icon={<IconHome />} label="Home" />

        <div className="sb-section-title">Intelligence</div>
        <NavItem
          screen="brief"
          icon={<IconBrief />}
          label="Weekly brief"
          count={content.sidebarBriefCount ?? undefined}
        />
        <NavItem screen="detail" icon={<IconEvidence />} label="Evidence" />
        <NavItem screen="prd" icon={<IconPrd />} label="PRD" />
        <NavItem
          screen="ondemand"
          icon={<IconAsk />}
          label="Ask Sprntly"
          count={content.sidebarConvCount ?? undefined}
        />

        <div className="sb-spacer" />

        <div className="sb-section-title">Workspace</div>
        <NavItem screen="settings" icon={<IconSettings />} label="Settings" />
      </div>

      <div className="sb-footer">
        <div className="sb-user">
          <div className="sb-avatar">{initials}</div>
          <div className="sb-user-info">
            <div className="sb-user-name">{displayName}</div>
          </div>
        </div>
      </div>
    </aside>
  )
}
