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
import { connectorsApi } from "../lib/api"
import { useBriefHydration } from "../lib/useBriefHydration"

export function AppShell({ children }: { children: React.ReactNode }) {
  const { activeCompany } = useCompany()
  const { setContent } = useContent()
  useBriefHydration(activeCompany)

  useEffect(() => {
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
  }, [setContent])

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
