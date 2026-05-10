"use client"

import { useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useAuth } from "../../lib/auth"
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
  const auth = useAuth()
  const [signingOut, setSigningOut] = useState(false)

  const handleSignOut = async () => {
    if (signingOut) return
    setSigningOut(true)
    try {
      await auth.signOut()
      // AuthGate will detect anonymous state and redirect to /sign-in
    } finally {
      setSigningOut(false)
    }
  }

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
    <aside className={`sidebar${sidebarCollapsed ? " sidebar--collapsed" : ""}`}>
      <div className="sb-top">
        <div className="sb-header">
          <div className="sb-brand">
            <span className="sb-brand-dot" aria-hidden />
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
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M9 18l6-6-6-6" />
              </svg>
            ) : (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M15 18l-6-6 6-6" />
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
            <div className="sb-user-email">{displayEmail}</div>
          </div>
          <button
            type="button"
            className="sb-signout"
            onClick={handleSignOut}
            disabled={signingOut}
            aria-label="Sign out"
            title="Sign out"
          >
            <SignOutIcon />
          </button>
        </div>
      </div>
    </aside>
  )
}

function SignOutIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {/* door + arrow leaving */}
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  )
}
