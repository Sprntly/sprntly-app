"use client"

import { useCallback, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useAuth } from "../../lib/auth"
import { profileDisplayName, useWorkspace } from "../../context/WorkspaceContext"
import type { ScreenId } from "../../types"
import {
  IconChats,
  IconHome,
  IconPrd,
  IconSettings,
  IconSources,
  IconTickets,
  IconBrief,
} from "./sidebar-icons"
import { IconSparkle } from "./app-icons"
import { CompanySwitcher } from "./CompanySwitcher"

interface SidebarProps {
  activeCompany?: string
  onSwitchCompany?: (slug: string) => void
}

export function Sidebar({ activeCompany, onSwitchCompany }: SidebarProps = {}) {
  const { currentScreen, goTo, sidebarCollapsed, toggleSidebar } = useNavigation()
  const { content } = useContent()
  const auth = useAuth()
  const { profile, workspace } = useWorkspace()
  const signingOutRef = useRef(false)
  const [signingOut, setSigningOut] = useState(false)

  const handleSignOut = useCallback(async () => {
    if (signingOutRef.current) return
    signingOutRef.current = true
    setSigningOut(true)
    try {
      await auth.signOut()
    } finally {
      signingOutRef.current = false
      setSigningOut(false)
    }
  }, [auth])

  const RailItem = ({
    screen,
    icon,
    label,
  }: {
    screen: ScreenId
    icon: React.ReactNode
    label: string
  }) => (
    <button
      type="button"
      className={`sb-rail-item${currentScreen === screen ? " active" : ""}`}
      title={label}
      onClick={() => goTo(screen)}
      aria-label={label}
    >
      {icon}
      <span className="nav-tooltip">{label} </span>
    </button>
  )

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

  const displayName =
    content.userName ??
    (auth.kind === "authed" ? profileDisplayName(profile, auth.user.email) : null) ??
    "Guest"
  const initials =
    content.userInitials ??
    displayName
      .split(/\s+/)
      .map((p) => p[0])
      .join("")
      .slice(0, 2)
      .toUpperCase()

  // First character of the company/workspace display name for the rail logo
  const companyInitial = (workspace?.display_name ?? workspace?.product?.name ?? "S").charAt(0).toUpperCase()

  /* ── Collapsed: icon-only rail (design default) ── */
  if (sidebarCollapsed) {
    return (
      <aside className="sidebar sidebar--collapsed">
        {/* Logo + expand */}
        <div className="sb-rail-header">
          <div className="sb-rail-logo" title={content.homeHeadline ?? "Sprntly"}>
            <span className="sb-rail-logo-text">{companyInitial}</span>
          </div>
          <button
            type="button"
            className="sb-rail-expand"
            onClick={toggleSidebar}
            title="Expand sidebar"
            aria-label="Expand sidebar"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
              <g stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none">
                <polyline points="7 6 11 12 7 18" />
                <polyline points="12 6 16 12 12 18" />
              </g>
            </svg>
          </button>
        </div>

        {/* New chat */}
        <button
          type="button"
          className="sb-rail-new"
          title="New chat"
          aria-label="New chat"
          onClick={() => goTo("chat")}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>

        {/* Main nav icons */}
        <div className="sb-rail-nav">
          <RailItem screen="chat" icon={<IconHome />} label="Home" />
          <RailItem screen="chats" icon={<IconChats />} label="All chats" />
          <RailItem screen="prd" icon={<IconPrd />} label="PRD" />
          <RailItem screen="prototype" icon={<IconSparkle />} label="Prototype" />
          <RailItem screen="tickets" icon={<IconTickets />} label="Project Management" />
        </div>

        <div className="sb-rail-spacer" />

        {/* Bottom icons */}
        <div className="sb-rail-bottom">
          <RailItem screen="sources" icon={<IconSources />} label="Sources" />
          <RailItem screen="settings" icon={<IconSettings />} label="Settings" />
        </div>
        <div className="divider-nav"></div>
        {/* User avatar */}
        <div className="sb-rail-user">
          <button
            type="button"
            className="sb-rail-avatar"
            title={`${displayName} · Sign out`}
            onClick={() => void handleSignOut()}
            disabled={signingOut}
          >
            {initials}
          </button>
          <button
            type="button"
            className="sb-rail-signout"
            onClick={() => void handleSignOut()}
            disabled={signingOut}
            title="Sign out"
            aria-label="Sign out"
          >
            <SignOutIcon />
          </button>
        </div>
      </aside>
    )
  }

  /* ── Expanded: full sidebar ── */
  return (
    <aside className="sidebar">
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
            aria-label="Collapse sidebar"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
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
          </button>
        </div>
      </div>

      {activeCompany && onSwitchCompany && (
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
        <NavItem
          screen="chats"
          icon={<IconChats />}
          label="All chats"
        />

        <div className="sb-section-title">Intelligence</div>
        <NavItem
          screen="brief"
          icon={<IconBrief />}
          label="Weekly brief"
          count={content.sidebarBriefCount ?? undefined}
        />
        {/* <NavItem screen="prd" icon={<IconPrd />} label="PRD" /> */}
        <NavItem screen="prototype" icon={<IconSparkle />} label="Prototype" />
        <NavItem screen="tickets" icon={<IconTickets />} label="Project Management" />
        <div className="sb-spacer" />

        <div className="sb-section-title">Workspace</div>
        <NavItem screen="sources" icon={<IconSources />} label="Sources" />
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
