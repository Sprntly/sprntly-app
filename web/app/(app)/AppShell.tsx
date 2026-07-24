"use client"

import { useEffect } from "react"
import dynamic from "next/dynamic"
import { useNavigation } from "../context/NavigationContext"
import { AIBar, Toast, ApproveModal, ContentPanel } from "../components/shared"

// Conditionally-visible overlays: each renders null until opened, so load them
// as separate async chunks from their concrete files (bypassing the shared
// barrel) to keep them out of the first-paint shell chunk. ApproveModal is NOT
// split: even while the approve modal is closed it renders the GenerateModal /
// GenerationLoadingScreen subtree and hosts useGeneratePrototype's
// cross-surface `da:generating` listener, so it must be live immediately.
const InviteModal = dynamic(() =>
  import("../components/shared/InviteModal").then((m) => m.InviteModal)
)
const ClaudeDrawer = dynamic(() =>
  import("../components/shared/ClaudeDrawer").then((m) => m.ClaudeDrawer)
)
const TicketDrawer = dynamic(() =>
  import("../components/shared/TicketDrawer").then((m) => m.TicketDrawer)
)
const CommandPalette = dynamic(() =>
  import("../components/shared/CommandPalette").then((m) => m.CommandPalette)
)
import { useCompany } from "../context/CompanyContext"
import { useContent } from "../context/ContentContext"
import { profileDisplayName, useWorkspace } from "../context/WorkspaceContext"
import { useAuth } from "../lib/auth"
import { connectorsApi, teamApi, type TeamMemberRecord } from "../lib/api"
import { useBriefHydration } from "../lib/useBriefHydration"
import { cleanInsightTypes } from "../lib/insight-types"
import { DesignAgentNotificationReplay } from "../components/design-agent/DesignAgentNotificationReplay"
import { useGenerationNotify } from "./hooks/useGenerationNotify"

export function AppShell({ children }: { children: React.ReactNode }) {
  useGenerationNotify()
  const auth = useAuth()
  const { activeCompany } = useCompany()
  const { profile, workspace } = useWorkspace()
  const { setContent } = useContent()
  // useBriefHydration is the single owner of brief loading/polling (and the
  // auto-regenerate side effect). Call it ONCE here and mirror its coarse kind
  // (plus the regenerating-over-existing-brief flag) into ContentContext so the
  // brief surface can render its indicators without re-invoking the
  // side-effectful hook (which would double-trigger generation).
  const briefHydration = useBriefHydration(activeCompany)
  useEffect(() => {
    setContent({
      briefHydration: briefHydration.state.kind,
      briefRegenerating: briefHydration.regenerating,
    })
  }, [briefHydration.state.kind, briefHydration.regenerating, setContent])

  useEffect(() => {
    if (auth.kind !== "authed") return
    const name = profileDisplayName(profile, auth.user.email)
    if (!name) return
    setContent({
      userName: name,
      userEmail: profile?.email ?? auth.user.email ?? null,
      userInitials: name
        .split(/\s+/)
        .map((p) => p[0])
        .join("")
        .slice(0, 2)
        .toUpperCase(),
    })
  }, [auth, profile, setContent])

  useEffect(() => {
    if (!workspace) return
    const product = workspace.product?.name
    setContent({
      homeHeadline: product
        ? `Your ${product} workspace`
        : `${workspace.display_name} workspace`,
    })
  }, [workspace, setContent])

  useEffect(() => {
    // Skip until we have a signed-in session; the connectors route 401s
    // without a Bearer token (require_company → require_session).
    if (!workspace?.id) return
    void connectorsApi
      .list()
      .then((r) => {
        setContent({
          connectedConnectorIds: r.connections
            .filter((c) => c.status === "active")
            .map((c) => c.provider),
        })
      })
      .catch(() => {})
  }, [setContent, workspace?.id])

  // The Top Insights filter is workspace-level: an admin picks the insight
  // types in onboarding / Settings → Comms & Brief, stored on
  // companies.notification_settings.brief_insight_types, and every member sees
  // the same filtered brief. Empty = surface everything. Mirrored into
  // ContentContext so BriefChat reads it without taking on workspace deps.
  useEffect(() => {
    if (!workspace) return
    const types = cleanInsightTypes(workspace.notification_settings?.brief_insight_types)
    setContent({ insightTypeFilter: types })
  }, [setContent, workspace])

  // Load real team members from the database so ticket reassignment and
  // other assignee pickers show actual company users instead of demo data.
  useEffect(() => {
    if (!workspace?.id) return
    void teamApi.list()
      .then((r) => {
        const COLORS = ["#2A6EC8", "#634AB0", "#C13838", "#179463", "#C16A0B", "#0E6E49", "#4A554F"]
        const members = r.members.map((m: TeamMemberRecord, i: number) => {
          const name = m.display_name || m.email || "Unknown"
          const initials = name.split(/\s+/).slice(0, 2).map((w: string) => w[0]?.toUpperCase() ?? "").join("")
          return {
            id: m.user_id,
            name,
            email: m.email ?? "",
            initials,
            role: m.role.charAt(0).toUpperCase() + m.role.slice(1) as "Admin" | "Viewer",
            color: COLORS[i % COLORS.length],
          }
        })
        setContent({ teamMembers: members })
      })
      .catch(() => {})
  }, [setContent, workspace?.id])

  const {
    closeDrawers,
    closeModal,
    setShareMenuOpen,
    setReviewPastOpen,
    paletteOpen,
    closePalette,
    togglePalette,
  } = useNavigation()

  useEffect(() => {
    const handleKeydown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeDrawers()
        closeModal()
        setShareMenuOpen(false)
        setReviewPastOpen(false)
        closePalette()
      }
      // Global search (⌘K / Ctrl+K). Shift+K stays with the AI bar's
      // focus shortcut (see AIBar.tsx), so require shiftKey to be off.
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault()
        togglePalette()
      }
    }
    document.addEventListener("keydown", handleKeydown)
    return () => document.removeEventListener("keydown", handleKeydown)
  }, [closeDrawers, closeModal, setShareMenuOpen, setReviewPastOpen, closePalette, togglePalette])

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (!target.closest(".share-menu") && !target.closest('[class*="share"]')) {
        setShareMenuOpen(false)
      }
      if (!target.closest(".review-past-menu") && !target.closest(".review-past-wrap")) {
        setReviewPastOpen(false)
      }
    }
    document.addEventListener("click", handleClick)
    return () => document.removeEventListener("click", handleClick)
  }, [setShareMenuOpen, setReviewPastOpen])

  return (
    <>
      {children}
      <AIBar />
      <Toast />
      {/* P6-05 (#8): replay an unacknowledged completion toast on EVERY authed
          page (not only the Design section) after a same-session reload. Renders
          null; sits beside <Toast/> inside NavigationProvider. */}
      <DesignAgentNotificationReplay />
      <ApproveModal />
      <InviteModal />
      <CommandPalette open={paletteOpen} onClose={closePalette} />
      <ClaudeDrawer />
      <TicketDrawer />
      <ContentPanel />
    </>
  )
}
