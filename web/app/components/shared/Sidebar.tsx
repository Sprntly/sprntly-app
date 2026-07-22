"use client"

import { useEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useAuth } from "../../lib/auth"
import { profileDisplayName, useWorkspace } from "../../context/WorkspaceContext"
import type { ScreenId } from "../../types"
import { IconSources } from "./sidebar-icons"
import { IconLayoutKanban, IconMessageCircle, IconPrompt, IconBulb, IconSettings, IconHistory, IconMessagePlus, IconBookmark, IconFiles, IconWand, IconSearch, IconSparkles, IconBook2 } from "@tabler/icons-react"
import { FeedbackModal } from "./FeedbackModal"
import { CreateWorkspaceModal } from "./CreateWorkspaceModal"
import { publicPath } from "../../lib/public-path"

interface SidebarProps {
  activeCompany?: string
  onSwitchCompany?: (slug: string) => void
}

export function Sidebar(_props: SidebarProps = {}) {
  const { currentScreen, goTo, goToNewChat, sidebarCollapsed, toggleSidebar, openPalette } = useNavigation()
  const { content } = useContent()
  const auth = useAuth()
  const {
    profile,
    workspace,
    workspaces = [],
    activeWorkspace,
    orgRole,
    setActiveWorkspace,
  } = useWorkspace()
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  // Workspace switcher (multi-workspace 2026-07): the brand name doubles as
  // the trigger; the menu lists the caller's workspaces + a create affordance.
  const [wsMenuOpen, setWsMenuOpen] = useState(false)
  const [createWsOpen, setCreateWsOpen] = useState(false)
  const wsMenuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!wsMenuRef.current) return
      if (!wsMenuRef.current.contains(e.target as Node)) setWsMenuOpen(false)
    }
    if (wsMenuOpen) document.addEventListener("mousedown", onClick)
    return () => document.removeEventListener("mousedown", onClick)
  }, [wsMenuOpen])

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
      <span className="sb-rail-label">{label}</span>
      <span className="nav-tooltip">{label}</span>
    </button>
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

  // The header shows the ACTIVE WORKSPACE (multi-workspace 2026-07); company
  // display name is the fallback while the workspaces list loads.
  const brandName =
    activeWorkspace?.name ??
    workspace?.display_name ??
    workspace?.product?.name ??
    content.homeHeadline ??
    "Sprntly"
  const companyInitial = brandName.charAt(0).toUpperCase()
  // Workspace creation is ORG owner/admin only (backend-enforced) — a
  // workspace-level admin who is a plain org member doesn't get the button.
  const canCreateWs = orgRole === "owner" || orgRole === "admin"
  const wsInteractive = workspaces.length > 1 || canCreateWs

  return (
    <aside className={`sidebar ${sidebarCollapsed ? "sidebar--collapsed" : "sidebar--expanded"}`}>
      {/* Logo + workspace switcher + expand/collapse toggle */}
      <div className="sb-rail-header">
        <div
          className="sb-rail-logo"
          title={content.homeHeadline ?? "Sprntly"}
          onClick={() => goTo("chat")}
          style={{ cursor: "pointer" }}
        >
          <span className="sb-rail-logo-text">
            {companyInitial}
            <span className="sb-rail-logo-dot">.</span>
          </span>
        </div>
        <div className="sb-ws-wrap" ref={wsMenuRef}>
          <button
            type="button"
            className={`sb-rail-brand-name sb-ws-trigger${wsInteractive ? "" : " sb-ws-trigger--static"}`}
            onClick={() => wsInteractive && setWsMenuOpen((v) => !v)}
            aria-haspopup="listbox"
            aria-expanded={wsMenuOpen}
            title={brandName}
            data-testid="workspace-switcher"
          >
            <span className="sb-ws-name">{brandName}</span>
            {wsInteractive && (
              <svg width="10" height="10" viewBox="0 0 24 24" aria-hidden>
                <path d="M6 9 L12 15 L18 9" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" />
              </svg>
            )}
          </button>
          {wsMenuOpen && (
            <div className="sb-ws-menu" role="listbox">
              {workspaces.map((w) => (
                <button
                  key={w.id}
                  type="button"
                  className={`sb-ws-row${w.id === activeWorkspace?.id ? " active" : ""}`}
                  onClick={() => {
                    setActiveWorkspace(w.id)
                    setWsMenuOpen(false)
                  }}
                  role="option"
                  aria-selected={w.id === activeWorkspace?.id}
                >
                  <span className="sb-ws-row-name">{w.name}</span>
                  {w.id === activeWorkspace?.id && (
                    <span className="sb-ws-row-meta">active</span>
                  )}
                </button>
              ))}
              {canCreateWs && (
                <>
                  <div className="sb-ws-sep" />
                  <button
                    type="button"
                    className="sb-ws-row sb-ws-row--create"
                    onClick={() => {
                      setWsMenuOpen(false)
                      setCreateWsOpen(true)
                    }}
                  >
                    + New workspace
                  </button>
                </>
              )}
            </div>
          )}
        </div>
        <button
          type="button"
          className="sb-rail-expand"
          onClick={toggleSidebar}
          title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!sidebarCollapsed}
        >
          <ChevronIcon collapsed={sidebarCollapsed} />
        </button>
      </div>

      {/* Global search (⌘K) — the modal itself is rendered by AppShell so the
          hotkey works even when the sidebar is collapsed or hidden. */}
      <button
        type="button"
        className="sb-rail-search"
        title="Search"
        aria-label="Search (Ctrl+K)"
        onClick={openPalette}
        data-testid="palette-trigger"
      >
        <IconSearch size={18} />
        <span className="sb-rail-label">Search</span>
        <kbd className="sb-rail-search-kbd">⌘K</kbd>
        <span className="nav-tooltip">Search</span>
      </button>

      {/* New chat */}
      <button
        type="button"
        className="sb-rail-new"
        title="New chat"
        aria-label="New chat"
        onClick={goToNewChat}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
        <span className="sb-rail-label">New chat</span>
      </button>

      {/* Main nav icons */}
      <div className="sb-rail-nav">
        <RailItem screen="brief" icon={<IconSparkles size={18} />} label="Top Insights" />
        <RailItem screen="chats" icon={<IconHistory size={18} />} label="Chat history" />
        <RailItem screen="artifacts" icon={<IconFiles size={18} />} label="Artifacts" />
        <RailItem screen="ideation" icon={<IconBulb size={18} />} label="Ideation" />
        {/* <RailItem screen="templates" icon={<IconBookmark size={18} />} label="Templates" /> */}
        {/* Skills and Sources are both hidden from the rail (keep
            functionality) — their screens, routes (/skills, /sources) and
            backends remain intact and reachable; uncomment to restore. */}
        {/* <RailItem screen="skills" icon={<IconWand size={18} />} label="Skills" /> */}
        {/* <RailItem screen="sources" icon={<IconSources />} label="Sources" /> */}
        {/* <RailItem screen="prototype" icon={<IconPrompt size={18} />} label="Prototype" /> */}
        {/* <RailItem screen="tickets" icon={<IconLayoutKanban size={18} />} label="Project Management" /> */}
      </div>

      <div className="sb-rail-spacer" />

      {/* Bottom icons — Guide + Settings + Feedback (Sign out lives in Settings → Account). */}
      <div className="sb-rail-bottom">
        {/* Guide links out to the public docs site (/docs), which lives outside
            the authenticated SPA — so it's a real anchor, not a goTo() screen.
            Opens in a new tab to preserve the user's in-app session. */}
        <a
          href={publicPath("/docs")}
          target="_blank"
          rel="noopener noreferrer"
          className="sb-rail-item"
          title="Guide"
          aria-label="Guide"
          data-testid="sidebar-guide-link"
        >
          <IconBook2 size={18} />
          <span className="sb-rail-label">Guide</span>
          <span className="nav-tooltip">Guide</span>
        </a>
        <RailItem screen="settings" icon={<IconSettings size={18} />} label="Settings" />
        <button
          type="button"
          className="sb-rail-item"
          title="Feedback"
          aria-label="Feedback"
          onClick={() => setFeedbackOpen(true)}
        >
          <IconMessagePlus size={18} />
          <span className="sb-rail-label">Feedback</span>
          <span className="nav-tooltip">Feedback</span>
        </button>
      </div>
      <div className="divider-nav" />

      {/* User identity row — display only. Signing out moved to Settings →
          Account, so no sign-out affordance here (icon or avatar click). */}
      <div className="sb-rail-user">
        <span className="sb-rail-avatar" title={displayName}>
          {initials}
        </span>
        <span className="sb-rail-username">{displayName}</span>
      </div>

      <FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
      <CreateWorkspaceModal open={createWsOpen} onClose={() => setCreateWsOpen(false)} />
    </aside>
  )
}

function ChevronIcon({ collapsed }: { collapsed: boolean }) {
  // Points right (»/›) when collapsed to invite expansion, left when expanded.
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ transform: collapsed ? "none" : "rotate(180deg)" }}
      aria-hidden
    >
      <polyline points="9 18 15 12 9 6" />
    </svg>
  )
}

