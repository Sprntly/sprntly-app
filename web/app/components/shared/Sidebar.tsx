"use client"

import { useCallback, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useAuth } from "../../lib/auth"
import { profileDisplayName, useWorkspace } from "../../context/WorkspaceContext"
import type { ScreenId } from "../../types"
import { IconSources } from "./sidebar-icons"
import { IconLayoutKanban, IconMessageCircle, IconPrompt, IconBulb, IconSettings, IconHistory, IconMessagePlus, IconBookmark } from "@tabler/icons-react"
import { FeedbackModal } from "./FeedbackModal"

interface SidebarProps {
  activeCompany?: string
  onSwitchCompany?: (slug: string) => void
}

export function Sidebar(_props: SidebarProps = {}) {
  const { currentScreen, goTo, goToNewChat } = useNavigation()
  const { content } = useContent()
  const auth = useAuth()
  const { profile, workspace } = useWorkspace()
  const signingOutRef = useRef(false)
  const [signingOut, setSigningOut] = useState(false)
  const [feedbackOpen, setFeedbackOpen] = useState(false)

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

  const companyInitial = (workspace?.display_name ?? workspace?.product?.name ?? "S").charAt(0).toUpperCase()

  return (
    <aside className="sidebar sidebar--collapsed">
      {/* Logo */}
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
      </div>

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
      </button>

      {/* Main nav icons */}
      <div className="sb-rail-nav">
        <RailItem screen="brief" icon={<IconMessageCircle size={18} />} label="Weekly brief" />
        <RailItem screen="chats" icon={<IconHistory size={18} />} label="All chats" />
        <RailItem screen="backlog" icon={<IconBulb size={18} />} label="Backlog Projects" />
        <RailItem screen="templates" icon={<IconBookmark size={18} />} label="Templates · what good looks like" />
        {/* <RailItem screen="prototype" icon={<IconPrompt size={18} />} label="Prototype" /> */}
        {/* <RailItem screen="tickets" icon={<IconLayoutKanban size={18} />} label="Project Management" /> */}
      </div>

      <div className="sb-rail-spacer" />

      {/* Bottom icons */}
      <div className="sb-rail-bottom">
        <RailItem screen="sources" icon={<IconSources />} label="Sources" />
        <RailItem screen="settings" icon={<IconSettings size={18} />} label="Settings" />
        {/* Feedback / feature request — sits at the bottom next to sign-out. */}
        <button
          type="button"
          className="sb-rail-item"
          title="Feedback"
          aria-label="Feedback"
          onClick={() => setFeedbackOpen(true)}
        >
          <IconMessagePlus size={18} />
          <span className="nav-tooltip">Feedback</span>
        </button>
      </div>
      <div className="divider-nav" />

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

      <FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
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
