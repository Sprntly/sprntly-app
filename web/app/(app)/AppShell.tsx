"use client"

import { useEffect } from "react"
import { useNavigation } from "../context/NavigationContext"
import {
  AIBar,
  Toast,
  ApproveModal,
  InviteModal,
  ClaudeDrawer,
  TicketDrawer,
} from "../components/shared"
import { useCompany } from "../context/CompanyContext"
import { useContent } from "../context/ContentContext"
import { profileDisplayName, useWorkspace } from "../context/WorkspaceContext"
import { useAuth } from "../lib/auth"
import { connectorsApi } from "../lib/api"
import { useBriefHydration } from "../lib/useBriefHydration"

export function AppShell({ children }: { children: React.ReactNode }) {
  const auth = useAuth()
  const { activeCompany } = useCompany()
  const { profile, workspace } = useWorkspace()
  const { setContent } = useContent()
  useBriefHydration(activeCompany)

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

  const { closeDrawers, closeModal, setShareMenuOpen, setReviewPastOpen } = useNavigation()

  useEffect(() => {
    const handleKeydown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeDrawers()
        closeModal()
        setShareMenuOpen(false)
        setReviewPastOpen(false)
      }
    }
    document.addEventListener("keydown", handleKeydown)
    return () => document.removeEventListener("keydown", handleKeydown)
  }, [closeDrawers, closeModal, setShareMenuOpen, setReviewPastOpen])

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
      <ApproveModal />
      <InviteModal />
      <ClaudeDrawer />
      <TicketDrawer />
    </>
  )
}
