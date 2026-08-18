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
import { useRouter } from "next/navigation"
import { IconLayoutKanban, IconMessageCircle, IconBulb } from "@tabler/icons-react"
import { useAuth } from "../../lib/auth"
import { useGuestSession } from "../../context/GuestSessionContext"
import { SprntlyMark } from "./SprntlyMark"

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
  const router = useRouter()
  const guestSession = useGuestSession()
  const displayName = useGuestDisplayName()
  const [signingOut, setSigningOut] = useState(false)
  const signingOutRef = useRef(false)

  const handleSignOut = useCallback(async () => {
    if (signingOutRef.current) return
    signingOutRef.current = true
    setSigningOut(true)
    try {
      await auth.signOut()
      // Left to its own devices, ArtifactShareGate (the wrapper above
      // GuestArtifactViewer) reacts to auth.kind flipping to "anonymous" by
      // rendering EntryGateScreen — the "shared with you" card, correct for
      // a first-time visitor but not what a deliberate sign-out should show.
      // Route to the real sign-in screen instead, same as EntryGateScreen's
      // own "Sign in" button — carrying the share token (or, for a bare-link
      // session with no token, the PRD's public_id — NEVER artifactId, which
      // is the raw sequential id this whole scope exists to stop exposing)
      // through. Not strictly load-bearing for correctness — postLoginPath()
      // re-resolves pending_share_token/pending_prd_public_id from
      // user_metadata on the NEXT sign-in regardless of this URL's query
      // string — but kept for symmetry/contextual UI, and degrades to a
      // bare /sign-in when guestSession is unexpectedly null.
      const query = guestSession?.token
        ? `?share=${encodeURIComponent(guestSession.token)}`
        : guestSession?.publicId
          ? `?prd=${encodeURIComponent(guestSession.publicId)}`
          : ""
      router.replace(`/sign-in${query}`)
    } finally {
      signingOutRef.current = false
      setSigningOut(false)
    }
  }, [auth, router, guestSession])

  return (
    <aside className="sidebar sidebar--collapsed" aria-label="Guest navigation">
      <div className="sb-rail-header">
        {/* The guest rail's glyph is Sprntly's own — unlike the signed-in
            sidebar, whose chip is the WORKSPACE's initial and stays a letter.
            A guest has no workspace, so "S." was standing in for the brand;
            the mark is the brand. */}
        <div className="sb-rail-logo sb-rail-logo--brand">
          <SprntlyMark size={17} title="Sprntly" />
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
