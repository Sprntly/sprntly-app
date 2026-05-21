"use client"

import { useCallback, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useAuth } from "../../lib/auth"
import type { ScreenId } from "../../types"
import {
  IconBrief,
  IconEvidence,
  IconHome,
  IconPrd,
  IconConnectors,
  IconSettings,
  IconSources,
} from "./sidebar-icons"
import { CompanySwitcher } from "./CompanySwitcher"

interface SidebarProps {
  activeCompany?: string
  onSwitchCompany?: (slug: string) => void
}

export function Sidebar({ activeCompany, onSwitchCompany }: SidebarProps = {}) {
  const { currentScreen, goTo, sidebarCollapsed, toggleSidebar } = useNavigation()
  const { content } = useContent()
  const { signOut } = useAuth()
  const signingOutRef = useRef(false)
  const [signingOut, setSigningOut] = useState(false)

  const handleSignOut = useCallback(async () => {
    if (signingOutRef.current) return
    signingOutRef.current = true
    setSigningOut(true)
    try {
      await signOut()
    } finally {
      signingOutRef.current = false
      setSigningOut(false)
    }
  }, [signOut])

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

      {activeCompany && onSwitchCompany && !sidebarCollapsed && (
        <CompanySwitcher activeSlug={activeCompany} onSwitch={onSwitchCompany} />
      )}

      <div className="sb-body">
        <div className="sb-section-title">Overview</div>
        <NavItem
          screen="chat"
          icon={<IconHome />}
          label="Home"
          count={content.sidebarConvCount ?? undefined}
        />

        <div className="sb-section-title">Intelligence</div>
        <NavItem
          screen="brief"
          icon={<IconBrief />}
          label="Weekly brief"
          count={content.sidebarBriefCount ?? undefined}
        />
        <NavItem screen="detail" icon={<IconEvidence />} label="Evidence" />
        <NavItem screen="prd" icon={<IconPrd />} label="PRD" />
        <div className="sb-spacer" />

        <div className="sb-section-title">Workspace</div>
        <NavItem screen="sources" icon={<IconSources />} label="Sources" />
        <NavItem
          screen="connectors"
          icon={<IconConnectors />}
          label="Connectors"
          count={
            content.connectedConnectorIds.length > 0
              ? content.connectedConnectorIds.length
              : undefined
          }
        />
        <NavItem screen="settings" icon={<IconSettings />} label="Settings" />
      </div>

      <div className="sb-footer">
        <div className="sb-user">
          <div className="sb-avatar">{initials}</div>
          <div className="sb-user-info">
            <div className="sb-user-name">{displayName}</div>
          </div>
          <button
            type="button"
            className="sb-signout"
            onClick={() => void handleSignOut()}
            disabled={signingOut}
            title="Sign out"
            aria-label="Sign out"
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
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M16 17l5-5-5-5M21 12H9"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
