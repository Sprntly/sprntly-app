"use client"

// A collapsed-only, non-interactive stand-in for the real Sidebar — required
// because GuestArtifactViewer never renders AppShell, so the real Sidebar
// (imported only by AppShell) is structurally impossible to reach here (AC21).
// Reuses the existing `.sidebar`/`.sb-rail-*` rail chrome, including the exact
// `.sb-rail-user`/`.sb-rail-avatar` identity chip the real Sidebar renders in
// its own collapsed state, rather than inventing new CSS.
//
// One deliberate divergence: the real Sidebar's avatar is display-only —
// sign-out lives in Settings → Account (Sidebar.tsx's own comment at its user
// row, and SettingsScreen.tsx's single "Sign out" button, direct-click, no
// confirmation). A guest has no Settings screen to reach (workspace-scoped,
// not mounted here at all), so this is the only place they CAN sign out —
// mirrors that same direct-click behavior on the avatar itself rather than
// inventing a menu.
import { useCallback, useRef, useState } from "react"
import { IconLayoutKanban, IconMessageCircle, IconBulb } from "@tabler/icons-react"
import { useAuth } from "../../lib/auth"

const DISABLED_NAV = [
  { label: "Top Insights", icon: <IconBulb size={18} /> },
  { label: "Chat", icon: <IconMessageCircle size={18} /> },
  { label: "Tickets", icon: <IconLayoutKanban size={18} /> },
]

function displayInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return "?"
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase()
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase()
}

// Real signed-in identity, not a static "GUEST" pill — this IS a real company
// member (auto-joined on domain match at signup), just without a workspace
// grant. useAuth() has no dependency on Workspace/Company context (grep,
// this worktree), so it's safe to read here even though GuestArtifactViewer
// never mounts WorkspaceProvider/CompanyProvider.
function useGuestDisplayName(): string {
  const auth = useAuth()
  if (auth.kind !== "authed") return "Guest"
  const meta = auth.user.user_metadata as { first_name?: string; last_name?: string } | undefined
  const name = [meta?.first_name, meta?.last_name].filter(Boolean).join(" ").trim()
  return name || auth.user.email || "Guest"
}

export function GuestRail() {
  const auth = useAuth()
  const displayName = useGuestDisplayName()
  const [signingOut, setSigningOut] = useState(false)
  const signingOutRef = useRef(false)

  const handleSignOut = useCallback(async () => {
    if (signingOutRef.current) return
    signingOutRef.current = true
    setSigningOut(true)
    try {
      // ArtifactShareGate (the wrapper above GuestArtifactViewer) watches
      // auth.kind and renders EntryGateScreen once it flips to "anonymous" —
      // no manual redirect/reload needed here.
      await auth.signOut()
    } finally {
      signingOutRef.current = false
      setSigningOut(false)
    }
  }, [auth])

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
      <div className="sb-rail-user">
        <button
          type="button"
          className="sb-rail-avatar"
          data-testid="guest-rail-pill"
          title={signingOut ? "Signing out…" : `${displayName} — Sign out`}
          onClick={handleSignOut}
          disabled={signingOut}
          aria-label={signingOut ? "Signing out" : `Sign out (${displayName})`}
          style={{ cursor: signingOut ? "default" : "pointer", border: "1px solid var(--accent-2)" }}
        >
          {displayInitials(displayName)}
        </button>
      </div>
    </aside>
  )
}
